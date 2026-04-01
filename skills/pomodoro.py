"""Pomodoro timer with work/break cycles"""
SKILL_NAME = "pomodoro"
SKILL_TRIGGERS = ["start pomodoro", "pomodoro timer", "focus timer", "work timer"]
SKILL_DESCRIPTION = "Pomodoro timer with work/break cycles"

import subprocess, threading, time

def run(task, app="", ctx=""):
    minutes = 25
    for w in task.split():
        try:
            n = int(w)
            if 1 <= n <= 120:
                minutes = n
                break
        except:
            pass
    def timer():
        time.sleep(minutes * 60)
        subprocess.run(["afplay", "/System/Library/Sounds/Glass.aiff"])
        subprocess.run(["osascript", "-e", f'display notification "Pomodoro complete! Take a break." with title "CODEC"'])
    threading.Thread(target=timer, daemon=True).start()
    return f"Pomodoro started: {minutes} minutes. I will alert you when it is time for a break."
