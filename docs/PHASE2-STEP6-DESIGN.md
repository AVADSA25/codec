# Phase 2 Step 6 — Trigger System (Design)

**Branch:** `phase2-step6-triggers`
**Source spec:** `docs/PHASE2-BLUEPRINT.md` §"Step 6"
**Status:** design phase, implementation follows in same PR
**Reviewer:** user

---

## 0 · Why this exists

CODEC today fires skills only when explicitly requested — wake word, chat command, MCP tool call. Step 6 lets a skill DECLARE a pattern that auto-fires it from the observer's signals (Step 5 ring buffer):

- `stripe_dashboard_helper.py` declares `window_title_match: "Stripe — Dashboard"` → fires when you focus the Stripe tab.
- `address_lookup.py` declares `clipboard_pattern: "^\\d+\\s+\\w+\\s+(St|Ave|Rd)"` → fires when you copy a postal address.
- `csv_validator.py` declares `file_change: "~/Downloads/*.csv"` → fires when a CSV lands.

The autopilot's hardcoded time triggers generalize into the same system. One mechanism, declarative.

**Risk surface**: auto-firing is exactly the kind of thing that caused the May 1 incident (Apple Reminders, Terminal popups, AskUserQuestion leaks). Step 6 must be **safe by default** — conservative cooldowns, opt-in confirmation, Step 3 §1.7 destructive consent gate reused, and Step 6 ships ZERO triggers (only the plumbing).

---

## 1 · Design

### 1.1 Skill-side declaration (Q3 — locked)

```python
# In any skill's module-level constants, alongside SKILL_TRIGGERS:
SKILL_OBSERVATION_TRIGGER = {
    "type": "window_title_match" | "clipboard_pattern" | "file_change" | "time" | "compound",
    "pattern": "...",                # type-specific (see §1.2)
    "cooldown_seconds": 600,         # default 10 min — safe by default
    "require_confirmation": True,    # default True — safe by default
    "destructive": False,            # if True, uses Step 3 §1.7 strict-consent
}
```

**All four fields are required** in v1. No optional fields, no implicit defaults at the matcher level — the SKILL author opts in explicitly.

### 1.2 Trigger types

| `type` | `pattern` is | Match against |
|---|---|---|
| `window_title_match` | regex string | observer snapshot's `active_window.title` |
| `clipboard_pattern` | regex string | observer snapshot's `clipboard.preview` |
| `file_change` | glob string | observer snapshot's `recent_files[].path` (mtime within last poll cycle) |
| `time` | cron-like string `"M H D Mo W"` | wall clock at evaluation time |
| `compound` | `{"op": "and"\|"or", "children": [trigger, trigger, ...]}` | recursive evaluation |

Time triggers limited to **≥1 minute granularity** — observer polls every 60s, no point in finer.

### 1.3 Evaluation hook — inline in observer poll

NO new PM2 service. After `codec_observer.poll()` appends to the ring buffer, it calls `codec_triggers.evaluate(snapshot)`. Saves a process, simpler audit trail, single source of truth on cadence.

```
codec-observer poll loop
        │
        ▼
   poll() → append to RingBuffer → emit observation_tick
        │
        ▼
   evaluate_triggers(snapshot)        ← new in Step 6
        │
        ├─ For each registered trigger from SkillRegistry:
        │     ├─ Type-specific match? → if no, skip
        │     ├─ Cooldown elapsed?    → if no, emit trigger_blocked + skip
        │     ├─ Per-trigger killed?  → if yes, skip silently
        │     └─ require_confirmation? destructive?
        │           ↓
        │           Use Step 3 §1.7 ask_user gate if destructive
        │           OR fire PWA notification + await answer if require_confirmation
        │           OR fire silently
        ▼
   codec_dispatch.run_skill(skill, task=<rendered context>, ...)
   ↑                                                   ↑
   already-hooked chokepoint                           emits trigger_fired
   (Step 2's run_with_hooks fires plugins, Step 4's
    self_improve plugin captures the result)
```

