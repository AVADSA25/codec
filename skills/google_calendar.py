"""Google Calendar — Check, create, and manage calendar events"""
SKILL_NAME = "google_calendar"
SKILL_TRIGGERS = ["calendar", "what's on my calendar", "my schedule", "my events", "meetings today", "add event", "add to calendar", "schedule meeting", "create event", "what do i have today", "what do i have tomorrow", "am i free", "next meeting"]
SKILL_DESCRIPTION = "Check your Google Calendar, create events, and view your schedule"

import json, os, datetime

TOKEN_PATH = os.path.expanduser("~/.codec/google_token.json")

def _get_service():
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    creds = Credentials.from_authorized_user_file(TOKEN_PATH)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_PATH, 'w') as f: f.write(creds.to_json())
    return build('calendar', 'v3', credentials=creds)

def run(task, app="", ctx=""):
    try:
        service = _get_service()
        low = task.lower()

        # Create event
        if any(w in low for w in ["add event", "add to calendar", "schedule meeting", "create event", "book"]):
            return _create_event_hint(task)

        # Check schedule
        now = datetime.datetime.utcnow()
        if "tomorrow" in low:
            start = (now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0)
            end = start + datetime.timedelta(days=1)
            label = "Tomorrow"
        elif "this week" in low:
            start = now
            end = now + datetime.timedelta(days=7)
            label = "This week"
        else:
            start = now.replace(hour=0, minute=0, second=0)
            end = start + datetime.timedelta(days=1)
            label = "Today"

        events = service.events().list(
            calendarId='primary',
            timeMin=start.isoformat() + 'Z',
            timeMax=end.isoformat() + 'Z',
            maxResults=15,
            singleEvents=True,
            orderBy='startTime'
        ).execute().get('items', [])

        if not events:
            return f"No events {label.lower()}. Your calendar is clear."

        lines = [f"{label}'s schedule ({len(events)} events):"]
        for e in events:
            start_str = e['start'].get('dateTime', e['start'].get('date', ''))
            if 'T' in start_str:
                t = datetime.datetime.fromisoformat(start_str.replace('Z', '+00:00'))
                time_str = t.strftime('%H:%M')
            else:
                time_str = 'All day'
            summary = e.get('summary', 'No title')
            lines.append(f"  {time_str} - {summary}")
        return "\n".join(lines)

    except Exception as e:
        return f"Calendar error: {str(e)}"

def _create_event_hint(task):
    return f"To create an event, ask Lucy: 'Ask Lucy to add to my calendar {task}'. Lucy has full calendar write access through Google Workspace."
