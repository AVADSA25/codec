"""One rule for the environment a child process gets (P-084, docs/ENV-SCRUB-DESIGN.md).

A variable whose NAME looks like it holds a secret (an API key, a token, a
password, ...) is left out of the environment CODEC hands to a command it
starts, so a command such as `env` cannot print the keys of the process that
started it. PATH, HOME, the locale and tool settings are kept, so programs
still resolve and run.

Callers: routes/vibe_exec.py (Run code), codec_vibe_runner.py (Vibe Run),
codec_vibe_agent_cmd.py (Vibe agent commands) and skills/terminal.py. Each
caller decides what it adds or removes on top (SSH_AUTH_SOCK, LC_ALL, PATH
extras). Keep this the only copy of the pattern: tests/test_envscrub.py fails
when it is pasted into another module.

Stdlib only, no I/O.
"""
from __future__ import annotations

import re
from typing import Iterable, Mapping

# Variable names that look secret-bearing (matched anywhere in the name, any case).
SECRET_NAME_RE = re.compile(
    r"(?i)(secret|password|passwd|token|credential|api[_-]?key|access[_-]?key|"
    r"private[_-]?key|bearer|session[_-]?token|auth[_-]?token|_key$|^key$)"
)


def scrub(env: Mapping[str, str], keep: Iterable[str] = ()) -> dict:
    """A new dict with the variables of `env` whose names do not look secret.
    A name listed in `keep` stays even when it matches. `env` is not changed."""
    kept = set(keep)
    return {k: v for k, v in env.items() if k in kept or not SECRET_NAME_RE.search(k)}
