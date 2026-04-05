"""Google Calendar — Check and create calendar events"""
SKILL_NAME = "google_calendar"
SKILL_TRIGGERS = [
    # create — long/specific triggers first so they match before short ones
    "put to my calendar", "put in my calendar", "put on my calendar",
    "put it in my calendar", "put it on my calendar", "put this in my calendar",
    "put an appointment", "put a meeting",
    "add to my calendar", "add to calendar", "add an event", "add event",
    "add appointment", "add a meeting", "add a reminder", "add it to my calendar",
    "create event", "create a meeting", "schedule a meeting", "schedule meeting",
    "schedule an appointment", "book a meeting", "book appointment",
    "set a reminder", "set an appointment", "new event", "new appointment",
    "remind me",
    # read
    "what's on my calendar", "what is on my calendar", "my schedule", "my events",
    "meetings today", "what do i have today", "what do i have tomorrow", "am i free",
    "next meeting", "check my calendar", "show my calendar", "calendar",
]
SKILL_DESCRIPTION = "Check and create Google Calendar events by voice"

import os, re, datetime, json

TOKEN_PATH = os.path.expanduser("~/.codec/google_token.json")

# ── Create intent detection ────────────────────────────────────────────────────
# Instead of exact phrases (brittle), detect VERB + NOUN intent.
# Any create-verb near a calendar-noun = create intent.
_CREATE_VERBS  = ["create", "add", "put", "set", "make", "book", "schedule",
                  "insert", "register", "log", "record", "remind", "new"]
_CALENDAR_NOUNS = ["calendar", "event", "appointment", "meeting", "reminder",
                   "booking", "slot", "session"]

def _is_create_intent(low: str) -> bool:
    has_verb  = any(v in low for v in _CREATE_VERBS)
    has_noun  = any(n in low for n in _CALENDAR_NOUNS)
    return has_verb and has_noun

# Keep CREATE_WORDS for _parse_title stripping (remove filler from title)
CREATE_WORDS = _CREATE_VERBS + _CALENDAR_NOUNS

def _get_service():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    creds = Credentials.from_authorized_user_file(TOKEN_PATH)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
    return build("calendar", "v3", credentials=creds)

# ── Date/time parsing ──────────────────────────────────────────────────────────

def _parse_datetime(text: str):
    """
    Extract a (start_dt, end_dt) pair from natural language.
    Returns datetime objects in local time (naive).
    """
    low = text.lower()
    now = datetime.datetime.now()
    today = now.date()

    # ── Day ──
    if "tomorrow" in low:
        target_date = today + datetime.timedelta(days=1)
    elif "today" in low:
        target_date = today
    else:
        # Day-of-week
        days = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]
        target_date = None
        for i, d in enumerate(days):
            if d in low:
                current_wd = today.weekday()
                delta = (i - current_wd) % 7
                if delta == 0:
                    delta = 7  # next occurrence
                target_date = today + datetime.timedelta(days=delta)
                break
        if target_date is None:
            # Try DD/MM or month name
            m = re.search(r'\b(\d{1,2})[/\-](\d{1,2})\b', low)
            if m:
                try:
                    target_date = datetime.date(now.year, int(m.group(2)), int(m.group(1)))
                except ValueError:
                    pass
            if target_date is None:
                target_date = today + datetime.timedelta(days=1)  # default tomorrow

    # ── Time ──
    hour, minute = 12, 0  # default noon

    # Normalise a.m./p.m. → am/pm and "half past X" → for simpler regex below
    low = re.sub(r'a\.m\.', 'am', low)
    low = re.sub(r'p\.m\.', 'pm', low)

    # Priority 1 — "HH:MM" or "HH.MM"  e.g. "10:30", "10.30"
    m = re.search(r'\b(\d{1,2})[:\.](\d{2})\s*(am|pm)?\b', low)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        suffix = (m.group(3) or "").strip()
        if suffix == "pm" and hour < 12:
            hour += 12
        elif suffix == "am" and hour == 12:
            hour = 0

    # Priority 2 — "HH MM am/pm"  e.g. "10 30 am"  (space-separated, needs am/pm anchor)
    elif re.search(r'\b(\d{1,2})\s+(\d{2})\s*(am|pm)\b', low):
        m = re.search(r'\b(\d{1,2})\s+(\d{2})\s*(am|pm)\b', low)
        hour, minute = int(m.group(1)), int(m.group(2))
        suffix = m.group(3)
        if suffix == "pm" and hour < 12:
            hour += 12
        elif suffix == "am" and hour == 12:
            hour = 0

    # Priority 3 — "HH am/pm" or "HH o'clock"  e.g. "10 am", "3pm"
    elif re.search(r'\b(\d{1,2})\s*(am|pm|o\'?clock|oclock)\b', low):
        m = re.search(r'\b(\d{1,2})\s*(am|pm|o\'?clock|oclock)\b', low)
        hour = int(m.group(1))
        # Only accept plausible hours (0-12 for am/pm, 0-23 for military)
        if hour > 23:
            hour = 12
        suffix = m.group(2)
        if suffix == "pm" and hour < 12:
            hour += 12
        elif suffix == "am" and hour == 12:
            hour = 0
        elif "clock" in suffix and 1 <= hour <= 7:
            hour += 12  # 1-7 o'clock → afternoon default

    # Priority 4 — natural words
    else:
        if "noon" in low:
            hour, minute = 12, 0
        elif "midnight" in low:
            hour, minute = 0, 0
        elif "morning" in low:
            hour, minute = 9, 0
        elif "afternoon" in low:
            hour, minute = 14, 0
        elif "evening" in low or "night" in low:
            hour, minute = 19, 0

    start_dt = datetime.datetime.combine(target_date, datetime.time(hour, minute))
    end_dt   = start_dt + datetime.timedelta(hours=1)
    return start_dt, end_dt

