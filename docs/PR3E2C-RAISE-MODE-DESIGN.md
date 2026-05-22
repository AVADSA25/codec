# PR-3E-2c — A-12 tranche 2c: `codec_llm.call(raise_on_error=True)` + migrate the raise-on-failure sites (DESIGN)

**Status:** IMPLEMENTED. Added `codec_llm.LLMError` + `codec_llm.call(raise_on_error=True)`; migrated all 4 sites (textassist, regen, agent_plan/_runner via a `QwenUnavailableError` adapter + new `_qwen_base()` helper). 14 new tests (`tests/test_llm_raise_mode.py`); 109 agent tests still green; full suite zero new failures; zero net-new ruff.
**Finding:** A-12 (continuation). Tranche 2 deferred the 4 sites whose contract is *fail-loud* (they must NOT silently return empty). This adds the `raise_on_error` mode and migrates them.
**Wave:** 3. Touches the autonomous-agent runtime → design-first + parity tests.

---

## 1. The 4 sites + why they were deferred

All 4 **raise (or fail-loud) on LLM failure** — the opposite of `codec_llm.call`'s never-raise. Migrating them with the current never-raise → `""` would silently degrade:

| Site | Current failure behavior | never-raise would… |
|---|---|---|
| `codec_textassist.call_qwen` | raises on non-200/malformed; caller's `except` shows an Error overlay | `pbcopy ""` + ⌘V → **paste empty over the user's selection** + "Text replaced!" |
| `scripts/regen_skill_descriptions._llm` | `raise_for_status()` (fail-loud dev script) | silently **write empty descriptions** |
| `codec_agent_plan._qwen_chat` | raises `QwenUnavailableError` on conn/timeout/non-200/malformed | swallow failure → plan drafting parses `""` |
| `codec_agent_runner._qwen_chat` | identical to agent_plan (`QwenUnavailableError`) | swallow failure → daemon never retries / parses `""` |

## 2. Contract — `raise_on_error: bool = False`

Add to `codec_llm.call`. **Default False → existing behavior unchanged** (codec.py / qwen_call / compaction / dictate are untouched).

When `True`: raise a new typed **`codec_llm.LLMError(Exception)`** whenever `call()` would otherwise return `""` — i.e. on **every** non-success path:
- non-200 (after exhausting `retries`)
- connection / timeout / other request exception (after exhausting `retries`)
- malformed response JSON or shape (`extract_content` → "")
- **200 but empty content** (no usable answer)

Rationale for raising on empty-200 too: all 4 sites treat an empty/unusable answer as a failure (textassist would paste nothing, regen would write nothing, the agents would parse nothing). "Never silently returns empty" is exactly the contract they want — so empty-200 → raise is a *strict improvement*, not just parity.

`retries` still applies before raising (the agents use the default `retries=1` = single attempt = raise immediately on first failure, matching their current single-POST shape).

## 3. Per-site migration

- **`textassist.call_qwen`** → `codec_llm.call(..., raise_on_error=True)`, keep the `### FINAL ANSWER:` strip at the call site (codec_llm already strips `<think>`). The caller's existing `try/except` (shows the Error overlay) now also fires on empty-200 — **fixing the destructive empty-paste**.
- **`regen._llm`** → `codec_llm.call(..., raise_on_error=True).strip('"').strip()`. Fail-loud preserved (LLMError propagates like `raise_for_status` did); gains `<think>` strip.
- **`agent_plan._qwen_chat` / `agent_runner._qwen_chat`** → thin adapter that **preserves the public `QwenUnavailableError`**:
  ```python
  import codec_llm
  try:
      return codec_llm.call(
          [{"role": "system", "content": system_prompt or ""},
           {"role": "user", "content": user_prompt}],
          base_url=_qwen_url_base(), model=_qwen_model(),
          max_tokens=max_tokens, temperature=0.2,
          timeout=QWEN_TIMEOUT, raise_on_error=True,
      )
  except codec_llm.LLMError as e:
      raise QwenUnavailableError(str(e)) from e
  ```
  (`_qwen_url()` returns the full `.../chat/completions`; codec_llm wants the base, so the adapter strips the suffix or uses the base helper — see implementation.)

## 4. Behavior deltas (documented)

1. **agent_plan/runner now strip `<think>` + send `enable_thinking=False` + have a `reasoning` fallback.** Their downstream parses the content as JSON — suppressing/stripping think makes that *more* robust (original passed raw content through). Improvement, not regression.
2. **All 4: empty-200 now raises** (was: `""` → empty paste / empty desc / parse-`""`). Strict improvement — fail-loud is the intent.
3. **agent_plan/runner: exception _message_ changes** (e.g. "qwen3.6 returned 500" → wrapped `LLMError` text) but the **TYPE `QwenUnavailableError` is preserved** via the adapter — the daemon's `except QwenUnavailableError` retry/abort logic is unaffected.
4. **agent_plan/runner: no added retries** — `retries=1` (default) keeps the single-attempt shape; the daemon owns retry/backoff at a higher level.

## 5. Test plan
- `tests/test_llm_raise_mode.py`: `call(raise_on_error=True)` raises `LLMError` on non-200 / connection-exception / empty-200; `raise_on_error=False` (default) still returns `""` for all three (regression guard on the existing contract); `LLMError` is an `Exception` subclass.
- Migration tests: monkeypatch `codec_llm.call` → assert `agent_plan._qwen_chat` / `agent_runner._qwen_chat` raise `QwenUnavailableError` (not `LLMError`) when codec_llm raises, and pass content through on success. Source-invariants: the 4 sites call `codec_llm.call(`, inline `requests.post(...chat/completions...)` / `raise_for_status` gone.
- Full suite: expect the 23 known-baseline failures, **zero new**. `regen_skill_descriptions` is a script (no manifest); textassist is not a skill module → **no manifest regen**.

## 6. Risk + rollback
- **Blast radius:** `codec_llm.py` (+`raise_on_error`/`LLMError`) + 4 call sites. The agent runtime is the sensitive part — covered by parity tests asserting `QwenUnavailableError` is still what propagates.
- **Rollback:** single-commit revert restores the inline impls. `raise_on_error` defaults False so no other caller can be affected.

## 7. After 2c — remaining A-12
Bridges (telegram/imessage), dashboard (4 non-stream + the `[SKILL:…]` stream tag-machine), voice `_stream_qwen` + agents (async `astream()` + queue at call site), skills tranche (translate/fact_extract/create_skill/skill_forge).
