"""CODEC Skill: Send iMessage via AppleScript.

PR-2F (closes D-13): recipient is validated against a strict phone/email
regex BEFORE AppleScript interpolation. Any input that fails the regex —
including the audit's documented breakout (`xx@x.com" of targetService\n
activate application "Calculator"\nset targetBuddy to buddy "yy@y.com`) —
is refused with `imessage_send_blocked` audit emit. Text body escape is
extended to cover `\\`, `\r`, `\t`, `\"`, `\n`. The recipient sits inside
an AppleScript identifier context (`buddy "..."`), not a string literal,
so injection there is higher-impact than the text body — validation
gates that surface entirely.
"""
SKILL_NAME = "imessage_send"
SKILL_DESCRIPTION = "Send an iMessage to a contact (phone number or Apple ID email)"
SKILL_TRIGGERS = [
    "text", "imessage", "send message to", "message",
    "send text", "text to", "sms",
]
SKILL_MCP_EXPOSE = True

import re, subprocess

_VERBS = (
    "send imessage to", "send message to", "send text to",
    "imessage", "text to", "send sms to",
    "message", "text", "sms",
)

# PR-2F (D-13): strict format gate. Anchored — no partial matches. No
# AppleScript metachars (quotes, newlines, backslashes, tabs, control chars)
# can survive these patterns. Length cap 254 = RFC 5321 SMTP email limit.
_PHONE_RE = re.compile(r"^\+?[1-9]\d{9,14}$")  # E.164-ish: 10-15 digits, optional +
_EMAIL_RE = re.compile(r"^[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+$")
_RECIPIENT_MAX_LEN = 254


def _validate_recipient(recipient: str) -> bool:
    """True if `recipient` is a syntactically valid phone or email AND
    contains no AppleScript metachars. Rejects everything else."""
    if not recipient or not isinstance(recipient, str):
        return False
    if len(recipient) > _RECIPIENT_MAX_LEN:
        return False
    return bool(_PHONE_RE.match(recipient) or _EMAIL_RE.match(recipient))


def _escape_text(text: str) -> str:
    """Escape every AppleScript metachar in the message body. Recipient
    is gated by validation (above); text body still goes through string
    interpolation, so escape all metachars here. Order matters: backslash
    first so subsequent escapes don't re-escape its own backslash."""
    if not isinstance(text, str):
        text = str(text)
    return (text
            .replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\r", "\\r")
            .replace("\n", "\\n")
            .replace("\t", "\\t"))


def _emit_blocked(reason: str, recipient_preview: str) -> None:
    """Audit emit for D-13 refusals. Recipient preview truncated to 32
    chars so adversarial multi-line breakouts don't bloat the log line."""
    try:
        from codec_audit import log_event
        log_event(
            "imessage_send_blocked",
            source="codec-skill-imessage-send",
            message=f"imessage_send refused: {reason}",
            level="warning",
            outcome="error",
            extra={"reason": reason, "recipient_preview": recipient_preview[:32]},
        )
    except Exception:
        pass


def _parse(task: str):
    """Return (recipient, body) or (None, None).

    Accepted phrasings:
      'text +34612345678 hi there'
      'imessage mom@icloud.com running late'
      'send message to +34612345678: meeting moved to 4'
      'recipient: +34612345678 | body: hi'   (structured — for MCP callers)
    """
    t = task.strip()

    # Structured form first
    m = re.search(r'recipient\s*[:=]\s*(\S+).*?(?:body|text|message)\s*[:=]\s*(.+)$',
                  t, re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1).strip(' "\''), m.group(2).strip().strip('"\'')

    low = t.lower()
    for v in sorted(_VERBS, key=len, reverse=True):
        m = re.match(r'^\s*' + re.escape(v) + r'\b', low)
        if m:
            t = t[m.end():].strip()
            break
    t = re.sub(r'^\s*[:,\-]+\s*', '', t).strip()

    # First token is recipient (phone or email)
    m = re.match(r'^\s*(\+?[\d\-\s\(\)]{7,}|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,})\s*[:,\-]?\s*(.*)$', t)
    if not m:
        return None, None
    recipient = re.sub(r'[\s\-\(\)]', '', m.group(1))
    body = m.group(2).strip().strip('"\'')
    return recipient, body


def _send(recipient: str, text: str) -> bool:
    # PR-2F: validation gate — refuse anything that isn't a syntactically
    # valid phone or email. This blocks the D-13 AppleScript breakout
    # before any string interpolation happens.
    if not _validate_recipient(recipient):
        _emit_blocked("invalid_recipient_format", recipient or "")
        return False
    escaped = _escape_text(text)
    scripts = [
        f'''tell application "Messages"
            set targetService to 1st account whose service type = iMessage
            set targetBuddy to buddy "{recipient}" of targetService
            send "{escaped}" to targetBuddy
        end tell''',
        f'''tell application "Messages"
            send "{escaped}" to (1st chat whose participants contains (buddy "{recipient}" of (1st account whose service type = iMessage)))
        end tell''',
    ]
    for s in scripts:
        try:
            r = subprocess.run(["osascript", "-e", s],
                               capture_output=True, text=True, timeout=15)
            if r.returncode == 0:
                return True
        except Exception:
            continue
    return False


def run(task, app="", ctx=""):
    recipient, body = _parse(task)
    if not recipient or not body:
        return ("Format: 'text <recipient> <message>' — e.g. "
                "'text +34612345678 running late' or "
                "'imessage mom@icloud.com hi'")
    if not _validate_recipient(recipient):
        # Surface the refusal to the user with a clear message; the audit
        # emit happens inside _send via _emit_blocked.
        _emit_blocked("invalid_recipient_format", recipient)
        return (f"❌ Refused to send: '{recipient[:32]}' isn't a valid phone "
                f"number or email. Use E.164 phone (+34612345678) or "
                f"user@example.com.")
    ok = _send(recipient, body)
    if ok:
        return f"✅ Sent to {recipient}: {body[:80]}"
    return f"❌ Failed to send iMessage to {recipient}. Check Messages app is signed in and recipient is reachable."
