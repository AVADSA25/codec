"""CODEC AppleScript quoting: one escape for text put inside an AppleScript
string literal (P-007).

Every osascript call that interpolates text into a "..." literal goes through
escape(): backslash first, then the double quote, then CR, LF and tab. Without
it a reminder, note or app name holding a backslash-quote could close the
string and append `& (do shell script "...")`. New sinks pass text as an argv
argument instead (`on run argv`, see codec.py), which needs no escaping.
"""


def escape(text) -> str:
    """Escape text for use inside an AppleScript double-quoted string.
    Order matters: backslash first so later escapes are not re-escaped."""
    if not isinstance(text, str):
        text = str(text)
    return (text
            .replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\r", "\\r")
            .replace("\n", "\\n")
            .replace("\t", "\\t"))


def notification_script(body, title, subtitle=None, sound=None) -> str:
    """A `display notification` script with every text field escaped."""
    script = f'display notification "{escape(body)}" with title "{escape(title)}"'
    if subtitle is not None:
        script += f' subtitle "{escape(subtitle)}"'
    if sound is not None:
        script += f' sound name "{escape(sound)}"'
    return script
