# PHASE 1 AUDIT A — CODE QUALITY + ARCHITECTURE

**Date:** 2026-05-17
**Auditor:** general-purpose agent (Audit A)
**Scope:** Static code-quality + architecture across the entire CODEC Python codebase (`codec*.py`, `routes/`, `skills/`, `whisper_server.py`).

## Summary
- **Total findings:** 22
- **Critical:** 1, **High:** 6, **Medium:** 12, **Low:** 3
- **Key themes:**
  - **Massive duplication around the entry-point/dispatch pipeline** — `codec.py`, `skills/codec.py`, `codec_core.py`, `codec_session.py` all contain near-identical screenshot/Qwen/TTS/SQL-init logic. The single biggest source of LOC waste is `build_session_script` (~270 LOC string-builder) duplicated AND orphaned in `codec.py` while the canonical deprecated version lives in `codec_core.py`.
  - **Silent error swallowing under a misleading log template.** The exact string `log.warning(f"Non-critical error: {e}")` appears 21 times across `codec_dashboard.py`, `codec_keyboard.py`, `routes/skills.py` and labels real failures (config-load failures, pgrep failures, subprocess cleanup failures) as "non-critical" — making them invisible during operation.
  - **Two parallel skill-loading systems.** `codec_core.loaded_skills` + `codec_core.load_skills` (legacy, used by `codec.py` and one `codec_dashboard.py` endpoint) coexist with the canonical `codec_dispatch` + `SkillRegistry` (used by chat, voice, MCP, telegram, imessage, session). They scan the same `SKILLS_DIR` independently — drift risk + 2x startup cost on machines with custom skills.
  - **One DEAD module (`skills/codec.py`, 460 LOC)** and several dead functions (`build_session_script` in codec.py: 273 LOC; `run_session_module` in codec_agent.py: 33 LOC; `_live_overlay_script_appkit_DISABLED` in codec_dictate.py: ~90 LOC; `codec_keyboard.start_keyboard_listener` flow: 398 LOC total module unused by production).

## Methodology
- **Linters:** Tried `ruff check .` and `bandit -r . -ll`. **Both blocked by sandbox permissions** — could not execute. `flake8` and `mypy` are not installed. `vulture` is installed but also sandbox-blocked. `pytest --collect-only` blocked.
- **Manual:** Read CLAUDE.md cover-to-cover. Read every Python file > 200 LOC at least partially (codec.py and codec_core.py in full; codec_dashboard.py, codec_voice.py, codec_agents.py, codec_observer.py, codec_session.py, codec_imessage.py, codec_telegram.py, codec_audit.py, codec_dictate.py, codec_config.py partially with targeted reads).
- **Pattern search via grep:** confirmed exception patterns, inline imports, SQL UPDATE shapes, `chat/completions` call-site duplication, `screencapture` call sites, `build_session_script` references, Pydantic model usage, config key reads.
- **Cross-reference:** Verified each pre-audit finding (P-1 through P-10) by direct read.
- **Test inventory:** 57 test files / 900 `def test_*` definitions (grep count).

## Findings

### A-1 — codec.py contains 273 LOC of dead `build_session_script` [HIGH]
**Location:** `codec.py:240-513`

> **Closed by PR-3A** (branch `fix/pr3a-delete-dead-build-session-script`). Deleted the orphan `build_session_script` from `codec.py` (current lines 242–516, 274 LOC incl. the `# ── BUILD SESSION SCRIPT` header). **Verify-first evidence:** zero invocations of codec.py's copy anywhere in the repo — the only `build_session_script(` *calls* are in `tests/test_session_execution.py` and they target `codec_core.build_session_script` (imported as `core`). The live session-launch path is `run_session_in_terminal` (imported `codec.py:47`, called post-deletion at ~line 398). The deprecated `codec_core.build_session_script` is **kept** (still exercised by `test_session_execution.py`); only the never-called duplicate was removed. `test_security.py:183` references the name in a docstring but asserts on `codec_agent.py` content — unaffected. Full suite after deletion: 1325 passing, 23 pre-existing failures (down from 24 — `test_all_skills_have_run` now passes), zero new. codec.py 1170→894 LOC.

