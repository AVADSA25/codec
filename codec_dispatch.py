"""CODEC Skill Dispatch — load and match skills from ~/.codec/skills/

Uses SkillRegistry for lazy loading: only metadata (name, triggers,
description) is parsed at startup via AST.  The actual module import
happens on first invocation of a skill.
"""
import contextlib
import contextvars
import logging
import math
import secrets
import time

# Audit emits route through the unified log_event adapter (real, not no-op)
# per docs/PHASE1-STEP1-DESIGN.md.
from codec_audit import log_event
from codec_concurrency import run_with_timeout
from codec_config import SKILLS_DIR, USER_SKILLS_DIR
from codec_hooks import HookVeto, run_with_hooks
from codec_skill_registry import SkillRegistry
from codec_skill_switches import is_off as _skill_off, off_message as _off_message

log = logging.getLogger('codec')

# Global registry instance shared across codec.py
registry = SkillRegistry(SKILLS_DIR, USER_SKILLS_DIR)

# ── Time limit for chat, voice and wake-word runs (P-087, docs/SKILL-TIMEOUTS-DESIGN.md) ──
# A skill run from those three is waited for at most SKILL_TIMEOUT_S seconds, so
# one hung skill (osascript behind an unanswered macOS prompt) cannot hang the
# call. Skills slow by design never get less than their entry here (None = no
# limit). config.json:skill_timeouts overrides: {"<skill>": seconds, "default":
# seconds}, 0 or null = no limit. The agent runner, scheduler, crews, triggers,
# sessions and bridges never enter time_limited(), so they keep no limit.
SKILL_TIMEOUT_S = 60.0
SLOW_SKILL_TIMEOUTS = {
    "delegate": 330,        # its webhook call waits up to 300 s
    "mcp_connect": 240,     # a server sign-in waits up to 180 s
    "skill_forge": 210,     # its model call may take 180 s
    "create_skill": 120,    # a 60 s model call, then the review post
    "fact_extract": 240,    # a 60 s model call, up to 3 tries
    "compare": 150,         # 60 s per model, 6 at a time
    "email_triage": 180,    # the mail fetch, then a 60 s model call
    "AI News Digest": 180,  # about a dozen feeds and searches in a row
    "prompt_feeder": 720,   # up to 12 prompts, 30 s each, in the browser
    "model_switch": None,   # server restart and model load, each bounded in codec_models
    "ask_user": None,       # waits for the owner's answer (ask_user.timeout_seconds)
    "stuck": None,          # asks the owner through ask_user
    "google_gmail": None,   # sending waits for the owner's "send"
}
_AUDIT_TRANSPORT = {"chat": "chat", "voice": "voice", "wake": "dispatch"}
_UNSET = object()
_TIME_LIMITED = contextvars.ContextVar("codec_skill_time_limited", default=None)


class SkillTimedOut(TimeoutError):
    """A skill run passed its time limit. The caller stopped waiting; the run
    itself goes on in a daemon thread (Python cannot stop a thread)."""

    def __init__(self, name, limit, elapsed):
        super().__init__(f"{name} passed its {limit:g} s limit")
        self.name, self.limit, self.elapsed = name, limit, elapsed


def _configured_timeouts():
    """config.json:skill_timeouts, as read at start."""
    try:
        from codec_config import cfg
        conf = cfg.get("skill_timeouts")
    except Exception:
        return {}
    return conf if isinstance(conf, dict) else {}


