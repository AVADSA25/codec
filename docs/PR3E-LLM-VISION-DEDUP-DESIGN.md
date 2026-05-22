# PR-3E — LLM-call + vision dedup (DESIGN)

**Status:** DRAFT — awaiting approval. No code changed yet (per AGENTS.md §11).
**Findings:** A-11 (vision dup, MEDIUM) + A-12 (51→45 `chat/completions` sites, MEDIUM, audit-flagged **large**).
**Wave:** 3. This is the **hottest code path in the repo** (every feature calls an LLM), so it gets design-first + a phased plan.

---

## 1. Reality check (what the trace found)

- **`codec_llm_proxy.py` is NOT a proxy.** It's a priority *queue* (semaphore) — its own docstring: *"Does NOT proxy HTTP — callers still make their own requests."* So A-12's "the module already exists, just add `call()`/`stream()`" is **inaccurate**: there is no call/stream helper to reuse. A-12 means **building a new canonical call API** (which uses the queue internally).
- **45 `chat/completions` sites** (was 51; some removed in earlier PRs) across **three shapes**: sync `requests`, async `httpx`, and streaming SSE — with copy-pasted headers, `Authorization: Bearer`, `enable_thinking=False`, `<think>` stripping, and `choices[0].message.content`/`.reasoning` parsing.
- **A-11 vision = 3 divergent impls:**
  - `codec.py` `vision_describe`/`_gemini_vision`/`_local_vision` — **sync** (`requests`), Gemini-flash → local-Qwen-VL fallback, PNG.
  - `codec_voice._analyze_screenshot` — **async** (`httpx`), Gemini → local fallback, JPEG.
  - `codec_session.screenshot_ctx` — **sync**, local-Qwen-VL **only** (no Gemini), PNG, with inline screencapture.

## 2. Why this is high-risk

These are the call paths behind voice, chat, vision, agents, bridges. A subtle
regression in payload shape, `<think>` stripping, streaming chunk parsing,
timeout, or error handling silently degrades a core feature. Blast radius =
everything. So: **small, behavior-parity tranches with mocked-HTTP tests that
assert payload/response equivalence — never a 45-site big-bang.**

## 3. Recommended plan — split A-11 from A-12, phase A-12

The audit lumps A-11 + A-12 as "PR-3E," but they're independent and A-12 is
"large." Recommended:

### This PR (PR-3E) — **A-11 vision dedup only** (contained, ~3 consumers)
- New **`codec_vision.py`**: the single canonical vision helper.
  - `describe_sync(image_b64, prompt, *, mime="image/png", max_tokens=800) -> str`
  - `async describe_async(image_b64, prompt, *, mime="image/jpeg", max_tokens=500, http=None) -> str`
  - Both: Gemini-flash (if `VISION_PROVIDER=="gemini"` and key present) → local-Qwen-VL fallback, reading config (`vision_base_url`, `vision_model`, `get_gemini_api_key`). One place to change the model / provider / API shape.
- Migrate the 3 consumers to delegate:
  - `codec.py`: `vision_describe` → `codec_vision.describe_sync`; drop `_gemini_vision`/`_local_vision`.
  - `codec_voice._analyze_screenshot` → `await codec_vision.describe_async(..., http=self._http)`.
  - `codec_session.screenshot_ctx` → `codec_vision.describe_sync` (gains Gemini fallback it lacked — a minor *improvement*, behaviorally a superset; flagged in the PR).
- **Tests:** mock HTTP; assert Gemini-first + local-fallback, payload shapes, mime handling, empty-on-failure. ~8 tests.
- **Risk:** medium-low (vision is less hot than chat; 3 well-understood sites). Behavior parity except session gaining the Gemini fallback (documented).

### Follow-on (PR-3E-2+, separate design) — **A-12 chat/completions**
- Build **`codec_llm.py`**: `call(messages, *, model, temperature, max_tokens, priority, **kw) -> str` (sync) + `stream(...)` (SSE generator) + an async variant. Centralizes headers, `enable_thinking`, `<think>` strip, `choices/reasoning` parse, queue-slot acquisition, timeouts, error shape.
- Migrate the 45 sites **in small tranches by subsystem**, each its own PR with parity tests: e.g. (1) codec.py + codec_session, (2) dashboard, (3) voice, (4) agents/agent_plan/agent_runner, (5) bridges (telegram/imessage), (6) misc (compaction/self_improve/watcher/textassist/dictate). Each tranche is independently revertable.
- This is deliberately **not** in this PR — 45 hot-path sites in one diff is unreviewable + high-risk.

## 4. API / schema changes
- New module `codec_vision.py` (this PR). No on-disk schema, no config changes
  (reuses existing `vision_*` config keys + `get_gemini_api_key`).
- `codec.py` loses `_gemini_vision`/`_local_vision` (internal); `vision_describe`
  kept as a thin delegate for any external caller.
- (A-12's `codec_llm.py` is a later PR.)

## 5. Test plan (this PR — A-11)
- New `tests/test_vision_dedup.py`:
  - `describe_sync`: Gemini path returns text; Gemini failure → local fallback;
    both fail → `""`; correct payload shape per provider; mime respected.
  - `describe_async`: same matrix with a mocked httpx client.
  - Source invariants: codec.py no longer defines `_gemini_vision`/`_local_vision`;
    voice + session call `codec_vision`.
- Regression: full suite (expect the 23 known failures, zero new). No `skills/`
  touched → no manifest regen.
- Manual (Mac Studio): voice "look at my screen" + a chat screenshot still
  describe correctly via both providers.

## 6. Risk + rollback
- **Blast radius (this PR):** 3 files edited + 1 new module. Vision only — chat
  paths untouched.
- **Rollback:** single-commit revert restores the inline impls. No persistent
  state touched.
- A-12 risk is deferred to its own phased PRs (each small + revertable).

## 7. Open question for you (Mickael)
**Q: scope of PR-3E?**
- **Option 1 (recommended):** PR-3E = **A-11 vision dedup only**, now. A-12
  (chat/completions) becomes its own phased effort with a separate design doc
  (build `codec_llm.call/stream` + migrate sites tranche-by-tranche). Keeps every
  PR reviewable + low-risk on the hottest path.
- **Option 2:** PR-3E = A-11 **+** A-12's canonical `codec_llm` API **+** the
  first chat tranche (codec.py + codec_session). Bigger, riskier single PR.
- **Option 3:** Do A-12 API first (no A-11 yet).

I recommend **Option 1**. Pick one and I'll implement + open the PR
(chat-review-then-merge — hot path).
