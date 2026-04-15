"""notification_reader skill — read and manage CODEC dashboard notifications."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SKILL_NAME = "notification_reader"
SKILL_DESCRIPTION = "Read unread CODEC notifications and their count. Pass 'count' for count only, 'read-all' to mark all as read, or leave empty to list recent notifications."
SKILL_TRIGGERS = [
    "check notifications", "my notifications", "any notifications",
    "unread notifications", "show notifications", "notification count",
    "read notifications", "mark notifications read"
]
SKILL_MCP_EXPOSE = True


def run(task: str = "", context: str = "") -> str:
    """Read or manage CODEC notifications."""
    from urllib.request import urlopen, Request
    from urllib.error import URLError

    base = "http://localhost:8090"
    headers = {"x-internal": "codec", "Content-Type": "application/json"}

    task_lower = task.lower().strip()

    if "count" in task_lower:
        try:
            req = Request(f"{base}/api/notifications/count", headers=headers)
            resp = urlopen(req, timeout=5)
            data = json.loads(resp.read().decode())
            count = data.get("count", data.get("unread", 0))
            return f"You have {count} unread notification(s)."
        except Exception as e:
            return f"Error checking notification count: {e}"

    if "read-all" in task_lower or "mark" in task_lower:
        try:
            req = Request(f"{base}/api/notifications/read-all",
                          data=b"{}",
                          headers=headers,
                          method="POST")
            resp = urlopen(req, timeout=5)
            return "All notifications marked as read."
        except Exception as e:
            return f"Error marking notifications read: {e}"

    # Default: list recent notifications
    try:
        req = Request(f"{base}/api/notifications", headers=headers)
        resp = urlopen(req, timeout=5)
        data = json.loads(resp.read().decode())

        if isinstance(data, list):
            notifs = data
        elif isinstance(data, dict):
            notifs = data.get("notifications", data.get("items", []))
        else:
            return f"Unexpected response format: {type(data)}"

        if not notifs:
            return "No notifications."

        lines = [f"{len(notifs)} notification(s):\n"]
        for n in notifs[:20]:
            title = n.get("title", n.get("message", "?"))
            ts = n.get("timestamp", n.get("created", ""))
            read = n.get("read", False)
            icon = "  " if read else "* "
            lines.append(f"{icon}{title} ({ts})")

        return "\n".join(lines)
    except Exception as e:
        return f"Error reading notifications: {e}"