def _seconds(value):
    """A configured limit: positive seconds, None for no limit (0 or null), _UNSET when unusable."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return _UNSET
    return float(value) if value > 0 else None


def skill_timeout(name):
    """Seconds one chat, voice or wake-word run of `name` may take, or None for no limit."""
    conf = _configured_timeouts()
    own = _seconds(conf[name]) if name in conf else _UNSET
    if own is not _UNSET:
        return own
    general = _seconds(conf["default"]) if "default" in conf else _UNSET
    if general is _UNSET:
        general = SKILL_TIMEOUT_S
    if general is None or name not in SLOW_SKILL_TIMEOUTS:
        return general
    slow = SLOW_SKILL_TIMEOUTS[name]
    return None if slow is None else max(general, slow)


@contextlib.contextmanager
def time_limited(via):
    """Skills run_skill starts inside this block get their time limit. `via` is
    "chat", "voice" or "wake"; consent questions and hooks are not counted."""
    token = _TIME_LIMITED.set(via)
    try:
        yield
    finally:
        _TIME_LIMITED.reset(token)


def _boxed(fn, *args):
    # The skill's own exceptions travel back as values, so the only TimeoutError
    # run_with_timeout raises is the limit's.
    try:
        return True, fn(*args)
    except Exception as e:
        return False, e


def run_bounded(name, fn, *args):
    """fn(*args) under name's limit (skill_timeout). Returns its result and
    raises its own exceptions; raises SkillTimedOut when the limit passes."""
    limit = skill_timeout(name)
    if limit is None:
        return fn(*args)
    ctx = contextvars.copy_context()
    t0 = time.monotonic()
    try:
        ok, value = run_with_timeout(ctx.run, limit, _boxed, fn, *args)
    except TimeoutError:
        raise SkillTimedOut(name, limit, time.monotonic() - t0) from None
    if ok:
        return value
    raise value


def timeout_message(name, limit):
    """The short reply for a run that passed its limit."""
    secs = f"{limit:g}"
    return (f"{str(name).replace('_', ' ')} timed out after {secs} second{'' if secs == '1' else 's'}. "
            "If a macOS permission prompt is open, answer it and try again.")


def note_timeout(err, via, correlation_id=None):
    """Write the one skill_timeout audit line for `err`; return the short reply."""
    log.warning("Skill '%s' passed its %gs limit (%s)", err.name, err.limit, via)
    log_event("skill_timeout", "codec-dispatch",
              f"Skill {err.name} passed its {err.limit:g} s limit",
              extra={"limit_s": err.limit, "via": via},
              level="warning", outcome="timeout", tool=err.name,
              transport=_AUDIT_TRANSPORT.get(via, "dispatch"),
              duration_ms=err.elapsed * 1000.0, error_type="SkillTimeout",
              correlation_id=correlation_id)
    return timeout_message(err.name, err.limit)


def load_skills():
    """Scan skill plugins from SKILLS_DIR — extracts metadata only (fast)."""
    registry.scan()


def check_skill(task):
    """Return a skill-like dict for the matching skill, or None.

    The dict has 'name' and a lazy 'run' key that triggers the actual
    module import on first call.  Stores all matching skill names so
    run_skill can fall through to the next match if a skill returns None.
    """
    # P3.6: a skill switched off on the Skills page never matches.
    matches = [m for m in registry.match_all_triggers(task) if not _skill_off(m)]
    if not matches:
        return None
    name = matches[0]
    return {
        'name': name,
        'triggers': registry.get_triggers(name),
        '_all_matches': matches,
        'run': lambda task, app="", **kw: registry.run(name, task, app),
    }


def run_skill(skill, task, app="", consent=None):
    """Execute a skill and return its result.

    If the skill returns None (indicating it can't handle the task),
    falls through to the next matching skill.

    `consent(skill_name, task) -> bool`, when given, is asked before each
    candidate runs (P-080: the wake word); a refusal stops the dispatch with
    codec_consent.LOCAL_CANCELLED and the skill never runs.
    """
    # License gate (paid edition only — fully inert for OSS/dev builds).
    # Wrapped so a licensing fault can NEVER break the core dispatch path.
    try:
        import codec_license
        if not codec_license.feature_allowed("skill_exec"):
            _st = codec_license.license_state()
            return (f"\U0001F512 Skill execution requires an active CODEC license — "
                    f"{_st.reason}. Activate in Settings to unlock.")
    except (ImportError, AttributeError):
        # B2 / SR-17: narrowed from `except Exception`. The actual failure
        # mode this guards is the import (license is an optional module on
        # OSS builds) or AttributeError on a transitional codec_license API.
        # Any other exception type from inside license_state() should
        # surface to the caller, not be swallowed here.
        pass  # fail-open: licensing must never break dispatch

    all_matches = skill.get('_all_matches', [skill.get('name')])
    # P3.6: a skill switched off on the Skills page does not run (an explicit pick included).
    live = [n for n in all_matches if not _skill_off(n)]
    if not live:
        return _off_message(all_matches[0] if all_matches else skill.get('name', '?'))
    all_matches = live
    # One correlation_id for the whole dispatch attempt (covers any
    # fall-through retries across matched skills).
    cid = secrets.token_hex(6)
    t0 = time.monotonic()
    via = _TIME_LIMITED.get()  # P-087: set by chat and the wake word only

    for skill_name in all_matches:
        if consent is not None and not consent(skill_name, task):
            from codec_consent import LOCAL_CANCELLED
            return LOCAL_CANCELLED
        try:
            # Wrap registry.run with the plugin hook surface (Phase 1 Step 2).
            # The cid generated above is the operation correlation_id; hooks
            # inherit it via run_with_hooks per Step 1 §1.4 + Step 2 §7.3.
            def _invoke(t, c, _name=skill_name):
                if via:
                    return run_bounded(_name, registry.run, _name, t, c if c else app)
                return registry.run(_name, t, c if c else app)
            result = run_with_hooks(
                tool_name=skill_name,
                task=task,
                context="",
                transport="dispatch",
                correlation_id=cid,
                invoke=_invoke,
            )
            if isinstance(result, HookVeto):
                # First veto wins per §5.3. Surface a deterministic string to
                # the caller (codec.py:_dispatch_inner / chat _try_skill /
                # chat _try_skill_by_name) — same shape as a normal skill
                # result, just contains the veto reason.
                log.info("Skill '%s' vetoed by plugin '%s': %s",
                         skill_name, result.plugin_name, result.reason)
                return (f"Skill '{skill_name}' was vetoed by plugin "
                        f"'{result.plugin_name}': {result.reason}")
            if result is None:
                log.info("Skill '%s' returned None — trying next match", skill_name)
                continue
            log_event("wake_dispatch", "codec-dispatch",
                      f"Skill: {skill.get('name', '?')}",
                      tool=skill_name,
                      duration_ms=(time.monotonic() - t0) * 1000.0,
                      extra={"result_len": len(str(result)) if result else 0},
                      correlation_id=cid)
            try:
                import os as _os
                _events_path = _os.path.expanduser("~/.codec/overlay_events.jsonl")
                with open(_events_path, "a") as _f:
                    _f.write(f'{{"type":"skill_fired","name":"{skill_name}"}}\n')
            except Exception as e:
                log.debug("Overlay event write failed: %s", e)
            return result
        except SkillTimedOut as e:
            # It may still be acting: answer now, do not start the next match.
            return note_timeout(e, via, correlation_id=cid)
        except Exception as e:
            log.warning("Skill '%s' error: %s — trying next match", skill_name, e)
            log_event("wake_skill_error", "codec-dispatch",
                      f"Skill error: {e}",
                      tool=skill_name,
                      outcome="error",
                      level="error",
                      error_type=type(e).__name__,
                      error=str(e)[:500],
                      correlation_id=cid)
            continue

    return None  # No skill could handle it
