"""CODEC Vibe IDE execution API — preview + run_code.

F5 / SR-54 (was E5): extracted from codec_dashboard.py. The 3 endpoints
that back the Vibe IDE's live-preview + code-execution panel.

  - /api/preview      writes user HTML to ~/.codec/preview.html
  - /preview_frame    serves it inside a sandboxed iframe
                      (CSP restricted: no dashboard APIs, no external resources)
  - /api/run_code     spawns a language-specific compiler/interpreter on a
                      tempfile, gates the source through `is_dangerous`,
                      cleans up the Rust `.out` binary in finally (H-8 fix).
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import time as _time

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter()
log = logging.getLogger("codec_dashboard")


# ── /api/run_code execution hardening (K1) ────────────────────────────────
# Proportionate sandboxing for the Vibe IDE's "run my code" feature. Unlike
# the python_exec skill (single-language, utility) this endpoint must run 8
# languages incl. compilers (go/rust) and bash, so a full codec_sandbox
# deny-default `sandbox-exec` profile is INTENTIONALLY NOT applied — its
# no-process-spawn + no-network rules break compilation and the IDE's
# legitimate use (a user testing an API call wants network). Instead we apply
# the two hardenings that are safe across every language:
#   1. resource limits (CPU / address space / FDs / output-file size) so a
#      runaway loop, memory bomb, FD leak, or disk-filling write is bounded;
#   2. a secret-stripped env so executed code can't read the operator's API
#      keys / tokens out of the inherited environment (PATH/HOME/tool config
#      are preserved so every interpreter + compiler still resolves).
# The existing is_dangerous() gate, dashboard auth, and 30s wall timeout
# remain the other layers.
_RUNCODE_RLIMIT_CPU_SECONDS = 25            # CPU time (< 30s wall) — kills infinite loops
_RUNCODE_RLIMIT_AS_BYTES = 2 * 1024 ** 3    # 2 GB address space — stops bombs, allows rustc/go
_RUNCODE_RLIMIT_NOFILE = 256                # compilers open many files
_RUNCODE_RLIMIT_FSIZE_BYTES = 64 * 1024 ** 2  # 64 MB max single output file — stops disk-fill

# Env var NAMES that look secret-bearing get dropped before the child runs.
_SENSITIVE_ENV_RE = re.compile(
    r"(?i)(secret|password|passwd|token|credential|api[_-]?key|access[_-]?key|"
    r"private[_-]?key|bearer|session[_-]?token|auth[_-]?token|_key$|^key$)"
)
# Matches the pattern but is functionally needed → keep.
_ENV_KEEP = {"SSH_AUTH_SOCK"}


def _preexec_set_rlimits():  # pragma: no cover - runs in forked child, before exec
    """Cap CPU / memory / FDs / output-file size in the child after fork.

    Each limit is best-effort (RLIMIT_AS is not enforced on all macOS
    versions) — wrapped so a soft failure never blocks the run. Limits are
    inherited across the bash→rustc→binary chain for the rust path, so the
    whole process tree is bounded."""
    import resource
    for _res, _val in (
        (resource.RLIMIT_CPU, _RUNCODE_RLIMIT_CPU_SECONDS),
        (resource.RLIMIT_AS, _RUNCODE_RLIMIT_AS_BYTES),
        (resource.RLIMIT_NOFILE, _RUNCODE_RLIMIT_NOFILE),
        (resource.RLIMIT_FSIZE, _RUNCODE_RLIMIT_FSIZE_BYTES),
    ):
        try:
            resource.setrlimit(_res, (_val, _val))
        except (ValueError, OSError):
            pass


def _hardened_run_env() -> dict:
    """Inherited environment minus secret-bearing vars. PATH / HOME / locale /
    tool config (GOPATH, GOCACHE, npm_config_*, …) are preserved so every
    interpreter + compiler still resolves — only API keys / tokens / secrets
    are dropped so executed code can't exfiltrate them via os.environ."""
    env = {k: v for k, v in os.environ.items()
           if k in _ENV_KEEP or not _SENSITIVE_ENV_RE.search(k)}
    env.setdefault("LC_ALL", "C.UTF-8")
    return env


