"""CODEC Skill: Music Control (Spotify + Apple Music)"""
SKILL_NAME = "music"
SKILL_DESCRIPTION = "Control Spotify or Apple Music playback"
SKILL_TRIGGERS = ["play music", "pause music", "next song", "previous song", "skip song",
                   "play spotify", "pause spotify", "resume music", "stop music",
                   "what song", "what is playing", "now playing", "play some music"]
import subprocess

def _spotify_running():
    r = subprocess.run(["pgrep", "-x", "Spotify"], capture_output=True, timeout=3)
    return r.returncode == 0

def _run_spotify(cmd):
    return subprocess.run(["osascript", "-e",
        f'tell application "Spotify" to {cmd}'], capture_output=True, text=True, timeout=5)

def _run_music(cmd):
    return subprocess.run(["osascript", "-e",
        f'tell application "Music" to {cmd}'], capture_output=True, text=True, timeout=5)

def run(task, app="", ctx=""):
    low = task.lower()
    use_spotify = _spotify_running() or "spotify" in low

    if any(k in low for k in ["pause", "stop music"]):
        if use_spotify:
            _run_spotify("pause")
            return "Spotify paused."
        _run_music("pause")
        return "Music paused."

    if any(k in low for k in ["play", "resume"]):
        if use_spotify:
            _run_spotify("play")
            return "Spotify playing."
        _run_music("play")
        return "Music playing."

    if any(k in low for k in ["next", "skip"]):
        if use_spotify:
            _run_spotify("next track")
            return "Skipped to next track."
        _run_music("next track")
        return "Skipped to next track."

    if "previous" in low:
        if use_spotify:
            _run_spotify("previous track")
            return "Previous track."
        _run_music("previous track")
        return "Previous track."

    if any(k in low for k in ["what song", "what is playing", "now playing"]):
        if use_spotify:
            r = subprocess.run(["osascript", "-e",
                'tell application "Spotify" to return (name of current track) & " by " & (artist of current track)'],
                capture_output=True, text=True, timeout=5)
            if r.stdout.strip():
                return f"Now playing: {r.stdout.strip()}"
        r = subprocess.run(["osascript", "-e",
            'tell application "Music" to return (name of current track) & " by " & (artist of current track)'],
            capture_output=True, text=True, timeout=5)
        if r.stdout.strip():
            return f"Now playing: {r.stdout.strip()}"
        return "Nothing playing right now."

    return None
