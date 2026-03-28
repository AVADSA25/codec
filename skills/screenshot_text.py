"""Take a screenshot and extract text via vision model"""
SKILL_NAME = "screenshot_text"
SKILL_TRIGGERS = ["read my screen", "read screen", "ocr", "text on screen", "what does it say", "screen text", "whats on my screen", "what is on my screen"]
SKILL_DESCRIPTION = "Screenshot the screen and extract text using vision"

import subprocess, tempfile, base64, requests

def run(task, app="", ctx=""):
    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        subprocess.run(["screencapture", "-x", tmp.name], timeout=5)
        with open(tmp.name, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        r = requests.post("http://localhost:8082/v1/chat/completions", json={
            "model": "mlx-community/Qwen2.5-VL-7B-Instruct-4bit",
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                {"type": "text", "text": "Describe everything visible on this screen. What apps are open? What text is visible? Be concise but complete."}
            ]}],
            "max_tokens": 4000
        }, timeout=30)
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"Screen read failed: {e}"
