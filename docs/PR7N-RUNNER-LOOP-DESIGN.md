# PR-7N — Runner action-loop: testable parse + budget backstop (Audit B / B-12 + B-14)

**Status:** design → TDD → ship
**Closes:** Audit-B **B-12** (`_qwen_next_action` conflates four concerns + reverse-engineers
state from skill strings) + **B-14** (step budget bounds checkpoints not LLM calls;
`extend_budget` unbounded).
**Branch:** `fix/pr7n-runner-loop`
**Touches:** `codec_agent_runner.py` + `routes/agents.py`.

## What

1. **B-12 (MEDIUM)** — `_qwen_next_action` is one ~170-line function doing prompt
   composition, history trim, regex reconstruction of file-iteration state from skill
   **output strings**, JSON extraction, and Action construction — near-untestable as a unit,
   and any skill output-format change silently breaks multi-file checkpoints. Extract the
   pure pieces to module-level, individually-testable functions; `_qwen_next_action` becomes
   a thin orchestrator (build prompt → call Qwen w/ retry → parse → build Action).

2. **B-14 (MEDIUM)** — each budgeted step can fire ≥2 `_qwen_next_action` calls (the
   correction-nudge retry), so the step budget doesn't actually bound LLM calls; and
   `extend_budget` adds up to 100 steps per call with **no cumulative ceiling** — the only
   backstop against a runaway/looping agent is defeated. Count **every** `_qwen_next_action`
   against a hard per-checkpoint cap, and cap the cumulative `extend_budget` override.

## Why it matters

- B-12: the regex-from-output-strings iteration tracker is brittle (a skill changing its
  result format breaks iteration with no error) and the monolith can't be unit-tested.
- B-14: an autonomous agent that never emits `checkpoint_done` (LLM loop) or an unbounded
  `extend_budget` is an unbounded local LLM-spend / runaway primitive.

## Design

### B-12 — extract pure units (no behavior change)

Promote the nested closures + extract two new helpers, all module-level + pure:
- `_parse_action_json(text) -> Optional[dict]` (fences / bare / truncated-balanced-brace).
- `_trim_history(h_list, cap=600) -> list`.
- `_extract_file_list(h_list) -> list` and `_already_read(h_list) -> set` (the regex
  iteration tracker — isolated so its fragility is contained + testable).
- `_build_file_iteration_hint(history) -> str` (wraps the two above).
- `_build_action_prompt(plan_dict, checkpoint, history, max_history=10) -> str`.
- `_action_from_json(d) -> Action`.

`_qwen_next_action` then: `prompt = _build_action_prompt(...)`; call Qwen + the existing
one-retry; `d = _parse_action_json(...)`; `return _action_from_json(d)`. **Byte-for-byte the
same prompt + parse + Action** — this PR does not change the iteration semantics (the deeper
"typed iteration tracker consuming structured history instead of 500-char slices" is noted
as a larger follow-up; the extraction already makes it testable + swappable).

### B-14 — explicit LLM-call cap + cumulative extend ceiling

- `_execute_checkpoint` counts **every** `_qwen_next_action` invocation (including the
  correction retry) via a local counter; when the count would exceed the effective `budget`,
  it raises `StepBudgetExhausted`. So `budget` now genuinely bounds **LLM calls**, not loop
  iterations — a correction-heavy step consumes its real share. (The `for step in
  range(budget)` loop stays as a secondary bound.)
- New `MAX_CHECKPOINT_STEP_BUDGET = 500` in `codec_agent_runner`. `extend_budget` (routes)
  caps the cumulative override at this ceiling: `new = min(base + additional, MAX)`, and
  returns **409** if `base >= MAX` (ceiling reached). The per-call `le=100` bound stays.
- **Authz gate on `extend_budget` is deferred** — it depends on B-3 (per-agent ownership
  authz, deferred pending a multi-user decision). Documented inline; the cumulative cap is
  the implementable half.

## Schema / API changes

- New module-level helpers in `codec_agent_runner` (all pure except the Qwen call stays in
  `_qwen_next_action`); new constant `MAX_CHECKPOINT_STEP_BUDGET`.
- `extend_budget` response/behavior: caps `new_budget` at the ceiling; 409 when already at
  ceiling. `ExtendBudgetBody` unchanged (`1<=additional_steps<=100`).
- No on-disk schema / audit-envelope change. `_qwen_next_action` signature unchanged.

## Rollback

Revert the single commit. The extraction is behavior-preserving; the budget cap + ceiling
are additive guards. No data migration.

## Test plan (TDD)

New `tests/test_runner_loop.py`:
- B-12 pure units: `_parse_action_json` (bare / ```json fence / truncated-with-trailing-prose
  / garbage→None); `_action_from_json` (checkpoint_done; skill_call field coercion; unknown
  keys ignored); `_extract_file_list` + `_already_read` (parse a file_ops list/read result);
  `_build_action_prompt` (contains goals + available skills + the file-iteration hint).
- B-14: `test_qwen_calls_capped_counting_corrections` — with a tiny budget and a model that
  forces a correction every step (bad→good), total `_qwen_next_action` calls are bounded by
  `budget` and `StepBudgetExhausted` is raised (corrections count). `test_extend_budget_caps_cumulative`
  — repeated extends can't push the override above `MAX_CHECKPOINT_STEP_BUDGET`; a 409 once
  at the ceiling.

Full suite: zero new failures vs the 41-failed baseline (the existing `_qwen_next_action` /
`_execute_checkpoint` tests must stay green — the extraction is behavior-preserving). Ruff:
zero delta vs origin/main.
