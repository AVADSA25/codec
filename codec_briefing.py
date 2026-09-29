"""Morning briefing (UI phase 3, P3.1; docs/P3.1-DESIGN.md).

Off by default; the owner switches it on in Settings. It is a P3.3 schedule job
(BRIEFING_ID) that runs the Daybreak `daily_kickoff` skill and delivers with
`deliver()`: a 45-60 second script from the local model, a `briefing` card on
Home with the open Daybreak threads, a content-free Web Push, and, when
speaking is on, the script waiting in STATE_PATH. `tick()` (the scheduler loop,
every minute) says it on the Mac at the first keyboard or mouse activity before
noon: a chime, then Kokoro sentence by sentence. It stops on the card's Stop,
on a wake-word line in the audit log, or when a voice call starts. It never
speaks to an empty room, and it waits while an image job or low memory.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from typing import Any, Dict, Optional

import codec_jsonstore

log = logging.getLogger("codec_briefing")

BRIEFING_ID = "sched_morning_briefing"
DEFAULT_WHEN = "weekdays at 7:30"
STATE_PATH = os.path.expanduser("~/.codec/briefing_state.json")
VOICE_SESSION_PATH = os.path.expanduser("~/.codec/voice_session.json")
CHIME = "/System/Library/Sounds/Glass.aiff"
ACTIVE_WITHIN_S = 60
SPEAK_UNTIL_HOUR = 12
MIN_FREE_GB = 4.0

_SPEAKING = {"on": False, "stop": threading.Event(), "proc": None}
_LOCK = threading.Lock()


# ── settings: the schedule job ────────────────────────────────────────────────
def _job() -> Optional[Dict[str, Any]]:
    import codec_scheduler
    return next((s for s in codec_scheduler.load_schedules() if s.get("id") == BRIEFING_ID), None)


def settings() -> Dict[str, Any]:
    import codec_scheduler
    job = _job()
    if not job:
        return {"enabled": False, "when": codec_scheduler.describe_when(codec_scheduler.parse_when(DEFAULT_WHEN)),
                "speak": True, "next_run": None, "last_run": None, "speaking": _SPEAKING["on"]}
    view = codec_scheduler.job_view(job)
    return {"enabled": bool(job.get("enabled")), "when": view["summary"], "speak": bool(job.get("speak", True)),
            "next_run": view["next_run"], "last_run": job.get("last_run"), "speaking": _SPEAKING["on"]}


def update(body: Dict[str, Any]) -> Dict[str, Any]:
    """Switch the briefing on or off, change its time or speaking. The job is
    created on first switch-on. Raises ValueError for an unreadable time."""
    import codec_scheduler
    when = codec_scheduler.parse_when(body["when"]) if isinstance(body.get("when"), str) else None
    now = datetime.now().isoformat()

    def mutate(schedules):
        job = next((s for s in schedules if s.get("id") == BRIEFING_ID), None)
        if job is None:
            if not body.get("enabled"):
                return
            job = {"id": BRIEFING_ID, "kind": "skill", "skill": "daily_kickoff", "task": "start my day",
                   "label": "Morning briefing", "deliver": ["briefing"], "speak": True, "created": now,
                   "enabled": False, "last_run": None, "managed": "briefing",
                   "when": codec_scheduler.parse_when(DEFAULT_WHEN)}
            schedules.append(job)
        if when:
            job["when"] = when
        if "speak" in body:
            job["speak"] = bool(body["speak"])
        if "enabled" in body:
            on = bool(body["enabled"])
            if on and not job.get("enabled"):
                job["enabled_at"] = now
            job["enabled"] = on
        w = job["when"]
        if w.get("type") == "daily":
            job.update(hour=w["hour"], minute=w["minute"], days=w["days"])

    codec_scheduler._update_schedules(mutate)
    return settings()


# ── waiting while busy ────────────────────────────────────────────────────────
def _free_gb() -> Optional[float]:
    try:
        out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return None
    size = re.search(r"page size of (\d+) bytes", out)
    pages = sum(int(n) for n in re.findall(r"Pages (?:free|inactive|speculative):\s+(\d+)", out))
    return pages * int(size.group(1)) / 1e9 if size and pages else None


def busy() -> Optional[str]:
    """Why the briefing should wait now, or None."""
    try:
        import codec_image
        msg = codec_image.busy_message()
        if msg:
            return "an image job is running"
    except Exception:
        pass
    free = _free_gb()
    if free is not None and free < MIN_FREE_GB:
        return "the Mac is short of memory"
    return None


# ── delivery ──────────────────────────────────────────────────────────────────
def _plain(text: str) -> str:
    s = re.sub(r"```[\s\S]*?(?:```|$)", " ", str(text or ""))
    s = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"https?://\S+", "", s)
    s = re.sub(r"^\s*(?:[-*+]|\d+[.)]|#{1,6})\s*", "", s, flags=re.M)
    s = re.sub(r"[`*_>|#]+", " ", s)
    return " ".join(s.split())


def _no_wake_word(text: str) -> str:
    # The wake-word listener hears the Mac's speakers: never say its name.
    return re.sub(r"\b(?:codec|kodak|codex)\b", "I", text, flags=re.I)


def make_script(briefing: str) -> str:
    """A 45-60 second spoken version of the briefing (110-150 words)."""
    import codec_llm
    try:
        from codec_config import get_llm_api_key
        with open(os.path.expanduser("~/.codec/config.json")) as f:
            config = json.load(f)
        answer = codec_llm.call(
            [{"role": "system", "content": "You write a short spoken morning briefing, read aloud by a voice. "
                                          "110 to 150 words, 45 to 60 seconds. Warm and natural. Plain sentences: "
                                          "no lists, symbols, links or headings. Never say the word CODEC."},
             {"role": "user", "content": "Turn this into the spoken briefing:\n\n" + str(briefing)[:6000]}],
            base_url=config.get("llm_base_url", "http://localhost:8083/v1"),
            model=config.get("llm_model", "mlx-community/Qwen3.6-35B-A3B-4bit"),
            api_key=get_llm_api_key() or "", max_tokens=400, temperature=0.5, timeout=120,
            extra_kwargs=config.get("llm_kwargs") or {}, raise_on_error=True)
        script = re.sub(r"<think>[\s\S]*?</think>", "", str(answer or "")).strip()
    except Exception as e:
        log.warning("[briefing] script from the model failed: %s", type(e).__name__)
        script = ""
    if not script:
        words = _plain(briefing).split()
        script = " ".join(words[:140])
    return _no_wake_word(_plain(script))


def _write_state(state: Dict[str, Any]) -> None:
    codec_jsonstore.atomic_write_json(STATE_PATH, state)


def _read_state() -> Dict[str, Any]:
    try:
        with open(STATE_PATH) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def deliver(sched: Dict[str, Any], output: str) -> Optional[str]:
    """The briefing job's delivery: card, push, and the script waiting to be said."""
    import codec_today
    threads = []
    try:
        import codec_daybreak
        threads = codec_daybreak.get_open_threads()
    except Exception as e:
        log.debug("[briefing] threads unavailable: %s", e)
    card = codec_today.add_card("Your morning briefing", output, kind="briefing", source=sched.get("id"),
                                threads=threads)
    try:
        import codec_push
        codec_push.notify("briefing")
    except Exception:
        pass
    if sched.get("speak", True):
        _write_state({"date": datetime.now().strftime("%Y-%m-%d"), "card_id": card["id"],
                      "script": make_script(output), "spoken": False,
                      "created": datetime.now().isoformat(timespec="seconds")})
    return card["id"]


