"""Google Gmail — Check emails, search inbox, draft, and send (consent-gated)"""
SKILL_NAME = "google_gmail"
SKILL_TRIGGERS = ["check email", "check my email", "my emails", "inbox", "unread emails", "new emails", "any emails", "latest emails", "email from", "search email", "send email", "send an email", "draft email", "draft an email", "save as draft", "create a draft", "gmail draft"]
SKILL_DESCRIPTION = "Check your Gmail inbox, search emails, save DRAFTS (never sent — safe, no confirmation needed), and send emails (sending always requires an explicit spoken/PWA confirmation). To save a draft: 'draft email to: someone@example.com subject: <subject> body: <message>'."
SKILL_MCP_EXPOSE = True

import os
import re

TOKEN_PATH = os.path.expanduser("~/.codec/google_token.json")

# Draft intent is checked BEFORE send so "draft an email …" never sends. A Gmail
# draft is only saved to the Drafts folder — never delivered — so it needs no
# consent gate (unlike send). \b keeps "drafts" (inbox search) sane.
_DRAFT_RE = re.compile(r"\bdraft\b", re.IGNORECASE)
# Matches an explicit send intent ("send email…", "send an email…", or a task
# starting with send). \b keeps "sender"/"unsend" out of the send path.
_SEND_RE = re.compile(r"^\s*send\b|\bsend\b[^.]*\bemail\b|\bemail\b[^.]*\bsend\b", re.IGNORECASE)

_USAGE = ("To send, repeat with explicit fields: "
          "send email to: someone@example.com subject: <subject> body: <message>")
_DRAFT_USAGE = ("To save a draft, include the body: "
                "draft email to: someone@example.com subject: <subject> body: <message>")


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


def _parse_draft(task):
    """Robust free-form parse for a draft request. LLMs do NOT reliably use the
    strict `to:/subject:/body:` markers — they write the whole email (often with
    an inline 'Subject:' line) after a phrase like 'save as a Gmail draft:'. So:
      to      = first email address after a 'to' cue (optional — a draft needs no recipient)
      subject = a 'Subject:' line pulled out of the content (first occurrence)
      body    = everything else (the email itself)
    Returns (to, subject, body)."""
    t = task or ""
    to_m = (re.search(r"\bto:\s*([^\s,;<>]+@[^\s,;<>]+)", t, re.IGNORECASE)
            or re.search(r"\bto\s+([^\s,;<>]+@[^\s,;<>]+)", t, re.IGNORECASE))
    to = to_m.group(1).strip() if to_m else None

    # Content = everything after an explicit 'body:' marker if the caller used
    # one; otherwise the whole task minus the leading draft command phrase.
    body_m = re.search(r"\bbody:\s*(.+)$", t, re.IGNORECASE | re.DOTALL)
    if body_m:
        content = body_m.group(1)
    else:
        content = re.sub(
            r"^\s*(please\s+)?(save\s+(it\s+)?as\s+a?\s*|create\s+a?\s*|write\s+a?\s*|compose\s+a?\s*|draft\s+a?\s*)*"
            r"(gmail\s+|an?\s+)?(email\s+|draft\s+)*(draft|email)?[^:\n]*:?\s*",
            "", t, count=1, flags=re.IGNORECASE)
    # Strip a leading explicit "to:/subject:" field left inside the content.
    content = re.sub(r"^\s*to:\s*[^\s,;<>]+@[^\s,;<>]+\s*", "", content, flags=re.IGNORECASE)

    # Subject: from the FULL task (catches both a structured 'subject: X body: …'
    # field AND an inline 'Subject: X' line in the email). Non-greedy, stops at
    # a 'body:' marker or newline so it's a single clean line.
    subject = None
    subj_m = re.search(r"\bsubject:\s*(.+?)(?=\s+body:|\r?\n|$)", t, re.IGNORECASE)
    if subj_m:
        subject = subj_m.group(1).strip()
    # Remove a leading/inline "Subject: …" line from the body so it isn't
    # duplicated inside the email text.
    content = re.sub(r"(?:^|\n)[ \t]*subject:[^\n]*\r?\n?", "\n", content,
                     count=1, flags=re.IGNORECASE).strip()
    return to, subject, content.strip()


def _create_draft(task):
    """Save a Gmail DRAFT. Never sends — the draft lands in the Drafts folder for
    the user to review/edit/send themselves. Because nothing leaves the mailbox,
    this is a safe, fully-reversible action and needs NO consent gate (a Project
    agent can create drafts autonomously; the human reviews before any send)."""
    to, subject, body = _parse_draft(task)
    if not body:
        return _DRAFT_USAGE
    try:
        import base64
        from email.mime.text import MIMEText
        service = _get_service()
        msg = MIMEText(body)
        if to:
            msg["to"] = to
        msg["subject"] = subject or "(no subject)"
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        d = service.users().drafts().create(
            userId="me", body={"message": {"raw": raw}}).execute()
        where = f" to {to}" if to else ""
        return (f"Draft saved{where}: \"{subject or '(no subject)'}\" "
                f"(draft id {d.get('id', '?')}). Review it in Gmail → Drafts.")
    except Exception as e:
        return f"Gmail draft error: {e}"


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
    t = task or ""
    # Draft BEFORE send: "draft an email …" must never trigger the send path.
    if _DRAFT_RE.search(t):
        return _create_draft(t)
    if _SEND_RE.search(t):
        return _send_email(t)
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
