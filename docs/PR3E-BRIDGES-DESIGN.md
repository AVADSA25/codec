# PR-3E-bridges — A-12: migrate the iMessage + Telegram `call_llm` text sites (DESIGN)

**Status:** IMPLEMENTED. Both `call_llm` text sites route through `codec_llm.call` (default never-raise → `None`-contract preserved; `chat_template_kwargs` filtered from kwargs). Removed a now-dead `import re` from `codec_imessage`. 8 new tests (`tests/test_llm_bridges.py`); full suite 1431 passing, zero new; zero net-new ruff.
**Finding:** A-12 (continuation). The two outbound bridges hand-roll the same OpenAI `chat/completions` text call.
**Wave:** 3. Smallest, lowest-risk tranche — never-raise is the *default* `codec_llm.call` behavior, so this is a near-mechanical swap.

---

## 1. The two sites (identical shape)

- `codec_telegram.call_llm` (text site `:482`)
- `codec_imessage.call_llm` (text site `:343`)

Both build `messages` (system + last-8 history + user), set headers (Bearer iff `llm_cfg["api_key"]`), POST a payload (`max_tokens=1500`, `temperature=0.7`, `stream=False`, `chat_template_kwargs.enable_thinking=False`, plus `llm_cfg["kwargs"]` **minus** any `chat_template_kwargs`), then:
- `{"error": …}` or no `choices` → log + **return `None`**
- parse content, strip `<think>`, `return content if content else None`
- `requests.exceptions.Timeout` → `None`; any other `Exception` → `None`

**Contract: `call_llm` returns `None` on every failure/empty** — the caller degrades gracefully (no reply / fallback). This is the *opposite* of the 2c sites: bridges WANT silent best-effort, so `codec_llm.call`'s default never-raise is exactly right.

The vision sites (`telegram:519`, `imessage:393`, via `llm_cfg["vision_url"]`) are **A-11** (codec_vision), NOT this tranche — left untouched.

## 2. Migration (each site)

Replace the headers + payload + `try/except` block with:
```python
import codec_llm
extra = {k: v for k, v in llm_cfg["kwargs"].items() if k != "chat_template_kwargs"}
content = codec_llm.call(
    messages, base_url=llm_cfg["base_url"], model=llm_cfg["model"],
    api_key=llm_cfg["api_key"], max_tokens=1500, temperature=0.7,
    timeout=120, extra_kwargs=extra,
)
return content if content else None
```
- **`None`-contract preserved**: `codec_llm.call` returns `""` on error / no-choices / timeout / exception / empty → `content if content else None` → `None`. Exact parity for the caller.
- **`enable_thinking=False` preserved**: `extra` strips `chat_template_kwargs` (same as the original) so the explicit flag in `_build_request` isn't overridden.
- **`<think>` strip** now handled by `codec_llm`.
- **Auth parity**: `api_key=llm_cfg["api_key"]` → Bearer iff non-empty (same as original).

## 3. Behavior deltas (minor, documented)
- **Logging granularity**: the original logged distinct messages ("LLM error" / "LLM no choices" / "LLM timeout" / "LLM call failed"); `codec_llm` logs one generic warning. Same observable behavior (`None` returned) — only the log string differs.
- Everything else is byte-parity for the caller.

## 4. Test plan
- `tests/test_llm_bridges.py`: for each bridge — `call_llm` returns the content on success (monkeypatched `codec_llm.call`); returns **`None`** when `codec_llm.call` returns `""` (graceful-degradation contract); passes `base_url`/`model`/`api_key` through and `extra_kwargs` with `chat_template_kwargs` **filtered out**. Source invariants: each calls `codec_llm.call(`, and `/chat/completions` count drops to **1** (only the vision site remains).
- Full suite: expect the 23 known-baseline failures, **zero new**. No `skills/` touched → no manifest regen.

## 5. Risk + rollback
- **Blast radius:** 2 functions in 2 outbound bridges. `None`-contract preserved → callers unaffected. Inbound stays PWA-only (unchanged).
- **Rollback:** single-commit revert.

## 6. After bridges — remaining A-12
dashboard (4 non-stream + the `[SKILL:…]` stream tag-machine), voice `_stream_qwen` + agents `Agent.run` (async `astream()` + queue at the call site), skills tranche (translate/fact_extract/create_skill/skill_forge).
