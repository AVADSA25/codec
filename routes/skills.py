"""CODEC Dashboard -- Skill-related routes (save, review, approve, forge, list)."""
import os, json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from routes._shared import (
    log, DASHBOARD_DIR, CONFIG_PATH, _get_skills_dir, _pending_skills,
)

router = APIRouter()


@router.post("/api/save_skill")
async def save_skill(request: Request):
    body = await request.json()
    filename = os.path.basename(body.get("filename", "custom_skill.py"))
    if not filename.endswith(".py"): filename += ".py"
    content = body.get("content", "")
    if "SKILL_DESCRIPTION" not in content or "def run(" not in content:
        return JSONResponse({"error": "Invalid skill: must contain SKILL_DESCRIPTION and def run()"}, status_code=400)
    from codec_config import is_dangerous_skill_code
    dangerous, reason = is_dangerous_skill_code(content)
    if dangerous:
        return JSONResponse({"error": f"Blocked: {reason}"}, status_code=400)
    path = os.path.join(_get_skills_dir(), filename)
    with open(path, "w") as f: f.write(content)
    return {"path": path, "skill": filename, "size": len(content)}


@router.post("/api/skill/review")
async def skill_review(request: Request):
    """Stage LLM-generated skill code for human review -- does NOT write to disk."""
    import uuid
    body = await request.json()
    code = body.get("code", "")
    filename = os.path.basename(body.get("filename", "custom_skill.py"))
    if not filename.endswith(".py"):
        filename += ".py"
    if not code:
        return JSONResponse({"error": "No code provided"}, status_code=400)
    review_id = str(uuid.uuid4())[:12]
    _pending_skills[review_id] = {"code": code, "filename": filename}
    return {"review_id": review_id, "code": code, "filename": filename}


@router.post("/api/skill/approve")
async def skill_approve(request: Request):
    """Approve a pending skill review -- writes to disk and removes from pending."""
    body = await request.json()
    review_id = body.get("review_id", "")
    if review_id not in _pending_skills:
        return JSONResponse({"error": "Review not found or already approved"}, status_code=404)
    pending = _pending_skills.pop(review_id)
    code = pending["code"]
    filename = pending["filename"]
    if "SKILL_DESCRIPTION" not in code or "def run(" not in code:
        return JSONResponse({"error": "Invalid skill: must contain SKILL_DESCRIPTION and def run()"}, status_code=400)
    from codec_config import is_dangerous_skill_code
    dangerous, reason = is_dangerous_skill_code(code)
    if dangerous:
        return JSONResponse({"error": f"Blocked: {reason}"}, status_code=400)
    skill_dir = _get_skills_dir()
    os.makedirs(skill_dir, exist_ok=True)
    path = os.path.join(skill_dir, filename)
    with open(path, "w") as f:
        f.write(code)
    return {"path": path, "skill": filename, "size": len(code)}


