"""CODEC Skill: Python Execution — run safe Python snippets.

Hardened in PR-2C (closes audit D-9):

1. AST validation at the gate.
   Replaces the previous substring blocker (trivially bypassable via
   getattr-reflection, vars(__builtins__), unicode-escape, etc.) with
   `codec_config.is_dangerous_skill_code`. AST walks the parsed tree
   for DANGEROUS_MODULES imports + DANGEROUS_CALLS invocations + known
   reflection patterns. Refusal emits `python_exec_blocked` audit
   event with the AST reason string.

2. macOS sandbox-exec at runtime.
   The Python interpreter runs inside `sandbox-exec -f <profile>`
   using `codec_sandbox`'s deny-default profile: no network, no
   process spawning, file writes restricted to SKILL_OUTPUT_DIR
   and /tmp. Even if AST-validated code somehow reaches a syscall
   the validator missed (novel reflection), the kernel blocks it.

3. Resource limits via preexec_fn.
   RLIMIT_CPU (5s), RLIMIT_AS (256MB), RLIMIT_NOFILE (32). `sandbox-exec`
   confines syscalls but doesn't cap CPU/memory — these caps prevent
   `while True: pass` and memory-bomb attacks from blocking the
   dashboard worker.

4. Minimal env.
   PATH=/usr/bin:/bin, no PYTHONPATH / LD_LIBRARY_PATH / HOME / SHELL.
   Strips inherited paths an attacker might use to escape /tmp.

The skill stays SKILL_MCP_EXPOSE=False — claude.ai over MCP HTTP
cannot reach this. Local chat / voice paths only.
"""
SKILL_NAME = "python_exec"
SKILL_DESCRIPTION = "Execute a Python code snippet and return the output"
SKILL_TRIGGERS = [
    "run python", "execute python", "python code", "python script",
    "calculate with python", "eval python",
]
SKILL_MCP_EXPOSE = False  # Too powerful for remote

import os
import re
import resource
import subprocess
import tempfile

# Resource caps applied to the sandboxed subprocess via preexec_fn.
_RLIMIT_CPU_SECONDS = 5         # wall-clock CPU max
_RLIMIT_AS_BYTES = 256 * 1024 * 1024  # address space cap
_RLIMIT_NOFILE = 32             # max open file descriptors
_SUBPROCESS_TIMEOUT = 10        # outer timeout (>= RLIMIT_CPU + safety)
_MAX_OUTPUT = 2000

# Whitelisted stdlib imports the wrapper prepends so the skill works
# for math/data tasks without the user having to import them.
_PREAMBLE = (
    "import math, json, re, datetime, collections, itertools, functools\n"
    "import statistics, decimal, fractions, random, string, textwrap\n"
)


def _emit_blocked(reason: str, code_preview: str) -> None:
    """Audit emit for AST-gate refusals."""
    try:
        from codec_audit import log_event
        log_event(
            "python_exec_blocked",
            source="codec-skill-python-exec",
            message=f"python_exec refused: {reason}",
            level="warning",
            outcome="error",
            extra={"reason": reason, "code_preview": code_preview[:200]},
        )
    except Exception:
        pass


def _is_safe_code(code: str):
    """AST-based safety check (PR-2C, D-9 closure). Returns (ok, reason).
    Delegates to the chokepoint `codec_config.is_dangerous_skill_code`
    so python_exec uses the SAME validator as SkillRegistry.load /
    /api/skill/approve / codec_self_improve."""
    try:
        from codec_config import is_dangerous_skill_code
    except Exception:
        # If codec_config can't be imported (test isolation, etc.) we
        # FAIL CLOSED — refuse rather than fall through to unsandboxed.
        return False, "AST validator unavailable (codec_config not importable)"
    try:
        dangerous, reason = is_dangerous_skill_code(code)
    except Exception as e:
        return False, f"AST validator raised: {e}"
    if dangerous:
        return False, reason
    return True, ""


