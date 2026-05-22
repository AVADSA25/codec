# A-4 — Skill-loader unification (DESIGN)

**Status:** ✅ IMPLEMENTED (Option A approved by Mickael). Shipped in branch
`fix/pr3-a4-skill-loader-unification`. This doc ships alongside the code.
**Finding:** A-4 (MEDIUM) in `docs/audits/PHASE-1-CODE-QUALITY.md`.
**Wave:** 3 (code quality). This is the first Wave-3 *refactor of live code* — hence the design-first gate.

---

## 1. What & why

CODEC has **two parallel skill-loading systems**. The audit framed this as a
"single source of truth" dedup, but the trace turned up **two consequences
bigger than duplication**:

1. **Security gap.** The legacy loader (`codec_core.load_skills`) `exec_module`s
   every skill file directly — it **bypasses PR-1A's load-time AST safety gate +
   the `skills/.manifest.json` hash check** that `SkillRegistry.load` enforces.
   So the **voice / wake-word path (codec.py) currently loads skills with no D-1
   safety gate.** Migrating it onto the canonical registry *closes* that gap.
2. **Plugin hooks bypassed.** The legacy `codec_core.run_skill` calls
   `skill['run'](task, app)` directly — it does **not** wrap the call in
   `run_with_hooks` (Phase 1 Step 2). So plugin lifecycle hooks (pre_tool /
   post_tool / veto / audit correlation_id) **never fire on voice-path skill
   calls.** The canonical `codec_dispatch.run_skill` does wrap them.

Plus the audit's original points: 2× startup cost (every skill imported eagerly
*and* AST-scanned lazily), and drift risk between the two trigger matchers.

## 2. Current state (traced)

### Canonical system — `codec_dispatch` (keep)
- `registry = SkillRegistry(SKILLS_DIR)` — lazy AST scan, **with** the PR-1A
  safety gate (manifest + `is_dangerous_skill_code`).
- `load_skills()` → `registry.scan()` (metadata only; fast).
- `check_skill(task)` → dict `{name, triggers, _all_matches, run(lazy)}`.
- `run_skill(skill, task, app)` → wraps `registry.run` in `run_with_hooks`
  (plugin lifecycle + a per-dispatch `correlation_id` + veto handling + the
  `wake_dispatch` audit emit + fall-through across `_all_matches`).

**Already-canonical consumers:** chat (`codec_dashboard` `_try_skill`),
`codec_session.py:857`, `codec_telegram.py`, `codec_imessage.py`,
`codec_agent_runner.py:613`, and dashboard startup (`codec_dashboard.py:3736`).
Voice (`codec_voice._load_skills`) uses **its own** `SkillRegistry` instance for
lazy matching — also gated, not on the legacy path.

### Legacy system — `codec_core` (to remove)
- `loaded_skills` (list of `{name, triggers, run}`), `load_skills()` (eager
  `exec_module`, **no safety gate**), `run_skill(skill, task, app)` (**no
  hooks**), `_load_custom_triggers()`.
- **Consumers (only two):**
  - **`codec.py`** — imports `loaded_skills, load_skills, run_skill`; defines
    `check_skills_ranked()` (iterates `loaded_skills`) + `check_skill()`; calls
    `load_skills()` at startup (line ~903); `_dispatch_inner` loops
    `for skill in check_skills_ranked(task): run_skill(...)` (lines ~317-320).
  - **`codec_dashboard.py` `/api/cortex/skills`** (line ~3491) — imports
    `loaded_skills, load_skills` from codec_core; lists them for the endpoint.

## 3. The `custom_triggers.json` wrinkle ⚠️ (needs your call)

The legacy `load_skills` applies **user trigger overrides** from
`~/.codec/custom_triggers.json` (codec_core.py:180). The canonical
`SkillRegistry` / `codec_dispatch` **does NOT** honor custom triggers — only
`routes/skills.py` reads them (for the management UI/API).

**Implication:** custom triggers currently affect **only** the legacy voice/wake
path (codec.py). They already do **not** apply to chat / MCP / session / telegram
/ imessage (all canonical). So today the feature is *inconsistent*.

Three ways to handle it in this migration:

| Option | Behavior after A-4 | Effort | Note |
|---|---|---|---|
| **A (recommended)** | Add custom_triggers support to `SkillRegistry.match_all_triggers` → custom triggers honored **everywhere** (voice + chat + MCP + …) | + ~20 LOC in registry + tests | **Improves** the feature (consistent), no regression. The right "single source of truth" outcome. |
| **B** | Migrate codec.py to canonical; custom triggers stop applying to voice (never applied elsewhere anyway) | minimal | Small **regression** for voice users who set custom triggers. |
| **C** | Defer A-4 until custom_triggers is designed into the registry separately | — | Punts the security-gap closure. |