**Description:** `build_session_script(safe_sys, session_id)` builds a multi-hundred-line Python script via `L.append("...")` strings. The function is **defined but never called anywhere in the codebase**. `codec.py:671` calls `run_session_in_terminal(safe_sys, session_id, task)` (from `codec_agent.py`) instead. A second copy of the same function lives in `codec_core.py:262-624` (430 LOC), marked `@deprecated` via `warnings.warn`. Tests in `tests/test_session_execution.py` exercise the deprecated `codec_core` version only, not `codec.py`'s orphan.
**Impact:** ~273 LOC of dead code in the most-read file in the repo. Investors / contributors reading `codec.py` to understand the agent pipeline will burn time on a function that does nothing. The dead code also embeds API keys/config (lines 250-251) as Python source — same anti-pattern that the migration to `codec_agent.run_session_in_terminal` was supposed to fix.
**Recommended fix:** Delete `codec.py:240-513`. Confirm tests still pass (they don't reference this copy). Then schedule removal of `codec_core.build_session_script` once `codec_core.close_session` etc. consumers are clean.
**Effort:** small (delete ~270 lines)

### A-2 — `skills/codec.py` is a 460-LOC stale fork of the main entry point, NOT a skill [HIGH]
**Location:** `skills/codec.py` (all 460 lines)

> **Closed by PR-3A** (branch `fix/pr3a-delete-dead-build-session-script`). Deleted `skills/codec.py` entirely (460 LOC). **Verify-first evidence:** the live registry `codec_skill_registry._extract_metadata('skills/codec.py')` returns `None` (no `def run` → skipped); the legacy `codec_core.load_skills` exec-imports then skips it (no `SKILL_TRIGGERS`/`run`); it was never in `skills/.manifest.json`; and no module does `from skills.codec import …`. The tk/`mainloop` text the audit might flag is a *string template* inside `show_overlay` (a subprocess overlay), not top-level code, and `main()` is `__name__`-guarded — so import is side-effect-free. Removing it fixed the pre-existing `test_all_skills_have_run` failure (it was the only skill file lacking `run()`). Manifest unchanged at 76 skills (it was never counted).

**Description:** This file has no `SKILL_NAME`, no `SKILL_TRIGGERS`, no skill-shape `run(task, app, ctx)` function (verified by grep). It's an old copy of `codec.py` (v1.0 banner says `v1.0`, top-level `def main()`, on_press/on_release handlers, F13/F18 logic). Both `codec_core.load_skills` (line 121) and `codec_skill_registry.SkillRegistry.scan` require the module to have `SKILL_TRIGGERS` AND `run` exported, so this file is NEVER loaded as a skill. It's dead.
**Impact:** Confuses anyone exploring `skills/` to understand the plugin system. Also contains a separately-evolved variant of `screenshot_ctx`, `dispatch`, `wake_word_listener` — bug fixes applied to `codec.py` (e.g. the 2026-04-16 double-TTS bug fix) don't propagate.
**Recommended fix:** Delete `skills/codec.py`. There's no version history to preserve that isn't already in `codec.py` or git.
**Effort:** small (delete file)

### A-3 — 21 instances of `log.warning(f"Non-critical error: {e}")` mask real failures [HIGH]
**Location:** 21 sites across `codec_dashboard.py`, `codec_keyboard.py`, `routes/skills.py`. Counts by file: `codec_keyboard.py:9` (lines 72, 82, 89, 251, 256, 319, 333, 345, 383); `codec_dashboard.py:9` (lines 374, 382, 1060, 1163, 1674, 1813, 1859, 2652, 2686); `routes/skills.py:3` (lines 99, 223, 226).

> **Closed by PR-3B** (branch `fix/pr3b-silent-except-cleanup`). All 20 surviving `"Non-critical error: {e}"` sites rewritten per the three-way categorization (the 21st was in `routes/skills.py`, removed when PR-1B deleted `/api/forge`+`/api/save_skill`). **codec_keyboard.py (9):** all subprocess/file/overlay teardown → narrowed to `OSError`/`subprocess.SubprocessError` + demoted to `log.debug` with specific messages ("Recording process cleanup failed", "Failed to remove short audio file", etc.); the one wake-word per-iteration handler kept `log.warning` with a clear "utterance skipped" message. **codec_dashboard.py (9):** 6 config-loads narrowed to `(OSError, json.JSONDecodeError)` with "Config read failed; proceeding without overrides"; `pgrep` narrowed to `(OSError, subprocess.SubprocessError)` (alive=False fallback preserved); vibe-preview narrowed to `OSError`; tempfile cleanup → `log.debug`. **routes/skills.py (2):** trigger-parse narrowed to `(ValueError, SyntaxError, OSError)` (recoverable, lists with no triggers); skills-dir listing failure escalated to `log.error` + a `skills_list_failed` audit emit (it's a real failure, not "non-critical"). The listener auto-restart loop (`codec_keyboard.py:392`) makes the narrowing safe. 0 `"Non-critical error"` strings remain in production code; full suite 1325 passing, zero new failures.
**Description:** This exact string template wraps `except Exception as e:` blocks that catch genuinely problematic conditions: `pgrep` failures (line 382), `config.json` parse failures (line 374, 1060, 1163), DB write failures, subprocess cleanup failures inside `codec_keyboard.py:wake_word_listener`. The phrase "non-critical" is misleading — a config parse failure that swallows silently is not non-critical; it means the user's overrides aren't applied and they have no idea why.
**Impact:** Real bugs hide behind this template. Operators see no errors in logs while features quietly degrade. Investor-grade software does NOT label config parse failures as "non-critical." (P-2 verified: 21 instances, wider than codec.py-only — full file list above.)
**Recommended fix:** For each site, decide: (a) the error is genuinely recoverable → keep `log.warning` but say WHAT recovered ("Config parse failed; using defaults: %s", not "Non-critical error: %s"); (b) the error is unexpected → escalate to `log.error` and emit an audit event via `log_event(..., outcome='error', level='error')`; (c) the error path is true cleanup (deleting a temp file that may not exist) → demote to `log.debug` AND narrow the except to the actual expected exception (FileNotFoundError, OSError).
**Effort:** medium (~3-4 hours of grep + reading + 21 individual fixes)

### A-4 — codec.py defines orphan `check_skills_ranked` + `check_skill` duplicating `codec_dispatch.check_skill` [MEDIUM]
**Location:** `codec.py:51-66` vs `codec_dispatch.py:29-45`
**Description:** Two parallel skill-loading systems coexist:
- **Legacy:** `codec_core.loaded_skills` (list) + `codec_core.load_skills()` (eager import of every skill via `importlib`), consumed by `codec.py:check_skills_ranked` and `codec_dashboard.py:3480` (the `/api/cortex/skills` endpoint).
- **Canonical:** `codec_dispatch.registry` (lazy AST scan) + `codec_dispatch.check_skill`, consumed by chat (`codec_dashboard.py:2355`), voice (`codec_voice.py`), MCP (`codec_mcp.py`), session (`codec_session.py:857`), telegram (`codec_telegram.py:210`), imessage (`codec_imessage.py:272`).

Both scan `SKILLS_DIR` independently, so a skill file is loaded twice in different ways (eager + lazy). CLAUDE.md §4 documents only the canonical lazy system; the legacy path is undocumented.
**Impact:** Drift risk (a fix to one trigger-matching code path doesn't reach the other), 2x startup cost, undocumented divergent behavior between voice path and chat/MCP path. Investor-grade red flag: "single source of truth" promised in CLAUDE.md but violated by the main entry-point itself.
**Recommended fix:** Migrate `codec.py:_dispatch_inner` to use `codec_dispatch.check_skill` + `run_skill` directly (this would also automatically wrap voice-path skill calls in `run_with_hooks` per Phase 1 Step 2, which the legacy path bypasses). Migrate `codec_dashboard.py:cortex_skills` to read from `codec_dispatch.registry`. Then delete `codec_core.loaded_skills`, `codec_core.load_skills`, `codec_core.run_skill`.
**Effort:** medium (touches 3 files + needs a careful test pass on the voice-skill code path)

### A-5 — `_dispatch_inner` is a 200-LOC monolithic function with mixed concerns [MEDIUM]
**Location:** `codec.py:545-744`
**Description:** Single function does: skill dispatch loop, draft detection + queueing, memory injection (5 distinct injection points), system-prompt build, voice-session state mutation, LLM HTTP call (with inline `import requests as _llm_req`), inline SQLite UPDATE, two follow-up calls into `CodecMemory`, TTS dispatch, AppleScript notification dispatch, error handling for all of the above. 200 lines, ≥4 levels of nesting in places, single try/except wrapping the LLM call (line 689-743). Pre-audit P-5 understated this as "~50 LOC" — the actual count is ~200.
**Impact:** Hard to test, hard to modify safely, hard to read. The 5 different memory injection mechanisms (lines 615-649) duplicate logic that already lives in `_enrich_messages` (`codec_dashboard.py:1930-2093`) and `codec_voice.generate_response` (`codec_voice.py:716-754`). Three independent implementations of "build a system prompt with memory context" — bugs fixed in one don't reach the others.
**Recommended fix:** Extract pure helpers — `_build_voice_system_prompt(task) -> str`, `_persist_voice_turn(session_id, task, answer, rid) -> None`, `_call_qwen_voice(messages) -> str | None`. Reduce `_dispatch_inner` to a flow of named calls (skill → draft → memory → LLM → persist → TTS). Aim for <80 LOC.
**Effort:** medium

### A-6 — `chat_completion` is a 440-LOC function with deep async/streaming nesting [MEDIUM]
**Location:** `codec_dashboard.py:2561-3003`
**Description:** Single async route handler that handles: slash-command dispatch, pre-LLM skill hijack, image multi-modal routing, document-attachment detection, web search enrichment, URL fetching, memory enrichment, LLM call with streaming, `[SKILL:...]` tag buffering + resolution, post-LLM skill routing. ~440 LOC. The inner `_stream_gen` async function (lines ~2830-2972) buffers tokens char-by-char looking for `[SKILL:` tags — interesting code but jammed inline.
**Impact:** Same as A-5 — review/test/modify burden. The tag-buffering logic has its own subtle correctness model (e.g., behavior on partial prefix match, 5000-char safety cap) that would benefit from being a tested unit.
**Recommended fix:** Extract the streaming + tag-buffering loop into its own helper module (e.g. `codec_chat_stream.py` with a class `SkillTagBuffer` that takes tokens and yields emit decisions). The result is a much easier path to test and modify the skill-tag protocol.
**Effort:** medium-large

### A-7 — `Agent.run` (codec_agents.py) is a 225-LOC ReAct loop with extensive inline logic [MEDIUM]
**Location:** `codec_agents.py:385-612`
**Description:** Core agent loop: builds system prompt, parses `TOOL:`/`FINAL:` text protocol, validates tool name + input, computes hook-wrapped tool execution under `copy_context`, runs Phase 1 Step 3 stuck detection in an executor, has special-case post-processing for `google_docs_create` URL fabrication. Many concerns interleaved. ~225 LOC.
**Impact:** Each Phase adds another wedge of behavior to this loop. The veto/stuck/destructive paths are interleaved with the LLM-call path. Hard to add new agent behaviors without risking regressions in the others.
**Recommended fix:** Pull out: (1) `_parse_action(text) -> tuple[Literal["tool","final"], dict]` — the regex protocol; (2) `_validate_tool_call(name, input) -> str|None` — the validation block; (3) `_execute_tool_with_hooks(tool, input) -> str` — the executor + run_with_hooks + stuck detection.
**Effort:** medium

### A-8 — `codec_keyboard.py` (398 LOC) is dead in production [MEDIUM]
**Location:** `codec_keyboard.py` (entire file). `start_keyboard_listener` defined at line 25.
**Description:** `start_keyboard_listener` is never imported except by `tests/test_full_product_audit.py` and `tests/test_transcript.py` (which only verifies callable existence and imports `clean_transcript`). Production keyboard handling lives inline in `codec.py:1015-1097` (`on_press`/`on_release`) and `codec.py:1141-1148` (`keyboard.Listener` startup). Two implementations of wake-word + double-tap detection.
**Impact:** ~398 LOC of unused engine module. Confusing for anyone trying to understand "where does F18 get handled."
**Recommended fix:** Either (a) migrate `codec.py:on_press/on_release` to use `start_keyboard_listener` (so the module becomes the canonical implementation), or (b) delete `codec_keyboard.py` and move `clean_transcript` to `codec_config.py` where it's already imported from. Option (a) is the bigger improvement because the codec_keyboard implementation appears to be the cleaner one.
**Effort:** medium

### A-9 — `_live_overlay_script_appkit_DISABLED` in codec_dictate.py is explicit dead code [LOW]
**Location:** `codec_dictate.py:161-250`

> **Closed by PR-3G** (branch `fix/pr3g-small-misc-cleanup`). Deleted the ~90-LOC `_live_overlay_script_appkit_DISABLED` function (name ends in `_DISABLED`, referenced by nothing — verified by grep). Available in git history if ever re-enabled.
**Description:** Function name ends in `_DISABLED`. Docstring says "kept but disabled because on some macOS builds it renders nothing visible when launched from a pm2-managed background process." ~90 LOC of NSPanel/AppKit code referenced by nothing.
**Impact:** Confuses the file. If the tkinter fallback works, this code shouldn't be in `main`.
**Recommended fix:** Delete and rely on git history. If kept for future re-enablement, move to a `docs/snippets/` Python file outside the load path.
**Effort:** small

### A-10 — `run_session_module` in codec_agent.py is unused (33 LOC) [LOW]
**Location:** `codec_agent.py:46-78`

> **Closed by PR-3G** (branch `fix/pr3g-small-misc-cleanup`). Deleted the unused `run_session_module` (33 LOC; grep confirmed only the definition existed). `run_session_in_terminal` (the live launcher) kept. The now-orphaned `import sys` was also removed.
**Description:** Defined alongside `run_session_in_terminal` but never called by anything in the codebase (grep -rn confirms only the definition line). Looks like a leftover from the Terminal-vs-subprocess design exploration.
**Impact:** Minor dead code.
**Recommended fix:** Delete.
**Effort:** small

### A-11 — `vision_describe` / `_gemini_vision` / `_local_vision` duplicated between codec.py and codec_voice.py [MEDIUM]
**Location:** `codec.py:69-111` (full implementation) and `codec_voice.py:659-714` (`_analyze_screenshot` — different shape but same Gemini Flash + local Qwen VL fallback pattern, same hardcoded `gemini-2.0-flash` model name and OpenAI vision-message shape). `codec_session.py:202-238` has yet another inline `screenshot_ctx` + vision call.
**Impact:** When the user upgrades Gemini model, switches vision provider, or fixes a vision-API regression, they have to touch 3 places. Investor-grade: violates "single source of truth" principle stated in CLAUDE.md §10 and codec_core.py docstring.
**Recommended fix:** Move `vision_describe` + provider routing into `codec_core.py` (or a new `codec_vision.py`) as the single canonical helper. Update `codec_voice.py._analyze_screenshot` to call it; update `codec_session.py:screenshot_ctx` to call it.
**Effort:** medium

### A-12 — 51 separate `chat/completions` HTTP call sites with copy-pasted payload shapes [MEDIUM]
**Location:** Sample sites: `codec.py:702`, `codec_dashboard.py:980,1076,1215`, `codec_voice.py:180,196,208,213`, `codec_session.py:215,278,307`, `codec_agents.py:51`, `codec_agent_plan.py:239`, `codec_agent_runner.py:148`, `codec_compaction.py:78`, `codec_self_improve.py:238`, `codec_telegram.py:471,508`, `codec_imessage.py:341,391`, `codec_textassist.py:33`, `codec_dictate.py:492`, `codec_watcher.py:86,182`. Total: 51 occurrences via `grep -rn "chat/completions"`.
**Impact:** Each site repeats: headers build, `Authorization: Bearer {api_key}` formatting, `{Content-Type: application/json}`, payload assembly with `chat_template_kwargs.enable_thinking=False`, `<think>` stripping, `try/except` for `r.json()` shape (`choices[0].message.content` or `.reasoning` fallback). Many also re-implement streaming SSE parsing. When the Qwen-3.6 upgrade landed, this is exactly the kind of change that needs to be applied in 20+ places.
**Recommended fix:** Add `codec_llm_proxy.call(messages, **kwargs)` and `codec_llm_proxy.stream(messages, **kwargs)` as the single canonical API (the module already exists at `codec_llm_proxy.py`, only 130 LOC, only used by codec_voice + codec_agents). Migrate all 51 sites over the course of the Phase 1 hardening. As a first step, just covering the 5 sites in codec.py + codec_dashboard.py + codec_session.py would remove ~80% of the most-edited duplication.
**Effort:** large

### A-13 — `_DANGEROUS_PATTERNS` re-declared in codec_dashboard.py:3779 (divergent from codec_config.DANGEROUS_PATTERNS) [HIGH]
**Location:** `codec_dashboard.py:3779-3794` vs `codec_config.py:125-157`

> **Already closed by PR-2C** (#52, D-10) — verified during PR-3G. The divergent dashboard blocklist existed only to serve `/api/execute`, which PR-2C deleted entirely along with `_DANGEROUS_PATTERNS`, `_is_command_safe`, and `execute_terminal`. Only a deletion-marker COMMENT remains in `codec_dashboard.py`. No live divergent blocklist exists; shell execution now routes solely through the `terminal` skill gated by `codec_config` (single source of truth). Pinned by `tests/test_dead_code_3g.py::test_a13_dashboard_dangerous_patterns_not_live` + the existing `tests/test_python_exec.py` D-10 assertions.
**Description:** `codec_dashboard.py` declares its own ~14-pattern regex blocklist for `/api/execute` terminal access (`rm -rf`, `mkfs`, `dd`, `shutdown`, etc.). `codec_config.py` declares ~60-pattern substring blocklist used by `is_dangerous()` (which checks shell commands generated by agents — `codec_agents.py:286`, `codec.py` via the session script). The lists differ: e.g. `codec_dashboard.py` blocks `pkill` and `sudo`; `codec_config.py` also blocks them BUT additionally catches `rm ` (any plain rm), `find -delete`, `:(){ :|:& };:` fork bomb, `curl|bash` etc. The dashboard's `/api/execute` endpoint is the more attacker-facing surface and has the smaller list.
**Impact:** Security drift between dashboard route and agent shell tool. A future change to `codec_config.DANGEROUS_PATTERNS` doesn't reach the dashboard. The dashboard's narrower list also misses fork bombs and pipe-to-bash patterns that codec_config catches. (CLAUDE.md §10 lists `_HTTP_BLOCKED` as a "don't-touch zone" but the dashboard's own list is undocumented and unprotected.)
**Recommended fix:** Replace `codec_dashboard.py:3803:_is_command_safe` with a call to `codec_config.is_dangerous(command)`. If the dashboard needs regex matching specifically (for word-boundary on `kill -9`), expose a regex-compiled version from `codec_config` so there's still one source list.
**Effort:** small (10 minutes + smoke test the `/api/execute` endpoint)

### A-14 — `codec.py` defines local `close_session` that shadows the imported one [LOW]
**Location:** `codec.py:43` imports `close_session` from `codec_core`. `codec.py:516` defines a local `close_session` that's the one actually used.

> **Closed by PR-3G** (branch `fix/pr3g-small-misc-cleanup`). Dropped `close_session` from the `from codec_core import (...)` line — it was shadowed by codec.py's own local `close_session()` def, making the import dead. Kept the local def (the one actually used). `codec_core.close_session` is untouched (other modules still import it).
**Description:** Both implementations do the same thing (kill PID, unlink files, AppleScript Terminal close). The shadowing makes the import on line 43 dead.
**Impact:** Confuses readers; subtle source of bugs if behaviors drift.
**Recommended fix:** Drop the import (line 43); keep the local one OR drop the local one and the import.
**Effort:** small

### A-15 — codec_config.py has no `config_version` field; no migration story for `~/.codec/config.json` [MEDIUM]
**Location:** `codec_config.py:1-319` (full file)
**Description:** `~/.codec/config.json` is loaded as a flat dict (line 9-18). When new fields are added (Phase 1/2/3 added `step_budget`, `stuck`, `observer`, `shift_report`, `ask_user`, …), there's no version stamp or migration handler. The Phase-doc tunables are documented in CLAUDE.md §10 "don't-touch zones" but the config file itself doesn't know its schema generation. P-10 confirmed.
**Impact:** Future schema changes risk silently breaking old user configs OR being silently ignored on machines that haven't been re-run through `setup_codec.py`. Investor-grade red flag: "if I upgrade the app, what happens to my config?" has no formal answer.
**Recommended fix:** Add `CONFIG_SCHEMA_VERSION = 1` in codec_config.py. On `load_config()`, check for `cfg["config_version"]`; if missing, treat as version 0 and write version 1 back. Run any future migrations via a small `_migrate_v0_to_v1(cfg) -> cfg` ladder. The first migration would just stamp the version.
**Effort:** small-medium

### A-16 — `codec.py` ignores `WAKE_PHRASES` config and hardcodes `_WAKE_KEYWORDS` with a duplicate entry [MEDIUM]
**Location:** `codec.py:973`

> **Closed by PR-3C** (branch `fix/pr3c-wire-config-knobs`). The inline `_WAKE_KEYWORDS` list (with the duplicate `"kodak"`) was replaced by a module-level deduped `_WAKE_KEYWORD_DEFAULTS` tuple + a testable `_is_wake_utterance(text)` helper. **Semantic correction over the audit's literal suggestion:** `WAKE_PHRASES` are multi-word phrases (`"hey codec"`, and the generic `"hey"`) while `_WAKE_KEYWORDS` are single fuzzy ASR tokens substring-matched against Whisper output — a naive swap would either miss homophone variants or false-wake on a bare `"hey"`. So `_is_wake_utterance` matches (1) homophone keyword substrings (legacy behavior, deduped) OR (2) configured `WAKE_PHRASES` substrings **guarded to ≥5 chars** so generic short entries can't false-trigger on ordinary speech. A user who customizes `wake_phrases` now actually gets picked up (e.g. `"jarvis online"`). `WAKE_PHRASES` is now imported into codec.py + used. 7 tests in `tests/test_config_wiring.py` (dedup, homophone match, custom-phrase match, no-false-wake on ordinary speech + bare "hey", empty-input safety, source invariant).
**Description:** `_WAKE_KEYWORDS = ["codec", "codex", "kodak", "kodec", "kodak", "co-dec", "caudec", "codag"]` — note `"kodak"` appears twice. `codec_config.WAKE_PHRASES` (defaults to `['hey codec', 'hey', 'okay codec', 'hey codex', 'hey coda', 'hey queue']`) is never read inside the wake-word listener in codec.py. The legacy `skills/codec.py:335` (A-2 dead module) is the only file that actually uses `WAKE_PHRASES`. Tests reference `WAKE_PHRASES` (`tests/test_state_machine.py:135`) as if it were the active list.
**Impact:** A user who customizes `wake_phrases` in `~/.codec/config.json` finds it has zero effect because codec.py uses a hard-coded list. The "kodak" duplicate is harmless but signals copy-paste origin.
**Recommended fix:** Replace the hardcoded list with `from codec_config import WAKE_PHRASES`. Strip duplicates with `set()`. Add a unit test that customizes WAKE_PHRASES and verifies the wake-word matcher picks it up.
**Effort:** small

### A-17 — `DRAFT_KEYWORDS_CFG` declared in codec.py:34 but never used [LOW]
**Location:** `codec.py:34`

> **Closed by PR-3C** (branch `fix/pr3c-wire-config-knobs`) via option (a), the user-respecting fix. `codec_core.is_draft` now checks the built-in `DRAFT_KEYWORDS` AND a lazily-read `_user_draft_keywords()` (from `cfg.get("draft_keywords", [])`, lowercased, malformed entries tolerated) — so a user's `draft_keywords` override in `~/.codec/config.json` now actually takes effect. The dead `DRAFT_KEYWORDS_CFG = _cfg.get("draft_keywords", [])` line in codec.py was removed (the wiring lives in codec_core now, reaching all is_draft callers, not just codec.py). 4 tests in `tests/test_config_wiring.py` (built-in match, user-configured match, malformed-config tolerance, dead-line removed).
**Description:** `DRAFT_KEYWORDS_CFG = _cfg.get("draft_keywords", [])` — declared at module load, never referenced again. The `is_draft()` function (imported from `codec_core`) uses its own internal `codec_core.DRAFT_KEYWORDS` list. So user-supplied `draft_keywords` overrides don't reach the production code path.
**Impact:** Documented config knob (per `setup_codec.py` and config example) has no effect.
**Recommended fix:** Either (a) wire `DRAFT_KEYWORDS_CFG` into `is_draft()` by having `codec_core` accept user overrides, or (b) delete line 34 and remove `draft_keywords` from `config.json.example`. Option (a) is the user-respecting fix.
**Effort:** small

### A-18 — 9 of 10 Pydantic response models in codec_dashboard.py are declared but unused [LOW]
**Location:** `codec_dashboard.py:37-86`

> **Closed by PR-3G** (branch `fix/pr3g-small-misc-cleanup`) via the delete option. Removed the 9 unused models (`StatusResponse`, `SkillItem`, `ConversationItem`, `ScheduleItem`, `ServiceStatus`, `CommandRequest`, `ChatRequest`, `AgentRunRequest`, `ErrorResponse`) — each had only its own definition (no `response_model=` wiring, no instantiation). `HealthResponse` (the one genuinely wired) is kept. The now-unused `from typing import Optional, List` line was removed (Optional already imported above; List was only used by the deleted models). The full typed-routes upgrade (wiring models to every route for richer OpenAPI) remains a future option, but the misleading phantom-typing dead code is gone now. `/api/health` still returns 200 (test pins it).
**Description:** `HealthResponse`, `StatusResponse`, `SkillItem`, `ConversationItem`, `ScheduleItem`, `ServiceStatus`, `CommandRequest`, `ChatRequest`, `AgentRunRequest`, `ErrorResponse` — only `HealthResponse` is referenced via `response_model=HealthResponse` (lines 3756, 3757). The other 9 exist but no route uses them.
**Impact:** Misleading: anyone reading the file's prelude assumes "this is a typed FastAPI app." It isn't. Routes return `dict` or `JSONResponse({...})`. The Pydantic models pollute the file's mental model with phantom typing.
**Recommended fix:** Either commit to typed responses (wire each model into the matching route's `response_model=`) or delete the unused models. The typed-routes path is the investor-grade upgrade and the OpenAPI docs at `/docs` would gain real schemas.
**Effort:** medium (wiring all 9 to their routes is a couple hours; documenting body shapes for POST endpoints is the bigger win)

### A-19 — codec_telegram + codec_imessage duplicate try_skill, _load_dispatch, call_llm, save_to_memory [MEDIUM]
**Location:**
- `try_skill`: `codec_telegram.py:220` vs `codec_imessage.py:282` — character-identical logic and skip list (`open_terminal`, `run_command`, `vibe_code`, `deep_chat`, `memory_search`, `ask_mike_to_build`).
- `_load_dispatch`: `codec_telegram.py:204-217` vs `codec_imessage.py:266-279` — near-identical lazy-load with try/except shim.
- `call_llm`: `codec_telegram.py:438` vs `codec_imessage.py:303` — different signature (telegram has no `sender`; imessage does) but ~95% identical: same payload shape, same `chat_template_kwargs`, same `<think>` strip, same try/except, same headers build.
- `save_to_memory`: imessage line 902, telegram line 555 — both call `CodecMemory.save("telegram"|"imessage", ...)`.
- `process_message`: telegram lines 579-786 (~207 LOC), imessage lines 935-1052 (~117 LOC) — different in flow but share trigger detection, attachment handling, skill-fallback-to-LLM, intent detection patterns.
**Impact:** Every fix to the outbound-bridge LLM call has to be applied twice. The two bridges have already drifted: telegram has audio transcription with Gemini fallback; imessage has goal tracking and intent classification that telegram doesn't.
**Recommended fix:** Extract a `codec_bridges.py` with `BridgeRouter` class that takes `(channel: str, sender: str, text: str, attachments: list)` and runs the canonical try_skill → detect_intent → call_llm → save_to_memory pipeline. Telegram and iMessage become ~50-LOC adapters that translate platform-specific message shapes into the canonical input.
**Effort:** large (but a significant investor-grade unlock — turns 2 ad-hoc bridges into a documented "add a channel" extension surface for future WhatsApp/Discord per CLAUDE.md §1)

### A-20 — Inline `import sqlite3` + raw `c = sqlite3.connect(DB_PATH)` for one-off UPDATE bypasses CodecMemory [MEDIUM]
**Location:** `codec.py:716-721` (the inline UPDATE) plus `codec_dashboard.py:1408` (qchat_db lazy global) plus `codec_dashboard.py:1714` (vibe_db lazy global) plus dozens of `c.execute(...)` direct SQL elsewhere.
**Description:** `codec.py:_dispatch_inner` opens a fresh sqlite3 connection, does ONE `UPDATE sessions SET response=? WHERE id=?`, commits, closes. No WAL pragma, no busy_timeout. The very next try-block (lines 723-728) does use `CodecMemory()` properly. So the same handler mixes raw SQL and the abstraction. (P-3 refuted: the SQL is `WHERE id=?` not `WHERE task=? AND app=? ORDER BY id DESC LIMIT 1` — no broken ORDER BY+LIMIT in UPDATE — but the inline-sqlite anti-pattern is real.)
**Impact:** Two consequences. (1) Under concurrent load (Phase 3 agent runner writing alongside voice handler writing) the codec.py UPDATE can hit "database is locked" because it skips the WAL+busy_timeout setup that `routes/_shared.get_db()` applies (`routes/_shared.py`). (2) Bypasses any future hook/audit you'd add to CodecMemory.
**Recommended fix:** Replace `codec.py:716-721` with `from codec_memory import CodecMemory; CodecMemory().update_session_response(rid, answer[:500])` (adding the method to CodecMemory if needed). Audit `codec_dashboard.py` for the same anti-pattern elsewhere (qchat_db / vibe_db are already lazy-cached globals with pragmas set — those are OK).
**Effort:** small for codec.py; medium if you want to clean up all inline sqlite users

### A-21 — `AGENT_NAME` in codec_config.py is declared but never read [LOW]
**Location:** `codec_config.py:25` (`AGENT_NAME = cfg.get('agent_name', 'C')`)

> **Closed by PR-3C** (branch `fix/pr3c-wire-config-knobs`). Deleted the dead `AGENT_NAME = cfg.get('agent_name', 'C')` constant (verified no importer — only its own declaration appeared in a repo-wide grep; the `agent_name` config key is still read inline where needed via `cfg.get('agent_name', ...)`). `ASSISTANT_NAME` + `USER_NAME` (both genuinely used) are kept. 2 tests in `tests/test_config_wiring.py` (AGENT_NAME removed, the other two kept).
**Description:** Greps for `AGENT_NAME` show only the declaration; no consumer. The config key `agent_name` IS read elsewhere via `cfg.get("agent_name", ...)` directly (e.g. `codec_agent.py:40`, `codec_slash_commands.py:345`, `codec_dashboard.py:555`) — so the module-level constant is genuinely dead.
**Impact:** Minor — only 1 line. But it triggers the "dead config key" investigation. Note `ASSISTANT_NAME` (line 26) IS used (`codec_voice.py:300`, `codec_watcher.py:26`), `USER_NAME` (line 27) IS used. Only `AGENT_NAME` is dead at module level.
**Recommended fix:** Either delete `AGENT_NAME = cfg.get('agent_name', 'C')` or replace the inline `cfg.get('agent_name', ...)` usages with `from codec_config import AGENT_NAME`.
**Effort:** small

### A-22 — One genuinely silent broad except in critical-path code: codec_dashboard.py vision-write [MEDIUM]
**Location:** `codec_dashboard.py:1114-1115`, `codec_dashboard.py:2998`, several others.

> **Partially closed by PR-3B** (branch `fix/pr3b-silent-except-cleanup`). The 20 A-3 sites — A-22's named HIGH-confidence subset — are all fixed (see A-3 closure). The clearest standalone HIDING_BUG the audit named — the post-LLM `[SKILL:name:query]` tag resolution (audit's old line 2998, now `codec_dashboard.py:3033`) — was a bare `except Exception: pass` that let a raw `[SKILL:...]` tag leak into the user's chat with zero footprint; now it logs `log.warning` + emits a `post_llm_skill_tag_failed` audit (behavior unchanged — tag stays, chat still returns). **Deliberately deferred (PR-3B-2):** the audit's "~50 HIDING_BUG" was an *estimate*, not an enumerated list, and the other named sites turned out to be **legitimate graceful-degradation paths** on inspection — e.g. `codec_voice.py:692` (Gemini vision → local Qwen fallback, already prints + falls back), `:750` (optional observer injection, explicitly "non-fatal"), `:773` (TTS returns None, caller handles it). The audit's named `codec_dashboard.py:1114` vision-DB-save site no longer exists in that form (code evolved). Aggressively narrowing working fallback paths risks regressions for no clear bug, so the residual A-22 sweep is split to a focused per-site survey (PR-3B-2) rather than rushed — protecting the "never break working code" invariant.
**Description:** Categorization survey of 814 `except Exception` instances + 30 bare `except:` outside generated code:
- **LEGITIMATE_NONCRITICAL** (majority, ~80%): teardown of subprocess proc, tempfile cleanup, optional-import probing, "config file might not exist" loads, `log.debug` already in the handler. Examples: `codec.py:850-867` (recording-process cleanup with `log.debug`), `codec_core.py:191-194` (process-alive check), `codec_audit.py:_write` (audit must never raise by design).
- **HIDING_BUG** (minority, ~50 instances): line 1114 (`save vision response to DB`) catches `Exception` and just logs a warning, but image-message persistence failure means the chat panel will be silently missing the image. Line 2998 (post-LLM skill routing) silently swallows `Exception` while resolving a `[SKILL:name:query]` tag — if the skill blows up, the user gets a raw tag in their chat. Several `codec_voice.py` exception-handlers also fall in this bucket (line 691, 712, 750, 772, 921, etc.).

The 21 `"Non-critical error: {e}"` instances (A-3) are a SUBSET of HIDING_BUG.
**Impact:** Real bugs in image persistence, skill execution, vision retry logic, post-LLM tag resolution all silently degrade with no audit footprint.
**Recommended fix:** Audit pass — for each HIDING_BUG site, decide: narrow the except (catch the actual `OSError` / `KeyError` / `requests.Timeout` you expect), AND emit a `log_event(..., outcome='error', level='error')` audit so operators see it. The current 30-day audit retention is the existing observability surface; use it.
**Effort:** medium

## Pre-audit finding verification

| ID | Status | Evidence |
|----|--------|----------|
| P-1 | **REFUTED** | grep `"' + AGENT_NAME + '"` and `'sys_p = "You are ' +'` across all `*.py` returns nothing in codec.py. `skills/codec.py:194` has `sys_p = "You are CODEC, a JARVIS-class AI assistant..."` as a plain string literal (no broken concatenation). The "concatenation-instead-of-f-string" pattern alleged in P-1 does not exist anywhere. `AGENT_NAME` is itself a dead module-level constant (see A-21). |
| P-2 | **CONFIRMED (broader)** | 21 instances total (not ~15), spread across `codec_keyboard.py` (9), `codec_dashboard.py` (9), `routes/skills.py` (3). NONE in `codec.py` itself. See A-3 for full list. |
| P-3 | **REFUTED** | `grep -rEn "UPDATE.*ORDER BY.*LIMIT"` returns no UPDATE statements with ORDER BY+LIMIT. `pwa_dispatch` does not exist in any module (`grep -rEn "pwa_dispatch"` returns 0). The one UPDATE in codec.py is at line 718: `UPDATE sessions SET response=? WHERE id=?` — uses primary key, not the alleged broken pattern. The inline-sqlite anti-pattern IS real but unrelated to ORDER BY+LIMIT. See A-20. |
| P-5 | **PARTIAL** | `dispatch` is actually ~12 LOC (lines 532-543), the real monolith is `_dispatch_inner` at lines 545-744 (~200 LOC, not ~50). `main()` is ~55 LOC (1099-1153, not ~150). The big monoliths actually live elsewhere: `chat_completion` 440 LOC (A-6), `Agent.run` 225 LOC (A-7), `_dispatch_inner` 200 LOC (A-5), `_pipeline` 178 LOC, `_enrich_messages` 165 LOC, `detect_intent` 135 LOC, `wake_word_listener` 118 LOC, `_run_agent` ~250 LOC, `process_message` (telegram) 207 LOC, `Session.run` ~90 LOC. P-5 was directionally correct (yes, monoliths exist) but had the specific numbers wrong. |
| P-6 | **CONFIRMED** | `codec.py:6` imports `sqlite3` at module level (so not strictly inline). However, `codec.py:716-721` opens a fresh `sqlite3.connect(DB_PATH)` and runs an inline UPDATE — exact anti-pattern P-6 describes (just at line 717, not "inside dispatch"). The same handler 7 lines later uses `CodecMemory` properly, so the inline raw-SQL is genuinely orphaned style. See A-20. Inline `import` statements DO exist in codec.py: line 235 (`import traceback`), 575 (`from codec_memory import CodecMemory`), 615 (same), 634 (`from codec_memory_upgrade import ...`), 690 (`import requests as _llm_req`), 743 (`import traceback`), 774 (`import fitz`), 895 (`import requests as req_wake`), 901 (`import sounddevice as sd`), 939 (`import wave, numpy as np`), 1100 (`from codec_logging import setup_logging`). Some are intentional (lazy-load `fitz` because it's PyMuPDF, a heavy dep); some look like leftover refactoring. |
| P-10 | **CONFIRMED** | `codec_config.py:1-319` has no `CONFIG_SCHEMA_VERSION`, no `config_version` field in any `cfg.get(...)`, no migration logic. See A-15. |

## Open Questions for Mickael

1. **A-1 + A-2 deletion safety:** Is there any out-of-tree consumer (a Mac app bundle build, an installer test, a private fork) that imports `codec.py:build_session_script` or `skills/codec.py`? Both are unused in the public repo's import graph. Confirming no out-of-repo callers would let us delete ~730 LOC in one commit.

2. **A-4 skill-loader unification:** `codec.py:_dispatch_inner` uses the legacy `loaded_skills` system specifically because that's what supports the "fall through to next match if skill returns None" pattern (`codec.py:558-596`). `codec_dispatch.run_skill` ALSO supports fall-through (`codec_dispatch.py:60-111`). Is there any other reason to keep two systems, or can we migrate codec.py to `codec_dispatch` and delete the duplicate?

3. **A-13 dashboard dangerous-pattern list:** The dashboard's narrower list misses fork bombs (`:(){ :|:& };:`), pipe-to-bash (`curl|bash`), and many sudo-equivalents. Is the `/api/execute` endpoint intended to be more permissive than the agent shell tool (different threat model), or is the divergence accidental? If accidental, replacing with `codec_config.is_dangerous` is the right fix. If intentional, the dashboard's looser list should still be derived from a documented subset of codec_config's list, not redeclared.

4. **A-19 bridge unification:** The two bridges are at very different feature levels. iMessage has goal tracking, restaurant decider, accountability check-in, deep-report-to-Google-Doc. Telegram has voice briefing generation. Were these features intentionally siloed by channel, or are they all candidates for cross-channel? A unified `BridgeRouter` would have to decide whether to surface every feature on every channel.

5. **A-22 silent excepts:** ~50 sites need narrowing + audit emits. Should this be done as one cleanup PR, or incrementally as each module is touched for other reasons? The latter avoids a 50-site mega-diff but takes 6+ months to converge.

6. **Test count vs CLAUDE.md claim:** CLAUDE.md §9 says "600+ tests collected (live count via `pytest --collect-only`)." Grep of `def test_*` returns 900 functions across 57 files. Either there are skipped/parametrized tests (likely) or the README claim is conservative. Sandbox blocked actual `pytest --collect-only`; an unblocked run would give the exact number.

## Files reviewed

Full reads:
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/CLAUDE.md` (architecture source of truth)
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/codec.py` (1153 LOC)
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/codec_config.py` (319 LOC)
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/codec_agent.py` (140 LOC)
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/codec_core.py` (623 LOC, key sections)
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/codec_dispatch.py` (113 LOC)
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/skills/codec.py` (460 LOC)

Partial reads (targeted):
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/codec_dashboard.py` (3859 LOC, sections 1-900, 1930-2095, 2349-2430, 2561-3003, 3477-3853)
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/codec_voice.py` (1528 LOC, sections 623-1100, 1217-1530)
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/codec_agents.py` (1750 LOC, sections 280-630, function index)
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/codec_observer.py` (927 LOC, prelude + docstrings)
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/codec_session.py` (984 LOC, function index + run())
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/codec_imessage.py` (1140 LOC, sections 266-365, 740-880, 935-1050)
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/codec_telegram.py` (835 LOC, function index, sections 200-260)
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/codec_dictate.py` (663 LOC, function index, sections 96-280)
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/codec_audit.py` (488 LOC, sections 1-490)
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/codec_proactive.py` (367 LOC, header)
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/codec_mcp.py` (338 LOC, sections 1-200)
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/routes/agents.py` (734 LOC, sections 1-100)
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/codec_alerts.py` (function index)

Grep surveys covering ALL Python files in the repo:
- All `except Exception` / `except:` patterns
- All `chat/completions` HTTP call sites
- All `import sqlite3` sites
- All `sys_p = "You are ' +` patterns (P-1)
- All `Non-critical error` patterns (P-2)
- All `UPDATE sessions` patterns (P-3)
- All `build_session_script` / `run_session_in_terminal` references
- All `loaded_skills` / `codec_dispatch` consumers
- All `WAKE_PHRASES` / `_WAKE_KEYWORDS` references
- All `AGENT_NAME` / `ASSISTANT_NAME` / `USER_NAME` references
- All `screencapture` and `generativelanguage.googleapis` call sites
- All `_live_overlay_script_appkit_DISABLED` / `build_session_script` / `run_session_module` references
- All `codec_keyboard` / `codec_marketplace` / `codec_proactive` import edges