### 1.4 Cooldown state

Per-trigger last-fired timestamp lives in **RAM only** (process restart resets all cooldowns). State file would be tempting but introduces consistency questions; RAM is simpler. Storage:

```python
# Module-level dict in codec_triggers.py
_LAST_FIRED: Dict[str, float] = {}    # key = "<skill_name>:<trigger_hash>"
_LAST_FIRED_LOCK = threading.Lock()
```

Trigger-hash includes the full trigger dict serialized — so editing a trigger's pattern/cooldown effectively resets its cooldown (no stale state).

### 1.5 Per-trigger kill switch (PWA)

PWA "Triggers" tab lists all registered triggers with:
- Skill name + trigger summary (one-line render)
- Last fired timestamp
- Cooldown remaining (live counter)
- Kill switch toggle

Kill switch state persists at `~/.codec/triggers_killed.json`:
```json
{
  "killed_keys": ["<skill_name>:<trigger_hash>", ...],
  "schema": 1
}
```

A killed trigger is skipped at evaluation time (silently — no `trigger_blocked` emit; that would spam the audit log if a popular trigger is killed).

Plus the global `TRIGGERS_ENABLED` env var (default `true`) disables the entire system at the observer-poll level.

### 1.6 require_confirmation flow

When `require_confirmation=True` AND `destructive=False`:

1. Trigger matches.
2. Cooldown OK.
3. Emit `trigger_evaluated`.
4. Send a PWA notification via `~/.codec/notifications.json` with `type="trigger_pending"` and a 60s response window.
5. PWA renders an inline "Approve / Skip" panel (similar to AskUserQuestion).
6. User clicks Approve → emit `trigger_fired`, run skill via `codec_dispatch.run_skill`.
7. User clicks Skip / 60s elapses → emit `trigger_blocked` with `reason="user_skipped"` or `"confirmation_timeout"`.

When `destructive=True`: route through `codec_ask_user.ask(destructive=True, ...)` from Step 3. Same literal-verb-match gate; two strikes → `ambiguous_consent` timeout.

When `require_confirmation=False` AND `destructive=False`: fire silently (no PWA notification, just `trigger_fired` audit emit).

### 1.7 Why Step 6 ships ZERO triggers

The blueprint mentions migrating autopilot.json → SKILL_OBSERVATION_TRIGGER. **Step 6 explicitly does NOT do this.** Reasons:

- `~/.codec/autopilot.json` currently has `enabled: false` and `triggers: []`. Nothing to migrate.
- Adding example triggers in Step 6 conflates infrastructure with policy.
- Step 6's job: build the plumbing. Skill authors (or future PRs) add triggers. Initial state: dormant.

So at merge time, `codec_triggers.evaluate()` runs every 60s but iterates over **zero registered triggers** and exits in <1ms. No fires possible.

---

## 2 · Implementation outline

### 2.1 New / modified files

| File | Purpose | LOC |
|---|---|---|
| `codec_triggers.py` (new) | Matcher engine, cooldown state, fire chokepoint | ~280 |
| `codec_skill_registry.py` (extend) | AST-extract `SKILL_OBSERVATION_TRIGGER` from skill files | ~30 |
| `codec_observer.py` (extend) | Call `codec_triggers.evaluate(snapshot)` after each poll | ~15 |
| `codec_audit.py` (extend) | 3 new event constants + frozenset | ~12 |
| `routes/triggers.py` (new) | `/api/triggers` GET + per-trigger kill toggle | ~120 |
| `tests/test_triggers.py` (new) | 35 tests (matcher + cooldowns + consent + kill switches) | ~500 |
| `AGENTS.md` | §3 + §6 + §10 updates | ~40 |
| **Total** | | ~+997 |

### 2.2 Module API

`codec_triggers.py`:

