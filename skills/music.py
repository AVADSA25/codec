"""CODEC Skill: Music Control (Spotify + Apple Music)"""
SKILL_NAME = "music"
SKILL_DESCRIPTION = "Control Spotify or Apple Music playback"
SKILL_TRIGGERS = ["play music", "pause music", "next song", "previous song", "skip song",
                   "play spotify", "pause spotify", "resume music", "stop music", "stop spotify",
                   "what song", "what is playing", "now playing", "play some music",
                   "stop the music", "pause the music", "play the music",
                   "next track", "skip track", "spotify play", "spotify pause", "spotify next"]
import subprocess

def _spotify_running():
    r = subprocess.run(["pgrep", "-x", "Spotify"], capture_output=True, timeout=3)
    return r.returncode == 0

def _run_spotify(cmd):
    return subprocess.run(["osascript", "-e",
        f'tell application "Spotify" to {cmd}'], capture_output=True, text=True, timeout=5)

def run(task, app="", ctx=""):
    low = task.lower()
    use_spotify = _spotify_running() or "spotify" in low

    # Check SPECIFIC commands first (before generic play/stop)
    if any(k in low for k in ["next", "skip"]):
        if use_spotify:
            _run_spotify("next track")
            return "Skipped to next track."
        subprocess.run(["osascript", "-e", 'tell application "Music" to next track'], timeout=5)
        return "Next track."

    if "previous" in low:
        if use_spotify:
            _run_spotify("previous track")
            return "Previous track."
        subprocess.run(["osascript", "-e", 'tell application "Music" to previous track'], timeout=5)
        return "Previous track."

    if any(k in low for k in ["what song", "what is playing", "now playing"]):
        if use_spotify and _spotify_running():
            r = subprocess.run(["osascript", "-e",
                'tell application "Spotify" to return (name of current track) & " by " & (artist of current track)'],
                capture_output=True, text=True, timeout=5)
            if r.stdout.strip():
                return f"Now playing: {r.stdout.strip()}"
        return "Nothing playing right now."

    # Then generic pause/stop
    if any(k in low for k in ["pause", "stop"]):
        if use_spotify:
            _run_spotify("pause")
            return "Spotify paused."
        subprocess.run(["osascript", "-e", 'tell application "Music" to pause'], timeout=5)
        return "Music paused."

    # Then generic play/resume (last — so "play next" doesn't hit this)
    if any(k in low for k in ["play", "resume"]):
        if use_spotify:
            if not _spotify_running():
                subprocess.run(["open", "-a", "Spotify"], timeout=5)
                import time; time.sleep(2)
            _run_spotify("play")
            return "Spotify playing."
        subprocess.run(["osascript", "-e", 'tell application "Music" to play'], timeout=5)
        return "Music playing."

    return None
