# PR-3E-chat-stream — A-12: codec_llm.stream(keepalive=) + migrate the dashboard chat stream+fallback pair (DESIGN)

**Status:** IMPLEMENTED. Added `codec_llm.KEEPALIVE` sentinel + `codec_llm.stream(keepalive=False)`; migrated the `chat_completion` stream path (`codec_llm.stream(keepalive=True)` → `SkillTagBuffer`) and the non-stream fallback (`codec_llm.call(raise_on_error=True)`) off shared `_common` args. Removed the now-dead `import requests as rq` + `headers` in the handler. 3 keepalive tests + chat-handler source invariants; `codec_session.qwen_stream` unaffected (keepalive off by default); full suite 1473 passing, zero new; zero net-new ruff. **Dashboard A-12 complete** (`/chat/completions` literals left = 5 vision-only).
**Finding:** A-12 (final dashboard piece). The `chat_completion` stream + non-stream fallback share one `payload` and were deferred from PR-3E-dashboard for two reasons (keepalive + shared payload).
**Wave:** 3. **The single hottest user-facing LLM site** (live chat stream) → keepalive TDD'd hard; behavior preserved exactly.

---

## 1. The two blockers (from the dashboard design §2)

1. **Keepalive.** `_stream_gen` sends `: keepalive\n\n` on empty "thinking" chunks (every 10th) so Cloudflare doesn't idle-drop the tunnel during a long think. `codec_llm.stream()` swallows empty deltas → migrating as-is loses keepalive.
2. **Shared payload.** The stream and non-stream branches build ONE `payload` (model/messages/max_tokens=28000/temp=0.7/top_p=0.9/frequency_penalty=1.1/+kwargs/chat_template_kwargs={enable_thinking: thinking}). They must migrate together so the request shape can't drift.

## 2. The `codec_llm.stream(keepalive=)` affordance

- New module sentinel **`codec_llm.KEEPALIVE = object()`**.
- `stream(..., keepalive: bool = False)`: on an **empty** content delta, when `keepalive=True`, count empties and `yield KEEPALIVE` every 10th (1st, 11th, 21st…) — matching the dashboard's `_empty_count % 10 == 1`. Non-empty deltas still `yield <str>`.
- **Default `keepalive=False` → existing consumers unchanged** (`codec_session.qwen_stream` never sees the sentinel; it still does `for delta in stream(): full += delta`).
- Return type widens `Iterator[str]` → `Iterator[Any]` (str content or the KEEPALIVE sentinel).

## 3. Dashboard migration (both sites, shared args)

Build the common args ONCE so the two paths can't drift:
```python
_extra = {"top_p": 0.9, "frequency_penalty": 1.1,
          **{k: v for k, v in kwargs.items() if k != "chat_template_kwargs"}}
_common = dict(base_url=base_url, model=model, api_key=api_key,
               max_tokens=28000, temperature=0.7, enable_thinking=thinking,
               extra_kwargs=_extra, timeout=300)
```

**Stream path** (`_stream_gen`): the inline `rq.post(stream=True)` + `iter_lines` + `data:`/`[DONE]` parse → `codec_llm.stream(messages, **_common, keepalive=True)`. The `SkillTagBuffer` (`buf`) + `_resolve_skill_tag` stay; the loop becomes:
```python
buf = SkillTagBuffer(_resolve_skill_tag)
try:
    for item in codec_llm.stream(messages, **_common, keepalive=True):
        if item is codec_llm.KEEPALIVE:
            yield ": keepalive\n\n"
            continue
        for s in buf.feed(item):
            yield _frame(s)
    for s in buf.finish():          # flush after stream end ([DONE] or close)
        yield _frame(s)
    if buf.visible_chars == 0:
        yield _frame(<blank-bubble fallback>)
    yield "data: [DONE]\n\n"
except Exception as e:
    yield f"data: {json.dumps({'error': str(e)})}\n\n"
```

**Non-stream fallback**: `rq.post(...)` + `r.json()` parse → `codec_llm.call(messages, **_common, raise_on_error=True)` (the original raised on `r.json()` failure → outer `except` → 500; `raise_on_error=True` preserves that). Keep the `### FINAL ANSWER:` strip + post-LLM `[SKILL:]` regex routing (codec_llm already strips `<think>`).

## 4. Behavior deltas (documented)
- **Closed-without-`[DONE]`**: `codec_llm.stream()` returns on both `[DONE]` and an abnormal close (indistinguishable to the caller), so the dashboard now runs `finish()` + the blank-bubble check + `data: [DONE]` after *either* ending. Previously a close-without-`[DONE]` only flushed the buffer (no `[DONE]` frame, no fallback). Net: the frontend now always gets a terminating `[DONE]`, and a fully-empty abnormal stream gets the graceful fallback instead of a hanging bubble — an improvement.
- Everything else byte-parity: keepalive cadence (every 10th empty), `<think>` strip (now inside `codec_llm`), `[SKILL:]` resolution (unchanged `SkillTagBuffer` + `_resolve_skill_tag`), the 28000/0.7/0.9/1.1 tuning, `enable_thinking=thinking` (frontend toggle wins), the non-stream 500-on-failure.

## 5. Test plan
- `tests/test_llm_stream.py` (extend): `stream(keepalive=True)` yields `KEEPALIVE` on the 1st (+11th) empty chunk and content as `str`; `keepalive=False` (default) yields **no** sentinel for the same empties (guards the qwen_stream contract); content-only stream is unaffected by the flag.
- Regression: `codec_session.qwen_stream` tests + `test_chat_stream` (SkillTagBuffer) stay green; full suite — 23 known-baseline failures, **zero new**.
- Source invariants: the chat-handler stream + non-stream inline `rq.post(.../chat/completions...)` are gone (the only `/chat/completions` literals left are the 5 vision sites → count 5); `codec_llm.stream(` + `codec_llm.KEEPALIVE` + `codec_llm.call(` present in the handler.
- No `skills/` touched → no manifest regen.

## 6. Risk + rollback
- **Blast radius:** `codec_llm.stream` (+`keepalive`/`KEEPALIVE`, default-off so no other consumer changes) + the two `chat_completion` branches. The risky logic (keepalive cadence) is unit-tested in `codec_llm`; the tag-machine is already `SkillTagBuffer` (tested).
- **Rollback:** single-commit revert.
- **This closes A-12 for the dashboard.** Remaining A-12 after: voice `_stream_qwen` + agents `Agent.run` (async `astream()`), skills tranche.
