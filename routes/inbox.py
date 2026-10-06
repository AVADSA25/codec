"""One Inbox (UI phase 3, P3.2; docs/P3.2-DESIGN.md).

Everything that needs the owner or reports back, from one endpoint the shell
polls instead of five: approvals waiting in the dashboard's queue, open
ask_user questions, and the reports, agent updates and suggestions in
~/.codec/notifications.json. The server decides which actions an item offers
and only ever offers the endpoints `_ACTION_OK` allows; there is no
"always allow".
"""
from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from routes import _shared

router = APIRouter()

PREVIEW = 300        # characters of text in the list; the item route gives the rest
MAX_LISTED = 150     # newest notification items listed (needs-you items always are)
_AGENT_TYPES = {"agent_update", "agent_blocked", "agent_question", "agent_done", "agent_aborted"}
_REPORT_TYPES = {"task_report", "shift_report"}
_ITEM_ID = re.compile(r"^(notif|auto)_[0-9a-z]{1,40}$")
_AGENT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_ACTION_OK = re.compile(r"^/api/(agents/[A-Za-z0-9_-]{1,64}/(pause|resume|grant)|proactive/(acknowledge|dismiss))$")


def _item_id(n: dict) -> str:
    """The notification's id, or a stable one for entries written without it
    (agent messages and suggestions): type, agent and first post time. The title
    is used only when there is no time (a batched agent update retitles itself)."""
    nid = str(n.get("id") or "")
    if _ITEM_ID.match(nid):
        return nid
    when = str(n.get("ts") or n.get("created") or "")
    key = "|".join([str(n.get("type") or ""), str(n.get("agent_id") or ""), when or str(n.get("title") or "")])
    return "auto_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]


def _created(n: dict) -> str:
    return str(n.get("created") or n.get("ts") or n.get("timestamp") or "")


def _epoch(value) -> float:
    """Seconds for sorting: writers use local time without a zone ("created") or
    UTC with one ("ts"); a number is already seconds."""
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _preview(text) -> tuple[str, bool]:
    s = str(text or "")
    return (s[:PREVIEW], len(s) > PREVIEW)


def _grant_kind(reason: str) -> str:
    """Chat's _grantKind: the permission kind a block reason names."""
    r = (reason or "").lower()
    if "domain" in r:
        return "network_domains"
    if "skill" in r:
        return "skills"
    if "write" in r:
        return "write_paths"
    if "read" in r:
        return "read_paths"
    return "network_domains"


def _agent_status(agent_id: str, cache: dict) -> str:
    if agent_id not in cache:
        status = ""
        if _AGENT_ID.match(agent_id or ""):
            try:
                import codec_agent_plan
                status = str(codec_agent_plan.load_manifest(agent_id).get("status") or "")
            except Exception:
                status = ""
        cache[agent_id] = status
    return cache[agent_id]


def _agent_actions(n: dict, status: str) -> list:
    aid = str(n.get("agent_id") or "")
    if not _AGENT_ID.match(aid):
        return []
    base = f"/api/agents/{aid}/"
    if status == "running":
        return [{"label": "Pause", "endpoint": base + "pause"}]
    if status == "paused":
        return [{"label": "Resume", "endpoint": base + "resume"}]
    if status.startswith("blocked") and n.get("type") == "agent_blocked":
        for a in n.get("actions") or []:
            hint = (a or {}).get("body_hint") or {}
            if (a or {}).get("label") == "Grant" and isinstance(hint.get("value"), str) and hint["value"].strip():
                value = hint["value"].strip()[:500]
                return [{"label": "Grant", "endpoint": base + "grant", "confirm": True,
                         "body": {"kind": _grant_kind(str(n.get("title") or "")), "value": value}}]
        return [{"label": "Resume", "endpoint": base + "resume"}]
    return []


def _suggestion_actions(n: dict) -> list:
    out = []
    for a in n.get("actions") or []:
        a = a or {}
        ep, body = str(a.get("endpoint") or ""), a.get("body_hint")
        if _ACTION_OK.match(ep) and (body is None or isinstance(body, dict)):
            out.append({"label": str(a.get("label") or "OK")[:40], "endpoint": ep, "body": body or {}})
    return out


def _approvals() -> list:
    with _shared._approval_lock:
        _shared._evict_expired_approvals()
        pending = [(aid, dict(a)) for aid, a in _shared._pending_approvals.items() if a.get("status") == "pending"]
    items = []
    for aid, a in pending:
        ts = float(a.get("timestamp") or 0)
        items.append({
            "id": f"approval_{aid}", "kind": "approval", "group": "needs_you", "read": False, "at": ts,
            "title": "Dangerous command" if a.get("is_dangerous") else "A command needs your approval",
            "body": str(a.get("explanation") or "")[:PREVIEW], "command": str(a.get("command") or "")[:300],
            "dangerous": bool(a.get("is_dangerous")), "approval_id": aid,
            "created": datetime.fromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%S") if ts else "",
            "expires_in": max(0, int(_shared._APPROVAL_TTL_SECONDS - (time.time() - ts))) if ts else None,
        })
    return items


