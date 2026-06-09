# CODEC Voice Modes — Flash / Default / Think

**Date:** 2026-06-09 · **Status:** APPROVED + IMPLEMENTED (operator decisions §8: 1=email-send in v1, 2=keep fast path, 3=voice+UI pills). Tests: tests/test_voice_modes.py (11) + tests/test_gmail_send.py (6).

## 1. What & why

CODEC's live voice-to-voice chat gets three modes, same interface:

| Mode | Promise | How |
|---|---|---|
| **Flash** ⚡ | Snappiest possible turn-around for quick back-and-forth | Trim everything that adds prefill/decode latency |
| **Default** | Exactly today's behavior | Untouched code path — zero regression |
| **Think** 🧠 | Live multi-step tool-calling while you talk: lights, music, web, calendar, email — with spoken progress while you wait | Route through a voice-scoped `codec_agents.Agent` loop with a curated skill allowlist |

Why: the live demo ("CODEC, kill the lights and put on some jazz" → both happen, narrated) and real daily utility (calendar+email+web by voice). The interface stays the current `/voice` page; think mode just *extends the wait* with spoken/visual progress — the exact pattern `dispatch_crew_from_voice` already uses (`codec_voice.py:1025-1044`).

## 2. Current facts this design builds on (verified, file:line)

- LLM call is hardcoded: `max_tokens=2000, temperature=0.7, enable_thinking=False` at `codec_voice.py:600-605`; `enable_thinking` stripped from `llm_kwargs` at `:209`.
- Per-turn injections: targeted memory `:674-683`, observer summary `:689-702`, context trim keeps 20 turns `:276,581-587`.
- Voice fires **one skill per utterance** (`dispatch_skill` `:920-955`); the only multi-step path is crews, which already stream spoken progress via `voice_cb` (`:1025-1044`) — the think-mode template.
- `Agent.run(task, context, callback)` fires callback on every `tool_call` + `complete` (`codec_agents.py:501,610-614,670`); tools come from `load_skill_tools()`; `max_tool_calls` per agent.
- Voice strict-consent exists: `_announce_pending_question` + `_resolve_voice_option_choice` with fuzzy match BYPASSED when strict (`codec_voice.py:94-154,847-918`).
- WS protocol has no mode field; client control messages are additive-friendly (`:1179-1214`).
- `google_gmail` is **read-only** today. `imessage_send` sends. `philips_hue`, `music`, `chrome_open`, `web_search`, `web_fetch`, `google_calendar` (read+create), `timer`, `reminders`, `weather`, `notes` all exist.
- Step budget `step_budget.voice=5` is configured but **not enforced** in the voice path.

## 3. Design

### 3.1 Mode state & switching (same interface)
- `VoicePipeline.mode ∈ {"flash","default","think"}`, initialized from `config.json:voice.default_mode` (default `"default"`).
- **Voice command switch** (primary): "flash mode" / "think mode" / "normal mode" matched pre-LLM in `_pipeline`; speaks a one-word confirm ("Flash."), persists for the session.
- **WS control message** (additive): client→server `{"type":"mode","mode":"think"}`; server acks `{"type":"mode","mode":...}` so the UI can show a small mode chip. Three tiny pills added to `codec_voice.html` header — no layout redesign.

