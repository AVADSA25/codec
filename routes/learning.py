"""Settings > Learning (UI phase 3, P3.12; docs/P3.12-DESIGN.md).

Read-only routes over what CODEC proposes and learns:
- the skill proposals the self-improvement run drafted in ~/.codec/skill_proposals/
  (Review and Approve on the page go through the existing /api/skill/review and
  /api/skill/approve gates; nothing here writes a skill);
- a readable diary of the last days: facts learned, skills proposed, approved or
  turned down. Built when asked, so there is no nightly job;
- the active facts fact_extract saved (Edit and Forget use the Memory routes).
"""
from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timedelta

from fastapi import APIRouter

router = APIRouter()

PROPOSALS_ROOT = os.path.expanduser("~/.codec/skill_proposals")
CODE_MAX = 20000
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")


def _read(path: str, limit: int) -> str | None:
    """A regular file's text (not a link), at most `limit` characters."""
    if os.path.islink(path) or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return f.read(limit)
    except (OSError, UnicodeDecodeError):
        return None


def _note(md: str) -> dict:
    """What the proposal note says: its check result, why it failed, what prompted it."""
    status = "passed" if "✅ PASSED" in md else "rejected" if "❌ REJECTED" in md else "unknown"

    def field(label):
        m = re.search(r"\*\*" + re.escape(label) + r":\*\*\s*(.+)", md)
        return m.group(1).strip()[:200] if m else ""

    reason = re.search(r"- Reason:\s*(.+)", md)
    return {"status": status, "reason": reason.group(1).strip()[:300] if reason and status == "rejected" else "",
            "gap_kind": field("Gap kind").replace("_", " "), "tool": field("Triggering tool"),
            "signals": field("Signal count")}


def _installed() -> set:
    names = set()
    try:
        from codec_dispatch import registry
        if not registry.names():
            registry.scan()
        names |= set(registry.names())
    except Exception:
        pass
    try:
        from routes._shared import _get_skills_dir
        names |= {f[:-3] for f in os.listdir(_get_skills_dir()) if f.endswith(".py")}
    except OSError:
        pass
    try:  # approved skills go to the user folder; one approved before a restart is not in the registry yet
        from codec_config import USER_SKILLS_DIR
        names |= {f[:-3] for f in os.listdir(USER_SKILLS_DIR) if f.endswith(".py") and not f.startswith("_")}
    except OSError:
        pass
    return names


def all_proposals() -> list:
    """Every proposal on disk, newest date first. Folder and file names are checked, links skipped."""
    out = []
    try:
        dates = sorted((d for d in os.listdir(PROPOSALS_ROOT) if _DATE_RE.match(d)), reverse=True)
    except OSError:
        return out
    for date in dates:
        folder = os.path.join(PROPOSALS_ROOT, date)
        if os.path.islink(folder) or not os.path.isdir(folder):
            continue
        for fn in sorted(os.listdir(folder)):
            name = fn[:-3] if fn.endswith(".py") else ""
            if not _NAME_RE.match(name):
                continue
            code = _read(os.path.join(folder, fn), CODE_MAX + 1)
            if code is None:
                continue
            md = _read(os.path.join(folder, name + ".md"), 50000) or ""
            out.append({"date": date, "name": name, **_note(md), "code": code[:CODE_MAX],
                        "truncated": len(code) > CODE_MAX})
    return out


@router.get("/api/learning/proposals")
def proposals():
    """The newest proposal per skill name, with the other days it was proposed."""
    installed, seen, out = _installed(), {}, []
    for p in all_proposals():
        if p["name"] in seen:
            seen[p["name"]]["earlier"].append(p["date"])
            continue
        p = dict(p, installed=p["name"] in installed, earlier=[])
        seen[p["name"]] = p
        out.append(p)
    return {"proposals": out}


def learned_facts(limit: int = 200) -> list:
    import codec_memory_upgrade as cmu
    # An edited learned fact keeps its learned:<hash> key, but the Memory routes save the
    # new value with their own source, so match the key too or Edit drops it from the list.
    rows = [f for f in cmu.query_valid_facts(limit=2000)
            if f.get("source") == "fact_extract" or str(f.get("key") or "").startswith("learned:")]
    rows.sort(key=lambda f: str(f.get("valid_from") or ""), reverse=True)
    return rows[:limit]


@router.get("/api/learning/facts")
def facts():
    return {"facts": learned_facts()}


def older_fact_count() -> int:
    """Facts fact_extract kept only as conversation rows (before P3.12 they never reached the facts table)."""
    try:
        from codec_config import DB_PATH
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=2)
        try:
            texts = {r[0] for r in con.execute(
                "SELECT content FROM conversations WHERE role='fact' AND session_id='fact_extract'")}
        finally:
            con.close()
    except Exception:
        return 0
    return len(texts - {f.get("value") for f in learned_facts(limit=5000)})


def _local_day(ts) -> str:
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d")
    except ValueError:
        return str(ts)[:10]


@router.get("/api/learning/diary")
def diary(days: int = 14):
    days = max(1, min(int(days), 60))
    start = (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    by_day: dict = {}

    def day(d):
        return by_day.setdefault(d, {"date": d, "facts": [], "proposed": [], "approved": [], "turned_down": []})

    for f in learned_facts(limit=1000):
        d = str(f.get("valid_from") or "")[:10]
        if d >= start:
            day(d)["facts"].append(str(f.get("value") or "")[:300])
    for p in all_proposals():
        if p["date"] >= start:
            day(p["date"])["proposed"].append({"name": p["name"], "status": p["status"]})
    try:
        import codec_audit
        events = codec_audit.read_events(since=start + "T00:00:00", limit=5000)
    except Exception:
        events = []
    for e in events:
        kind = {"skill_approved": "approved", "skill_review_rejected": "turned_down"}.get(e.get("event"))
        name = str(((e.get("extra") or {}).get("filename")) or "").removesuffix(".py")[:64]
        d = _local_day(e.get("ts"))
        if kind and name and d >= start:
            day(d)[kind].append(name)
    return {"days": sorted(by_day.values(), key=lambda x: x["date"], reverse=True), "older_facts": older_fact_count()}
