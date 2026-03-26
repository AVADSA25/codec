"""Lucy VPA — Delegate complex tasks to Lucy via n8n webhook"""
SKILL_NAME = "lucy"
SKILL_TRIGGERS = ["ask lucy", "tell lucy", "lucy", "delegate", "send to lucy", "invoice", "expense", "calorie", "daily briefing", "book a call", "vapi"]
SKILL_DESCRIPTION = "Delegates complex tasks to Lucy — invoices, expenses, calorie tracking, phone calls, and multi-step workflows"

import requests, json

LUCY_WEBHOOK = "http://localhost:5678/webhook/q-to-lucy"

def run(task, app="", ctx=""):
    try:
        clean = task.lower()
        for word in ["ask lucy", "tell lucy", "lucy", "delegate to lucy", "send to lucy"]:
            clean = clean.replace(word, "").strip()
        if not clean:
            clean = task

        r = requests.post(LUCY_WEBHOOK, json={
            "message": clean,
            "source": "codec",
            "app": app
        }, timeout=300)

        if r.status_code == 200:
            try:
                data = r.json()
                if isinstance(data, dict) and data.get("output"):
                    return data["output"]
                return "Lucy responded but no output parsed"
            except:
                return r.text[:500] if r.text else "Lucy processed but no response"
        else:
            return f"Lucy error (status {r.status_code})"
    except requests.exceptions.Timeout:
        return "Lucy is still thinking - check Telegram for her response"
    except Exception as e:
        return f"Could not reach Lucy: {str(e)}"
