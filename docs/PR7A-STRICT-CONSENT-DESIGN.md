# PR-7A — fix the phantom destructive-consent gate (Audit B / B-1)

> Wave 7 (Audit B burn-down). Closes **B-1** — the highest-confidence CRITICAL
> from `docs/audits/PHASE-1-PROJECTS-PILOT.md`. Reference: that doc, §B-1.

## What's broken (verified)

`codec_agent_runner._strict_consent` (the destructive-op consent gate for the
autonomous agent) does:

```python
from codec_ask_user import strict_consent_gate   # ← does NOT exist
```

`codec_ask_user` exposes no `strict_consent_gate` (confirmed: defined nowhere;
only a `MagicMock` in `tests/test_agent_runner.py`). In production the import
raises `ImportError`, the `except` returns `ConsentResult(approved=False,
timed_out=True, user_response="ask_user_unavailable")`, so:

- a destructive op the LLM **does** flag (`is_destructive=true`) is **never
  actually shown to the user** — it dead-ends at `blocked_on_destructive`;
- the literal-verb consent prompt the Step-3 §1.7 design promises **never runs**.

It survived because every test mocks the `_strict_consent` *wrapper*, never the
real body (B-1's own observation).

## The fix

Rewire `_strict_consent` to the **real** API, `codec_ask_user.ask(...)`, which
already implements §1.7 strict-consent (literal-verb match, generic "yes"
rejected, two-strike → `ambiguous_consent` timeout) on the reply path
(`submit_answer`):

```python
answer = codec_ask_user.ask(
    question,
    destructive=True,
    destructive_verb="confirm",
    timeout=deadline,
    agent=action.agent or "agent",
    asked_from="crew",
    tool_name=action.skill,
)
```

Return contract of `ask()` (read from source):
- **answered** (in `destructive=True` mode the question only reaches
  `answered` when the reply contained the verb) → returns the answer string →
  **approved**.
- timeout / two-strike ambiguous-consent → returns `TIMEOUT_SENTINEL` → **not
  approved, `timed_out=True`** (caller → `blocked_on_destructive`, fail-safe).
- `ASKUSER_ENABLED=false` → returns `DISABLED_SENTINEL` → treat as **not
  approved, `timed_out=True`** (blocked, never auto-approve).

Mapping → `ConsentResult`:

| `ask()` returns | approved | timed_out |
|---|---|---|
| `TIMEOUT_SENTINEL` | False | True |
| `DISABLED_SENTINEL` | False | True |
| any other string (verb-matched answer) | True | False |

**Verb:** `"confirm"` — the framework's sanctioned non-generic default
(`_default_destructive_verb` fallback); generic "yes"/"ok" are still rejected by
`ask()`'s §1.7 logic. The question text instructs typing `confirm`.

## Scope

- Only `_strict_consent`'s body changes. `ConsentResult`,
  `_enforce_destructive_gate`, the caller in `_execute_checkpoint`, and the
  `blocked_on_destructive` state machine are **unchanged**.
- Existing consent tests mock `_strict_consent` at the wrapper level → unaffected.
- **Not in scope:** B-2 (gate trusts LLM-self-declared flags) is the separate,
  larger PR-7B; this PR makes the consent prompt *function* for flagged ops.

## Test plan (`tests/test_strict_consent_fix.py`)

Red→green, exercising the **real** `_strict_consent` (the gap B-1 left):
- verb-matched answer (mock `codec_ask_user.ask` → `"confirm"`) → `approved=True`,
  `timed_out=False`, and `ask` was called with `destructive=True`.
- `ask` → `TIMEOUT_SENTINEL` → `approved=False, timed_out=True`.
- `ask` → `DISABLED_SENTINEL` → `approved=False, timed_out=True` (blocked, not approved).
- **regression guard:** `inspect.getsource(_strict_consent)` does NOT reference
  `strict_consent_gate`, and `codec_ask_user` has no such attribute (so the
  phantom can't return).

Plus: full `test_agent_runner.py` (51 tests) stays green (they mock the wrapper).

## Rollback

Single-function change in `codec_agent_runner.py` + one new test. `git revert`.
