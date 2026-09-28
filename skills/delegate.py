"""CODEC Workflow — Delegate complex tasks via n8n webhook

Posts to the local n8n webhook below unless ~/.codec/config.json has a
"delegate" block, e.g. {"url": "https://n8n.example.com/webhook/x",
"auth_header": "X-Webhook-Key", "key_slot": "delegate_webhook_key"}.
The header value is read from the Keychain slot, never from config."""
SKILL_NAME = "delegate"
SKILL_TRIGGERS = ["delegate", "send to workflow", "invoice", "expense", "calorie", "daily briefing", "book a call", "vapi"]
SKILL_DESCRIPTION = "Delegates complex tasks to CODEC workflows — invoices, expenses, calorie tracking, phone calls, and multi-step workflows"
SKILL_MCP_EXPOSE = True

import requests

WORKFLOW_WEBHOOK = "http://localhost:5678/webhook/codec-delegate"


def _target():
    """(url, headers) for the webhook call: config "delegate" block or the default."""
    try:
        from codec_config import cfg
        conf = cfg.get("delegate") or {}
    except Exception:
        conf = {}
    headers = {}
    header, slot = conf.get("auth_header"), conf.get("key_slot")
    if header and slot:
        try:
            from codec_keychain import keychain_get
            key = keychain_get(slot)
        except Exception:
            key = None
        if key:
            headers[header] = key
    return conf.get("url") or WORKFLOW_WEBHOOK, headers


def run(task, app="", ctx=""):
    try:
        clean = task.lower()
        for word in ["delegate", "send to workflow"]:
            clean = clean.replace(word, "").strip()
        if not clean:
            clean = task

        url, headers = _target()
        r = requests.post(url, json={
            "message": clean,
            "source": "codec",
            "app": app
        }, headers=headers, timeout=300)

        if r.status_code == 200:
            try:
                data = r.json()
                if isinstance(data, dict) and data.get("output"):
                    return data["output"]
                return "CODEC workflow responded but no output parsed"
            except Exception:
                return r.text[:500] if r.text else "CODEC workflow processed but no response"
        else:
            return f"CODEC workflow error (status {r.status_code})"
    except requests.exceptions.Timeout:
        return "CODEC workflow is still processing - check Telegram for the response"
    except Exception as e:
        return f"Could not reach CODEC workflow: {str(e)}"
