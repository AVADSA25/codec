"""CODEC Skill: Translate (French <-> English via Qwen)"""
SKILL_NAME = "translate"
SKILL_DESCRIPTION = "Translate between French and English"
SKILL_TRIGGERS = ["translate", "in french", "in english", "en francais", "en anglais",
                   "how do you say", "comment dit-on", "what does", "mean in"]

def run(task, app="", ctx=""):
    import requests
    low = task.lower()
    if "in french" in low or "en francais" in low or "to french" in low:
        direction = "Translate to French. Reply ONLY in French."
    elif "in english" in low or "en anglais" in low or "to english" in low:
        direction = "Translate to English. Reply ONLY in English."
    else:
        direction = "If the text is English, translate to French. If French, translate to English. Reply ONLY with the translation."
    text = task
    for remove in ["translate","in french","in english","to french","to english",
                    "en francais","en anglais","how do you say","comment dit-on","please","can you","for me"]:
        text = text.lower().replace(remove, "")
    text = text.strip().strip('"').strip("'").strip()
    if not text: return None
    try:
        r = requests.post("http://localhost:8081/v1/chat/completions",
            json={"model":"mlx-community/Qwen3.5-35B-A3B-4bit",
                "messages":[
                    {"role":"system","content":"You are a translator. You ONLY output the translated text. NEVER output Chinese. NEVER explain. Just the translation."},
                    {"role":"user","content":direction + " Text: " + text}
                ],"max_tokens":200,"temperature":0.3,
                "chat_template_kwargs":{"enable_thinking":False}}, timeout=30)
        if r.status_code == 200:
            result = r.json()["choices"][0]["message"].get("content","").strip()
            if result: return text + " -> " + result
    except: pass
    return "Translation failed."