**My recommendation: Option A** — it closes the security gap *and* makes
custom_triggers work consistently for the first time. It's the only option with
zero regression.

## 4. Proposed change (Option A)

1. **`codec_skill_registry.py`** — teach `match_all_triggers` (and the trigger
   accessors used by the dashboard) to overlay `~/.codec/custom_triggers.json`:
   a skill's effective triggers = `custom[name].triggers` if present, else the
   AST-extracted `SKILL_TRIGGERS`. Cached; reloaded on `scan()`. This makes the
   canonical path honor the same overrides the legacy path did.
2. **`codec.py`** — in `_dispatch_inner`, replace
   `for skill in check_skills_ranked(task): run_skill(...)` with
   `skill = codec_dispatch.check_skill(task); if skill: result = codec_dispatch.run_skill(skill, task, app)`
   (canonical `run_skill` already does the fall-through internally via
   `_all_matches`). Replace the startup `load_skills()` with
   `codec_dispatch.load_skills()`. Delete codec.py's local `check_skills_ranked`
   + `check_skill` + the `loaded_skills, load_skills, run_skill` import from
   codec_core.
3. **`codec_dashboard.py` `/api/cortex/skills`** — read from
   `codec_dispatch.registry` (`registry.names()` + `get_triggers` +
   `get_description`) instead of `codec_core.loaded_skills`.
4. **`codec_core.py`** — delete `loaded_skills`, `load_skills`,
   `run_skill`, `_load_custom_triggers` (now unused). Keep everything else
   (transcribe, focused_app, the DB helpers, etc.).

## 5. API / schema changes
- No on-disk schema changes. `custom_triggers.json` format unchanged (Option A
  just makes the canonical registry read the same file).
- `codec_core` public surface shrinks: `loaded_skills` / `load_skills` /
  `run_skill` removed. (Grep-confirmed only codec.py + cortex_skills import them;
  both migrated in the same PR.)
- `codec.py` loses `check_skills_ranked` / `check_skill` (codec.py-local; the
  only `check_skill` consumer outside codec.py — `codec_session.py` — already
  imports from `codec_dispatch`).

## 6. Migration / compatibility
- Pure code-path swap; no data migration. First run after merge: codec.py's
  `codec` process scans via the gated registry instead of eager-importing.
- Behavior parity: voice/wake skill dispatch now (a) goes through the AST gate,
  (b) fires plugin hooks, (c) honors custom_triggers (Option A) — all
  *additive* improvements, no user-visible change for the happy path.

## 7. Test plan
- **New** `tests/test_skill_loader_unification.py`:
  - codec.py no longer imports `loaded_skills`/`load_skills`/`run_skill` from
    codec_core (source invariant); `check_skills_ranked` removed.
  - codec_core no longer exports the three legacy symbols.
  - `codec_dispatch.check_skill` + `run_skill` round-trip a real skill (e.g.
    `calculator`) → correct result.
  - Custom-triggers (Option A): write a tmp `custom_triggers.json`, scan, assert
    `registry.match_all_triggers` honors the override; assert a skill with a
    custom trigger matches via codec_dispatch.
  - cortex_skills endpoint returns skills from the registry (TestClient).
  - **Security regression test:** a skill file that fails `is_dangerous_skill_code`
    and isn't in the manifest is NOT runnable via the voice path (proves the gate
    now covers codec.py).
- **Regression:** full suite (`pytest`), expect the 23 known pre-existing
  failures, zero new. Manifest unchanged (no skill files edited).
- **Manual (Mac Studio):** restart `codec`; say a wake-word skill command (e.g.
  "hey codec, calculate 2+2") → verify it runs + a `wake_dispatch` audit line
  appears with a `correlation_id` (proving hooks fired). Set a custom trigger in
  `custom_triggers.json`, restart, verify voice honors it.

## 8. Risk + rollback
- **Risk:** the voice/wake dispatch is core UX. The swap is small and behavior-
  parity, but skill execution on that path changes implementation. Mitigation:
  the round-trip + security + custom-trigger tests above; manual voice check.
- **Blast radius:** 3 files edited (codec.py, codec_dashboard.py,
  codec_skill_registry.py) + deletions in codec_core.py. No DB/schema/PM2 changes.
- **Rollback:** single-commit revert restores the legacy symbols + codec.py
  helpers. No persistent state touched, so revert is clean.

## 9. Open question for you (Mickael)
**Q: custom_triggers handling — Option A (add to registry, honored everywhere),
B (drop from voice), or C (defer A-4)?** I recommend **A**. Once you pick, I'll
implement + open the PR (chat-review-then-merge, since it touches the live
dispatch path).
