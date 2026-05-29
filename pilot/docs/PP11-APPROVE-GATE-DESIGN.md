# PP-11 — AST safety gate at skill approve

**Closes:** Pilot audit **P-3** (skill-approve was a bare `shutil.move` with no safety
check). **Repo:** `~/codec/`.

## What & Fix
A compiled skill lands in `~/.codec/skills/.pending/pilot_{slug}.py`. The operator
approves it → `approve_pending()` moved the file into the active `~/.codec/skills/` dir
with no inspection. If a malicious skill ever reached the pending dir (prompt-injected
trace, tampered file), approve waved it straight through to a load-time-executed location.

Pilot is a separate repo and can't cleanly import the parent's
`codec_config.is_dangerous_skill_code`, so PP-11 **vendors a minimal equivalent** in
`pilot/safety.py` — the same allow-by-denylist AST walk:

- `is_dangerous_skill_code(code) -> (dangerous, reason)`. Denylists dangerous module
  imports (`os`, `subprocess`, `ctypes`, `shutil`, `importlib`, `signal`, `pty`, `socket`)
  and dangerous call names (`eval`, `exec`, `compile`, `__import__`, `globals`, `locals`,
  `getattr`, `setattr`, `delattr`, `vars`). A `SyntaxError` counts as dangerous — we won't
  approve a file we can't parse.
- Wired into `approve_pending()` **before** `shutil.move`: read the pending source, run the
  gate, and on a positive raise `PermissionError` + emit `audit("skill_blocked", ...)`. The
  pending file is **left in place** (not deleted) so the operator can inspect what was refused.

## Defense in depth
This is the third independent layer, not the only one:
1. **PP-2** — the compiler can't emit injected code in the first place (`_safe`/`_int`).
2. **PP-11 (this)** — fails fast at approve, before a dangerous file reaches the active dir.
3. **Parent `SkillRegistry`** — AST-checks non-manifest skills again at load time.

Any one layer closes the path on its own; PP-11 closes it at the earliest operator-visible
moment (approve) so a dangerous file never reaches `~/.codec/skills/`.

## Tests (`tests/test_phase17_approve_gate.py`)
Gate flags dangerous import / `eval` / syntax-error; passes the compiled-skill shape;
`approve_pending` raises `PermissionError` on a `subprocess` skill AND leaves it out of the
active dir; approves a safe skill. 6 tests, all green; full phase10–17 security suite (26
tests) still green, ruff clean.
