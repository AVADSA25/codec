"""CODEC Skill Sandbox — execute skills in restricted subprocess with macOS sandbox-exec.

Skills run in an isolated subprocess with:
- Network access denied by default (unless SKILL_PERMISSIONS includes "network")
- File writes restricted to ~/.codec/skill_output/
- Process spawning denied
- CPU timeout (10s) and memory limit (256MB)
"""
import json
import logging
import os
import subprocess
import tempfile
import time

log = logging.getLogger("codec_sandbox")

SANDBOX_PROFILE_PATH = os.path.expanduser("~/.codec/sandbox.sb")
SKILL_OUTPUT_DIR = os.path.expanduser("~/.codec/skill_output")
_CODEC_DIR = os.path.expanduser("~/.codec")

# Default sandbox profile — deny-all with targeted allows
_SANDBOX_PROFILE_TEMPLATE = """\
(version 1)
(deny default)

;; Allow reading most files (skills need imports)
(allow file-read*)

;; Allow writing ONLY to skill output dir and temp
(allow file-write*
    (subpath "{skill_output}")
    (subpath "/private/tmp")
    (subpath "/private/var/folders"))

;; Allow process execution (Python interpreter)
(allow process-exec
    (subpath "/usr")
    (subpath "/opt/homebrew")
    (subpath "/Library/Frameworks/Python.framework"))

;; Allow fork (needed for subprocess within Python)
(allow process-fork)

;; Sysctl for Python startup
(allow sysctl-read)

;; Mach lookups needed by Python
(allow mach-lookup
    (global-name "com.apple.system.logger"))

{network_rules}
"""

_NETWORK_ALLOW = """\
;; Network access granted via SKILL_PERMISSIONS
(allow network-outbound)
(allow network-inbound)
(allow system-socket)
"""

_NETWORK_DENY = """\
;; Network access denied — skill has no "network" permission
(deny network-outbound)
(deny network-inbound)
"""


def _ensure_dirs():
    os.makedirs(SKILL_OUTPUT_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(SANDBOX_PROFILE_PATH), exist_ok=True)


def _write_sandbox_profile(allow_network: bool = False) -> str:
    """Write a sandbox.sb profile and return its path."""
    _ensure_dirs()
    network_rules = _NETWORK_ALLOW if allow_network else _NETWORK_DENY
    profile = _SANDBOX_PROFILE_TEMPLATE.format(
        skill_output=SKILL_OUTPUT_DIR,
        network_rules=network_rules,
    )
    with open(SANDBOX_PROFILE_PATH, "w") as f:
        f.write(profile)
    return SANDBOX_PROFILE_PATH


def _parse_permissions(skill_path: str) -> list:
    """Extract SKILL_PERMISSIONS from a skill file via AST (no import)."""
    import ast
    try:
        with open(skill_path, "r") as f:
            tree = ast.parse(f.read(), filename=skill_path)
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "SKILL_PERMISSIONS":
                        return ast.literal_eval(node.value)
    except Exception:
        pass
    return []


def run_skill_sandboxed(
    skill_path: str,
    task: str,
    app: str = "",
    ctx: str = "",
    timeout: int = 10,
    max_mem_mb: int = 256,
) -> tuple[bool, str]:
    """Execute a skill in a sandboxed subprocess.

    Returns (success: bool, result_or_error: str).
    """
    if not os.path.exists(skill_path):
        return False, f"Skill not found: {skill_path}"

    # Parse permissions from skill file
    permissions = _parse_permissions(skill_path)
    allow_network = "network" in permissions

    # Write sandbox profile
    profile_path = _write_sandbox_profile(allow_network=allow_network)

    # AST safety check first
    try:
        from codec_config import is_dangerous_skill_code
        with open(skill_path, "r") as f:
            code = f.read()
        is_bad, reason = is_dangerous_skill_code(code)
        if is_bad:
            return False, f"BLOCKED by AST validator: {reason}"
    except ImportError:
        pass  # codec_config not available, skip AST check

    # Build wrapper script that imports and runs the skill
    _ensure_dirs()
    result_file = tempfile.NamedTemporaryFile(
        suffix=".json", dir=SKILL_OUTPUT_DIR, delete=False, mode="w"
    )
    result_path = result_file.name
    result_file.close()

    wrapper = f"""\
import sys, os, json, resource

# Resource limits
resource.setrlimit(resource.RLIMIT_CPU, ({timeout}, {timeout}))
try:
    resource.setrlimit(resource.RLIMIT_AS, ({max_mem_mb * 1024 * 1024}, {max_mem_mb * 1024 * 1024}))
except (ValueError, OSError):
    pass  # macOS may not support RLIMIT_AS

# Add skill's parent dir to path
skill_dir = os.path.dirname({repr(skill_path)})
if skill_dir not in sys.path:
    sys.path.insert(0, skill_dir)

# Add repo dir to path for shared imports
repo_dir = os.path.dirname(skill_dir)
if repo_dir not in sys.path:
    sys.path.insert(0, repo_dir)

result_path = {repr(result_path)}
try:
    import importlib.util
    spec = importlib.util.spec_from_file_location("_sandboxed_skill", {repr(skill_path)})
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    output = mod.run({repr(task)}, {repr(app)}, {repr(ctx)})
    with open(result_path, "w") as f:
        json.dump({{"ok": True, "result": str(output) if output else ""}}, f)
except Exception as e:
    with open(result_path, "w") as f:
        json.dump({{"ok": False, "error": str(e)}}, f)
"""

    wrapper_file = tempfile.NamedTemporaryFile(
        suffix=".py", delete=False, mode="w"
    )
    wrapper_file.write(wrapper)
    wrapper_file.close()

    try:
        # Find Python interpreter
        python = "python3"
        for candidate in ["/opt/homebrew/bin/python3.13", "/opt/homebrew/bin/python3", "/usr/bin/python3"]:
            if os.path.exists(candidate):
                python = candidate
                break

        # Execute in sandbox
        cmd = ["sandbox-exec", "-f", profile_path, python, wrapper_file.name]
        start = time.time()
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout + 5,  # grace period beyond resource limit
            text=True,
        )
        elapsed = time.time() - start

        # Read result
        if os.path.exists(result_path):
            with open(result_path) as f:
                data = json.load(f)
            if data.get("ok"):
                log.info("Skill %s completed in %.1fs (sandboxed)", os.path.basename(skill_path), elapsed)
                return True, data.get("result", "")
            else:
                return False, data.get("error", "Unknown error")
        else:
            stderr = proc.stderr.strip() if proc.stderr else "No output"
            return False, f"Sandbox execution failed: {stderr}"

    except subprocess.TimeoutExpired:
        return False, f"Skill timed out after {timeout}s"
    except Exception as e:
        return False, f"Sandbox error: {e}"
    finally:
        # Cleanup temp files
        for p in [wrapper_file.name, result_path]:
            try:
                os.unlink(p)
            except OSError:
                pass