def _questions() -> list:
    try:
        from codec_ask_user import _load_pending_questions
        records = _load_pending_questions().get("pending_questions", [])
    except Exception:
        return []
    items = []
    for q in records:
        if q.get("status") != "pending":
            continue
        if q.get("deadline") and 0 < _epoch(q["deadline"]) <= time.time():
            continue  # past its deadline: it can no longer be answered
        agent = q.get("agent")
        items.append({
            "id": f"question_{q.get('id')}", "kind": "question", "group": "needs_you", "read": False,
            "title": f"{agent} is asking" if agent else "CODEC is asking",
            "body": str(q.get("question") or "")[:2000], "question_id": q.get("id"),
            "options": [str(o)[:120] for o in (q.get("options") or [])][:8],
            "deadline": q.get("deadline"), "strict": bool(q.get("consent_strict")),
            "verb": q.get("destructive_verb") if q.get("consent_strict") else None,
            "agent": agent, "created": str(q.get("asked_at") or ""), "at": _epoch(q.get("asked_at")),
        })
    return items


def _notification_items(notifs: list) -> list:
    cache: dict = {}
    items = []
    for n in notifs:
        t = n.get("type") or "task_report"
        if t == "question":
            continue  # the pending question is the item; answered ones are history
        if t in _REPORT_TYPES:
            kind, group = "report", "reports"
        elif t in _AGENT_TYPES:
            kind, group = "agent", "agents"
        elif t == "proactive_suggestion":
            kind, group = "suggestion", "suggestions"
        else:
            continue
        text, more = _preview(n.get("body") or n.get("message"))
        item = {"id": _item_id(n), "kind": kind, "group": group, "type": t,
                "title": str(n.get("title") or ("Report" if kind == "report" else t.replace("_", " ")))[:200],
                "body": text, "more": more, "created": _created(n), "at": _epoch(_created(n)), "read": bool(n.get("read")),
                "status": str(n.get("status") or "")}
        if kind == "report":
            doc = n.get("doc_url")
            item["doc_url"] = doc if isinstance(doc, str) and doc.startswith("https://") else None
            if item["status"] == "running":
                item["read"] = True  # not finished: it does not count as new yet
        elif kind == "agent":
            aid = str(n.get("agent_id") or "")
            status = _agent_status(aid, cache)
            item.update(agent_id=aid, agent_status=status, actions=_agent_actions(n, status))
            if t == "agent_blocked" and status.startswith("blocked"):
                item["group"] = "needs_you"
        else:
            item["actions"] = _suggestion_actions(n)
        items.append(item)
    # One needs-you card per blocked agent: its newest block.
    seen, out = set(), []
    for it in sorted(items, key=lambda i: i["at"], reverse=True):
        if it["group"] == "needs_you":
            if it["agent_id"] in seen:
                it["group"] = "agents"
            seen.add(it["agent_id"])
        out.append(it)
    return out


@router.get("/api/inbox")
async def inbox():
    """Every item, newest first, with counts for the badge and the filters."""
    notif_items = _notification_items(_shared._load_notifications())
    needs = [i for i in notif_items if i["group"] == "needs_you"]
    rest = [i for i in notif_items if i["group"] != "needs_you"][:MAX_LISTED]
    items = sorted(_approvals() + _questions() + needs, key=lambda i: i["at"], reverse=True) + rest
    counts = {"needs_you": sum(1 for i in items if i["group"] == "needs_you")}
    for g in ("reports", "agents", "suggestions"):
        counts[g] = sum(1 for i in notif_items if i["group"] == g and not i["read"])
    counts["badge"] = counts["needs_you"] + counts["reports"] + counts["agents"] + counts["suggestions"]
    return {"items": items, "counts": counts}


@router.get("/api/inbox/item/{item_id}")
async def inbox_item(item_id: str):
    """One report, agent update or suggestion with its whole text (Open; Chat's #report=)."""
    if not _ITEM_ID.match(item_id):
        return JSONResponse({"error": "bad id"}, status_code=400)
    for n in _shared._load_notifications():
        if _item_id(n) == item_id and (n.get("type") or "task_report") != "question":
            doc = n.get("doc_url")
            return {"id": item_id, "type": n.get("type") or "task_report", "title": str(n.get("title") or ""),
                    "body": str(n.get("body") or n.get("message") or ""), "created": _created(n),
                    "doc_url": doc if isinstance(doc, str) and doc.startswith("https://") else None}
    return JSONResponse({"error": "not found"}, status_code=404)


@router.post("/api/inbox/read")
async def inbox_read(request: Request):
    """Mark items read. An id-less notification gets its id written on, so it is found directly next time."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    ids = body.get("ids") if isinstance(body, dict) else None
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        return JSONResponse({"error": "ids must be a list of item ids"}, status_code=400)
    want = {i for i in ids[:500] if _ITEM_ID.match(i)}
    if not want:
        return {"marked": 0}
    import codec_jsonstore
    marked = 0
    with _shared._notif_lock, codec_jsonstore.file_lock(_shared.NOTIFICATIONS_PATH):
        notifs = _shared._load_notifications()
        for n in notifs:
            iid = _item_id(n)
            if iid in want:
                n.setdefault("id", iid)
                if not n.get("read"):
                    n["read"] = True
                    marked += 1
        if marked:
            _shared._write_notifications(notifs)
    return {"marked": marked}