```python
class Trigger:
    """Validated trigger dict. Constructed from skill module's
    SKILL_OBSERVATION_TRIGGER. Holds the original dict + a stable hash key."""
    skill_name: str
    type: str
    pattern: Any
    cooldown_seconds: int
    require_confirmation: bool
    destructive: bool
    key: str    # "<skill_name>:<sha8(trigger_dict)>"

# Public API
def discover_triggers(registry) -> List[Trigger]: ...
    # AST-walk the skill registry, validate each SKILL_OBSERVATION_TRIGGER
    
def evaluate(snapshot: dict, *, fire: bool = True) -> List[dict]: ...
    # Match the snapshot against all registered triggers; return list of
    # candidates. If fire=True, also dispatch matches that pass cooldown +
    # confirmation. Returns one dict per evaluated trigger with status.

def fire(trigger: Trigger, snapshot: dict) -> bool: ...
    # Internal — runs the full gate chain (cooldown, consent, dispatch).

# Kill switch + persistence
def is_killed(trigger_key: str) -> bool: ...
def set_killed(trigger_key: str, killed: bool) -> None: ...

# Cooldown
def cooldown_remaining(trigger_key: str, cooldown_seconds: int) -> float: ...
def mark_fired(trigger_key: str) -> None: ...
```

`codec_skill_registry.py` addition:

```python
def get_observation_trigger(name: str) -> Optional[dict]: ...
    # Returns the SKILL_OBSERVATION_TRIGGER dict for a skill, or None.
    # Validated at metadata-extraction time; bad triggers logged + ignored.
```

### 2.3 Observer integration

In `codec_observer.poll()`, after `_emit_observation_tick()`:

```python
if cfg.get("triggers_enabled", True) and _triggers_enabled_env():
    try:
        from codec_triggers import evaluate as _eval_triggers
        _eval_triggers(snapshot)
    except Exception as e:
        log.debug("[observer] trigger evaluation failed: %s", e)
```

Try/except — trigger failures NEVER break observer polling. Single integration point.

### 2.4 PWA endpoint contract

```
GET  /api/triggers
    → {triggers: [...], killed: [...], total: N}
GET  /api/triggers/<trigger_key>
    → trigger detail + last_fired_at + cooldown_remaining
POST /api/triggers/<trigger_key>/kill
    → toggle killed state, returns new state
```

Auth-gated by existing `/api/*` middleware.

---

## 3 · Audit envelope additions (extending Step 1 §1.2)

Three new event types. All `outcome="ok"` for `trigger_evaluated` and `trigger_fired`; `outcome="warning"` for `trigger_blocked`. All `level="info"` (operational signals).

| Event | Source | When | Extra fields |
|---|---|---|---|
| `trigger_evaluated` | `codec-triggers` | A trigger's pattern matched (before cooldown / consent gate) | `trigger_key`, `skill_name`, `trigger_type`, `match_summary` |
| `trigger_fired` | `codec-triggers` | Skill actually invoked | `trigger_key`, `skill_name`, `trigger_type`, `dispatch_correlation_id` |
| `trigger_blocked` | `codec-triggers` | Cooldown / confirmation reject / consent failure | `trigger_key`, `skill_name`, `block_reason` (`cooldown` \| `user_skipped` \| `confirmation_timeout` \| `ambiguous_consent` \| `killed`) |

All inherit `correlation_id` from the wrapping observer poll's cid.

---

## 7 · Test plan

35 tests across `tests/test_triggers.py`. Same pattern as Step 5 — redirect `codec_audit._AUDIT_LOG` to `tmp_path`, **mock `codec_dispatch.run_skill`** (NEVER fire real skills in tests).

### 7.1 Trigger validation (5)
- `test_trigger_dict_with_all_required_fields_validates`
- `test_trigger_dict_missing_field_rejected`
- `test_trigger_dict_unknown_type_rejected`
- `test_trigger_key_stable_across_reloads`
- `test_trigger_key_changes_when_pattern_edited`

### 7.2 Match logic per type (10)
- 2× window_title_match (match / no-match)
- 2× clipboard_pattern (match / no-match + non-string content)
- 2× file_change (glob match / glob mismatch)
- 2× time (cron match within minute / no match)
- 2× compound (AND-success / OR-success)

