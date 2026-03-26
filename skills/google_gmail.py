"""Google Gmail — Check emails, search inbox"""
SKILL_NAME = "google_gmail"
SKILL_TRIGGERS = ["check email", "check my email", "my emails", "inbox", "unread emails", "new emails", "any emails", "latest emails", "email from", "search email"]
SKILL_DESCRIPTION = "Check your Gmail inbox, search for emails, and view recent messages"

import json, os

TOKEN_PATH = os.path.expanduser("~/.codec/google_token.json")

def _get_service():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    creds = Credentials.from_authorized_user_file(TOKEN_PATH)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_PATH, 'w') as f: f.write(creds.to_json())
    return build('gmail', 'v1', credentials=creds)

def run(task, app="", ctx=""):
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
