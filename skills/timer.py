"""CODEC Skill: Timer & Alarm — set timers and reminders"""
SKILL_NAME = "timer"
SKILL_DESCRIPTION = "Set timers and alarms with voice notifications"
SKILL_TRIGGERS = ["set a timer", "timer for", "remind me in", "alarm in", "countdown",
                   "set timer", "wake me", "alert me in", "minutes timer", "minute timer",
                   "second timer"]

import threading, subprocess, re

def run(task, app="", ctx=""):
    low = task.lower()

    # Parse duration
    seconds = 0

    # Match "X hours"
    h_match = re.search(r'(\d+)\s*hours?', low)
    if h_match:
        seconds += int(h_match.group(1)) * 3600

    # Match "X minutes" or "X min"
    m_match = re.search(r'(\d+)\s*(?:minutes?|min)', low)
    if m_match:
        seconds += int(m_match.group(1)) * 60

    # Match "X seconds" or "X sec"
    s_match = re.search(r'(\d+)\s*(?:seconds?|sec)', low)
    if s_match:
        seconds += int(s_match.group(1))

    # If just a number with no unit, assume minutes
    if seconds == 0:
        num_match = re.search(r'(\d+)', low)
        if num_match:
            seconds = int(num_match.group(1)) * 60

    if seconds == 0:
        return None  # Decline — couldn't parse a duration

    # Extract label
    label = "Timer done!"
    for pattern in [r'to\s+(.+)', r'for\s+(.+?)(?:\s+in\s+|\s+timer|\s*$)']:
        label_match = re.search(pattern, low)
        if label_match:
            candidate = label_match.group(1).strip()
            # Don't use duration text as label
            if not re.match(r'^[\d\s]*(min|sec|hour|minute|second)', candidate) and len(candidate) > 2:
                label = candidate
                break

    # Format display
    if seconds >= 3600:
        display = f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    elif seconds >= 60:
        display = f"{seconds // 60}m {seconds % 60}s" if seconds % 60 else f"{seconds // 60} minutes"
    else:
        display = f"{seconds} seconds"

    def timer_callback():
        # macOS notification + sound
        subprocess.run(["osascript", "-e",
            f'display notification "{label}" with title "CODEC Timer" sound name "Glass"'],
            capture_output=True, timeout=5)
        # Also speak it
        try:
            import requests
            r = requests.post("http://localhost:8083/v1/audio/speech",
                json={"model": "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16",
                      "input": f"Timer finished. {label}", "voice": "af_nicole"},
                stream=True, timeout=20)
            if r.status_code == 200:
                import tempfile
                tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
                [tmp.write(c) for c in r.iter_content(4096)]
                tmp.close()
                subprocess.Popen(["afplay", tmp.name])
        except: pass

    t = threading.Timer(seconds, timer_callback)
    t.daemon = True
    t.start()

    return f"Timer set: {display}. I'll notify you when it's done."
