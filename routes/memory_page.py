"""Settings > Memory (UI phase 2, P2.4; docs/P2.4-DESIGN.md): see, correct and forget what CODEC
remembers. Small routes over existing functions: facts are superseded or closed, never deleted.
Every change is one `memory_changed` audit event with metadata only (never a value). Behind the
dashboard login like every /api route; PUT, POST and DELETE are CSRF-checked."""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()

CONFIG_PATH = os.path.expanduser("~/.codec/config.json")
FACT_VALUE_MAX = 1000
ABOUT_MAX = 1500
NIGHTLY_ID = "sched_auto_memorize"
NIGHTLY_WHEN = "every day at 03:30"


def _audit(action: str, **extra) -> None:
    try:
        from codec_audit import log_event
        log_event("memory_changed", "codec-dashboard", f"Memory page: {action}", extra={"action": action, **extra})
    except Exception:
        pass


async def _body(request: Request) -> Dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        body = None
    return body if isinstance(body, dict) else {}


def _bad(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


# ── Facts and working threads ─────────────────────────────────────────────────
@router.get("/api/memory/facts")
def facts():
    import codec_daybreak
    import codec_memory_upgrade as cmu
    rows = [f for f in cmu.query_valid_facts(limit=500) if not str(f.get("key") or "").startswith("thread:")]
    return {"facts": rows, "threads": codec_daybreak.get_open_threads()}


@router.put("/api/memory/facts")
async def edit_fact(request: Request):
    """A new value for an active fact; the old one stays in its history (supersede)."""
    import codec_memory_upgrade as cmu
    body = await _body(request)
    key, value = str(body.get("key") or ""), str(body.get("value") or "").strip()
    if not key or key.startswith("thread:"):
        return _bad("Pick a fact to edit.")
    if not value:
        return _bad("A fact needs a value. To remove it, use Forget.")
    if len(value) > FACT_VALUE_MAX:
        return _bad(f"A fact holds {FACT_VALUE_MAX} characters at most.")
    current = cmu.query_valid_facts(key=key, limit=1)
    if not current:
        return _bad("That fact is no longer active.", 404)
    if current[0]["value"] == value:
        return {"ok": True, "changed": False}
    cmu.store_fact(key, value, fact_type=current[0].get("fact_type") or "generic", source="memory page", supersede=True)
    _audit("fact_edited", key=key, value_len=len(value))
    return {"ok": True, "changed": True}


@router.post("/api/memory/facts/forget")
async def forget_fact(request: Request):
    """Close an active fact (valid_until = now). Its history stays."""
    import codec_memory_upgrade as cmu
    key = str((await _body(request)).get("key") or "")
    if not key or key.startswith("thread:"):
        return _bad("Pick a fact to forget.")
    rows = cmu.expire_fact(key)
    if not rows:
        return _bad("That fact is no longer active.", 404)
    _audit("fact_forgotten", key=key, rows=rows)
    return {"ok": True}


@router.get("/api/memory/facts/history")
def fact_history(key: str = ""):
    import codec_memory_upgrade as cmu
    if not key:
        return _bad("Pick a fact.")
    return {"key": key, "versions": cmu.get_fact_history(key)[:50]}


# ── Standing rules ────────────────────────────────────────────────────────────
@router.get("/api/memory/rules")
def rules():
    import codec_standing_rules as sr
    return {"rules": sr.list_rules(), "max_rules": sr.MAX_RULES, "max_chars": sr.MAX_RULE_CHARS}


@router.post("/api/memory/rules")
async def add_rule(request: Request):
    import codec_standing_rules as sr
    text = str((await _body(request)).get("text") or "")
    res = sr.add_rule(text)
    if not res.get("ok"):
        return _bad(res.get("message") or "Could not add the rule.")
    _audit("rule_added", rule_id=res["rule"]["id"], text_len=len(res["rule"]["text"]), rules=len(sr.list_rules()))
    return {"ok": True, "rules": sr.list_rules()}


@router.delete("/api/memory/rules/{rule_id}")
def remove_rule(rule_id: str):
    import codec_standing_rules as sr
    if not any(r.get("id") == rule_id for r in sr.list_rules()):
        return _bad("No rule has that id.", 404)
    res = sr.remove_rule(rule_id)
    if not res.get("ok"):
        return _bad(res.get("message") or "Could not remove the rule.")
    _audit("rule_removed", rule_id=rule_id, rules=len(sr.list_rules()))
    return {"ok": True, "rules": sr.list_rules()}


@router.post("/api/memory/rules/clear")
def clear_rules():
    import codec_standing_rules as sr
    n = len(sr.list_rules())
    sr.clear_rules()
    _audit("rules_cleared", rules=n)
    return {"ok": True, "rules": []}


# ── About me (identity.txt: voice and the wake word read it) ──────────────────
@router.get("/api/memory/about")
def about():
    import codec_memory_upgrade as cmu
    return {"text": cmu.load_identity(), "max": ABOUT_MAX}


@router.put("/api/memory/about")
async def save_about(request: Request):
    import codec_memory_upgrade as cmu
    text = str((await _body(request)).get("text") or "").replace("\r\n", "\n").strip()
    if len(text) > ABOUT_MAX:
        return _bad(f"About me holds {ABOUT_MAX} characters at most.")
    os.makedirs(os.path.dirname(cmu.IDENTITY_PATH), exist_ok=True)
    tmp = cmu.IDENTITY_PATH + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text + ("\n" if text else ""))
    os.chmod(tmp, 0o600)
    os.replace(tmp, cmu.IDENTITY_PATH)
    _audit("about_saved", text_len=len(text))
    return {"ok": True, "text": text}


