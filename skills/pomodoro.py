"""Pomodoro timer with work/break cycles"""
SKILL_NAME = "pomodoro"
SKILL_TRIGGERS = [
    "start pomodoro", "pomodoro timer", "focus timer", "work timer",
    "stop pomodoro", "cancel pomodoro", "pomodoro status", "pomodoro state",
    "end pomodoro", "pomodoro",
]
SKILL_DESCRIPTION = "Pomodoro timer with work/break cycles (start/stop/status)"

import subprocess, threading, time, os, json

_STATE_FILE = os.path.expanduser("~/.codec/pomodoro_state.json")
_active_thread = None


def _read_state():
    try:
        with open(_STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return None


def _write_state(state):
    os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
    with open(_STATE_FILE, "w") as f:
        json.dump(state, f)


def _clear_state():
    try:
        os.remove(_STATE_FILE)
    except FileNotFoundError:
        pass


def run(task, app="", ctx=""):
    low = (task or "").lower().strip()

    # ── stop / cancel ─────────────────────────────────────────────────────
    if any(k in low for k in ["stop", "cancel", "end", "kill"]):
        state = _read_state()
        _clear_state()
        if state:
            return f"Pomodoro cancelled (was: {state.get('minutes', '?')} min, started {state.get('started_at', '?')})."
        return "No pomodoro was running."

    # ── status ────────────────────────────────────────────────────────────
    if any(k in low for k in ["status", "state", "how much", "time left", "remaining"]):
        state = _read_state()
        if not state:
            return "No pomodoro is running."
        ends_at = state.get("ends_at_ts", 0)
        remaining = max(0, int(ends_at - time.time()))
        if remaining == 0:
            _clear_state()
            return "Pomodoro has completed."
        mins, secs = divmod(remaining, 60)
        return f"Pomodoro: {mins}m {secs}s remaining (of {state.get('minutes')} min)."

    # ── start (default) ───────────────────────────────────────────────────
    # Don't auto-start if one is already active
    existing = _read_state()
    if existing and existing.get("ends_at_ts", 0) > time.time():
        remaining = int(existing["ends_at_ts"] - time.time())
        mins = remaining // 60
        return f"Pomodoro already running — {mins} min left. Say 'stop pomodoro' to cancel."

    minutes = 25
    for w in task.split():
        try:
            n = int(w)
            if 1 <= n <= 120:
                minutes = n
                break
        except:
            pass

    started = time.time()
    ends = started + minutes * 60
    _write_state({
        "minutes": minutes,
        "started_at": time.strftime("%H:%M", time.localtime(started)),
        "started_at_ts": started,
        "ends_at_ts": ends,
    })

    def timer():
        time.sleep(minutes * 60)
        # Only fire if still the active pomodoro (not cancelled)
        cur = _read_state()
        if cur and abs(cur.get("ends_at_ts", 0) - ends) < 1:
            subprocess.run(["afplay", "/System/Library/Sounds/Glass.aiff"])
            subprocess.run(["osascript", "-e", 'display notification "Pomodoro complete! Take a break." with title "CODEC"'])
            _clear_state()

    threading.Thread(target=timer, daemon=True).start()
    return f"Pomodoro started: {minutes} minutes. I will alert you when it is time for a break."