@router.post("/api/forge")
async def forge_skill(request: Request):
    """Convert arbitrary code (or a URL to code) into a CODEC skill using the LLM"""
    import re as _re
    body = await request.json()
    code = body.get("code", "").strip()
    if not code or len(code) < 4:
        return JSONResponse({"error": "No code provided"}, status_code=400)

    # URL import: if code is a URL, fetch the source first
    source_url = None
    if code.startswith(("http://", "https://")):
        try:
            import requests as _rq_url
            resp = _rq_url.get(code, timeout=15, headers={"User-Agent": "CODEC-Forge/1.0"})
            if resp.status_code != 200:
                return JSONResponse({"error": f"URL fetch failed: {resp.status_code} {code}"}, status_code=400)
            source_url = code
            code = resp.text.strip()
            if not code:
                return JSONResponse({"error": "URL returned empty content"}, status_code=400)
        except Exception as e:
            return JSONResponse({"error": f"URL fetch error: {e}"}, status_code=400)

    cfg = {}
    try:
        with open(CONFIG_PATH) as f: cfg = json.load(f)
    except Exception as e:
        log.warning(f"Non-critical error: {e}")

    base_url = cfg.get("llm_base_url", "http://localhost:8081/v1")
    model = cfg.get("llm_model", "")
    api_key = cfg.get("llm_api_key", "")
    kwargs = {k: v for k, v in cfg.get("llm_kwargs", {}).items() if k != "enable_thinking"}

    headers = {"Content-Type": "application/json"}
    if api_key: headers["Authorization"] = "Bearer " + api_key

    url_note = f"\n(Fetched from: {source_url})" if source_url else ""

    prompt = f"""Convert the following code into a CODEC skill Python file.

CRITICAL: Convert THIS EXACT CODE below. Do NOT invent a weather skill or any other unrelated skill.
Base the skill NAME, DESCRIPTION, TRIGGERS, and implementation ENTIRELY on the actual code provided.{url_note}

OUTPUT ONLY the Python file content — no markdown, no backticks, no explanation.

EXACT FORMAT REQUIRED:
\"\"\"CODEC Skill: [Name derived from the actual code]\"\"\"
SKILL_NAME = "[lowercase_name_matching_what_the_code_does]"
SKILL_DESCRIPTION = "[One line describing what THIS code actually does]"
SKILL_TRIGGERS = ["phrase 1", "phrase 2", "phrase 3", "phrase 4"]

import os, json  # only imports actually needed

def run(task, app="", ctx=""):
    # Wrap the actual code logic here
    return "result string"  # must return a string

RULES:
- SKILL_NAME: lowercase, underscores only — name it after what the code ACTUALLY does
- SKILL_TRIGGERS: natural phrases a user would say to run THIS specific skill
- run() must always return a string
- Preserve the core logic of the original code
- Add error handling around external calls

CODE TO CONVERT:
{code}"""

    try:
        import requests as rq_forge
        payload = {"model": model, "messages": [{"role": "user", "content": prompt}],
                   "max_tokens": 1500, "temperature": 0.1,
                   "chat_template_kwargs": {"enable_thinking": False}}
        payload.update(kwargs)
        r = rq_forge.post(base_url + "/chat/completions", json=payload, headers=headers, timeout=90)
        if r.status_code != 200:
            return JSONResponse({"error": f"LLM returned {r.status_code}"}, status_code=502)

        raw = r.json()["choices"][0]["message"].get("content", "").strip()
        raw = _re.sub(r'<think>[\s\S]*?</think>', '', raw).strip()
        raw = _re.sub(r'^```[\w]*\n?', '', raw).strip()
        raw = _re.sub(r'\n?```$', '', raw).strip()

        # Title line: if first line isn't valid Python, wrap it as a docstring
        lines = raw.split('\n')
        if lines:
            first = lines[0].strip()
            valid_starts = ('"""', "'''", 'import ', 'from ', 'SKILL_', '#', 'def ', 'class ', '@')
            if first and not any(first.startswith(s) for s in valid_starts):
                lines[0] = '"""' + first + '"""'
                raw = '\n'.join(lines)

        if "SKILL_NAME" not in raw or "def run" not in raw:
            return JSONResponse({"error": "LLM output is not a valid skill", "raw": raw}, status_code=422)

        name_match = _re.search(r'SKILL_NAME\s*=\s*["\'](\w+)["\']', raw)
        skill_name = name_match.group(1) if name_match else "forged_skill"

        BLOCKED_IN_SKILLS = [
            "os.system(", "subprocess.", "eval(", "exec(", "__import__",
            "importlib", "shutil.rmtree", "open('/etc", "open('/dev", "ctypes",
        ]
        for blocked in BLOCKED_IN_SKILLS:
            if blocked in raw:
                return JSONResponse({"error": f"Blocked pattern in forged skill: {blocked}", "raw": raw}, status_code=403)

        try:
            compile(raw, f"{skill_name}.py", "exec")
        except SyntaxError as e:
            return JSONResponse({"error": f"Syntax error in generated skill: {e}", "raw": raw}, status_code=422)

        skills_dir = _get_skills_dir()
        os.makedirs(skills_dir, exist_ok=True)
        filepath = os.path.join(skills_dir, f"{skill_name}.py")
        with open(filepath, "w") as f: f.write(raw)

        repo_skills = os.path.join(DASHBOARD_DIR, "skills")
        if os.path.isdir(repo_skills):
            with open(os.path.join(repo_skills, f"{skill_name}.py"), "w") as f: f.write(raw)

        msg = f"Skill '{skill_name}' forged!"
        if source_url:
            msg += f" (imported from URL)"
        msg += " Run: pm2 restart ava-autopilot"
        return {"skill_name": skill_name, "path": filepath, "code": raw,
                "source_url": source_url, "message": msg}

    except Exception as e:
        import traceback; traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/skills")
async def skills():
    """List installed skills"""
    skills_dir = _get_skills_dir()
    result = []
    try:
        for f in sorted(os.listdir(skills_dir)):
            if f.endswith(".py") and not f.startswith("_"):
                path = os.path.join(skills_dir, f)
                name = f.replace(".py", "")
                triggers = []
                try:
                    with open(path) as sf:
                        for line in sf:
                            if "SKILL_TRIGGERS" in line:
                                import ast
                                triggers = ast.literal_eval(line.split("=", 1)[1].strip())
                                break
                except Exception as e:
                    log.warning(f"Non-critical error: {e}")
                result.append({"name": name, "triggers": triggers})
    except Exception as e:
        log.warning(f"Non-critical error: {e}")
    return result
