"""CODEC Skill: Timer via Apple Clock app"""
SKILL_NAME = "timer"
SKILL_DESCRIPTION = "Set timers using the Apple Clock app"
SKILL_TRIGGERS = ["set a timer", "timer for", "remind me in", "alarm in",
                   "set timer", "minute timer", "minutes timer", "second timer", "countdown",
                   "timer", "start a timer", "wake me", "alert me in"]
import subprocess, re

def run(task, app="", ctx=""):
    low = task.lower()
    seconds = 0
    h = re.search(r'(\d+)\s*hours?', low)
    if h: seconds += int(h.group(1)) * 3600
    m = re.search(r'(\d+)\s*(?:minutes?|min)', low)
    if m: seconds += int(m.group(1)) * 60
    s = re.search(r'(\d+)\s*(?:seconds?|sec)', low)
    if s: seconds += int(s.group(1))
    if seconds == 0:
        n = re.search(r'(\d+)', low)
        if n: seconds = int(n.group(1)) * 60
    if seconds == 0: return None

    if seconds >= 3600:
        display = f"{seconds//3600}h {(seconds%3600)//60}m"
    elif seconds >= 60:
        display = f"{seconds//60} minutes"
    else:
        display = f"{seconds} seconds"

    # Use Apple Clock app to set the timer
    try:
        # Open Clock app and use Shortcuts to set timer via Siri
        subprocess.Popen(["shortcuts", "run", "Set Timer",
                          "-i", str(seconds)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

    # Fallback: also set a local timer with sound alert
    import threading, tempfile
    def fire():
        subprocess.run(["afplay", "/System/Library/Sounds/Glass.aiff"], timeout=5)
        import time; time.sleep(0.5)
        subprocess.run(["afplay", "/System/Library/Sounds/Glass.aiff"], timeout=5)
        subprocess.run(["osascript", "-e",
            f'display notification "Timer: {display} is up!" with title "CODEC" sound name "Glass"'], timeout=5)
        try:
            import requests
            r = requests.post("http://localhost:8085/v1/audio/speech",
                json={"model": "mlx-community/Kokoro-82M-bf16",
                      "input": f"Your {display} timer is done.", "voice": "am_adam"},
                stream=True, timeout=20)
            if r.status_code == 200:
                tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
                [tmp.write(c) for c in r.iter_content(4096)]; tmp.close()
                subprocess.Popen(["afplay", tmp.name])
        except Exception:
            pass

    t = threading.Timer(seconds, fire)
    t.daemon = True
    t.start()
    print(f"[Timer] Set for {seconds}s ({display})")
    return f"Timer set for {display}."
