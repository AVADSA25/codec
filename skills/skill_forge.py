"""CODEC Skill: Skill Forge — Convert any code into a CODEC skill"""
SKILL_NAME = "skill_forge"
SKILL_DESCRIPTION = "Converts any code, script, or framework into a working CODEC skill"
SKILL_TRIGGERS = [
    "forge skill", "import skill", "convert to skill", "make this a skill",
    "forge this", "turn this into a skill", "forge code", "skill from code",
    "wrap this as a skill", "make a skill from this"
]

import os, requests, json, re

SKILLS_DIR = os.path.expanduser("~/.codec/skills")
CONFIG_PATH = os.path.expanduser("~/.codec/config.json")


def run(task, app="", ctx=""):
    """Extract code from ctx (clipboard/screen) or task, then forge it into a CODEC skill."""
    # Determine what code to forge — prefer ctx (screen/clipboard content)
    code_to_forge = ""
    if ctx and len(ctx.strip()) > 30:
        # ctx might contain code pasted in
        code_to_forge = ctx.strip()
    else:
        # Try to extract code block from task itself
        code_match = re.search(r'```[\w]*\n?([\s\S]+?)```', task)
        if code_match:
            code_to_forge = code_match.group(1).strip()
        else:
            # Use the full task minus the trigger phrase
            cleaned = task
            for trigger in SKILL_TRIGGERS:
                cleaned = re.sub(re.escape(trigger), '', cleaned, flags=re.IGNORECASE)
            code_to_forge = cleaned.strip()

    if not code_to_forge or len(code_to_forge) < 20:
        return (
            "Skill Forge needs some code to work with. "
            "Try: paste your code, then say 'forge this into a skill'. "
            "Or use the Forge button in Vibe Code."
        )

    # Load LLM config
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)

    base_url = cfg.get("llm_base_url", "http://localhost:8081/v1")
    model = cfg.get("llm_model", "")
    api_key = cfg.get("llm_api_key", "")
    kwargs = cfg.get("llm_kwargs", {})

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key

    # Remove enable_thinking if present (causes empty responses on local Qwen)
    safe_kwargs = {k: v for k, v in kwargs.items() if k != "enable_thinking"}

    prompt = f"""Convert the following code into a CODEC skill Python file.

OUTPUT ONLY the Python file content — no markdown, no backticks, no explanation.

Follow this EXACT format:
\"\"\"CODEC Skill: [Descriptive Name]\"\"\"
SKILL_NAME = "[lowercase_name_with_underscores]"
SKILL_DESCRIPTION = "[One line: what this skill does]"
SKILL_TRIGGERS = ["natural phrase 1", "natural phrase 2", "natural phrase 3", "natural phrase 4"]

import os, requests, json  # only what's needed

def run(task, app="", ctx=""):
    # Implementation using the converted code
    # task = what the user said
    # app = focused application
    # ctx = screen context
    return "Response string"  # must return a string

RULES:
- SKILL_NAME: lowercase, underscores only, descriptive
- SKILL_TRIGGERS: 3-5 natural phrases a user would say to activate this
- run() must always return a string (result, status, or error message)
- Wrap the original logic cleanly; add helpful error handling
- If the original code fetches data, return a formatted summary string
- No hardcoded secrets — read from config or env if keys needed

CODE TO CONVERT:
{code_to_forge}"""

    try:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1200,
            "temperature": 0.2
        }
        payload.update(safe_kwargs)

        r = requests.post(base_url + "/chat/completions", json=payload, headers=headers, timeout=90)
        if r.status_code != 200:
            return f"Forge failed: LLM returned {r.status_code}. Is the model server running?"

        raw = r.json()["choices"][0]["message"].get("content", "").strip()

        # Strip think tags and markdown fences
        raw = re.sub(r'<think>[\s\S]*?</think>', '', raw).strip()
        raw = re.sub(r'^```[\w]*\n?', '', raw).strip()
        raw = re.sub(r'\n?```$', '', raw).strip()

        if "SKILL_NAME" not in raw or "def run" not in raw:
            return "Forge produced invalid output. The LLM didn't follow the skill format. Try again with cleaner code."

        # Extract skill name
        name_match = re.search(r'SKILL_NAME\s*=\s*["\'](\w+)["\']', raw)
        if not name_match:
            return "Could not extract SKILL_NAME from forged skill."

        skill_name = name_match.group(1)
        filepath = os.path.join(SKILLS_DIR, f"{skill_name}.py")

        # Syntax check
        try:
            compile(raw, filepath, "exec")
        except SyntaxError as e:
            return f"Forged skill has a syntax error: {e}. Try with simpler code."

        # Save
        os.makedirs(SKILLS_DIR, exist_ok=True)
        with open(filepath, "w") as f:
            f.write(raw)

        return (
            f"Skill '{skill_name}' forged and saved to {filepath}. "
            f"Run: pm2 restart ava-autopilot to activate it."
        )

    except Exception as e:
        return f"Forge error: {e}"
