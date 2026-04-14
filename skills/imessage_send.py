"""CODEC Skill: Send iMessage via AppleScript"""
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
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
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
    ok = _send(recipient, body)
    if ok:
        return f"✅ Sent to {recipient}: {body[:80]}"
    return f"❌ Failed to send iMessage to {recipient}. Check Messages app is signed in and recipient is reachable."
