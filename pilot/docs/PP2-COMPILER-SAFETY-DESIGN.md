# PP-2 — Compiler injection-safety + slug-traversal guard

**Closes:** Pilot audit **P-2** (trace→skill compiler injects `run.task` raw into a
docstring → RCE; scroll/wait numerics spliced unescaped) + **P-11** (slug path/glob
traversal in skill review). See `codec-repo/docs/audits/PHASE-1-PILOT-AUDIT.md`.
**Repo:** `~/codec/` (Pilot).

## What

`compiler.py` built generated skill/script source with f-strings that interpolated
attacker-influenceable trace fields **un-escaped**:
- `run.task` raw inside a `"""…"""` docstring (`compile_skill`, `compile_trace` headers) →
  a task containing `"""` broke out into module-level code → **RCE at skill import**.
- `scroll` `amount` and `wait` `ms` spliced into `page.evaluate("…{delta_y}")` / `wait({ms})`
  with no `int()` cast → JS/Python injection.
- `index` / `result` interpolated into `# comments` → a newline broke out of the comment.

`skill_review.py` interpolated the URL `{slug}` into `glob(f"pilot_{slug}*.py")` + `Path`
without sanitizing → `..`/glob-metachar reachable on the (now-authed) endpoints.

## Fix

- `_safe(s)` — strips `\`, collapses `"""`→`'''`→`'`, and newlines→spaces, capped 200 chars;
  applied to every trace-derived field embedded in a **docstring or `# comment`** (task,
  run_id, status, index, result, unknown-action name).
- `_int(v, default)` — coerces `scroll` amount + `wait` ms to int (fallback on non-numeric)
  so a string payload can't reach `evaluate()`/`wait()`.
- Value positions (url, text, xpath, name, SKILL_DESCRIPTION) keep `!r` (`repr`) — already
  safe.
- `compile_skill` runs `compile(source, …, "exec")` before returning and **raises**
  `ValueError` on `SyntaxError` — fail closed rather than emit non-compiling (possibly
  injected) source.
- `skill_review.get_pending` / `approve_pending` / `reject_pending` apply `slugify(slug)` to
  the incoming slug (neutralizes `/`, `.`, `*`).

**Note:** this closes the injection at the *source* (the compiler can no longer emit
attacker code). Routing `approve_pending` through the parent's `is_dangerous_skill_code`
gate is the complementary defense-in-depth (P-3) — deferred: Pilot can't cleanly import the
parent `codec_config` (separate repos), and the parent registry already AST-checks
non-manifest skills at load. Auto-generated skills would ideally move to a data-trace +
fixed-loader (no free-form source) — a larger follow-up.

## Tests (`tests/test_phase8_compiler_safety.py`, pytest, no browser)

task-can't-break-docstring (compile_skill + compile_trace), scroll amount int-only, wait ms
int-only, normal trace compiles, traversal slug doesn't resolve. 6 tests; the existing
native `test_phase5` (real chromium) still passes → behavior-preserving for normal traces.
