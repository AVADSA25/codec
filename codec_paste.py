"""Paste the clipboard into the focused field with a real Cmd+V key event.

Replaces pyautogui in codec_dictate.py, codec_textassist.py and codec_watcher.py (audit
P-011): pyautogui pulls mouseinfo (GPLv3+) and the paid app ships permissive or LGPL
code only. Quartz comes with pyobjc-framework-quartz, which the buyer lock already has.

Like pyautogui this posts hardware-level events (CGEventPost on the HID tap), so the
paste lands in a full-screen app too, where an osascript "keystroke" can lose the
modifier. Cmd is pressed first, held across a short pause, and always released: a Cmd
left held would turn every later click and keypress into a shortcut.
"""
import subprocess
import time

KEY_COMMAND = 55   # kVK_Command
KEY_V = 9          # kVK_ANSI_V (the physical key, as pyautogui used)


def _quartz():
    import Quartz  # noqa: PLC0415 - macOS only, imported when first used
    return Quartz


def _post(q, keycode, down, flags):
    event = q.CGEventCreateKeyboardEvent(None, keycode, down)
    q.CGEventSetFlags(event, flags)
    q.CGEventPost(q.kCGHIDEventTap, event)


def _osascript_paste():
    subprocess.run(
        ["osascript", "-e",
         'tell application "System Events" to keystroke "v" using command down'],
        capture_output=True, timeout=5)


def paste_clipboard(settle=0.05):
    """Send Cmd+V. `settle` is the pause after Cmd goes down and after V comes up.

    Returns True when the key events were posted, False when it fell back to
    osascript (Quartz missing or refusing the event).
    """
    try:
        q = _quartz()
        cmd = q.kCGEventFlagMaskCommand
        _post(q, KEY_COMMAND, True, cmd)
        try:
            time.sleep(settle)
            _post(q, KEY_V, True, cmd)
            _post(q, KEY_V, False, cmd)
            time.sleep(settle)
        finally:
            _post(q, KEY_COMMAND, False, 0)
        return True
    except Exception:
        _osascript_paste()
        return False
