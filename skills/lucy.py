"""CODEC Workflow — Delegate complex tasks via n8n webhook"""
SKILL_NAME = "delegate"
SKILL_TRIGGERS = ["delegate", "send to workflow", "invoice", "expense", "calorie", "daily briefing", "book a call", "vapi"]
SKILL_DESCRIPTION = "Delegates complex tasks to CODEC workflows — invoices, expenses, calorie tracking, phone calls, and multi-step workflows"
SKILL_MCP_EXPOSE = True

import requests

WORKFLOW_WEBHOOK = "http://localhost:5678/webhook/codec-delegate"

def run(task, app="", ctx=""):
    try:
        clean = task.lower()
        for word in ["delegate", "send to workflow"]:
            clean = clean.replace(word, "").strip()
        if not clean:
            clean = task

        r = requests.post(WORKFLOW_WEBHOOK, json={
            "message": clean,
            "source": "codec",
            "app": app
        }, timeout=300)

        if r.status_code == 200:
            try:
                data = r.json()
                if isinstance(data, dict) and data.get("output"):
                    return data["output"]
                return "CODEC workflow responded but no output parsed"
            except:
                return r.text[:500] if r.text else "CODEC workflow processed but no response"
        else:
            return f"CODEC workflow error (status {r.status_code})"
    except requests.exceptions.Timeout:
        return "CODEC workflow is still processing - check Telegram for the response"
    except Exception as e:
        return f"Could not reach CODEC workflow: {str(e)}"