# ── speaking at the first activity ────────────────────────────────────────────
def _idle_seconds() -> float:
    try:
        import codec_observer
        return float(codec_observer._idle_seconds())
    except Exception:
        return 1e9  # unknown: treat as nobody there


def _audit_path() -> str:
    try:
        import codec_audit
        return str(codec_audit._AUDIT_LOG)
    except Exception:
        return os.path.expanduser("~/.codec/audit.log")


def _wake_heard(path: str, offset: int) -> bool:
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            return b'"wake_word_detected"' in f.read()
    except OSError:
        return False


def _should_stop(audit_path: str, audit_offset: int) -> Optional[str]:
    if _SPEAKING["stop"].is_set():
        return "stopped"
    if os.path.exists(VOICE_SESSION_PATH):
        return "voice call"
    if _wake_heard(audit_path, audit_offset):
        return "wake word"
    return None


def _tts(text: str) -> Optional[bytes]:
    import requests
    try:
        with open(os.path.expanduser("~/.codec/config.json")) as f:
            config = json.load(f)
    except (OSError, ValueError):
        config = {}
    r = requests.post(config.get("tts_url", "http://localhost:8085/v1/audio/speech"),
                      json={"model": config.get("tts_model", "mlx-community/Kokoro-82M-bf16"), "input": text,
                            "voice": config.get("tts_voice", "am_adam"),
                            "speed": float(config.get("tts_speed", 1.0))}, timeout=120)
    return r.content if r.status_code == 200 and r.content else None


