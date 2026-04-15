"""CODEC Autopilot — ambient autonomous scheduler.

Runs as its own PM2 process. Reads ~/.codec/autopilot.json for triggers and
fires CODEC skills at the appointed time. Lightweight: single thread,
polls every 30s.

Config schema (~/.codec/autopilot.json):
    {
      "enabled": true,
      "timezone": "Europe/Madrid",
      "triggers": [
        {"name": "morning_briefing", "at": "07:30", "days": "weekdays",
         "skill": "google_calendar", "task": "list today events", "tts": true},
        {"name": "news_digest", "at": "07:45", "days": "daily",
         "skill": "ai_news_digest", "task": "latest AI news"},
        {"name": "weather_check", "at": "08:00", "days": "daily",
         "skill": "weather", "task": "weather in Marbella", "tts": true}
      ]
    }

Days: "daily" | "weekdays" | "weekends" | comma list e.g. "mon,wed,fri"

Each trigger fires at most once per day. State tracked in
~/.codec/autopilot_state.json.
"""
from __future__ import annotations

import json
import os
import sys
import time
import logging
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

_REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "skills"))

from codec_skill_registry import SkillRegistry
from codec_config import SKILLS_DIR
from codec_audit import audit

log = logging.getLogger("codec-autopilot")
logging.basicConfig(
    level=logging.INFO,
    format="[autopilot] %(asctime)s %(message)s",
    datefmt="%H:%M:%S",
)

CONFIG = Path(os.path.expanduser("~/.codec/autopilot.json"))
STATE = Path(os.path.expanduser("~/.codec/autopilot_state.json"))
POLL_SEC = 30


DAY_CODES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _load_config() -> dict:
    if not CONFIG.exists():
        default = {
            "enabled": False,
            "timezone": "Europe/Madrid",
            "triggers": [],
            "_comment": "Set enabled=true and add triggers. See codec_autopilot.py docstring.",
        }
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps(default, indent=2))
        return default
    try:
        return json.loads(CONFIG.read_text())
    except Exception as e:
        log.error("config parse error: %s", e)
        return {"enabled": False, "triggers": []}


def _load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except Exception:
            pass
    return {}


def _save_state(state: dict):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    os.replace(tmp, STATE)


def _day_matches(days_spec: str, weekday: int) -> bool:
    s = (days_spec or "daily").lower().strip()
    if s == "daily":
        return True
    if s == "weekdays":
        return weekday < 5
    if s == "weekends":
        return weekday >= 5
    # comma list
    want = {x.strip() for x in s.split(",")}
    return DAY_CODES[weekday] in want


def _speak(text: str):
    """Best-effort TTS via tts_say skill; falls back to macOS `say`."""
    try:
        registry = SkillRegistry(SKILLS_DIR)
        registry.scan()
        mod = registry.load("tts_say")
        if mod and hasattr(mod, "run"):
            mod.run(f"say {text}")
            return
    except Exception:
        pass
    try:
        import subprocess
        subprocess.run(["say", text[:500]], timeout=30)
    except Exception:
        pass


def _fire(trigger: dict, registry: SkillRegistry):
    name = trigger.get("name", "unnamed")
    skill = trigger.get("skill")
    task = trigger.get("task", "")
    tts = bool(trigger.get("tts", False))
    t0 = time.time()
    try:
        mod = registry.load(skill)
        if mod is None or not hasattr(mod, "run"):
            log.error("trigger %s: skill %s not found", name, skill)
            audit(f"autopilot:{name}", outcome="error", error_type="SkillNotFound",
                  duration_ms=(time.time()-t0)*1000)
            return
        result = mod.run(task, "")
        dur_ms = (time.time() - t0) * 1000
        log.info("trigger %s → %s (%.0fms) : %s", name, skill, dur_ms, str(result)[:120])
        audit(f"autopilot:{name}", outcome="ok",
              duration_ms=dur_ms, extra={"skill": skill, "task": task[:200]})
        if tts and result:
            _speak(str(result)[:400])
    except Exception as e:
        dur_ms = (time.time() - t0) * 1000
        log.exception("trigger %s failed: %s", name, e)
        audit(f"autopilot:{name}", outcome="error",
              error_type=type(e).__name__, duration_ms=dur_ms)


def _tick(cfg: dict, state: dict, registry: SkillRegistry):
    if not cfg.get("enabled"):
        return
    tzname = cfg.get("timezone", "Europe/Madrid")
    try:
        tz = ZoneInfo(tzname)
    except Exception:
        tz = timezone.utc
    now = datetime.now(tz)
    today_key = now.strftime("%Y-%m-%d")
    current_hm = now.strftime("%H:%M")

    for trig in cfg.get("triggers", []):
        name = trig.get("name")
        at = trig.get("at")
        if not name or not at:
            continue
        if not _day_matches(trig.get("days", "daily"), now.weekday()):
            continue
        last = state.get(name)
        if last == today_key:
            continue  # already fired today
        # Fire when time has been reached (at or just past)
        if current_hm >= at:
            _fire(trig, registry)
            state[name] = today_key
            _save_state(state)


def main():
    log.info("CODEC Autopilot starting. config=%s state=%s poll=%ss",
             CONFIG, STATE, POLL_SEC)
    registry = SkillRegistry(SKILLS_DIR)
    registry.scan()
    while True:
        try:
            cfg = _load_config()
            state = _load_state()
            _tick(cfg, state, registry)
        except Exception as e:
            log.exception("tick error: %s", e)
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