def _minimal_safe_env() -> dict:
    """Stripped-down env for the sandboxed subprocess. Drops inherited
    PYTHONPATH / LD_LIBRARY_PATH / SHELL / HOME so an attacker can't
    point the interpreter at a writable site-packages dir or leak
    paths via env. PATH is minimal."""
    return {
        "PATH": "/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "LC_ALL": "C.UTF-8",
        "LANG": "C.UTF-8",
    }


def _preexec_set_rlimits():
    """Run in the child after fork, before exec. Caps CPU, memory, FDs.

    RLIMIT_AS isn't honored on all macOS versions — wrapped in try so
    a soft fail doesn't block startup. The sandbox-exec syscall block
    is the primary defense; rlimits are belt-and-suspenders against
    runaway compute and FD leaks.
    """
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (_RLIMIT_CPU_SECONDS, _RLIMIT_CPU_SECONDS))
    except (ValueError, OSError):
        pass
    try:
        resource.setrlimit(resource.RLIMIT_AS, (_RLIMIT_AS_BYTES, _RLIMIT_AS_BYTES))
    except (ValueError, OSError):
        pass
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (_RLIMIT_NOFILE, _RLIMIT_NOFILE))
    except (ValueError, OSError):
        pass


def _sandbox_command(python_bin: str, script_path: str) -> list:
    """Build a `sandbox-exec -f <profile> <python> <script>` argv using
    `codec_sandbox`'s deny-default profile. Returns the argv list ready
    for `subprocess.run`."""
    from codec_sandbox import _write_sandbox_profile
    profile_path = _write_sandbox_profile(allow_network=False)
    return ["/usr/bin/sandbox-exec", "-f", profile_path, python_bin, script_path]


def _extract_code(task: str):
    """Pull Python code from the task text. Unchanged from pre-PR-2C —
    parsing is independent of the safety / sandbox layers."""
    # Try triple backtick block
    m = re.search(r'```(?:python)?\s*\n?(.*?)```', task, re.DOTALL)
    if m:
        return m.group(1).strip()
    # Try after "code:" or "run:"
    for kw in ["code:", "run:", "execute:", "python:"]:
        idx = task.lower().find(kw)
        if idx >= 0:
            return task[idx + len(kw):].strip()
    # Strip trigger words and use remaining text
    t = task
    for w in SKILL_TRIGGERS:
        t = re.sub(re.escape(w), "", t, flags=re.IGNORECASE).strip()
    return t.strip() if t.strip() else None


def _python_bin() -> str:
    """First reachable Python interpreter — falls back to /usr/bin/python3
    if homebrew copies aren't present."""
    for candidate in (
        "/opt/homebrew/bin/python3.13",
        "/opt/homebrew/bin/python3",
        "/usr/bin/python3",
    ):
        if os.path.exists(candidate):
            return candidate
    return "python3"


def run(task, app="", ctx=""):
    code = _extract_code(task)
    if not code:
        return "No Python code provided. Use: run python ```your code here```"

    # ── Stage 1: AST validation ──
    safe, reason = _is_safe_code(code)
    if not safe:
        _emit_blocked(reason, code)
        return f"Blocked for safety: {reason}"

    # ── Stage 2: sandbox-exec subprocess ──
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False,
                                            dir="/tmp") as f:
            f.write(_PREAMBLE)
            f.write(code + "\n")
            tmp_path = f.name

        cmd = _sandbox_command(_python_bin(), tmp_path)
        result = subprocess.run(
            cmd,
            capture_output=True, text=True,
            timeout=_SUBPROCESS_TIMEOUT,
            env=_minimal_safe_env(),
            preexec_fn=_preexec_set_rlimits,
        )

        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()

        if result.returncode == 0:
            output = stdout or "(no output)"
            return output[:_MAX_OUTPUT]
        # Common sandbox / rlimit error patterns surface here.
        error = stderr or stdout or "Unknown error"
        return f"Error:\n{error[:1000]}"

    except subprocess.TimeoutExpired:
        return f"Execution timed out ({_SUBPROCESS_TIMEOUT}s wall-clock limit)"
    except FileNotFoundError as e:
        # sandbox-exec missing or python binary missing
        return f"Execution error: sandbox or interpreter unavailable — {e}"
    except Exception as e:
        return f"Execution error: {type(e).__name__}: {e}"
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