### 3.2 Flash mode ⚡
All changes are per-call parameters inside `generate_response`/`_stream_qwen` when `mode=="flash"` (the 35B model stays — prefill size is the lever):
- `max_tokens` 2000 → **400** (voice rule is 1–3 sentences anyway; caps worst-case decode)
- **Skip observer injection** (`:689-702`) and **skip per-turn targeted-memory injection** (`:674-683`; warmup memory from `warmup_llm` stays)
- Context trim 20 → **8 turns** (smaller prefill on every turn)
- Flash system-prompt variant: same identity, rule tightened to "reply in ONE short sentence"
- TTS speed 1.15 → **1.25**
- Skill dispatch unchanged (it's already the fastest path).

### 3.3 Think mode 🧠
- Utterance flow: crew triggers → single-skill trigger match (existing fast path, kept) → **voice agent loop** (new) instead of plain LLM chat.
- The loop: `Agent(name="CODEC", role=<voice-think prompt>, tools=think_tools, max_tool_calls=6).run(user_text, callback=voice_progress_cb)` — `voice_progress_cb` mirrors `voice_cb`: on each `tool_call` it sends a transcript line **and speaks** a short narration ("Turning off the lights…"), which doubles as the keep-alive during the extended wait.
- **Curated allowlist `VOICE_THINK_SKILLS`** (config-overridable at `voice.think.skills`):
  `philips_hue, music, chrome_open, web_search, web_fetch, weather, time, timer, reminders, notes, google_calendar, google_gmail, imessage_send` (+ `gmail send` per §3.4 decision).
  **Hard exclusions, not configurable around:** `terminal, python_exec, file_write, file_ops, system, process_manager, pm2_control, ax_control, pilot, create_skill` — the same class `_HTTP_BLOCKED` protects.
- **Destructive gating:** `imessage_send`, calendar-create, email-send route through the existing Step-3 strict-consent voice flow (literal verb spoken to confirm; fuzzy bypassed). Read/lookup tools run free.
- **Budgets:** `max_tool_calls=6` + wall-clock guard 120s (config `voice.think.max_seconds`) + the user can interrupt — existing `interrupted` event (`:1179`) is checked between tool calls and aborts the loop with a spoken "Stopped."
- `enable_thinking` stays **False** even in think mode — "think" = tool reasoning via the agent loop, not Qwen `<think>` tags (which would add tens of seconds on the 35B). Listed as a future tunable.
- Plugin hooks + audit: tool execution goes through the skill `Tool` wrappers → existing `run_with_hooks` chokepoint in `Agent._execute_tool_with_hooks` — every action audited + vetoable, nothing new to build.

### 3.4 Email send (decision pending)
`google_gmail` is read-only. To satisfy "send an email by voice": add a `send` action to `skills/google_gmail.py` (Gmail API `users.messages.send`, scope already authed? — verify token scopes; if scope missing, re-auth flow documented in PR). Voice path: compose → CODEC reads the draft aloud → strict consent ("say 'send' to confirm") → send → audit. Built-in skill edit ⇒ **manifest regen required** (PR-1A).

## 4. Schema / API changes
- **config.json — additive only, safe defaults ⇒ NO config_version bump (A-15):**
  `voice.default_mode`, `voice.flash.{max_tokens,context_turns,tts_speed}`, `voice.think.{max_tool_calls,max_seconds,skills}`.
- **WS protocol — additive message types only:** client `{"type":"mode",...}`; server `{"type":"mode",...}` ack; think-mode progress reuses existing `transcript`/`status` types (new status value `"tool_running"`).
- **Audit:** one new event name `voice_mode_changed` (info, single-emit). AGENTS.md §6 updated in the PR.
- No memory-db, no identity-file, no `_HTTP_BLOCKED` changes.

## 5. Files touched
| File | Change |
|---|---|
| `codec_voice.py` | mode state + switch, flash params, think agent loop + progress cb + consent + budgets |
| `codec_voice.html` | 3 mode pills + current-mode chip; send mode over WS |
| `routes/websocket.py` | pass `voice.default_mode` into pipeline ctor |
| `skills/google_gmail.py` (+ manifest) | optional `send` action per §3.4 decision |
| `tests/test_voice_modes.py` | new — see §6 |
| `AGENTS.md` §6 | `voice_mode_changed` event row |

## 6. Test plan (TDD; heal-loop riding along)
- Mode-switch phrase parsing (flash/think/normal, embedded in sentences, wake-word noise).
- Flash: monkeypatch `codec_llm.astream`, assert `max_tokens=400`, no observer/memory injection calls, 8-turn trim.
- Think: agent constructed with ONLY allowlisted tools (assert `terminal` etc. absent even if config tries to add them); callback narration emitted per tool call; interrupt event aborts between calls; wall-clock guard.
- Consent: destructive tool in think mode raises the ask_user flow; generic "yes" rejected under strict.
- Gmail send (if approved): unit-mocked API call + consent gate; manifest regen in CI check.
- Live: scripted demo run — "kill the lights and put on jazz" two-tool chain.

## 7. Migration & rollback
- Defaults make this invisible: `voice.default_mode="default"` ⇒ behavior is byte-identical until a mode is switched.
- Kill switch: `VOICE_MODES_ENABLED=false` env (pattern of Step-3 flags) — forces default mode, hides pills.
- Rollback = flip the env var or revert the PR; no state files, no schema migrations.

## 8. Open decisions (operator)
1. Email-send in v1 vs fast-follow (§3.4).
2. Think routing: keep single-skill fast path before the agent loop (recommended) vs everything through the agent.
3. Mode pills in UI + voice command (recommended) vs voice command only.
