"""Google Tasks skill for CODEC — list and manage tasks"""
import os

SKILL_NAME = "google_tasks"

SKILL_TRIGGERS = ["google tasks", "my tasks", "task list", "to do", "todo", "show tasks", "add task", "complete task", "check tasks"]
SKILL_DESCRIPTION = "View and manage Google Tasks"

def _get_creds():
    import sys; sys.path.insert(0, os.path.expanduser("~/codec-repo"))
    from codec_google_auth import get_credentials
    return get_credentials()

def run(task, context=None):
    try:
        from googleapiclient.discovery import build
        creds = _get_creds()
        service = build("tasks", "v1", credentials=creds)
        task_lower = task.lower()

        tasklists = service.tasklists().list(maxResults=10).execute().get("items", [])
        if not tasklists:
            return "No Google Tasks lists found."

        target_list = tasklists[0]
        for tl in tasklists:
            for w in task_lower.split():
                if w in tl.get("title", "").lower():
                    target_list = tl
                    break

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