### 7.3 Cooldown (5)
- `test_cooldown_blocks_within_window`
- `test_cooldown_allows_after_window`
- `test_cooldown_per_trigger_independent`
- `test_cooldown_reset_on_pattern_edit` (new key = fresh state)
- `test_cooldown_emits_trigger_blocked_with_reason_cooldown`

### 7.4 Confirmation + destructive (8)
- `test_require_confirmation_false_destructive_false_fires_silently`
- `test_require_confirmation_true_creates_pwa_notification`
- `test_require_confirmation_user_approve_fires_trigger`
- `test_require_confirmation_user_skip_emits_trigger_blocked`
- `test_require_confirmation_timeout_emits_trigger_blocked`
- `test_destructive_routes_through_ask_user`
- `test_destructive_two_strike_emits_trigger_blocked`
- `test_destructive_verb_match_fires_trigger`

### 7.5 Kill switches + integration (7)
- `test_per_trigger_kill_blocks_evaluation_silently`
- `test_per_trigger_kill_state_persists_to_file`
- `test_global_TRIGGERS_ENABLED_false_skips_evaluate`
- `test_global_TRIGGERS_ENABLED_default_true`
- `test_observer_poll_calls_trigger_evaluate`
- `test_observer_poll_failure_doesnt_break_polling` (trigger raises → observer continues)
- `test_skill_registry_extracts_SKILL_OBSERVATION_TRIGGER`

---

## 8 · Rollback plan

| Severity | Action |
|---|---|
| Triggers misbehaving (rapid fires, bad pattern) | `TRIGGERS_ENABLED=false` env on codec-observer + `pm2 restart codec-observer`. Polling continues; no triggers evaluated. |
| Specific bad trigger | PWA "Triggers" tab → kill toggle. Persistent across restarts. |
| Audit-event flood (>50 trigger_evaluated/min) | `audit_report.py` flags it. Manual investigation; same revert path. |
| Hard revert | `git revert <step-6-merge>` + `pm2 restart codec-observer`. Observer drops back to Step 5 behavior. |

---

## 9 · Open questions (none for v1)

The blueprint's Q1-Q6 + my own Q5.1-Q5.7 covered all the architectural decisions Step 6 needs. Implementation moves forward. **I'll surface anything that comes up during implementation in the PR description, NOT block on a separate review cycle.** Confirmed with user: "you decide."

---

## 10 · Diff inventory

| File | LOC | Status |
|---|---|---|
| `codec_triggers.py` (new) | ~+280 | new |
| `tests/test_triggers.py` (new) | ~+500 | new |
| `routes/triggers.py` (new) | ~+120 | new |
| `codec_skill_registry.py` | ~+30 | extend |
| `codec_observer.py` | ~+15 | extend |
| `codec_audit.py` | ~+12 | extend |
| `AGENTS.md` | ~+40 | extend |
| `docs/PHASE2-STEP6-DESIGN.md` (this file) | already created | ships in Step 6 PR |
| **Total functional + tests** | **~+997** | |

In line with Step 5 (~+1,448) and Phase 1 step sizes.

---

## Appendix · Safety summary

What this PR does NOT do:
- Does NOT ship any skill with `SKILL_OBSERVATION_TRIGGER` set
- Does NOT migrate autopilot.json (it's empty anyway)
- Does NOT auto-fire any skill at merge time
- Does NOT touch `_HTTP_BLOCKED`
- Does NOT create Apple Reminders / Notes / Calendar entries
- Does NOT add a new PM2 service (triggers run inline in codec-observer)

What can fire after merge (the user has to opt in twice):
1. User edits a skill file to add `SKILL_OBSERVATION_TRIGGER = {...}`
2. PM2 restart codec-observer (or `pm2 reload`) so registry re-scans
3. THEN observer polls eventually match and fire — subject to confirmation gate per skill author's choice

This is the same trust model as plugins (Phase 1 Step 2): user-curated local Python, no marketplace, no auto-install.
