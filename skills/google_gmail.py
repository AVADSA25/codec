"""Google Gmail — Check emails, search inbox, send (consent-gated)"""
SKILL_NAME = "google_gmail"
SKILL_TRIGGERS = ["check email", "check my email", "my emails", "inbox", "unread emails", "new emails", "any emails", "latest emails", "email from", "search email", "send email", "send an email"]
SKILL_DESCRIPTION = "Check your Gmail inbox, search emails, and send emails (sending always requires an explicit spoken/PWA confirmation)"
SKILL_MCP_EXPOSE = True

import os
import re

TOKEN_PATH = os.path.expanduser("~/.codec/google_token.json")

# Matches an explicit send intent ("send email…", "send an email…", or a task
# starting with send). \b keeps "sender"/"unsend" out of the send path.
_SEND_RE = re.compile(r"^\s*send\b|\bsend\b[^.]*\bemail\b|\bemail\b[^.]*\bsend\b", re.IGNORECASE)

_USAGE = ("To send, repeat with explicit fields: "
          "send email to: someone@example.com subject: <subject> body: <message>")


def _get_service():
    import sys; sys.path.insert(0, os.path.expanduser("~/codec-repo"))
    from codec_google_auth import build_service
    return build_service("gmail", "v1")


def _parse_send(task):
    """Extract (to, subject, body). to/body required; subject optional."""
    to = re.search(r"to:\s*([^\s,;<>]+@[^\s,;<>]+)", task, re.IGNORECASE)
    if not to:
        to = re.search(r"\bto\s+([^\s,;<>]+@[^\s,;<>]+)", task, re.IGNORECASE)
    subject = re.search(r"subject:\s*(.+?)(?=\s+body:|$)", task, re.IGNORECASE | re.DOTALL)
    body = re.search(r"body:\s*(.+)$", task, re.IGNORECASE | re.DOTALL)
    return (to.group(1).strip() if to else None,
            subject.group(1).strip() if subject else "",
            body.group(1).strip() if body else None)


def _send_email(task):
    """Consent-gated send (Phase-1 Step-3 strict consent, verb='send').
    Fails CLOSED: no confirmation → no send, ever."""
    to, subject, body = _parse_send(task)
    if not to or not body:
        return _USAGE
    try:
        import sys; sys.path.insert(0, os.path.expanduser("~/codec-repo"))
        import codec_ask_user
        preview = body[:140] + ("…" if len(body) > 140 else "")
        answer = codec_ask_user.ask(
            f"Send this email? To {to} — subject \"{subject or '(no subject)'}\" — "
            f"\"{preview}\". Say 'send' to confirm.",
            destructive=True, destructive_verb="send", tool_name="google_gmail")
    except Exception as e:
        return f"Couldn't get send confirmation ({e}) — email NOT sent."
    sentinels = {getattr(codec_ask_user, "TIMEOUT_SENTINEL", ""),
                 getattr(codec_ask_user, "DISABLED_SENTINEL", "")}
    if not answer or answer in sentinels or "send" not in answer.lower():
        return "Okay — email not sent."
    try:
        import base64
        from email.mime.text import MIMEText
        service = _get_service()
        msg = MIMEText(body)
        msg["to"] = to
        msg["subject"] = subject or "(no subject)"
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
        return f"Email sent to {to}: {subject or '(no subject)'}."
    except Exception as e:
        return f"Gmail send error: {e}"


def run(task, app="", ctx=""):
    if _SEND_RE.search(task or ""):
        return _send_email(task)
    try:
        service = _get_service()
        low = task.lower()

        # Search for specific emails
        query = "is:inbox"
        if "unread" in low:
            query = "is:unread is:inbox"
        elif "from" in low:
            # Extract sender name
            parts = low.split("from")
            if len(parts) > 1:
                sender = parts[1].strip().split()[0] if parts[1].strip() else ""
                if sender:
                    query = f"from:{sender} is:inbox"

        results = service.users().messages().list(
            userId='me', q=query, maxResults=8
        ).execute()

        messages = results.get('messages', [])
        if not messages:
            return "No emails found matching your search."

        lines = [f"Found {len(messages)} emails:"]
        for msg_ref in messages[:8]:
            msg = service.users().messages().get(
                userId='me', id=msg_ref['id'], format='metadata',
                metadataHeaders=['From', 'Subject', 'Date']
            ).execute()
            headers = {h['name']: h['value'] for h in msg.get('payload', {}).get('headers', [])}
            sender = headers.get('From', 'Unknown')
            # Clean sender name
            if '<' in sender:
                sender = sender.split('<')[0].strip().strip('"')
            subject = headers.get('Subject', 'No subject')
            snippet = msg.get('snippet', '')[:60]
            is_unread = 'UNREAD' in msg.get('labelIds', [])
            marker = "* " if is_unread else "  "
            lines.append(f"{marker}{sender}: {subject}")
            if snippet:
                lines.append(f"    {snippet}...")
        return "\n".join(lines)

    except Exception as e:
        return f"Gmail error: {str(e)}"
