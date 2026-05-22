# PR-3D — extract helpers from the 3 monolith functions (A-5/6/7) (DESIGN)

**Status:** IN PROGRESS — split into 3 sub-PRs.
- **3D-a (A-7 `Agent.run`) IMPLEMENTED** — extracted `_parse_action`, `_validate_tool_call`, `_execute_tool_with_hooks`; `run()` 230 → 177 LOC; 13 unit + 112 regression tests green.
- **3D-b (A-5 `_dispatch_inner`) IMPLEMENTED** — extracted `_build_voice_system_prompt(task)` + `_persist_voice_turn(task, answer, rid)`; `_dispatch_inner` 188 → 131 LOC; 7 unit tests; zero new suite failures; zero net-new ruff. (Faithfulness note: `_persist_voice_turn` does its own `from codec_memory import CodecMemory` — the original relied on the build block's local import being in `_dispatch_inner`'s scope, which the extraction removed.)
- **3D-c (A-6 `chat_completion`) IMPLEMENTED** — extracted the `<think>` + `[SKILL:...]` token machine into new **`codec_chat_stream.py`** (`SkillTagBuffer` + shared `SKILL_TAG_RE`); `_stream_gen` keeps the SSE/HTTP plumbing + the injected `_resolve_skill_tag`. `chat_completion` 466 → 379 LOC. 13 unit tests for the buffer; zero new suite failures; zero net-new ruff. Faithfulness preserved exactly: same-chunk `</think>` dropped, think-adjacent text emitted-but-uncounted, dropped-tag empty frames still emitted, 5000-char cap, cross-chunk tag assembly. **Bonus:** `SkillTagBuffer` is now the tested unit the deferred A-12 dashboard-stream migration needs (it can consume `codec_llm.stream()`'s raw tokens).

**PR-3D COMPLETE** — all three monoliths decomposed (A-7 #72, A-5 #73, A-6 this PR), each behavior-preserving with its own unit tests + the full regression suite green.
**Findings:** A-5 (`_dispatch_inner`, 188 LOC), A-6 (`chat_completion`, 466 LOC), A-7 (`Agent.run`, 230 LOC) — all MEDIUM.
**Wave:** 3 (complexity reduction). These are the **three hottest functions in the repo** (voice dispatch · chat handler · agent loop), so: split into one-PR-per-function + behavior-preserving extractions with tests. **No big-bang.**

---

## 1. Why split (not one PR)

~884 LOC across three central control-flow functions. A single diff touching all three would be unreviewable and high-blast-radius on the paths behind voice, chat, and agents. The same "small reviewable tranches on hot paths" rule that governed A-12 applies here. **Plan: 3 sub-PRs (3D-a, 3D-b, 3D-c), one function each, each independently revertable, each green before the next.**

The unbreakable rule for all three: **pure behavior-preserving extraction.** Extract named helpers; the function body becomes a flow of calls. No logic changes, no "while I'm here" fixes. Tests assert the extracted helpers in isolation AND that the existing suite (which already exercises these paths) stays green.

## 2. Per-function extraction plan (from the audit)

### A-7 — `codec_agents.Agent.run` (230 LOC) — *lowest risk, cleanest units*
Pull out (audit §A-7):
- `_parse_action(text) -> tuple[Literal["tool","final"], dict]` — the `TOOL:`/`INPUT:`/`FINAL:` regex protocol. **Pure function.**
- `_validate_tool_call(name, tool_input, tools) -> str | None` — the validation block (unknown tool, bad input). **Pure function.**
- `_execute_tool_with_hooks(...) -> str` — the `copy_context` + `run_with_hooks` + stuck-detection executor.
`run()` becomes: build prompt → loop{ LLM → `_parse_action` → (final? return) → `_validate_tool_call` → `_execute_tool_with_hooks` }. Pure functions = trivial TDD; covered by `test_agents_crews.py`.

### A-5 — `codec._dispatch_inner` (188 LOC) — *voice path, medium risk*
Pull out (audit §A-5):
- `_build_voice_system_prompt(task) -> str` — the system-prompt + memory/facts injection block.
- `_persist_voice_turn(session_id, task, answer, rid) -> None` — the DB write + `CodecMemory` saves.
- (the LLM call is already `codec_llm.call` from A-12 tranche 1.)
`_dispatch_inner` becomes: skill → draft → `_build_voice_system_prompt` → `codec_llm.call` → `_persist_voice_turn` → TTS. Target <80 LOC.

### A-6 — `codec_dashboard.chat_completion` (466 LOC) — *biggest, highest strategic value, do last*
Extract the streaming + `[SKILL:...]` tag-buffering loop into a new **`codec_chat_stream.py`** with a tested **`SkillTagBuffer`** class (audit §A-6): feed it tokens, it yields emit-decisions (handles partial-prefix match, the 5000-char safety cap, `<think>` state, tag resolution). **Strategic bonus:** this is the SAME tag-machine that blocks the deferred **A-12 dashboard-stream** migration — once `SkillTagBuffer` is a tested unit consuming raw tokens, the dashboard stream can finally consume `codec_llm.stream()`. So A-6 ties PR-3D and A-12 together — best done last, when the extraction pattern is proven.

## 3. Recommended order + this PR

**A-7 → A-5 → A-6.** Rationale: A-7's extractions are pure functions (cleanest TDD, lowest risk, strong existing test coverage) → proves the pattern. A-5 next (voice, medium). A-6 last (biggest + unblocks A-12 dashboard — the capstone).

**Recommended for THIS PR: A-7 (`Agent.run`).**

## 4. Test plan (A-7, this PR)
- New `tests/test_agent_run_helpers.py`: `_parse_action` (TOOL+INPUT, FINAL, malformed/no-marker, multi-line input, whitespace); `_validate_tool_call` (unknown tool → message, known tool → None); `_execute_tool_with_hooks` (hook-wrapped result, veto string, stuck-path) with a stub tool + monkeypatched hooks.
- Regression: `tests/test_agents_crews.py` + the full suite stay green (these already drive `Agent.run` end-to-end). Expect the 23 known-baseline failures, **zero new**.
- No `skills/` touched → no manifest regen.

## 5. Risk + rollback
- **Blast radius (this PR):** `codec_agents.py` only — `Agent.run` body + 3 new private helpers in the same module. Behavior-preserving; the agent/crew suite is the safety net.
- **Rollback:** single-commit revert.
- A-5 + A-6 deferred to their own sub-PRs (each small + revertable + design-noted here).

## 6. Open question for you (Mickael) — which monolith first?

- **Option 1 (recommended):** **A-7 `Agent.run`** — pure-function extractions (`_parse_action`, `_validate_tool_call`, `_execute_tool_with_hooks`), lowest risk, cleanest tests. Proves the pattern; A-5 + A-6 follow as their own PRs.
- **Option 2:** **A-6 `chat_completion`** first — extract `SkillTagBuffer` into `codec_chat_stream.py`. Highest value (tested tag-machine + unblocks the A-12 dashboard stream) but biggest/riskiest single PR.
- **Option 3:** **A-5 `_dispatch_inner`** first — voice-path helpers (`_build_voice_system_prompt`, `_persist_voice_turn`).

I recommend **Option 1**. Pick one and I'll implement (TDD) + open the PR (chat-review-then-merge). The other two follow in order.
