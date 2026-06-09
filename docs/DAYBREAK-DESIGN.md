# DAYBREAK — Morning Kickoff + Working-Threads Live Memory

**Date:** 2026-06-09 · **Status:** APPROVED (operator option A, "all over — not just voice") + IMPLEMENTED.
Synthesized from a 5-reader code audit (shift-report internals, facts spine, action-skill
interfaces, chat gating, open-threads state). Tests: `tests/test_daybreak.py`.

## 1. What & why

"Good morning CODEC — where did we leave off, what's today, what needs follow-up?" answered
in <8s from **every** surface (voice, wake-word, chat, MCP), grounded in a persistent
**working-threads** memory of what the user is doing — the continuity spine of a true
assistant.

## 2. Architecture

- **`codec_daybreak.py`** (engine): `assemble_briefing(trigger_text)` + threads API
  `save_thread(kind, text)` / `close_thread(match)` / `get_open_threads()` /
  `get_working_context()` (≤600-char prompt block, priority-first, max 7).
- **Threads = temporal facts** in the existing `facts` table (`~/.codec/memory.db`):
  `key = "thread:{kind}:{slug}"`, `kind ∈ working_on|waiting_on|priority|follow_up`,
  `fact_type="thread"`, `source="daybreak"`. Same-key re-save auto-supersedes
  (`store_fact(..., supersede=True)`); closing uses NEW additive
  `codec_memory_upgrade.expire_fact(key)` (sets `valid_until`, keeps `superseded_by` NULL).
  **Audit override honored:** documented `valid_at()` time-travel does NOT exist in code —
  design only relies on `valid_until IS NULL` (AGENTS.md §5 corrected in this change).
- **Injection per surface:**
  - *Voice + wake-word:* ZERO edits — both `_build_system_prompt`s already inject
    `query_valid_facts(limit=20)` as `[ACTIVE FACTS]`; thread facts ride along automatically.
  - *Chat:* facts were never injected; ONE additive block in
    `routes/chat.py:_build_chat_system_prompt` right after the base prompt format —
    `get_working_context()` (≤150 tokens), try/except, vanishes when disabled.
  - *MCP:* no injection (Step-5 contract: mcp transport never injects); claude.ai calls the
    MCP-exposed `daily_kickoff` / `thread_note` tools explicitly.
- **Skills (thin shims, both `SKILL_MCP_EXPOSE=True`):**
  - `daily_kickoff` — triggers: "good morning", "where did we leave off", "where did we left
    off", "where we left off", "start my day", "daybreak", "daily kickoff", "kick off my day".
    Deliberately NO "briefing" (collides with the `daily_briefing` crew voice triggers) and
    NO "what did i do today" (shift-report spurious-fire precedent).
  - `thread_note` — every trigger contains "thread" (anti-spurious-fire): "note a thread",
    "track a thread", "new thread", "close thread", "thread done", "open threads",
    "my threads", "list threads". Kind inferred from text (waiting on→waiting_on,
    priority→priority, follow up→follow_up, else working_on).
- **Skill sub-calls** from the engine use the codec_observer importlib precedent (one-time
  sys.path of user skills dir + repo skills dir, module cache, pre-warm imports on the main
  thread before the worker fan-out). Trade-off accepted: sub-calls bypass per-skill hooks;
  the wrapping `daily_kickoff` dispatch is hooked + audited, and `daybreak_completed`
  records which sections ran.

## 3. Briefing assembly (4 sections, never raises, budget-reaped)

1. **Where we left off** (local, main thread): newest `type="shift_report"` notification
   ≤36h old (body head) → fallback `CodecMemory.get_sessions` yesterday topics; open
   threads; `crew_start` without `crew_complete`/`crew_error` in last 24h (own audit-log
   scanner, string-ts compare, rotation-aware); blocked/awaiting agents from
   `~/.codec/agents/*/manifest.json` (statuses blocked_on_permission, blocked_on_destructive,
   paused, awaiting_approval, revised; EXCLUDES blocked_on_qwen/running AND plan_failed —
   live-fire showed weeks-old failed experiments reading as morning priorities).
