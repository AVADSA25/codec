# PR-3E-dashboard — A-12: migrate the dashboard `chat/completions` text sites (DESIGN)

**Status:** IMPLEMENTED — **Option 1**. Migrated `_qwen_chat_classify` (clean), the `command` Flash fallback (2 error strings → 1; documented), and the crew-report writer (`raise_on_error=True` preserves the raise). Removed 2 now-dead `import requests as rq` (+ a placeholder-less f-string) → net-negative ruff. 5 tests (`tests/test_dashboard_llm.py`); full suite 1469 passing, zero new. **Chat stream + non-stream fallback pair deferred** to a `codec_llm.stream(keepalive=)` PR (shared payload + Cloudflare keepalive).
**Finding:** A-12 (continuation). The dashboard has the most-tangled remaining LLM sites — the hottest user-facing path.
**Wave:** 3.

---

## 1. The 5 dashboard TEXT sites (vision sites are A-11, untouched)

| # | line | what | independent? | nuance |
|---|---|---|---|---|
| 1 | `2465` `_qwen_chat_classify` | auto-escalate classifier | ✅ yes | **CLEAN** — returns `""` on failure (→ caller falls back); gains `<think>` strip before its JSON parse (improvement). No UX change. |
| 2 | `974` `command` Flash fallback | quick chat reply | ✅ yes | own `payload`/`headers_llm`; has **2 distinct error strings** ("returned an error" / "empty response") that `call()` would collapse to one (like the voice-reply collapse in tranche 1). |
| 3 | `3082` crew-report writer | crew → Google Doc | ✅ yes | `enable_thinking=True`; **raises** on failure today (→ outer except). Needs `raise_on_error=True` to preserve that, else it writes an empty report. |
| 4 | `2890` chat non-stream fallback | `chat_completion` | ❌ no | **shares the `payload` dict** (2782–2793) with the stream path → migrating it alone would break the shared-payload guarantee. Migrate **with** #5. |
| 5 | `2842` chat stream `_stream_gen` | `chat_completion` | ❌ no | **keepalive blocker**: sends `: keepalive` on empty "thinking" chunks to hold the Cloudflare tunnel; `codec_llm.stream()` swallows empty deltas, so migrating as-is drops keepalive. Needs a `stream()` keepalive affordance first. |

## 2. The two blockers (why this isn't one clean swap)

- **Keepalive (site 5).** `codec_llm.stream()` yields only non-empty deltas. During a long think the dashboard would produce no output → Cloudflare idle-drop. Fix: add an opt-in keepalive to `stream()` — e.g. a `KEEPALIVE` sentinel yielded every Nth empty chunk when `keepalive=True` (default off → `codec_session.qwen_stream` unaffected). The dashboard turns the sentinel into `: keepalive\n\n`.
- **Shared payload (sites 4+5).** The stream and non-stream branches of `chat_completion` build ONE `payload` dict and one `headers`. They must migrate together so the request shape can't drift between them.

## 3. Recommended split

- **THIS PR (3 independent non-stream sites):** classifier (`2465`, clean) + Flash (`974`, error-string collapse documented) + crew report (`3082`, `raise_on_error=True` preserves the raise). Each gets `codec_llm.call`. Behavior deltas documented + tested.
- **NEXT PR (chat-handler pair):** add `codec_llm.stream(keepalive=…)`, then migrate `_stream_gen` (site 5, feeding raw tokens through the existing `SkillTagBuffer`) **and** the non-stream fallback (site 4) together off the shared payload. This is the single riskiest LLM site (the live chat stream) → its own design + streaming tests.

## 4. Behavior deltas (this PR, documented)
- **Flash (974):** the two distinct error strings collapse to one "Sorry, the AI didn't respond — please try again." (parity-equivalent UX; same precedent as the voice-reply collapse). All three sites gain `<think>` stripping via `codec_llm`.
- **Crew report (3082):** migrated with `raise_on_error=True` so an LLM failure still raises into the existing outer handler (no empty-report regression). `kwargs` passed unfiltered (matches the original `payload.update(kwargs)`, which lets kwargs override `enable_thinking`).
- **Classifier (2465):** `enable_thinking=False` + `<think>` strip now applied → cleaner JSON for `_classify_chat_message` (improvement; failure still returns `""`).

## 5. Test plan (this PR)
- `tests/test_dashboard_llm.py`: each of the 3 sites calls `codec_llm.call` with the right `base_url`/`model`/tuning (monkeypatched), maps success through, and degrades correctly (classifier → `""`; Flash → fallback string; crew report → `raise_on_error` propagates). Source invariants: the 3 inline POSTs gone, vision + chat-pair POSTs still present.
- Full suite: expect the 23 known-baseline failures, **zero new**. No `skills/` touched → no manifest regen.

## 6. Open question for you (Mickael) — scope

- **Option 1 (recommended):** the **3 independent non-stream sites** (classifier + Flash + crew report) now. Defer the chat-handler stream+fallback pair to a dedicated PR that adds `codec_llm.stream(keepalive=)` (the live chat stream is the #1 UX path — it deserves its own streaming tests).
- **Option 2:** **everything** — the 3 non-stream sites **plus** `codec_llm.stream(keepalive=)` + the chat stream/fallback pair, in one PR. Bigger, and puts the riskiest LLM site in the same diff.
- **Option 3:** **classifier only** — the single zero-nuance clean win; defer Flash + crew report + the pair.

I recommend **Option 1**. Pick one and I'll implement (TDD) + open the PR.
