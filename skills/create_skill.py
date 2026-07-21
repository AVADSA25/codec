"""CODEC Skill: Create New Skills by Voice

Security: Generated code is staged for human review via the dashboard
review gate. Code is NEVER written to disk without approval.
"""
SKILL_NAME = "create_skill"
SKILL_DESCRIPTION = "Create new CODEC skills by describing what you want"
SKILL_MCP_EXPOSE = True
SKILL_TRIGGERS = ["create a skill", "make a skill", "new skill", "build a skill",
                   "create skill", "write a skill", "add a skill"]
import os
import sys
import requests
import json
import logging
import re

# Ensure the repo root is importable regardless of the caller's sys.path state
# (mirrors the proven pattern in health_check.py / notification_reader.py). The
# MCP daemon already puts the repo on sys.path, but a sandboxed subprocess or a
# future caller may not — and this module's `from codec_keychain import ...`
# below MUST resolve or the review POST silently loses its auth token.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

log = logging.getLogger("codec")

SKILLS_DIR = os.path.expanduser("~/.codec/skills")
CONFIG_PATH = os.path.expanduser("~/.codec/config.json")

# Dangerous patterns that must NEVER appear in generated skill code
BLOCKED_IN_SKILLS = [
    "os.system(", "subprocess.call(", "subprocess.run(", "subprocess.Popen(",
    "eval(", "exec(", "__import__", "importlib", "shutil.rmtree",
    "open('/etc", "open('/dev", "ctypes", "os.remove(", "os.unlink(",
    "os.rmdir(", "shutil.move(", "open('/tmp",
]


def _validate_skill_code(code):
    """Validate generated skill code for safety and correctness.
    Returns (ok: bool, error_message: str)."""
    # Must contain required skill metadata
    if "SKILL_NAME" not in code or "def run(" not in code:
        return False, "Generated code doesn't look like a valid skill (missing SKILL_NAME or def run)."

    # Must contain SKILL_DESCRIPTION
    if "SKILL_DESCRIPTION" not in code:
        return False, "Generated code missing SKILL_DESCRIPTION."

    # Block dangerous patterns in generated code
    code_lower = code.lower()
    for blocked in BLOCKED_IN_SKILLS:
        if blocked.lower() in code_lower:
            return False, f"Blocked dangerous pattern in generated code: {blocked}"

    # Must compile
    try:
        compile(code, "<generated_skill>", "exec")
    except SyntaxError as e:
        return False, f"Generated code has a syntax error: {e}. Try describing the skill differently."

    return True, ""


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

    base_url = cfg.get("llm_base_url", "http://localhost:8083/v1")
    model = cfg.get("llm_model", "")
    # PR-2B (D-15 partial): llm_api_key from Keychain.
    try:
        from codec_config import get_llm_api_key as _kc_get_llm
        api_key = _kc_get_llm()
    except Exception:
        api_key = cfg.get("llm_api_key", "")
    kwargs = cfg.get("llm_kwargs", {})

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key

    prompt = f"""Write a CODEC skill Python file. Follow this EXACT format:

\"\"\"CODEC Skill: [Name]\"\"\"
SKILL_NAME = "[lowercase_name]"
SKILL_DESCRIPTION = "[one line description]"
SKILL_MCP_EXPOSE = True
SKILL_TRIGGERS = ["trigger phrase 1", "trigger phrase 2", "trigger phrase 3"]

def run(task, app="", ctx=""):
    # Your code here
    return "Response string spoken back to user"

RULES:
- ONLY output the Python code, nothing else
- No markdown, no backticks, no explanation
- The run() function must return a string
- Keep it simple and functional
- SKILL_NAME must be lowercase with underscores only
- Do NOT use subprocess, os.system, eval, exec, or __import__

NETWORK — this is the most important rule:
- PREFER computing the answer locally with the Python standard library
  (math, datetime, statistics, json, re, ...). Most things people ask for —
  dates, moon phases, conversions, formatting, sums, timers — are pure
  computation and need no network at all.
- NEVER invent an API URL. Do not guess a hostname or an endpoint path. If you
  are not CERTAIN a public endpoint exists at exactly that URL, do not call it.
  A skill that calls a made-up endpoint fails every single time it runs, and
  looks like the skill is broken.
- Only use `requests` when the data genuinely cannot be computed locally (live
  weather, prices, news) AND you are certain of the real endpoint.

The skill should: {description}"""

    try:
        # A-12 (PR-3E-skills-misc): codec_llm.call (never-raise → "" → fallback;
        # <think> strip built in). kwargs passed through (matches payload.update).
        import codec_llm
        code = codec_llm.call(
            [{"role": "user", "content": prompt}],
            base_url=base_url, model=model, api_key=api_key,
            max_tokens=1000, temperature=0.3, extra_kwargs=kwargs, timeout=60,
        )
        if not code:
            return "Failed to generate skill. LLM returned error."
        # Clean up any markdown
        code = code.replace("```python", "").replace("```", "").strip()

        # Validate safety and correctness BEFORE anything else
        ok, err = _validate_skill_code(code)
        if not ok:
            return err

        # Extract skill name from generated code
        name_match = re.search(r'SKILL_NAME\s*=\s*["\'](\w+)["\']', code)
        if not name_match:
            return "Could not determine skill name from generated code."

        skill_name = name_match.group(1)
        filepath = os.path.join(SKILLS_DIR, f"{skill_name}.py")

        if os.path.exists(filepath):
            return f"Skill {skill_name} already exists. Delete it first or choose a different name."

        # ── ROUTE THROUGH REVIEW GATE ──
        # Stage for human review via dashboard API — NEVER write directly to disk.
        # The review endpoint sits behind AuthMiddleware (PR-2D): internal callers
        # must present the per-process HMAC token or the request 401s. Without this
        # header, create_skill over MCP/voice got "Not authenticated".
        try:
            _ipc_token = ""
            try:
                from codec_keychain import get_internal_token
                _ipc_token = get_internal_token() or ""
            except Exception as _tok_err:
                # NEVER swallow this silently: an empty token makes the review
                # POST 401 with a misleading "review gate returned error", which
                # is exactly what turned beat-24's one-line auth miss into a
                # multi-hour debug. Make the failure visible in the logs.
                log.warning(
                    "create_skill: internal IPC token fetch failed (%s: %s) — "
                    "review POST will 401",
                    type(_tok_err).__name__, _tok_err,
                )
            review_resp = requests.post(
                "http://localhost:8090/api/skill/review",
                json={"code": code, "filename": f"{skill_name}.py"},
                headers={"x-internal-token": _ipc_token},
                timeout=10,
            )
            if review_resp.status_code == 200:
                review_id = review_resp.json().get("review_id", "?")
                return (
                    f"Skill '{skill_name}' generated and staged for review (ID: {review_id}). "
                    f"Open the Dashboard → Skills to review and approve it before it becomes active."
                )
            else:
                # Surface the empty-token case explicitly so the next operator
                # isn't misled by a bare "Not authenticated" from the gate.
                _hint = (
                    " (internal auth token was empty — check codec_keychain / "
                    "repo on sys.path)" if not _ipc_token else ""
                )
                return (
                    f"Skill generated but review gate returned error: "
                    f"{review_resp.text[:100]}{_hint}"
                )
        except requests.ConnectionError:
            return (
                f"Skill '{skill_name}' generated but dashboard is unreachable for review. "
                f"Start the dashboard and use /api/skill/review to submit it manually."
            )

    except Exception as e:
        return f"Error creating skill: {e}"
