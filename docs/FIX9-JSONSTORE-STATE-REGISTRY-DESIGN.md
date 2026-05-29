# FIX9 — Promote `codec_jsonstore` to the mandatory state registry

> Follow-on design note required by `docs/SECURITY-REMEDIATION-DESIGN.md` Fix #9
> and CLAUDE.md §11 (large multi-module surface). **No code is written until
> this note is approved.** Closes audit finding C8 (~25 ad-hoc `~/.codec/*.json`
> writers).

**Author:** audit follow-up · **Status:** AWAITING APPROVAL

---

## 1. What & why

`codec_jsonstore.py` already provides the two correct primitives:
`atomic_write_json(path, data)` (unique tmp + flush + **fsync** + atomic
replace + 0600) and `file_lock(path)` (cross-process flock for a
read-modify-write). Per PR-4C they were meant to be the single chokepoint for
all `~/.codec/*.json` persistence. They are **not yet universal** — a survey of
HEAD finds ~30 writer sites still doing raw `json.dump(f)` (no fsync, often no
atomicity, sometimes a read-modify-write with no lock) plus 2 modules that
hand-roll their own atomic helper.

Consequences (all observed by the audit): partial-write corruption on crash,
lost-update races between the 11 PM2 daemons, and inconsistent perms.

**Goal:** migrate every ad-hoc writer onto the shared primitives, converge the
duplicate helpers, add a `docs/STATE-FILES.md` registry, and add a CI guard
(same shape as the Fix #10 A-12 guard) that fails when a NEW raw `json.dump`
to a `~/.codec` path is introduced.

## 2. Inventory (from HEAD survey — implementation step 1 produces the exact line list)

**ALREADY-SAFE** (`codec_jsonstore`): `codec_jsonstore.py` itself; the Fix #5
sites (grants.json via `grants_lock`, both notifications.json read-modify-writes
in `codec_ask_user` now under `file_lock`); `codec_oauth_provider` fallback
(migrated in Fix #1b).

**HAS-OWN-ATOMIC** (hand-rolled tmp+fsync+replace — durable but duplicate code,
candidates to converge):
- `codec_ask_user._atomic_write_text` (uses `json.dumps(..., default=str)`)
- `codec_agent_plan._atomic_write_json` (0600 + 0700 dir, `sort_keys=False`)

**AD-HOC-UNSAFE** (raw `json.dump`, ~30 sites) — representative grouping:
- **Full-overwrite, low risk:** `codec_alerts.py:48`, `codec_marketplace.py:51,74`,
  `codec_memory_upgrade.py:235`, `codec_proactive.py:209`, `codec_agent.py:62`,
  `skills/pomodoro.py:32`, `routes/skills.py:235` (custom_triggers.json),
  `routes/agents.py:180` (custom agent save).
- **Read-modify-write (NEED `file_lock`):** `routes/_shared.py:202,241`
  (the shared notifications read/write — used by scheduler, heartbeat,
  autopilot), `codec_heartbeat.py:404` (notifs), `codec_scheduler.py:127`
  (notifications), `codec_heartbeat.py:258` (executed history).
- **Daemon state files:** `codec_imessage.py:101`, `codec_heartbeat.py:371`,
  `codec_scheduler.py:40`, `codec_voice.py:161` (voice_session.json),
  `codec_agent_messaging.py:98`.
- **Don't-touch / sensitive (migrate LAST, per-file sign-off):**
  `codec_config.py:68,120,250` (config.json), `routes/auth.py:239,314,349`
  (auth/TOTP config writes), `codec_google_auth.py:92` (Google OAuth token),
  `codec_dashboard.py:652,693,3064,3375,3410` (config/schedules).

(`codec_sandbox.py:202,205` are inside a generated wrapper string, not a live
state write — excluded. `codec_core.py` likewise emits writes as script text.)

## 3. Migration hazards (must be handled, NOT mechanical)

1. **`default=str`.** `atomic_write_json` does `json.dump(data, f, indent=2)`
   with **no `default=str`**. Any writer currently persisting non-JSON-native
   values (e.g. `datetime`, `Path`) relies on `default=str` and will raise under
   a naive swap. `codec_ask_user._atomic_write_text` does this deliberately.
   → **Decision A:** add an optional `default` param to `atomic_write_json`, or
   require callers to pre-serialize. (Recommended: add `default=None` passthrough
   so the primitive subsumes the `_atomic_write_text` use case cleanly.)
2. **`sort_keys`.** Some writers use `sort_keys=True` (memory_upgrade) for
   stable diffs; the primitive uses insertion order. Where stability matters
   (anything hashed/compared), preserve it. → `atomic_write_json` may need a
   `sort_keys` passthrough.
3. **Read-modify-write vs overwrite.** RMW sites need `file_lock` wrapping the
   load→modify→write (like Fix #5), not just `atomic_write_json`. Misclassifying
   an RMW as an overwrite re-opens the lost-update race.
4. **Don't-touch zones** (config.json, oauth/Google tokens, auth) — these are
   in the CLAUDE.md protected set. Migrate LAST, one file per commit, surfaced
   to the operator (same protocol as Fix #1b's oauth surfacing).

## 4. Proposed phased plan (each phase its own commit, suite green between)

- **Phase 0 — primitive hardening:** add `default`/`sort_keys` passthrough to
  `atomic_write_json` (TDD); add `file_lock`-aware `read_modify_write(path, fn)`
  convenience if it reduces churn. No call-site change yet.
- **Phase 1 — full-overwrite low-risk sites** → `atomic_write_json`. Mechanical,
  one commit, regression by existing per-module tests.
- **Phase 2 — read-modify-write sites** → `file_lock` + `atomic_write_json`
  (notably `routes/_shared.py` notifications, heartbeat, scheduler). Add
  concurrent-write no-clobber tests (Fix #5 pattern).
- **Phase 3 — converge duplicate helpers:** replace `codec_ask_user.
  _atomic_write_text` and `codec_agent_plan._atomic_write_json` bodies with calls
  to the (now `default=`-aware) `codec_jsonstore` primitive; keep the thin
  wrappers as named shims so call sites don't churn.
- **Phase 4 — don't-touch zones**, one file per commit, operator sign-off each
  (config.json, Google token, auth writes).
- **Phase 5 — guardrails:** `docs/STATE-FILES.md` registry (path → writer →
  reader → lock policy); `tests/test_jsonstore_invariant.py` CI guard that fails
  on a NEW raw `json.dump`/`write_text(json.dumps(...))` targeting a `~/.codec`
  path outside an allowlist (mirrors the Fix #10 A-12 guard).

## 5. Test plan

- Phase 0: unit tests for `atomic_write_json` `default`/`sort_keys` + fsync
  (extend existing jsonstore tests).
- Phases 1-4: each migrated file keeps its existing tests green; RMW sites add a
  concurrent-writer no-clobber test.
- Phase 5: the invariant guard test + a synthetic-violation test (proves it
  catches a new raw writer), exactly like `tests/test_a12_invariant.py`.

## 6. Rollback

Per-phase, per-file reverts. No schema changes (same JSON shapes, same paths,
same-or-better perms). The only behavior change is *durability + atomicity*,
which is strictly safer.

## 7. Open decisions (need sign-off)

- **Decision A — primitive signature:** add `default=` + `sort_keys=`
  passthrough to `atomic_write_json` (Recommended), vs. require callers to
  pre-serialize to text. The former lets Phase 3 fully retire the duplicate
  helpers.
- **Decision B — scope of this fix:** Phases 0-3 + 5 now, Phase 4 (don't-touch
  zones) deferred to a separate sign-off (Recommended), vs. all phases in one
  PR. Phase 4 touches protected files and should not be bundled.
- **Decision C — `read_modify_write` helper:** add a `codec_jsonstore.
  read_modify_write(path, mutate_fn)` convenience (lock + load + mutate + atomic
  write) to standardize RMW sites, vs. inline `file_lock` at each (as Fix #5
  did). A helper reduces the chance a future RMW forgets the lock.

## 8. Risk

Medium-high by surface area, low per-site. The phasing keeps each commit small
and independently revertible; the don't-touch zones are quarantined to Phase 4
with explicit sign-off. The Phase 5 guard prevents regression once migrated.
