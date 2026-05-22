# PR-3E-2 — A-12 tranche 2: `codec_llm.stream()` + migrate remaining `chat/completions` sites (DESIGN)

**Status:** APPROVED — **Option 1** chosen (keystone `stream()` + `codec_session.qwen_stream` proof + non-streaming core trivials: compaction, textassist, dictate, regen script). Implementing (TDD). See §4.
**Finding:** A-12 (continuation). PR-3E shipped `codec_llm.call()` (non-streaming) + migrated 2 sites. This designs the **streaming** keystone `codec_llm.stream()` and the phased migration of the **22 remaining text-chat sites**.
**Wave:** 3. Hottest path in the repo → design-first + small reviewable tranches, never a big-bang.

---

## 1. Ground-truth inventory (22 unique text-chat sites)

A full read-the-source survey (not just grep) found **22 live inline `chat/completions` text-chat sites** remaining after PR-3E. Key facts:

- **No separate cloud client.** `codec_ava_client.py` does **not** exist in this worktree — "cloud" is purely a runtime config repoint of `llm_base_url`/`api_key` to an OpenAI-compatible endpoint. So `codec_llm.call/stream` serves local **and** cloud as long as `base_url`/`api_key` pass through. No special cloud branch needed.
- **3 streaming, 19 non-streaming.** **2 async** (`codec_voice._stream_qwen` httpx, `codec_agents.Agent.run` httpx); the rest sync `requests` (one sync-httpx: `codec_compaction`).
- **2 queue-coupled.** `codec_voice._stream_qwen` (`llm_queue.acquire(CRITICAL)` @593/621) and `codec_agents.Agent.run` (`MEDIUM` @439/448). The semaphore must stay **at the call site** — `codec_llm` must never own queue acquisition.
- **Vision sites are out of scope.** Dashboard (1069/1208/1680/2666), telegram/imessage `process_image`, `codec_watcher.screenshot_ctx`, `skills/screenshot_text` still hand-roll vision POSTs — those are the **A-11** dedup target (codec_vision), not A-12. `skills/mouse_control` UI-TARS is a different model entirely → leave as-is.

