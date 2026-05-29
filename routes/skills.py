"""CODEC Dashboard -- Skill-related routes (review, approve, list, triggers).

Skill creation is exclusively via the review-and-approve flow:
    POST /api/skill/review   →  stages code for human review (no disk write)
    POST /api/skill/approve  →  writes to disk after explicit approval

The legacy direct-write endpoints `/api/save_skill` (D-3) and `/api/forge`
(D-2) were removed in PR-1B (see `docs/audits/PHASE-1-SECURITY.md`). Both
were CRITICAL RCE-enabling paths: save_skill wrote LLM/user-supplied code
straight to `<skills_dir>/<name>.py` after only a substring blocker; forge
fetched arbitrary URLs (SSRF) and turned the response into a skill via the
LLM. Defense in depth pairs with PR-1A's `SkillRegistry.load` AST gate —
even if a malicious file reached disk via some other path, the load-time
hash + AST check refuses it.
"""
import json
import os

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from routes._shared import (
    log, _get_skills_dir, _pending_skills,
)

router = APIRouter()


def _pinned_builtin_names():
    """Basenames (e.g. 'calculator.py') of hash-pinned built-in skills, read
    from the committed <repo>/skills/.manifest.json. An approved user skill must
    never take one of these names: doing so shadows the trusted built-in (or,
    if the write dir is the repo skills dir, overwrites the hash-pinned file).
    Fix #7b / H2·H6. Returns a lowercased set on success, or **None on any read
    failure** so the caller can FAIL CLOSED (re-audit N20: returning an empty
    set let the approve guard block nothing during a transient manifest read
    failure). Lowercased so the guard holds on case-insensitive filesystems."""
    try:
        repo_skills = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills"
        )
        with open(os.path.join(repo_skills, ".manifest.json"), encoding="utf-8") as f:
            data = json.load(f)
        return {str(n).lower() for n in (data.get("skills") or {}).keys()}
    except Exception:
        return None


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
    # Fix #7b (H2/H6): never let an approved skill take a hash-pinned built-in's
    # name — that would shadow (or overwrite) the trusted built-in. The PR-1A
    # load-time hash/AST gate is the last line of defense; this refuses at the
    # write point so the trusted file is never displaced in the first place.
    # re-audit N20: FAIL CLOSED — _pinned_builtin_names() returns None if the
    # manifest can't be read, in which case we refuse rather than allow (the old
    # empty-set return blocked nothing during a transient read failure).
    _pinned = _pinned_builtin_names()
    if _pinned is None or filename.lower() in _pinned:
        _reason = ("manifest_unreadable" if _pinned is None
                   else "pinned_builtin_overwrite")
        try:
            from codec_audit import log_event
            log_event(
                "skill_approve_blocked", source="codec-routes-skills",
                message=f"refused approve of {filename}: {_reason}",
                level="warning", outcome="denied",
                extra={"filename": filename, "reason": _reason},
            )
        except Exception:
            pass
        _msg = (
            "Refused: cannot verify the protected built-in manifest."
            if _pinned is None
            else f"Refused: '{filename}' is a protected built-in skill name and cannot be overwritten"
        )
        return JSONResponse({"error": _msg}, status_code=400)
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
                except (ValueError, SyntaxError, OSError) as e:
                    log.warning(
                        f"Failed to parse SKILL_TRIGGERS for {name}; "
                        f"listing with no triggers: {e}")
                result.append({"name": name, "triggers": triggers})
    except OSError as e:
        # Listing the skills dir itself failed — the endpoint returns an empty
        # list and the user sees no skills. That's a real failure, not "non-critical".
        log.error(f"Failed to list skills dir {skills_dir}: {e}")
        try:
            from codec_audit import log_event
            log_event(
                "skills_list_failed", source="codec-routes-skills",
                message=f"Skills listing failed: {e}",
                level="error", outcome="error",
                extra={"skills_dir": str(skills_dir), "error": str(e)[:200]},
            )
        except Exception:
            pass
    return result


# ── Custom Triggers Management ────────────────────────────────────────────────
CUSTOM_TRIGGERS_PATH = os.path.expanduser("~/.codec/custom_triggers.json")


def _load_custom_triggers() -> dict:
    try:
        with open(CUSTOM_TRIGGERS_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


@router.get("/api/triggers")
async def list_triggers():
    """Return all skills with their default + custom triggers and hotkeys."""
    skills_dir = _get_skills_dir()
    custom = _load_custom_triggers()
    skills = []
    try:
        for fname in sorted(os.listdir(skills_dir)):
            if not fname.endswith(".py") or fname.startswith("_"):
                continue
            name = fname[:-3]
            triggers, description = [], ""
            try:
                with open(os.path.join(skills_dir, fname)) as f:
                    src = f.read()
                import ast
                for line in src.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("SKILL_TRIGGERS"):
                        # Handle multi-line lists
                        start = src.index("SKILL_TRIGGERS")
                        bracket_start = src.index("[", start)
                        bracket_end = src.index("]", bracket_start) + 1
                        triggers = ast.literal_eval(src[bracket_start:bracket_end])
                        break
                for line in src.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("SKILL_DESCRIPTION"):
                        description = ast.literal_eval(stripped.split("=", 1)[1].strip())
                        break
            except Exception:
                pass
            custom_triggers = custom.get(name, {}).get("triggers")
            skills.append({
                "name": name,
                "description": description,
                "default_triggers": triggers,
                "triggers": custom_triggers if custom_triggers is not None else triggers,
                "customized": custom_triggers is not None,
            })
    except Exception as e:
        log.warning(f"Trigger list error: {e}")
    # Hotkeys
    hotkeys = [
        {"key": "F13", "action": "Toggle CODEC on/off", "editable": False},
        {"key": "F18", "action": "Voice command (hold to record)", "editable": False},
        {"key": "F16", "action": "Text input dialog", "editable": False},
        {"key": "** (double star)", "action": "Screenshot + vision analysis", "editable": False},
        {"key": "++ (double plus)", "action": "Document input mode", "editable": False},
        {"key": "-- (double minus)", "action": "Open live voice chat", "editable": False},
        {"key": "Right CMD (hold)", "action": "Dictate — speak, release to paste", "editable": False},
        {"key": "L (during dictate)", "action": "Live typing mode", "editable": False},
    ]
    wake_words = ["hey codec", "hey", "okay codec", "hey codex", "hey coda", "hey queue"]
    return {"skills": skills, "hotkeys": hotkeys, "wake_words": wake_words}


@router.post("/api/triggers")
async def save_triggers(request: Request):
    """Save custom triggers for one or more skills."""
    body = await request.json()
    custom = _load_custom_triggers()
    for skill_name, data in body.items():
        triggers = data.get("triggers")
        if triggers is not None and isinstance(triggers, list):
            # Filter empty strings
            triggers = [t.strip().lower() for t in triggers if t.strip()]
            if triggers:
                custom[skill_name] = {"triggers": triggers}
            else:
                custom.pop(skill_name, None)
        elif triggers is None:
            # Reset to default
            custom.pop(skill_name, None)
    os.makedirs(os.path.dirname(CUSTOM_TRIGGERS_PATH), exist_ok=True)
    with open(CUSTOM_TRIGGERS_PATH, "w") as f:
        json.dump(custom, f, indent=2)
    return {"status": "saved", "custom_count": len(custom)}
