# PR-3E-async — A-12: async `codec_llm.acall()` + `astream()` for voice + agents (DESIGN)

**Status:** IMPLEMENTED — **Option 2**. Added async `codec_llm.acall()` (mirrors `call()` + `raise_on_error`, injected client) and `codec_llm.astream()` (mirrors `stream()` + keepalive, but **propagates** exceptions). Migrated `codec_voice._stream_qwen` (astream, queue CRITICAL, per-token `<think>` strip + spoken error kept), `codec_agents.Agent.run` (acall `raise_on_error=True`, queue MEDIUM), and the agents research-refiner (acall, never-raise → defaults). Removed the now-dead `codec_agents._qwen_url()` (added `_qwen_base()`); added voice `QWEN_BASE_URL`. When a client is injected the helpers pass no per-request timeout (use the client's configured timeout — exact parity). 12 new tests; crew + voice regression green; full suite 1485 passing, zero new; zero net-new ruff. **A-12 streaming complete** — only the skills tranche remains.
**Finding:** A-12 (the last LLM tranche). The 3 remaining inline sites are **async** (httpx) and **queue-coupled** — voice `_stream_qwen` (streaming, CRITICAL) and agents (non-stream; `Agent.run` MEDIUM + a research-refiner with no queue).
**Wave:** 3. Two hot paths (voice + crews) → split-able, queue stays at the call site.

---

## 1. The 3 async sites

| site | kind | queue | failure behavior today | think-strip |
|---|---|---|---|---|
| `codec_voice._stream_qwen` | **async stream** (`self._http.stream` + `aiter_lines`) | CRITICAL (acquire/release around the whole stream) | `except: yield "Sorry, I had a processing error."` (spoken) | **per-token** `re.sub` on each delta |
| `codec_agents.Agent.run` LLM call | **async non-stream** (`_async_http.post`) | MEDIUM | `except: return f"LLM error: {e}"` (early-exit) | on full response after the call |
| `codec_agents` research-refiner | **async non-stream** (`_async_http.post`) | none | `except:` → default result | on full response |

## 2. Two new async helpers (mirror call()/stream())

### `acall(messages, *, base_url, model, api_key="", max_tokens=500, temperature=0.7, timeout=120, enable_thinking=False, extra_kwargs=None, http=None, raise_on_error=False) -> str`
Async sibling of `call()`. Builds headers/payload via the shared `_build_request`, POSTs with an injected `httpx.AsyncClient` (`http=`; makes + closes its own if None, like `codec_vision.describe_async`), parses via `extract_content` (content→reasoning, `<think>` strip). `raise_on_error` mirrors `call()`. **No retries loop needed** (the agents sites do a single POST). Queue acquire/release stays at the **call site** — `codec_llm` never touches the semaphore.

### `astream(messages, *, base_url, model, api_key="", max_tokens=500, temperature=0.7, timeout=120, enable_thinking=False, extra_kwargs=None, http=None, keepalive=False) -> AsyncIterator`
Async sibling of `stream()`: `http.stream("POST", url, …)` + `aiter_lines` → `data:`/`[DONE]` parse → yield raw deltas (+ `KEEPALIVE` sentinel when `keepalive=True`).

**Contract difference (documented):** `astream()` **propagates** exceptions (it does NOT wrap the stream in try/except), whereas sync `stream()` never-raises. Rationale: `astream`'s sole consumer (`voice._stream_qwen`) already wraps the loop in `try/except → yield "Sorry…"` + `finally: release(CRITICAL)`, and a silent failure there is a UX regression (voice would say nothing). Letting exceptions propagate preserves voice **exactly** — the only change at the call site is swapping the inline `self._http.stream` loop for `astream`; voice keeps its per-token `<think>` strip, its error string, and its queue release.

## 3. Per-site migration

- **voice `_stream_qwen`** → keep `acquire(CRITICAL)` / `try` / `except → "Sorry…"` / `finally release`; inner loop becomes `async for token in codec_llm.astream(messages, base_url=QWEN_BASE_URL, model=QWEN_MODEL, max_tokens=max_tokens, temperature=0.7, enable_thinking=False, extra_kwargs={"top_p":0.9,"frequency_penalty":0.8,**LLM_KWARGS}, http=self._http): token = re.sub(r"<think>…</think>","",token); if token: yield token`. (No keepalive — voice is a local WS, not the Cloudflare tunnel.)
- **agents `Agent.run`** → keep `acquire(MEDIUM)`/`finally release`; the POST+parse becomes `response = await codec_llm.acall(messages, base_url=_qwen_base(), model=_qwen_model(), max_tokens=4000, temperature=0.7, enable_thinking=self.thinking, http=_async_http, raise_on_error=True)`; the `except: return "LLM error: {e}"` stays (catches `LLMError`). (The redundant post-call `<think>` re.sub can stay — harmless.)
- **agents research-refiner** → `text = await codec_llm.acall(..., http=_async_http)` (default never-raise → `""` → the existing parse falls back to defaults, matching its `except`). Add `_qwen_base()` if not already present (it is, from 2c… actually that was agent_plan/runner — `codec_agents` may need its own; check at impl).

## 4. Test plan
- `tests/test_llm_async.py`: `acall` — success returns content (fake async client), `<think>` strip, `raise_on_error` raises `LLMError` on non-200/exception, default returns `""`; uses an injected fake `http`. `astream` — yields raw deltas from a fake async line-stream, stops at `[DONE]`, `keepalive=True` yields `KEEPALIVE` on empties, and **propagates** a raised error (no swallow). All driven via `asyncio.run` + a fake async client (no `pytest-asyncio`).
- Regression: `test_agents_crews` (112) stay green; voice tests stay green; full suite — 23 baseline, **zero new**.
- Source invariants: voice/agents call `codec_llm.astream(`/`codec_llm.acall(`; the inline `_async_http.post(_qwen_url()` / `self._http.stream(` chat sites gone (queue calls remain at the call site).

## 5. Risk + rollback
- **Blast radius:** `codec_llm` (+2 async fns, default-injected client) + voice `_stream_qwen` + 2 agents sites. Queue handling unchanged (stays at call site). Voice exact-parity via the propagate contract; agents covered by the crew suite.
- **Rollback:** single-commit revert.
- **This closes A-12 streaming.** After: only the skills tranche remains.

## 6. Open question for you (Mickael) — scope

- **Option 1 (recommended):** **split** — `acall()` + the 2 agents sites now (cleaner, mirrors `call()`, crew suite covers it); `astream()` + voice next (the propagate contract + per-token strip + spoken error get focused streaming tests). Keeps voice and crews in separate diffs (blast-radius isolation on two hot paths).
- **Option 2:** **both** — `acall()` + `astream()` + all 3 sites in one PR. Closes A-12 streaming in one go, but puts voice + crews in the same diff.
- **Option 3:** **astream + voice first** (then acall + agents).

I recommend **Option 1**. Pick one and I'll implement (TDD) + open the PR.