def _play(path: str, audit_path: str, audit_offset: int) -> Optional[str]:
    """Play one file with afplay; stop it early when asked. Returns the reason
    it was stopped, or None when it played to the end."""
    proc = subprocess.Popen(["afplay", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _SPEAKING["proc"] = proc
    try:
        while proc.poll() is None:
            why = _should_stop(audit_path, audit_offset)
            if why:
                proc.terminate()
                proc.wait(timeout=5)
                return why
            time.sleep(0.2)
        return None
    finally:
        _SPEAKING["proc"] = None


def speak(script: str) -> str:
    """Chime, then the script sentence by sentence. Returns 'done' or why it stopped."""
    audit_path = _audit_path()
    try:
        audit_offset = os.path.getsize(audit_path)
    except OSError:
        audit_offset = 0
    _SPEAKING["stop"].clear()
    _SPEAKING["on"] = True
    try:
        if os.path.exists(CHIME):
            why = _play(CHIME, audit_path, audit_offset)
            if why:
                return why
        for sentence in re.split(r"(?<=[.!?])\s+", script):
            if not sentence.strip():
                continue
            why = _should_stop(audit_path, audit_offset)
            if why:
                return why
            audio = _tts(sentence.strip())
            if not audio:
                continue
            fd, path = tempfile.mkstemp(suffix=".mp3")
            with os.fdopen(fd, "wb") as f:
                f.write(audio)
            try:
                why = _play(path, audit_path, audit_offset)
            finally:
                os.unlink(path)
            if why:
                return why
        return "done"
    finally:
        _SPEAKING["on"] = False


def stop() -> bool:
    """The card's Stop button."""
    _SPEAKING["stop"].set()
    return _SPEAKING["on"]


def tick(now: Optional[datetime] = None) -> Optional[str]:
    """Say today's waiting script if someone is at the Mac now. Returns what it
    did ('speaking', a reason it waited, or None when nothing is waiting)."""
    now = now or datetime.now()
    with _LOCK:
        state = _read_state()
        if not state.get("script") or state.get("spoken") or state.get("date") != now.strftime("%Y-%m-%d"):
            return None
        if now.hour >= SPEAK_UNTIL_HOUR:
            return "too late"
        if _SPEAKING["on"]:
            return "already speaking"
        if os.path.exists(VOICE_SESSION_PATH):
            return "voice call"
        if _idle_seconds() > ACTIVE_WITHIN_S:
            return "nobody there"
        why = busy()
        if why:
            return why
        state["spoken"] = True
        state["spoken_at"] = now.isoformat(timespec="seconds")
        _write_state(state)
    threading.Thread(target=_speak_logged, args=(state["script"],), name="codec-briefing-speak", daemon=True).start()
    return "speaking"


def _speak_logged(script: str) -> None:
    try:
        result = speak(script)
        log.info("[briefing] spoken: %s", result)
    except Exception as e:
        log.warning("[briefing] speaking failed: %s", e)
