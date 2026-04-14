"""Google Tasks skill for CODEC — list, add, and complete tasks"""
import os, re

SKILL_NAME = "google_tasks"

SKILL_TRIGGERS = [
    "google tasks", "my tasks", "task list", "to do", "todo",
    "show tasks", "check tasks", "list tasks",
    "add task", "add a task", "new task", "create task", "create a task",
    "add to my to-do", "add to my todo", "add to to-do", "add to todo",
    "add to my to do list", "add to my to-do list",
    "complete task", "finish task", "mark task",
]
SKILL_DESCRIPTION = "View, add, and manage Google Tasks"


def _get_creds():
    import sys
    sys.path.insert(0, os.path.expanduser("~/codec-repo"))
    from codec_google_auth import get_credentials
    return get_credentials()


_WRITE_VERBS = (
    "add task", "add a task", "new task", "create task", "create a task",
    "create new task", "make a task", "make task",
    "add to my to-do list", "add to my to do list", "add to my todo list",
    "add to my to-do", "add to my todo", "add to to-do", "add to todo",
    "add to my list", "add to the list",
    "todo add", "to do add",
)

_COMPLETE_VERBS = ("complete task", "finish task", "mark task", "done with task", "mark done")


def _extract_title(task: str) -> str:
    """Pull the task title out of phrases like:
       'add to my to-do list: edit and upload demo video by tomorrow'
       'create new task called review email'
       'add a task titled "x"'"""
    t = task.strip()
    low = t.lower()

    # Strip leading write-verb phrase (longest match first, word-boundary)
    verbs_sorted = sorted(_WRITE_VERBS, key=len, reverse=True)
    for v in verbs_sorted:
        m = re.match(r'^\s*' + re.escape(v) + r'\b', low)
        if m:
            t = t[m.end():].strip()
            low = t.lower()
            break

    # Strip leading punctuation connectors (:, ,, -) — not word-chars, no \b
    t = re.sub(r'^\s*[:,\-]+\s*', '', t).strip()
    low = t.lower()

    # Strip word connectors after the verb
    for connector in ["called", "titled", "named", "saying", "that says", "to"]:
        m = re.match(r'^\s*' + re.escape(connector) + r'\b', low)
        if m:
            t = t[m.end():].strip()
            low = t.lower()
            break

    # Strip surrounding quotes and trailing punctuation
    t = t.strip().strip('"\'').rstrip("?.,!").strip()
    return t


def _parse_due(title: str) -> tuple[str, str | None]:
    """Extract 'by tomorrow' / 'by next monday' etc from the title.
    Returns (cleaned_title, due_rfc3339_or_None)."""
    from datetime import datetime, timedelta
    low = title.lower()
    due = None
    for pat, delta in [
        (r'\s+by\s+tomorrow\b', 1),
        (r'\s+tomorrow\b', 1),
        (r'\s+by\s+today\b', 0),
        (r'\s+today\b', 0),
    ]:
        if re.search(pat, low):
            d = datetime.now() + timedelta(days=delta)
            # Google Tasks expects RFC3339 at midnight UTC
            due = d.strftime("%Y-%m-%dT00:00:00.000Z")
            title = re.sub(pat, '', title, flags=re.IGNORECASE).strip()
            break
    return title, due


def _pick_list(service, task_lower: str):
    """Pick a task list by keyword match, default to the first one."""
    tasklists = service.tasklists().list(maxResults=10).execute().get("items", [])
    if not tasklists:
        return None, []
    target = tasklists[0]
    for tl in tasklists:
        for w in task_lower.split():
            if len(w) > 2 and w in tl.get("title", "").lower():
                target = tl
                break
    return target, tasklists


def run(task, context=None):
    try:
        from googleapiclient.discovery import build
        creds = _get_creds()
        service = build("tasks", "v1", credentials=creds)
        low = task.lower()

        target_list, tasklists = _pick_list(service, low)
        if target_list is None:
            return "No Google Tasks lists found."

        # ── Write intent: add new task ────────────────────────────────────
        if any(v in low for v in _WRITE_VERBS):
            title = _extract_title(task)
            if not title or len(title) < 2:
                return "What should I add to your tasks? (e.g. 'add task call the dentist')"
            title, due = _parse_due(title)
            body = {"title": title}
            if due:
                body["due"] = due
            new_task = service.tasks().insert(
                tasklist=target_list["id"], body=body
            ).execute()
            when = f" (due {due[:10]})" if due else ""
            return f"✅ Added to {target_list['title']}: {new_task.get('title', title)}{when}"

        # ── Complete intent: mark a task done ─────────────────────────────
        if any(v in low for v in _COMPLETE_VERBS):
            # Find task whose title keyword matches
            items = service.tasks().list(
                tasklist=target_list["id"], maxResults=50, showCompleted=False
            ).execute().get("items", [])
            # Extract candidate keyword(s) after the verb
            words = [w for w in low.split() if len(w) > 3
                     and w not in ("task", "tasks", "complete", "finish", "mark", "done", "with")]
            match = None
            for it in items:
                t_low = it.get("title", "").lower()
                if any(w in t_low for w in words):
                    match = it
                    break
            if not match:
                return "Couldn't find a matching task to complete."
            match["status"] = "completed"
            service.tasks().update(
                tasklist=target_list["id"], task=match["id"], body=match
            ).execute()
            return f"✅ Marked done: {match.get('title')}"

        # ── Default: list pending tasks ───────────────────────────────────
        tasks_result = service.tasks().list(
            tasklist=target_list["id"], maxResults=20, showCompleted=False
        ).execute()
        items = tasks_result.get("items", [])

        if not items:
            return f"\u2705 **{target_list['title']}** \u2014 no pending tasks!"

        lines = [f"\U0001f4cb **{target_list['title']}** \u2014 {len(items)} pending tasks:\n"]
        for t in items:
            due = ""
            if t.get("due"):
                due = f" (due {t['due'][:10]})"
            lines.append(f"  \u2610 {t.get('title', 'Untitled')}{due}")

        if len(tasklists) > 1:
            lines.append(f"\n_You have {len(tasklists)} task lists: {', '.join(tl['title'] for tl in tasklists)}_")
        return "\n".join(lines)
    except Exception as e:
        return f"Google Tasks error: {e}"