@router.post("/api/preview")
async def preview_code(request: Request):
    body = await request.json()
    code = body.get("code", "")
    preview_path = os.path.expanduser("~/.codec/preview.html")
    with open(preview_path, "w") as f:
        f.write(code)
    return {"url": "/preview_frame", "path": preview_path}


@router.get("/preview_frame", response_class=HTMLResponse)
async def preview_frame():
    try:
        with open(os.path.expanduser("~/.codec/preview.html")) as f:
            content = f.read()
        # Restrict preview with CSP — no access to dashboard APIs or external resources
        return HTMLResponse(content, headers={
            "Content-Security-Policy": "default-src 'self' 'unsafe-inline' data: blob:; connect-src 'none'; form-action 'none'",
            "X-Frame-Options": "SAMEORIGIN",
        })
    except OSError as e:
        log.warning(f"Preview file read failed; showing placeholder: {e}")
        return HTMLResponse("<html><body style='background:#0a0a0a;color:#888;padding:40px;font-family:sans-serif'><h2>No preview available</h2><p>Write some HTML and click Preview.</p></body></html>")


@router.post("/api/run_code")
async def run_code(request: Request):
    body = await request.json()
    code = body.get("code", "")
    language = body.get("language", "python")
    if not code.strip():
        return JSONResponse({"error": "No code"}, status_code=400)
    from codec_config import is_dangerous
    if is_dangerous(code):
        return JSONResponse({"error": "Blocked: code contains dangerous pattern"}, status_code=403)
    # J1: reject languages we can't actually run instead of silently feeding a
    # .java/.cpp/.sql file to python3.13 (ext_map had more langs than cmd_map).
    cmd_template = {
        "python": ["python3.13"],
        "javascript": ["node"],
        "typescript": ["npx", "ts-node"],
        "bash": ["bash"],
        "go": ["go", "run"],
        "rust": ["rustc"],          # special-cased below
        "swift": ["swift"],
        "ruby": ["ruby"],
    }
    if language not in cmd_template:
        return JSONResponse({"error": f"Unsupported language: {language}"}, status_code=400)
    ext_map = {"python": ".py", "javascript": ".js", "typescript": ".ts", "bash": ".sh", "go": ".go", "rust": ".rs", "swift": ".swift", "ruby": ".rb"}
    ext = ext_map.get(language, ".txt")
    tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False, mode="w")
    tmp.write(code)
    tmp.close()
    cmd_map = {
        "python": ["python3.13", tmp.name],
        "javascript": ["node", tmp.name],
        "typescript": ["npx", "ts-node", tmp.name],
        "bash": ["bash", tmp.name],
        "go": ["go", "run", tmp.name],
        "rust": ["rustc", tmp.name, "-o", tmp.name + ".out", "&&", tmp.name + ".out"],
        "swift": ["swift", tmp.name],
        "ruby": ["ruby", tmp.name],
    }
    cmd = cmd_map.get(language, ["python3.13", tmp.name])
    # For rust, compile+run in one shell command
    if language == "rust":
        cmd = ["bash", "-c", f"rustc {tmp.name} -o {tmp.name}.out 2>&1 && {tmp.name}.out"]
    start = _time.time()
    try:
        # K1: env-stripped + resource-limited child (see module header). The
        # rlimits bound runaway compute / memory / FDs / output size; the env
        # withholds operator secrets. cwd stays $HOME so tool config resolves.
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=os.path.expanduser("~"),
            env=_hardened_run_env(),
            preexec_fn=_preexec_set_rlimits,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        return {"stdout": stdout.decode(errors="replace")[:10000], "stderr": stderr.decode(errors="replace")[:5000], "exit_code": proc.returncode, "elapsed": round(_time.time() - start, 1)}
    except asyncio.TimeoutError:
        return {"stdout": "", "stderr": "Timed out (30s)", "exit_code": -1, "elapsed": 30}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        # H-8: also unlink the Rust-compiled `<tmp>.out` binary (only created for
        # rust; for other langs the path doesn't exist → FileNotFoundError is
        # caught). The source tmp was already cleaned here; the .out leaked.
        for _p in (tmp.name, tmp.name + ".out"):
            try:
                os.unlink(_p)
            except OSError as e:
                log.debug(f"Temp file cleanup failed for {_p}: {e}")
