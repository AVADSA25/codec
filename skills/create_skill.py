"""CODEC Skill: Create New Skills by Voice"""
SKILL_NAME = "create_skill"
SKILL_DESCRIPTION = "Create new CODEC skills by describing what you want"
SKILL_TRIGGERS = ["create a skill", "make a skill", "new skill", "build a skill",
                   "create skill", "write a skill", "add a skill"]
import os, requests, json

SKILLS_DIR = os.path.expanduser("~/.codec/skills")
CONFIG_PATH = os.path.expanduser("~/.codec/config.json")

def run(task, app="", ctx=""):
    # Extract what the skill should do
    description = task.lower()
    for remove in ["create a skill", "make a skill", "new skill", "build a skill",
                    "create skill", "write a skill", "add a skill", "that", "to", "for", "please", "can you"]:
        description = description.replace(remove, "")
    description = description.strip()
    if not description or len(description) < 5:
        return "What should the skill do? Try: create a skill that checks bitcoin price"

    # Load LLM config
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f: cfg = json.load(f)

    base_url = cfg.get("llm_base_url", "http://localhost:8081/v1")
    model = cfg.get("llm_model", "")
    api_key = cfg.get("llm_api_key", "")
    kwargs = cfg.get("llm_kwargs", {})

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key

    prompt = f"""Write a CODEC skill Python file. Follow this EXACT format:

\"\"\"CODEC Skill: [Name]\"\"\"
SKILL_NAME = "[lowercase_name]"
SKILL_DESCRIPTION = "[one line description]"
SKILL_TRIGGERS = ["trigger phrase 1", "trigger phrase 2", "trigger phrase 3"]

def run(task, app="", ctx=""):
    # Your code here
    return "Response string spoken back to user"

RULES:
- ONLY output the Python code, nothing else
- No markdown, no backticks, no explanation
- The run() function must return a string
- Use subprocess for system commands
- Use requests for HTTP calls
- Keep it simple and functional
- SKILL_NAME must be lowercase with underscores only

The skill should: {description}"""

    try:
        payload = {"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 1000, "temperature": 0.3}
        payload.update(kwargs)
        r = requests.post(base_url + "/chat/completions", json=payload, headers=headers, timeout=60)
        if r.status_code != 200:
            return "Failed to generate skill. LLM returned error."

        code = r.json()["choices"][0]["message"].get("content", "").strip()
        # Clean up any markdown
        code = code.replace("```python", "").replace("```", "").strip()

        if "SKILL_NAME" not in code or "def run" not in code:
            return "Generated code doesn't look like a valid skill. Try again with a clearer description."

        # Extract skill name from generated code
        import re
        name_match = re.search(r'SKILL_NAME\s*=\s*["\'](\w+)["\']', code)
        if not name_match:
            return "Could not determine skill name from generated code."

        skill_name = name_match.group(1)
        filepath = os.path.join(SKILLS_DIR, f"{skill_name}.py")

        if os.path.exists(filepath):
            return f"Skill {skill_name} already exists. Delete it first or choose a different name."

        # Validate the code compiles
        try:
            compile(code, filepath, "exec")
        except SyntaxError as e:
            return f"Generated code has a syntax error: {e}. Try describing the skill differently."

        # Save it
        os.makedirs(SKILLS_DIR, exist_ok=True)
        with open(filepath, "w") as f:
            f.write(code)

        return f"Skill {skill_name} created and saved. Restart CODEC to load it. File: {filepath}"

    except Exception as e:
        return f"Error creating skill: {e}"