### Site map (codec_llm targets)
| Subsystem | Sites | Stream | Notes |
|---|---|---|---|
| dashboard | 6 | 2 of 6 | `:2854` stream is the hard one (skill-tag machine); `:973`,`:2464`,`:2975`,`:3167` non-stream |
| voice | 1 | yes | `_stream_qwen` — async + queue CRITICAL |
| session | 1 | yes | `qwen_stream` — sync, already falls back to migrated `qwen_call` |
| agents | 1 | no | `Agent.run` — async + queue MEDIUM |
| agent_plan / agent_runner | 2 | no | **RAISE** `QwenUnavailableError` (opposite of call()'s never-raise) |
| bridges telegram / imessage | 2 | no | identical OpenAI shape, Timeout branch |
| compaction / self_improve / watcher / textassist / dictate | 5 | no | each one isolated POST; some have bespoke retry |
| skills/scripts (translate, fact_extract, create_skill, skill_forge, regen script) | 5 | no | skill files → manifest regen on edit |

## 2. The keystone — `codec_llm.stream()` (this PR)

```python
def stream(
    messages, *, base_url, model, api_key="", max_tokens=500, temperature=0.7,
    timeout=120.0, enable_thinking=False, extra_kwargs=None,
) -> Iterator[str]:
    """POST with stream=True and yield raw assistant content deltas.

    Handles internally: header/payload build (shared with call()), the SSE
    framing (`data: ` prefix, `[DONE]` sentinel), `choices[0].delta.content`
    extraction, and per-chunk error tolerance. Yields the RAW content deltas
    in order. Never raises — on connect/HTTP/parse error it logs and stops
    yielding (caller sees a short/empty stream and applies its own fallback)."""
```

**Design decisions:**
- **Sync generator now; async deferred.** Only `codec_session.qwen_stream` (sync) consumes it this PR. An async `astream()` (for voice/agents) lands with the voice tranche (2e) — building it now without a consumer is YAGNI.
- **Yields RAW content deltas — think-stripping stays with the caller.** *(Revised from the initial draft, which had `stream()` strip `<think>` internally.)* Rationale: `qwen_stream` writes each delta to stdout **live** and only `strip_think`s the *accumulated* result — so internal stripping would silently drop the reasoning it currently shows live (a parity break), and a streaming tag-stripper needs fiddly partial-tag buffering. Yielding raw gives **exact parity** for `qwen_stream` (caller does `for tok: stdout.write(tok); full += tok` then `strip_think(full)`), is simpler/safer for the first streaming API, and matches reality — the dashboard already owns its own cross-chunk `<think>` + `[SKILL:...]` machine (§3.1). `stream()` still centralizes all the real boilerplate (headers/payload, `requests.post(stream=True)`, SSE framing, never-raise).
- **Never raises** (parity with `call()`). The raise-on-failure sites get a separate contract (§3.3), not this PR.
- **Shared internals.** `stream()` reuses `call()`'s header/payload builder (extract a private `_build_request(...)`).

## 3. Three hard constraints (how each future tranche handles them)

### 3.1 dashboard `:2854` — the skill-tag stream machine (defer to its own PR)
Mid-stream char-by-char `[SKILL:...]` buffering (5000-cap, prefix validation), cross-chunk `<think>` state, SSE keepalive comments, `[DONE]` re-framing, blank-bubble fallback. **Plan:** `stream()` yields clean tokens; the dashboard keeps its tag machine and SSE re-emit, consuming `stream()` only for the raw-line→token layer. Migrated **last** (tranche 2d/2e), never absorbed into codec_llm.

### 3.2 async + queue coupling (voice, agents)
Both wrap the POST in `llm_queue.acquire/release` and use a shared module httpx client. **Plan:** `codec_llm.astream()`/an async `call` accepts an injected client (`http=`, like `codec_vision.describe_async`) and the **caller keeps** acquire/release around the codec_llm call. codec_llm never touches the semaphore. Deferred to tranche 2e (voice) / a small agents PR.

### 3.3 raise-on-failure (agent_plan, agent_runner)
These RAISE `QwenUnavailableError`; `call()` never raises. **Plan (tranche 2c):** add `raise_on_error: bool = False` to `codec_llm.call` — when True, re-raise a typed `LLMUnavailableError` (or the caller maps a sentinel). A design decision, not a swap → its own PR. **Not this PR.**

## 4. Recommended scope for THIS PR (tranche 2a)

**Build the keystone, prove it on the simplest consumer, clear the easy non-streaming wins:**
1. **New `codec_llm.stream()`** (sync generator, §2) + refactor `call()`/`stream()` to share `_build_request`.
2. **Migrate the simplest streaming site:** `codec_session.qwen_stream` → consume `codec_llm.stream()` (keeps its stdout write + fallback to the already-migrated `qwen_call` on empty/error). Proof-of-API.
3. **Migrate the genuinely-clean non-streaming trivials:** `codec_compaction.py` (already has a fallback summary on empty), `codec_dictate.py` (every failure mode already collapses to "use raw body" — exactly what `call()`'s never-raise → `""` maps to).

> **Refinement during implementation (read-the-source).** The approved Option 1 listed 4 trivials; reading the actual downstreams moved **2 of them to tranche 2c**:
> - **`codec_textassist.py`** RAISEs on LLM failure, and its caller's `except` shows an *Error* overlay. With `call()`'s never-raise → `""`, the success path would `pbcopy ""` + ⌘V, **pasting empty over the user's selection** and showing "Text replaced!" — a destructive regression. Needs `raise_on_error`.
> - **`scripts/regen_skill_descriptions.py`** uses `raise_for_status()` (fail-loud dev script); never-raise would silently write empty descriptions. Needs `raise_on_error`.
>
> Both have the **same raise-on-failure contract** as `agent_plan`/`agent_runner`, so they migrate together in **tranche 2c** when `codec_llm.call` gains `raise_on_error: bool`. Deferring honors "never break working code."

**Explicitly deferred** (each its own tranche/PR): skills/*.py (translate/fact_extract/create_skill/skill_forge — bundled into a "skills tranche" so manifest regen doesn't mix with core), bridges (2b), **raise-on-error sites: textassist + regen + agent_plan/runner (2c, add `raise_on_error` mode)**, dashboard non-stream (2d), voice `_stream_qwen` + dashboard stream + `astream()` (2e).

### Test plan
- New `tests/test_llm_stream.py`: `stream()` yields the **raw** deltas in order from a fake SSE body (incl. a `<think>` delta — NOT stripped); stops cleanly on `data: [DONE]`; blank / non-`data:` / garbage-JSON lines skipped without raising; HTTP non-200 → empty stream (no raise); connection exception → empty stream (no raise); payload carries `stream: True` + the shared `_build_request` shape (model/messages/max_tokens/temperature/enable_thinking + extra_kwargs).
- Migrated-site tests: `qwen_stream` consumes `stream()`, writes deltas, returns `strip_think(full)`, and falls back to `qwen_call` on empty stream; `codec_compaction.compact_context` and `codec_dictate` draft-refine call `codec_llm.call` with the right base_url/model (monkeypatched), and fall back correctly on `""`.
- Full suite: expect the 23 known-baseline failures, **zero new**. No `skills/` touched → **no manifest regen**.

## 5. Risk + rollback
- **Blast radius (this PR):** `codec_llm.py` (+stream), `codec_session.py` (qwen_stream), 3 small non-streaming modules + 1 script. Streaming behavior change confined to session (which already falls back to the migrated qwen_call).
- **Rollback:** single-commit revert restores the inline impls. No persistent state touched.
- The hot/hard sites (dashboard stream, voice async, agent raise-mode) are **all deferred** to later small PRs.

## 6. Open question for you (Mickael) — scope of this PR

- **Option 1 (recommended):** `stream()` keystone **+** session-stream proof **+** non-streaming core trivials (compaction, textassist, dictate, regen script). ~5 sites + 1 new API. Builds the keystone, proves it, clears easy wins. Skills + bridges + dashboard + voice + raise-mode each follow as their own small PRs.
- **Option 2 (tightest):** `stream()` keystone **+** session-stream proof **only**. Pure API + one consumer, smallest possible review. Non-streaming trivials move to a later batch.
- **Option 3 (bigger):** `stream()` **+** `astream()` **+** all 3 streaming sites (session + voice async + dashboard tag-machine). Most value, but pulls in the two hardest constraints (async+queue, the dashboard skill-tag machine) into one PR — highest risk on the hottest path.

I recommend **Option 1**. Pick one and I'll implement (TDD) + open the PR (chat-review-then-merge).
