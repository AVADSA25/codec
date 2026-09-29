"""Proactive check-in (UI phase 3, P3.5; docs/P3.5-DESIGN.md).

One more check inside the codec_heartbeat cycle, not a new daemon. Default off.
When on, every `every_minutes` during active hours, and only while the owner is
at the Mac and no image job holds memory, the LOCAL model gets a compact
snapshot (observer metadata without titles or screen text, calendar events of
the next 2 hours, due follow-ups, the count of unread important email, failing
services, the owner's checklist). It answers NO_REPLY unless something needs
attention; a hit becomes a `checkin` card on Home (Act, Snooze, Dismiss today,
Never for this pattern, through the /api/proactive endpoints), within a daily
cap, and can say one sentence aloud. Every run past the timing gates is audited
as `checkin_run` (metadata only).

Kill switches: config ``checkin.enabled`` (the Settings toggle) and the
CHECKIN_ENABLED=false environment variable.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

log = logging.getLogger("codec_checkin")

CONFIG_PATH = os.path.expanduser("~/.codec/config.json")
STATE_PATH = os.path.expanduser("~/.codec/checkin_state.json")
OBSERVER_PATH = os.path.expanduser("~/.codec/observer_buffer.json")
AWAY_AFTER_S = 600
PATTERN_PREFIX = "checkin:"
DEFAULTS: Dict[str, Any] = {"enabled": False, "every_minutes": 45, "active_from": "08:00", "active_until": "20:00",
                            "daily_cap": 4, "speak": False, "checklist": []}
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍]")


# ── settings ──────────────────────────────────────────────────────────────────
def _read_config() -> Dict[str, Any]:
    try:
        with open(CONFIG_PATH) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def settings() -> Dict[str, Any]:
    raw = _read_config().get("checkin")
    out = dict(DEFAULTS)
    if isinstance(raw, dict):
        out.update({k: raw[k] for k in DEFAULTS if k in raw})
    return out


def clean_settings(body: Dict[str, Any]) -> Dict[str, Any]:
    """Checked settings from the page; raises ValueError with a message."""
    out = settings()
    if "enabled" in body:
        out["enabled"] = bool(body["enabled"])
    if "speak" in body:
        out["speak"] = bool(body["speak"])
    if "every_minutes" in body:
        n = int(body["every_minutes"])
        if not 30 <= n <= 120:
            raise ValueError("Check in every 30 to 120 minutes.")
        out["every_minutes"] = n
    if "daily_cap" in body:
        n = int(body["daily_cap"])
        if not 1 <= n <= 12:
            raise ValueError("Allow 1 to 12 check-ins a day.")
        out["daily_cap"] = n
    for key in ("active_from", "active_until"):
        if key in body:
            v = str(body[key]).strip()
            if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v):
                raise ValueError("Active hours look like 08:00 and 20:00.")
            out[key] = v
    if out["active_from"] >= out["active_until"]:
        raise ValueError("Active hours must start before they end.")
    if "checklist" in body:
        items = body["checklist"]
        if isinstance(items, str):
            items = items.splitlines()
        items = [str(i).strip()[:200] for i in (items or []) if str(i).strip()]
        if len(items) > 20:
            raise ValueError("Keep the checklist to 20 lines.")
        out["checklist"] = items
    return out


def save_settings(body: Dict[str, Any]) -> Dict[str, Any]:
    import codec_jsonstore
    new = clean_settings(body)
    with codec_jsonstore.file_lock(CONFIG_PATH):
        config = _read_config()
        config["checkin"] = new
        codec_jsonstore.atomic_write_json(CONFIG_PATH, config)
    return new


# ── state: last run and today's hits ─────────────────────────────────────────
def _read_state() -> Dict[str, Any]:
    try:
        with open(STATE_PATH) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(state: Dict[str, Any]) -> None:
    import codec_jsonstore
    codec_jsonstore.atomic_write_json(STATE_PATH, state)


def status(now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now()
    state = _read_state()
    today = now.strftime("%Y-%m-%d")
    return {"last_run": state.get("last_run"), "last_outcome": state.get("last_outcome"),
            "hits_today": int(state.get("hits", {}).get(today, 0))}


# ── the snapshot (metadata only; for the local model) ────────────────────────
def strip_emoji(text: str) -> str:
    return " ".join(_EMOJI.sub("", str(text or "")).split())


def _observer_apps(now: datetime) -> Dict[str, Any]:
    """The current app and the apps of the last 10 minutes: names only."""
    try:
        with open(OBSERVER_PATH) as f:
            data = json.load(f)
        entries = data.get("entries") if isinstance(data, dict) else data
    except (OSError, ValueError):
        return {}
    apps: List[str] = []
    for e in entries or []:
        win = (e or {}).get("active_window") or {}
        app = win.get("app") if isinstance(win, dict) else None
        if app and app not in apps:
            apps.append(str(app))
    current = None
    if entries:
        win = (entries[-1] or {}).get("active_window") or {}
        current = win.get("app") if isinstance(win, dict) else None
    return {"current_app": current, "recent_apps": apps[-8:]}


def _calendar_next_hours() -> str:
    try:
        import codec_daybreak
        text = codec_daybreak._run_source("google_calendar", "what's on my calendar today")
        return str(text or "")[:1500]
    except Exception:
        return ""


def _follow_ups() -> List[str]:
    try:
        import codec_daybreak
        return [t["text"] for t in codec_daybreak.get_open_threads() if t.get("kind") in ("follow_up", "waiting_on")][:10]
    except Exception:
        return []


def _important_unread() -> Optional[int]:
    try:
        import importlib.util
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills", "google_gmail.py")
        spec = importlib.util.spec_from_file_location("google_gmail_checkin", path)
        gmail = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gmail)
        res = gmail._get_service().users().messages().list(
            userId="me", q="is:unread is:important in:inbox", maxResults=50).execute()
        return len(res.get("messages") or [])
    except Exception:
        return None


def _failing_services() -> List[str]:
    """CODEC PM2 apps PM2 gave up restarting, as plain words (read only: the
    heartbeat's own check sends the owner alerts; this one must not)."""
    try:
        import codec_heartbeat
        return [strip_emoji(f"{p.get('name')} has crashed and stopped restarting")
                for p in codec_heartbeat._pm2_jlist() or []
                if codec_heartbeat._is_codec_app(p.get("name", "")) and (p.get("pm2_env") or {}).get("status") == "errored"][:10]
    except Exception:
        return []


def snapshot(now: datetime, cfg: Dict[str, Any]) -> Dict[str, Any]:
    return {"now": now.strftime("%A %H:%M"), "observer": _observer_apps(now),
            "calendar_today": _calendar_next_hours(), "follow_ups": _follow_ups(),
            "unread_important_email": _important_unread(), "failing_services": _failing_services(),
            "checklist": list(cfg.get("checklist") or [])}


# ── the model (local only) ────────────────────────────────────────────────────
_SYSTEM = ("You are CODEC's quiet check-in. Look at the snapshot of the owner's day. Reply with exactly NO_REPLY "
           "unless something needs their attention in the next two hours (a meeting about to start that they may "
           "not be ready for, a follow-up that is due, important unread mail piling up, a failing service, an item "
           "on their checklist). If something does, reply with one JSON object and nothing else: "
           '{"pattern": "short-slug", "title": "few words", "message": "one or two sentences", '
           '"spoken": "one short sentence to say aloud"}. No emoji.')


def _ask_model(snap: Dict[str, Any]) -> str:
    import codec_cloud_models
    import codec_llm
    from codec_config import get_llm_api_key
    config = _read_config()
    base_url, model = codec_cloud_models.local_only(config.get("llm_base_url", "http://localhost:8083/v1"),
                                                    config.get("llm_model", "mlx-community/Qwen3.6-35B-A3B-4bit"),
                                                    config)
    if not codec_cloud_models.is_local_url(base_url):
        raise RuntimeError("no local model")
    answer = codec_llm.call([{"role": "system", "content": _SYSTEM},
                             {"role": "user", "content": json.dumps(snap, ensure_ascii=False)}],
                            base_url=base_url, model=model, api_key=get_llm_api_key() or "", max_tokens=300,
                            temperature=0.2, timeout=120, extra_kwargs=config.get("llm_kwargs") or {},
                            raise_on_error=True)
    return re.sub(r"<think>[\s\S]*?</think>", "", str(answer or "")).strip()


def parse_answer(answer: str) -> Optional[Dict[str, str]]:
    """None for NO_REPLY (or anything unreadable); else pattern, title, message, spoken."""
    text = str(answer or "").strip()
    if not text or text.upper().startswith("NO_REPLY"):
        return None
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    message = strip_emoji(data.get("message") or "")
    if not message:
        return None
    slug = re.sub(r"[^a-z0-9-]+", "-", str(data.get("pattern") or "attention").lower()).strip("-")[:40] or "attention"
    return {"pattern": PATTERN_PREFIX + slug, "title": strip_emoji(data.get("title") or "Something needs you")[:80],
            "message": message[:600], "spoken": strip_emoji(data.get("spoken") or message)[:200]}


# ── the run ───────────────────────────────────────────────────────────────────
def _audit(outcome: str, reason: str = "", **extra) -> None:
    try:
        from codec_audit import log_event
        log_event("checkin_run", "codec-heartbeat", f"Check-in: {outcome}{(' (' + reason + ')') if reason else ''}",
                  extra={"result": outcome, "reason": reason, **extra},  # "outcome" is an envelope field
                  level="warning" if outcome == "error" else "info")
    except Exception:
        pass


def _idle_seconds() -> float:
    try:
        import codec_observer
        return float(codec_observer._idle_seconds())
    except Exception:
        return 1e9


def _image_busy() -> bool:
    try:
        import codec_image
        return bool(codec_image.busy_message())
    except Exception:
        return False


def _enabled(cfg: Dict[str, Any]) -> bool:
    env = (os.environ.get("CHECKIN_ENABLED") or "true").strip().lower()
    return bool(cfg.get("enabled")) and env not in ("0", "false", "no", "off")


def run_checkin(now: Optional[datetime] = None) -> str:
    """One heartbeat's check-in. Returns what happened (for logs and tests)."""
    now = now or datetime.now()
    cfg = settings()
    if not _enabled(cfg):
        return "off"
    state = _read_state()
    last = state.get("last_run")
    try:
        if last and now - datetime.fromisoformat(last) < timedelta(minutes=int(cfg["every_minutes"])):
            return "not yet"
    except ValueError:
        pass
    hhmm = now.strftime("%H:%M")
    if not cfg["active_from"] <= hhmm < cfg["active_until"]:
        return "outside hours"
    today = now.strftime("%Y-%m-%d")
    state["last_run"] = now.isoformat(timespec="seconds")
    hits = {today: int(state.get("hits", {}).get(today, 0))}

    def done(outcome: str, reason: str = "", **extra) -> str:
        state["last_outcome"], state["hits"] = outcome, hits
        _write_state(state)
        _audit(outcome, reason, hits_today=hits[today], **extra)
        return outcome if not reason else f"{outcome}: {reason}"

    if _idle_seconds() > AWAY_AFTER_S:
        return done("skipped", "away")
    if _image_busy():
        return done("skipped", "image job")
    if hits[today] >= int(cfg["daily_cap"]):
        return done("skipped", "daily cap")
    try:
        answer = _ask_model(snapshot(now, cfg))
    except Exception as e:
        return done("error", type(e).__name__)
    hit = parse_answer(answer)
    if not hit:
        return done("no_reply")
    import codec_proactive
    if codec_proactive.is_pattern_killed(hit["pattern"]) or codec_proactive.is_pattern_dismissed_today(hit["pattern"]):
        return done("dropped", "pattern off", pattern=hit["pattern"])
    import codec_today
    codec_today.add_card(hit["title"], hit["message"], kind="checkin", source=hit["pattern"])
    hits[today] += 1
    if cfg.get("speak"):
        try:
            import codec_scheduler
            codec_scheduler._speak_on_mac(hit["spoken"])
        except Exception as e:
            log.debug("check-in speak failed: %s", e)
    return done("hit", pattern=hit["pattern"])