2. **Today** (parallel threads): calendar via `_run_source("google_calendar", "what do i
   have today")` (a `_READ_OVERRIDES` hard-forced read phrase); weather via
   `_run_source("weather", "weather today")`.
3. **Follow-ups**: pending questions (read-only `codec_ask_user._load_pending_questions`,
   status=="pending"); reminders (parallel; handles None return); unread email (parallel,
   `"unread emails"` — contains neither "send" nor "from", avoiding both gmail traps);
   notification count.
4. **Suggested priorities** (derived, no I/O): priority threads → blocked agents → pending
   questions → oldest working_on; cap 4; else "Clean slate — pick your battle."

**Budget:** ThreadPoolExecutor(4) for the network calls; main thread does all local reads
meanwhile; `future.result(timeout=remaining)` against `daybreak.time_budget_seconds`
(default 8, 0.5s render margin); timed-out source → "(X didn't answer in time)" line.

## 4. Chat gating (audited exactly)

- "good morning codec" = 3 words → `_is_conversational` False → pre-LLM hijack fires.
- "where did we left off yesterday" → no pattern hit → fires.
- **"?" trap is real**: any "?" → conversational → hijack skipped. Fixes: (a) both skills
  added to `CHAT_SKILL_ALLOWLIST` (mandatory for either gate), (b) one example line in
  `codec_dashboard._DASHBOARD_ADDON` teaching the post-LLM `[SKILL:daily_kickoff:...]` tag.
  `_is_conversational` itself is NOT modified (shared blast radius).

## 5. Config / audit / docs

- Config (additive, no schema bump): `daybreak.{time_budget_seconds, lookback_hours,
  max_threads_in_context, working_context_char_cap, include_calendar, include_weather,
  include_email, include_reminders}`.
- Audit: 3 new single-emit events, `DAYBREAK_EVENTS` frozenset in `codec_audit.py`:
  `daybreak_completed` (info; duration_ms top-level; extra sections_included,
  skipped_sources, open_threads_count, word_count), `daybreak_thread_saved` (kind, key,
  superseded, text_len), `daybreak_thread_closed` (key, rows_expired). Thread text never
  enters audit lines.
- AGENTS.md: §2 repo map, §6 events, §5 `valid_at` correction.
- **No new files in `~/.codec/`** — threads are ordinary facts rows; no state file, no
  notifications posted.

## 6. Files touched

NEW: `codec_daybreak.py`, `skills/daily_kickoff.py`, `skills/thread_note.py`,
`tests/test_daybreak.py`, this doc.
EDIT: `routes/chat.py` (allowlist + inject), `codec_dashboard.py` (addon example),
`codec_memory_upgrade.py` (`expire_fact`), `codec_audit.py` (constants),
`skills/.manifest.json` (regen), `AGENTS.md`.
**Zero edits** to any file in open PRs #189–#192 except shared regen/doc files
(`skills/.manifest.json`, `AGENTS.md`) — Daybreak PR lands AFTER those merge, rebased,
manifest regenerated fresh.

## 7. Kill switch + rollback

`DAYBREAK_ENABLED` env (default true; false|0|no|off): briefing returns "Daybreak is
disabled."; `get_working_context()` returns "" (chat injection vanishes). Thread
save/close stay functional (plain facts ops). Rollback: delete 3 files + revert 4 small
edits + manifest regen; optional data sweep
`UPDATE facts SET valid_until=datetime('now') WHERE key LIKE 'thread:%' AND valid_until IS NULL`.

## 8. Known limitations (accepted)

Calendar window is UTC-day (late-evening bleed at UTC+2); reminders can't distinguish
missing Automation permission from zero reminders; "?"-suffixed phrasings rely on the LLM
tag path; crew-boundary false positives at the 24h edge; voice 20-slot facts window shared
with other facts (7-thread cap + close-hygiene mitigate; dedicated voice block deferred
until #189–#192 merge). Separately logged: `fact_extract`'s `mem.store_fact` is a silent
no-op bug (pre-existing, out of scope → docs/known-issues.md).
