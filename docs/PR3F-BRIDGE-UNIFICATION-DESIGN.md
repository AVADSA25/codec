# PR-3F — bridge unification (A-19) (DESIGN)

**Status:** IMPLEMENTED — **Option 1** (scoped). New `codec_bridges.py` holds the 4 shared helpers (`load_dispatch`, `try_skill`, `call_llm(channel, …)`, `save_to_memory(channel, conv_id, …)`); `codec_telegram` + `codec_imessage` now `from codec_bridges import try_skill` + keep thin channel-injecting wrappers for `call_llm`/`save_to_memory` (call sites unchanged). `process_message` left per-bridge (drifted). Removed telegram's now-dead `sqlite3` import; updated the 2 stale #71 source-invariants. 10 new tests (`tests/test_bridges.py`); full suite 1502 passing, zero new; zero net-new ruff. **Audit-A complete.**
**Finding:** A-19 (MEDIUM, "large") — `codec_telegram` + `codec_imessage` duplicate `try_skill`, `_load_dispatch`, `call_llm`, `save_to_memory`, and parts of `process_message`.
**Wave:** 3 (last Audit-A item). Outbound bridges = security-sensitive, working code → small + behavior-preserving, **don't force the drifted paths together**.

---

## 1. Reality check (what changed since the audit was written)

- **The churny dup is already gone.** The audit's headline pain — "every fix to the outbound-bridge LLM call has to be applied twice" — was resolved in **PR-3E-bridges (#71)**: both `call_llm`s now call `codec_llm.call` (the only remaining diff is the one-line persona: "via Telegram" vs "via iMessage").
- **The two `process_message` flows have intentionally DRIFTED** (per the audit's own impact note): telegram has audio transcription + Gemini fallback + daily-briefing; imessage has goal-tracking + intent classification. Forcing them into one `BridgeRouter` pipeline (the audit's "large" recommendation) means reconciling those divergent features — **high risk of breaking a working bridge**.
- The genuinely-identical, **low-churn** leftovers: `try_skill` (character-identical incl. the `_SKIP` set), `_load_dispatch` (near-identical lazy shim), `save_to_memory` (both `CodecMemory.save(channel, …)`), and the `call_llm` wrapper (now ~persona + `codec_llm.call`).

## 2. The risk in the audit's full vision

`BridgeRouter` unifying `process_message` is the **large + risky** part: it would have to absorb telegram-only (audio/Gemini/briefing) and imessage-only (goals/intent) behavior into one flow without regressing either. That contradicts "never break working code" for two live outbound channels. The dedup payoff there is **low** (the flows are genuinely different), the risk **high**.

## 3. Recommended scope — extract the 4 shared helpers, leave `process_message`

New **`codec_bridges.py`**:
- `load_dispatch()` — the lazy `codec_dispatch` import shim (one copy).
- `try_skill(text) -> (name|None, result|None)` — identical skill match + the `_SKIP` set.
- `call_llm(channel, text, llm_cfg, conversation_history=None, system_prompt_override=None) -> str|None` — the canonical bridge LLM call (`codec_llm.call` + the persona chosen by `channel`, `None`-contract preserved, `chat_template_kwargs` filtered). iMessage's extra `sender` arg is dropped (it was unused in the LLM call).
- `save_to_memory(channel, user_text, reply)` — the `CodecMemory.save` pair.

`codec_telegram` + `codec_imessage` import these; **their `process_message` (and telegram's audio / imessage's goals) stay put** — `process_message` calls the shared `try_skill`/`call_llm`/`save_to_memory` instead of local copies. Net: real dedup of the shared surface + `codec_bridges.py` becomes the documented "add a channel" seed (CLAUDE.md §1 WhatsApp/Discord), WITHOUT touching the drifted flows.

## 4. Test plan (recommended scope)
- `tests/test_bridges.py`: `try_skill` honors the `_SKIP` set + returns `(name, result)`; `call_llm("telegram"/"imessage", …)` builds the right persona + maps `codec_llm.call` → `None` on empty (monkeypatched); `save_to_memory` calls `CodecMemory.save` with the channel. Source invariants: both bridges import from `codec_bridges`; the duplicated `try_skill`/`_load_dispatch` defs are gone.
- Regression: bridge import smoke + full suite — 23 known-baseline failures, **zero new**. No `skills/` touched → no manifest regen.

## 5. Risk + rollback
- **Blast radius (recommended scope):** new `codec_bridges.py` + the 2 bridges' helper defs replaced by imports. `process_message` flows untouched → telegram audio + imessage goals can't regress. Inbound stays PWA-only (unchanged).
- **Rollback:** single-commit revert.

## 6. Open question for you (Mickael) — scope

- **Option 1 (recommended):** extract the **4 shared helpers** into `codec_bridges.py`; both bridges import them; `process_message` stays per-bridge. Real dedup + the extension seed, low risk. (~the safe 70% of A-19.)
- **Option 2 (full BridgeRouter):** also unify `process_message` into one pipeline. Matches the audit's vision + the "add a channel" unlock, but must reconcile the **drifted** telegram/imessage flows → highest risk on two working outbound channels.
- **Option 3 (skip / document-closed):** A-19's churny pain (`call_llm`) is already fixed by #71; the rest is low-churn. Mark A-19 "largely addressed; full unification deferred — flows intentionally drifted," and move on (no code change).

I lean **Option 1** — it banks the safe dedup + seeds `codec_bridges.py` without risking the drifted flows. Pick one and I'll implement (or, for Option 3, just update the audit docs).
