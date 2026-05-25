# Scoped strict mode for the skill-safety AST gate

**Closes:** the Phase-1 cross-cutting follow-up — `codec_config.is_dangerous_skill_code` is
allow-by-omission (misses network / serialization primitives), flagged as a risk for
*auto-generated* skills. **Repo:** codec-repo. **Approved by Mickael 2026-05-25.**

## The nuance (why a global denylist would be wrong)
The base gate intentionally allows `requests`/`urllib`/`open` etc. — **hand-written user
skills legitimately do HTTP and file I/O**, the user curates their own `~/.codec/skills/`, and
`python_exec` is sandboxed. Adding those to the *global* denylist would break legitimate user
skills + user-invoked skill creation. So strict mode is **opt-in and narrowly scoped**.

## What changed
- `is_dangerous_skill_code(code, strict=False)` — **default is byte-for-byte unchanged** (proven
  by the existing `test_skill_registry` + `test_skill_sandbox` suites still passing). It's the
  gate for user-skill load (`codec_skill_registry`), `python_exec` (`codec_sandbox`), and
  user-invoked generation (`skill_forge`, `create_skill`→approve).
- `strict=True` adds `_SKILL_STRICT_EXTRA_MODULES = {pickle, marshal, shelve, smtplib, ftplib,
  telnetlib}` — the **rarely-legit-in-a-utility-skill** primitives: deserialization-RCE /
  persistence + legacy exfil protocols.
- Applied at **exactly one site: `codec_self_improve._validate`** — the nightly *autonomous*
  LLM drafter (no explicit user intent). A drafted proposal reaching for those is refused.

## Deliberately NOT blocked, even in strict
**HTTP (`requests`/`urllib`/`httpx`) and `open()`.** They're common + legitimate, and
self_improve proposals are **draft-only + human-reviewed before promotion** — so the review
gate (not a blanket block) is the right control for exfil-via-HTTP concerns. Blocking them would
neuter the drafter (it could never propose a useful fetch/file skill) and broke the existing
`test_validate_accepts_safe_skill` contract (which expects a `requests` skill to be acceptable).

## Why this site only
- `codec_skill_registry` load / `python_exec` → user-controlled → **default** (no over-gating).
- `skill_forge` / `create_skill` → user-invoked with explicit intent + human approve → **default**.
- Pilot's auto-compiled skills (the original "auto-generated" worry) → already gated at approve
  by **PP-11** (Pilot's own vendored AST gate, separate repo).
- `codec_self_improve` → the one fully-autonomous generator → **strict**.

## Tests (`tests/test_strict_ast_gate.py`, CI-gated)
Default mode still allows network/open + still blocks base (os/subprocess/eval); strict blocks
serialization + legacy-exfil; strict deliberately ALLOWS http/open; strict still blocks base;
self_improve refuses a `pickle` draft but accepts an HTTP draft. 8 tests; the self_improve case
skips cleanly if optional deps are absent on the runner.

## Rollback
Revert the commit. Default-mode callers are untouched, so rollback is behaviorally inert.