def _parse_title(text: str) -> str:
    """
    Extract event title by stripping trigger phrases, time/date words, and filler.
    """
    low = text.lower()

    # Remove action filler (verbs + connecting words, keep nouns as they may be the title)
    for phrase in ["can you please", "can you", "could you", "please", "i want you to",
                   "i need you to", "i would like", "would you", "hey codec",
                   "create an event", "create event", "add an event", "add event",
                   "add to my calendar", "add to calendar", "put to my calendar",
                   "put in my calendar", "put on my calendar", "put inside my calendar",
                   "put it in my calendar", "schedule a meeting", "schedule meeting",
                   "book a meeting", "book appointment", "set a reminder",
                   "set an appointment", "new event", "new appointment",
                   "on my calendar", "in my calendar", "to my calendar",
                   "my calendar", "to the calendar", "the calendar"]:
        low = low.replace(phrase, " ")

    # Normalise a.m./p.m.
    low = re.sub(r'a\.m\.', 'am', low)
    low = re.sub(r'p\.m\.', 'pm', low)
    # Remove date/time patterns — order matters (longest first)
    low = re.sub(r'\b\d{1,2}[:\.]?\d{2}\s*(am|pm)?\b', '', low)       # "10:30 am", "10.30", "1030"
    low = re.sub(r'\b\d{1,2}\s+\d{2}\s*(am|pm)\b', '', low)           # "10 30 am"
    low = re.sub(r'\b\d{1,2}\s*(am|pm|h|o\'?clock|oclock)\b', '', low) # "3pm", "10 am"
    low = re.sub(r'\b\d{1,2}[/\-]\d{1,2}\b', '', low)                  # "29/03"
    low = re.sub(r'\b\d+\b', '', low)                                   # any remaining stray digits
    low = re.sub(r'\b(tomorrow|today|tonight|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b', '', low)
    low = re.sub(r'\b(noon|midnight|morning|afternoon|evening|night|at|on|for|the|a|an|please|can you|could you|i mean|yeah|okay|right)\b', '', low)
    low = re.sub(r'[.\-]', ' ', low)                                    # leftover punctuation
    low = re.sub(r'\s+', ' ', low).strip()

    # Capitalise nicely
    title = low.title() if low else "Event"
    return title or "Event"

# ── Main run ───────────────────────────────────────────────────────────────────

def run(task, app="", ctx=""):
    try:
        service = _get_service()
        low = task.lower()

        # ── CREATE path ──
        if _is_create_intent(low):
            start_dt, end_dt = _parse_datetime(task)
            title = _parse_title(task)

            # Determine timezone offset (local)
            tz_offset = datetime.datetime.now(datetime.timezone.utc).astimezone().strftime("%z")
            tz_str = tz_offset[:3] + ":" + tz_offset[3:]  # "+01:00"

            event_body = {
                "summary": title,
                "start": {"dateTime": start_dt.strftime("%Y-%m-%dT%H:%M:00") + tz_str},
                "end":   {"dateTime": end_dt.strftime("%Y-%m-%dT%H:%M:00") + tz_str},
            }

            created = service.events().insert(
                calendarId="primary", body=event_body
            ).execute()

            time_str = start_dt.strftime("%-d %B at %-I:%M %p").replace(" 0", " ").strip()
            return (
                f"Done. '{created.get('summary', title)}' added to your Google Calendar "
                f"for {time_str}."
            )

        # ── READ path ──
        now = datetime.datetime.utcnow()
        if "tomorrow" in low:
            start = (now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            end   = start + datetime.timedelta(days=1)
            label = "Tomorrow"
        elif "this week" in low or "week" in low:
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end   = start + datetime.timedelta(days=7)
            label = "This week"
        else:
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end   = start + datetime.timedelta(days=1)
            label = "Today"

        events = service.events().list(
            calendarId="primary",
            timeMin=start.isoformat() + "Z",
            timeMax=end.isoformat() + "Z",
            maxResults=15,
            singleEvents=True,
            orderBy="startTime",
        ).execute().get("items", [])

        if not events:
            return f"No events {label.lower()}. Your calendar is clear."

        lines = [f"{label}'s schedule — {len(events)} event(s):"]
        for e in events:
            s = e["start"].get("dateTime", e["start"].get("date", ""))
            if "T" in s:
                t = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
                time_str = t.strftime("%-I:%M %p")
            else:
                time_str = "All day"
            lines.append(f"  {time_str} — {e.get('summary', 'No title')}")
        return "\n".join(lines)

    except Exception as e:
        return f"Calendar error: {e}"
