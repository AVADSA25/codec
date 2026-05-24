# PR-7B — server-derive destructiveness (Audit B / B-2, part 1)

> Wave 7. Partial close of **B-2** (the permission gate trusts flags the LLM
> declares about its own action). Reference: `docs/audits/PHASE-1-PROJECTS-PILOT.md` B-2.

## The hole

`_enforce_destructive_gate` only invokes the (now-working, post-B-1) consent
gate when `action.is_destructive` is True — and that flag is **set by Qwen
itself** (`_qwen_next_action` reads `is_destructive` from the model's JSON). So
the model bypasses consent for a destructive op simply by emitting
`is_destructive: false` (or omitting it). Same flaw applies to `touches_path` /
`network_call` for the path/domain gates.

## This PR (the highest-value, lowest-risk slice)

**Invariant: the agent can only *upgrade* an action's risk, never downgrade it.**
Make destructiveness `effective = LLM_flag OR server_signal`, where the server
signal is computed independent of the model:

1. **Dangerous-skill boundary** — `action.skill ∈ codec_config._HTTP_BLOCKED`
   (`python_exec`, `terminal`, `process_manager`, `pm2_control`, `ax_control`):
   arbitrary code / shell / process control is *always* destructive. Precise,
   zero false positives.
2. **Destructive-intent verbs** — a word-boundary match of an irreversible verb
   (`delete`, `remove`, `wipe`, `send`, `transfer`, `pay`, `kill`, `drop`,
   `format`, `overwrite`, …) in the task text. Over-triggering here is the
   *safe* direction (an extra consent prompt) and aligns with Step-3 §1.7's own
   destructive-verb list — confirming before "send"/"delete"/"pay" is desired
   behaviour for an autonomous agent, not a false positive.

`_enforce_destructive_gate` then gates on `_effective_destructive(action)`. The
consent path (B-1) does the rest.

## Why only destructiveness (not the full B-2) here

The full fix — deriving `touches_path`/`network_call`/`reads_path` + their
path/domain *values* server-side — needs a **per-skill capability model** (or
post-hoc resource reporting from skills), which is an XL, architectural change
across ~76 skills with real design trade-offs (curated capability table vs new
`SKILL_CAPABILITIES` metadata) and would reclassify actions in ways that touch
many of the 51 runner tests. That deserves its own **design-first** PR and a
decision from Mickael. **This PR closes the worst, cleanest part now** — an
autonomously-running agent can no longer execute shell/code or an obviously
destructive task without hitting the consent gate, regardless of what the LLM
claims about itself. B-2 stays **open (partial)**.

## Scope / safety

- Only adds `_server_destructive_signal` + `_effective_destructive` and swaps the
  one `if not action.is_destructive` check in `_enforce_destructive_gate`.
  OR-only ⇒ never *removes* a gate that fired before.
- Verified against the full `test_agent_runner.py` (51 tests) — any test whose
  non-destructive action carries a destructive verb / dangerous skill is
  updated to reflect the (correct, stricter) new behaviour.

## Test plan (`tests/test_derive_destructive.py`)

- `is_destructive=False` + `skill="terminal"` (∈ `_HTTP_BLOCKED`) ⇒ effective
  destructive ⇒ `_enforce_destructive_gate` routes to consent (mock `_strict_consent`).
- `is_destructive=False` + task `"delete ~/x"` ⇒ effective destructive.
- benign (`skill="web_search"`, task `"look up weather"`, `is_destructive=False`)
  ⇒ NOT destructive ⇒ auto-approve (no regression).
- `is_destructive=True` ⇒ destructive (unchanged).
- OR-only invariant: server signal can't be turned off by the LLM flag.

## Rollback

Two small helpers + one gate line in `codec_agent_runner.py` + one test.
`git revert`.