# ── The two switches ─────────────────────────────────────────────────────────
def _read_config() -> Dict[str, Any]:
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        return {}


def _nightly_job():
    import codec_scheduler
    return next((s for s in codec_scheduler.load_schedules() if s.get("id") == NIGHTLY_ID), None)


def settings() -> Dict[str, Any]:
    import codec_scheduler
    mem = _read_config().get("memory")
    mem = mem if isinstance(mem, dict) else {}
    job = _nightly_job()
    when = codec_scheduler.job_view(job)["summary"] if job else codec_scheduler.describe_when(
        codec_scheduler.parse_when(NIGHTLY_WHEN))
    return {"fact_extract": mem.get("fact_extract", True) is not False,
            "nightly": bool(job and job.get("enabled")), "nightly_when": when,
            "nightly_last_run": (job or {}).get("last_run")}


def _set_nightly(on: bool) -> None:
    """The managed schedule job that runs auto_memorize each night; created on first switch-on.
    It delivers nothing (`silent`): the run history in Tasks is the record."""
    import codec_scheduler
    now = datetime.now().isoformat()

    def mutate(schedules):
        job = next((s for s in schedules if s.get("id") == NIGHTLY_ID), None)
        if job is None:
            if not on:
                return
            when = codec_scheduler.parse_when(NIGHTLY_WHEN)
            job = {"id": NIGHTLY_ID, "kind": "skill", "skill": "auto_memorize", "task": "last 24 hours",
                   "label": "Learn from the day's chats", "deliver": ["silent"], "created": now,
                   "enabled": False, "last_run": None, "managed": "memory", "when": when,
                   "hour": when["hour"], "minute": when["minute"], "days": when["days"]}
            schedules.append(job)
        if on and not job.get("enabled"):
            job["enabled_at"] = now
        job["enabled"] = on

    codec_scheduler._update_schedules(mutate)


@router.get("/api/memory/settings")
def memory_settings():
    return settings()


@router.put("/api/memory/settings")
async def save_memory_settings(request: Request):
    body = await _body(request)
    if "fact_extract" in body:
        import codec_jsonstore
        with codec_jsonstore.file_lock(CONFIG_PATH):
            cfg = _read_config()
            mem = cfg.get("memory") if isinstance(cfg.get("memory"), dict) else {}
            mem["fact_extract"] = bool(body["fact_extract"])
            cfg["memory"] = mem
            codec_jsonstore.atomic_write_json(CONFIG_PATH, cfg)
        _audit("fact_extract_switched", on=bool(body["fact_extract"]))
    if "nightly" in body:
        _set_nightly(bool(body["nightly"]))
        _audit("nightly_switched", on=bool(body["nightly"]))
    return settings()
