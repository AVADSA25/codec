"""Lucy VPA — Delegate tasks to Lucy via n8n webhook"""
SKILL_NAME = "lucy"
SKILL_TRIGGERS = ["ask lucy", "tell lucy", "lucy", "delegate", "send to lucy", "email", "calendar", "schedule", "book", "invoice", "expense", "calorie"]
SKILL_DESCRIPTION = "Delegates tasks to Lucy, your virtual personal assistant (email, calendar, reminders, invoices, expenses, and more)"

import requests, json

LUCY_WEBHOOK = "http://localhost:5678/webhook/q-to-lucy"

def run(task, app="", ctx=""):
    try:
        # Clean the task — remove trigger words
        clean = task.lower()
        for word in ["ask lucy", "tell lucy", "lucy", "delegate to lucy", "send to lucy"]:
            clean = clean.replace(word, "").strip()
        if not clean:
            clean = task

        r = requests.post(LUCY_WEBHOOK, json={
            "message": clean,
            "source": "codec",
            "app": app
        }, timeout=30)

        if r.status_code == 200:
            try:
                data = r.json()
                if isinstance(data, dict) and data.get("output"):
                    return data["output"]
                return f"Lucy is on it: {clean}"
            except:
                return f"Lucy is on it: {clean}"
        else:
            return f"Lucy didn't respond (status {r.status_code})"
    except Exception as e:
        return f"Couldn't reach Lucy: {str(e)}"
