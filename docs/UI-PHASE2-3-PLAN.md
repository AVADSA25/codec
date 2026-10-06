# UI Phase 2 and 3 — work queue

Status: approved by the owner on 2026-09-29 ("finish and implement all that was planned"). Source: the UI review of 2026-09-28; Phase 1 shipped as #396-#399 (docs/UI-PHASE1-DESIGN.md).

## How each item is built

One item is one PR, worked top to bottom. For each item:

1. Write `docs/<ITEM>-DESIGN.md` (what, why, API or schema change, test plan, rollback). For this plan the owner
   pre-approved the design notes, so the build does not stop for approval (overrides AGENTS.md §11 for this plan only).
2. Build in a git worktree, never in the shared live tree. Write the unlazy GATES.md first.
3. Tests sized like the neighbouring files, `ruff check .`, `python3 tools/stamp_static.py --check`, full `pytest`.
4. On the Mac: check in the browser on the throwaway dashboard (:8190, temp HOME) at 1470x956 and 375x812, and in
   the light theme. Never root-zoom the phone, never a position:fixed composer (see docs/known-issues.md history).
5. PR, CI green, squash-merge, fast-forward the live tree. Restart codec-dashboard after the merge, and
   codec-heartbeat, codec-observer or open-codec only when their code changed; check they are up (pre-approved).
   Then check the change on the live dashboard.
6. In the same PR, set the item's status below to `done (#PR)`. Push the branch after every commit, so nothing is lost
   if the Mac is switched off mid-item.

**Without the Mac** (cloud session): steps 1-3 and the PR only. No browser check against a live CODEC, no deploy,
no merge. Set the status to `PR open (#PR), needs Mac test`. These PRs are tested and merged on the Mac later, in order.

**Pre-approved for this plan:** additive qchat.db changes after a backup copy (never delete rows or columns);
the KaTeX and Mermaid downloads listed in MATH. **Stop and ask the owner** before anything that spends money, adds a
service or a dependency not named here, deletes user data, or changes a don't-touch zone in AGENTS.md §10 in a way
this file does not describe. Security lists (`_HTTP_BLOCKED`, `_HTTP_ONLY_BLOCKED`, `_HTTP_CONSENT_REQUIRED`,
strict consent) are add-only. No personal data in the repo. No emoji in the UI.

## Queue

| # | Item | Effort | Needs the Mac | Status |
|---|---|---|---|---|
| 1 | OBS: Observer disk file made safe | S | yes | done (#401) |
| 2 | P2.1: Desktop sidebar, phone tab bar, and every page on the shared shell | M | yes | done (#402) |
| 3 | P2.2: Rename, pin, archive, group, load more and export chats | M | yes | done (#403) |
| 4 | P2.14: Installable PWA with an offline shell | M | yes | done (#404) |
| 5 | P3.13: Push to the phone for approvals, questions, briefing and agent results | L | yes | done (#405) |
| 6 | P2.5: Dictation through CODEC's local Whisper, not Google | M | yes | done (#407) |
| 7 | P2.8: Read aloud that reads the whole answer, with voice and speed | S | yes | done (#408) |
| 8 | P3.3: Schedule any skill, crew or prompt in plain language | M | yes | done (#409) |
| 9 | P3.1: Morning briefing that runs by itself, speaks, then leaves a card | M | yes | done (#410) |
| 10 | P3.5: Proactive check-in that speaks only when something matters | M | yes | done (#411) |
| 11 | P2.9: Calm voice call with standard controls | M | yes | done (#412) |
| 12 | MATH: Math and diagrams in replies | S | no | done (#413) |
| 13 | P2.3: '/' commands, '@' skills and agents, and a Cmd+K palette | M | no | done (#415) |
| 14 | P2.4: Memory page: see, correct and forget what CODEC remembers | M | no | done (#416) |
| 15 | P2.6: Styled menus and sheets instead of native select, confirm, prompt and alert | M | no | done (#417) |
| 16 | P2.7: Keep regenerated versions and add thumbs feedback | M | no | done (#418) |
| 17 | P2.10: Settings with human labels, help text and proper controls | M | no | done (#419) |
| 18 | P2.11: Temporary chat that is not saved to history or memory | M | no | done (#421) |
| 19 | P2.12: Thumbnails for attachments and a lightbox | M | no | done (#422) |
| 20 | P2.13: Accessibility baseline | M | no | done (#423) |
| 21 | P2.15: Readable Cortex and Audit | M | no | done (#430) |
| 22 | P3.2: One Inbox for approvals, questions, reports and agent updates | M | no | done (#431) |
| 23 | P3.4: 'Today' home instead of the Flash log | L | no | done (#432) |
| 24 | P3.6: Skills page: browse, try, check readiness | M | no | PR open (#433), needs Mac test |
| 25 | P3.7: 'CODEC is watching' indicator with pause | M | no | open |
| 26 | P3.8: Activity board for projects and background jobs | M | no | open |
| 27 | P3.9: Connections: Google, MCP clients, bridges, permissions | M | no | open |
| 28 | P3.10: Side-by-side model compare from any reply | M | no | open |
| 29 | P3.11: Usage and cloud spend | S | no | open |
| 30 | P3.12: Learning page: review what CODEC proposes to learn | L | no | open |

Items marked "Needs the Mac" use local services (Keychain, Whisper, Kokoro, the local model, PM2) or are the riskiest layout changes; build them on the Mac first. The others can be built without it.

## Items

### OBS: Observer disk file made safe

The observer mirrors its last ~10 minutes to ~/.codec/observer_buffer.json on every poll so other processes (skills/observer_recall.py) can read it. Owner decision 2026-09-29: keep the file, but write it owner-only (0600, atomic), store no clipboard text (content type and length only), and wipe it on pause and on the long-idle reset. Correct the Step 5 privacy text in AGENTS.md (it says RAM only). 'What was I doing?' must keep working.

### P2.1: Desktop sidebar, phone tab bar, and every page on the shared shell

Turn chat's existing history drawer (it already has search and New Chat) into the persistent 272px sidebar described in the brief, and add the phone icon tab bar (Today, Chat, Voice, Tasks, Inbox). Move the header, nav and side-panel markup and CSS, today copied into 8 pages, into codec.css plus one codec-shell.js. The menu button gets a sliders icon instead of the house icon titled 'Home'. On Home, move Audit, Cortex and Connector into Settings or their own pages, leaving the tab row short. Finish the type scale on Voice, Vibe, Cortex, Audit and Auth. Once every page is on tokens, make S/M/L drive --fs-scale everywhere and remove the root zoom entirely.

### P2.2: Rename, pin, archive, group, load more and export chats

Backend: add pinned and archived columns (additive, with a qchat.db backup first), PATCH /api/qchat/session/{sid} for rename, pin and archive, offset paging to replace LIMIT 30, and GET /api/qchat/session/{sid}/export?format=md|json. Change saving from append-only to replace-or-mark, so regenerate, edit and stop no longer leave discarded rows that come back on reload; P2.7 depends on this. UI: a three-dot menu on each row, a Pinned section, date groups, infinite scroll, an archived view, multi-select delete with an in-page confirm, and optional 'Save to Google Doc' through the existing google_docs skill. No public share links.

### P2.14: Installable PWA with an offline shell

Register a service worker that caches only the static shell (HTML, codec.css, fonts, icons) and never /api responses. When the Mac is unreachable, the PWA shows a calm 'Mac unreachable' state. Add proper 192, 512 and maskable icons plus manifest shortcuts (New chat, Voice, Start my day). Add an Install button that uses the already-captured deferredPrompt. Web Push is P3.13 (decided 2026-09-29).

### P3.13: Push to the phone for approvals, questions, briefing and agent results

Web Push is CODEC's own channel for approvals, questions, briefing and agent done or blocked (owner decision 2026-09-29: no outside chat app needed; everything in CODEC and a web browser). The push carries no content, only a generic line such as 'CODEC needs your approval'; the page shows the detail. VAPID keys live in the Keychain; the existing `cryptography` dependency signs them (no new dependency). Per-type toggles in Settings; the fan-out runs when a notification is posted. Telegram stays an optional bridge for people who use it, not the default. Needs P2.14 (service worker). iOS needs the PWA added to the Home Screen.

### P2.5: Dictation through CODEC's local Whisper, not Google

Add POST /api/transcribe, which proxies to the configured stt_url (whisper-stt :8084). It is auth-protected and size-capped, sets no language so Whisper auto-detects French, Spanish or English, and reuses the noise and hallucination filters in codec_voice.py:620-632. The mic buttons on Chat, Home and Vibe record with MediaRecorder, show a level meter, and insert the transcript for review before sending. Keep Web Speech only as an explicit opt-in labelled 'uses your browser's cloud service'. Chrome sends Web Speech audio to Google today.

### P2.8: Read aloud that reads the whole answer, with voice and speed

Split the reply into sentences on the client and queue them. Today it stops at 500 characters, or 300 on Home. Change /api/tts to return audio bytes per request, not the shared ~/.codec/pwa_audio.mp3 that parallel requests would overwrite, and accept POST for long text. Add a small player (pause, resume, stop, speed 0.9-1.4x). Strip Markdown and code before speaking. In Settings, add a voice picker with a 3-second preview (needs a voice-list endpoint) and a tts_speed slider; the backend already reads tts_speed but Settings does not expose it.

### P3.3: Schedule any skill, crew or prompt in plain language

Extend the existing Tasks > Schedules tab rather than building a new page. A job can be a crew, a skill or a free prompt. It is created in plain language ('weekdays at 7:30', 'every 2h') or with 'Schedule this' from any reply's menu. Per-job options: speak the result; deliver to a notification, Today or a Google Doc; 'notify only if the output changed'; and continuity (feed in the previous run's output). History keeps the full output, which also fixes the '|' versus JSON-lines mismatch. Extend the existing same-day catch-up to runs missed across midnight.

### P3.1: Morning briefing that runs by itself, speaks, then leaves a card

Off by default, switched on in Settings. Extend the scheduler so a job can be a skill (routes/schedules.py requires 'crew' today; skill jobs run through codec_dispatch.run_skill). The Morning briefing job (default weekdays 07:30 local) runs daily_kickoff; the loaded local model turns the result into a 45-60 second script. Kokoro speaks it on the Mac only at the first activity the observer sees after the scheduled time (never to an empty room), with a chime first and barge-in to stop it. It skips while ~/.codec/image_job.lock is held or a big model job holds memory. It leaves a 'briefing' card on Home (Open in chat, Mark thread done, Snooze, Draft reply) and sends a content-free Web Push ('Your briefing is ready') when push is on. This changes DAYBREAK-DESIGN.md's 'no notifications' line for opted-in users only; update that doc.

### P3.5: Proactive check-in that speaks only when something matters

Add one more check inside the existing codec_heartbeat cycle, not a new daemon. Every 30-60 minutes during active hours, skipped when the owner is idle or away or when an image job holds memory, the local model gets a compact snapshot. It holds observer metadata, calendar events in the next 2 hours, due follow-ups, the count of unread high-priority email and failing services, plus a checklist the owner edits in Settings. It must answer NO_REPLY unless something needs attention, with a daily cap. A hit posts a proactive_suggestion with Act, Snooze, Dismiss today and Never for this pattern, which wires up the unused /api/proactive endpoints, and it can optionally say one sentence aloud. Default off, a kill-switch toggle, and every run audited. Strip the emoji from heartbeat alert text when it becomes cards.

### P2.9: Calm voice call with standard controls

Replace the spinning ring, the full-screen glow and the gradient buttons with one level-driven orb and flat buttons; keep the existing #statusLabel caption and RMS level signal. Show the transcript as chat bubbles, and change the hard-coded 'M' user label, the owner's initial in a public repo, to 'You'. Use Plex instead of 'SF Pro Display', add a visible hold-to-talk hint, and turn the Screenshot alert into a toast. New controls: Mute; Stop speaking (sends the existing {type:'interrupt'} plus stopAudio); a Type box that sends typed turns through a new 'text' control handled in codec_voice.py; tool chips built from the tool narration the server already sends; 'Continue in chat', which POSTs the transcript to /api/qchat/save; and a mic device picker.

### MATH: Math and diagrams in replies

Vendor KaTeX 0.18.9 (dist/katex.min.js, dist/katex.min.css, the 20 dist/fonts/*.woff2) and Mermaid 12.0.0 (dist/mermaid.min.js) from cdn.jsdelivr.net into static/vendor with SHA-256 rows in SOURCES.md (download approved 2026-09-29). static/codec-md.js renders $...$ and $$...$$ with KaTeX and ```mermaid blocks with Mermaid, both loaded only when a reply needs them. Output stays inside the DOMPurify contract; Mermaid runs with securityLevel 'strict'.

### P2.3: '/' commands, '@' skills and agents, and a Cmd+K palette

Add GET /api/slash_commands, which returns the registry's usage strings; today they are reachable only by sending '/help'. Typing '/' at the start of the composer opens a filtered popover of the backend commands plus client-side actions (/new, /brief, /image, /think, /project). Typing '@' opens a picker of skills, grouped and described using registry SKILL_NAMEs (not file stems), plus crews, custom agents and connected MCP servers. Picking one inserts a chip and sends a new 'skill' body field for explicit routing, instead of relying on trigger matching. Cmd+K opens a palette over the same data: search chats, jump to a page, run a skill, switch model, start the briefing. Cmd+/ shows a shortcut sheet.

### P2.4: Memory page: see, correct and forget what CODEC remembers

Add a Settings > Memory page with small new routes over existing functions. It shows active facts (query_valid_facts) with edit, Forget (expire_fact) and History (get_fact_history); working threads from thread_note, with Close; standing rules as an editable list (list/add/remove/clear); an 'About me' box for identity.txt; a search over memory.db with Rebuild index (/api/memory/*, which no page calls today); and toggles for auto_memorize and fact_extract. Link to the existing System Prompts editor instead of duplicating it. Replies that used memory show a small 'Memory used' link, which needs _enrich_messages to return metadata.

### P2.6: Styled menus and sheets instead of native select, confirm, prompt and alert

Build one keyboard-navigable popover menu for the model, crew, research-model, image-size and 'Load saved' pickers. Build one modal (a bottom sheet on the phone) for delete chat, approve plan, reject reason, abort agent, TOTP enable and disable, prompt reset, Cortex restart and Vibe delete. Route every alert() through the existing showToast(). The chat schedule button (prompt() for whole hours only) opens the existing Tasks schedule form in a sheet instead. Replace the bare 'Transparent' checkbox with a switch and the Max Steps spinner with a stepper.

### P2.7: Keep regenerated versions and add thumbs feedback

Regenerate and edit keep earlier answers as versions of the same turn, browsed with '2 / 3' arrows. Only the selected version goes into chatHist and the saved session; this depends on the save change in P2.2. Add thumbs up and down with an optional reason ('wrong', 'too long', 'didn't use my data'), stored locally in a qchat_feedback table and counted in the shift report and in self_improve input. The more menu offers 'Retry with Think' and 'Retry with another model', reusing the model picker and toggleThinking.

### P2.10: Settings with human labels, help text and proper controls

Give every raw snake_case key a friendly label and a one-line help text. Keep the existing sections and the instant-save pattern (toggleWakeInstant) and extend both to every field. Add proper controls (dropdowns, sliders, switches) and an 'Advanced' raw JSON editor. Add friendly sections for shift_report, observer (enable, OCR, cadence), daybreak, ask_user timeout, step_budget and image. PUT /api/config currently flattens everything to the top level, so it must learn to write nested blocks. Read location.hash so the '/#settings' links from 6 pages open Settings instead of Flash.

### P2.11: Temporary chat that is not saved to history or memory

A toggle in the + menu, shown by an eye-off icon and a dashed composer border. When it is on, the client skips /api/qchat/save and sends temporary:true. routes/chat.py then skips memory recall, the observer summary, and the memory-writing skill paths (auto_memorize, fact_extract, thread_note, standing_rules). The label says 'Not saved to history or memory', not 'no trace', because the audit log still records events.

### P2.12: Thumbnails for attachments and a lightbox

Attachment tiles keep the browser-side downscaled image as a 56px thumbnail, or show a file-type icon with name and size, with a progress ring while uploading. Sent user messages keep their thumbnails. Clicking a chat image or a generated image opens the existing #screenModal as a lightbox with a download button. The vision text description still goes to the model, but on screen it sits in a collapsible 'What CODEC saw'. Storing thumbnails across reloads needs a column in qchat.

### P2.13: Accessibility baseline

Add aria-labels to icon-only buttons; role=log with aria-live=polite on #messages and the voice transcript; role=status on toasts. Add a global :focus-visible ring and remove the 15 bare outline:none rules. Use real <button>s instead of clickable divs and spans. Drawers and modals get role=dialog, a focus trap, Esc to close and focus return. Toggles get aria-pressed, tabs get role=tablist, and the TOTP digits get autocomplete=one-time-code. Add a prefers-reduced-motion block. Most of this lands for free inside the P2.1 and P2.6 components.

### P2.15: Readable Cortex and Audit

Cortex: set a 12px minimum; turn the floating panels into a bottom sheet on the phone; add zoom-in, zoom-out and fit buttons (wheel and pinch zoom already work); replace the hard-coded '89 skills / 12 Agent Crews / 7 Products / Qwen2.5-VL-7B' text with live counts; point 'Open :port' at the dashboard host instead of localhost; escape skill text before inserting it as HTML. Audit: make the pills focusable buttons, add paging past 200 events, and add a 'Verify integrity' button through a new read-only GET /api/audit/verify that wraps verify_audit_log. Retire the duplicate /audit page in favour of the Settings audit view.

### P3.2: One Inbox for approvals, questions, reports and agent updates

One drawer on desktop and a tab or sheet on the phone, with a count badge and filters: Needs you / Reports / Agents / Suggestions. It replaces the fixed approval banner, the bell link to /tasks#reports, and the Reports tab, which marks everything read on click. Every item carries inline actions. Questions are answered by reusing the existing ask_user panel code from chat and Flash; approvals get Allow once or Deny; reports get Open or Discuss in chat; agents get Pause, Resume or Grant. Items are marked read when opened. One /api/inbox endpoint (or SSE) replaces the five pollers that run every 3-30 seconds. No 'always allow' until a design shows it cannot widen _HTTP_BLOCKED, _HTTP_CONSENT_REQUIRED or strict consent.

### P3.4: 'Today' home instead of the Flash log

Home becomes Today: greeting and this morning's briefing card (replay); 'Needs you' reusing the Inbox cards; 'Next up' (next 3 calendar events); open threads (working on / waiting on / follow up) with Close and Snooze through a new read route over the 'thread:*' facts; running and finished agents; a summary of yesterday's shift report; quick tiles (now playing, a Hue scene, volume, a timer, screenshot). The Flash composer stays at the bottom and History stays one tap away, so no working feature is lost.

### P3.6: Skills page: browse, try, check readiness

Start from the Cortex Skills Registry panel. List all skills by category with description, trigger phrases and 'Try it' (prefills the composer), using registry SKILL_NAMEs. Show which paths each skill works on: chat, voice or MCP only. Add a readiness check explaining why a skill cannot run: a missing Serper or Pexels key, Google not connected, a missing Screen Recording or Accessibility permission. Add the safe read-only skills (health_check, backup_status, audit_report, memory_history) to the chat allowlist. Fold Voice Triggers and Skill Reviews into this page. Ship on/off toggles only after codec_dispatch enforces cfg['skills']; the existing /skills enable|disable writes the list but nothing reads it.

### P3.7: 'CODEC is watching' indicator with pause

Show an eye icon in the header while the observer runs, with a menu:
- Pause for 15 minutes, 1 hour or until tomorrow, through a new ~/.codec/observer_paused_until flag that the daemon reads on every poll. The pause also stops the disk-mirror writes.
- 'What CODEC sees now', which reads ~/.codec/observer_buffer.json and shows metadata only, because /api/observer/buffer runs in the wrong process and returns an empty buffer.
- A link to observer settings.
Fix the /api/triggers route clash by renaming the voice-trigger route in routes/skills.py and updating codec_dashboard.html:2740 and 2797. That makes the Step 6 trigger list reachable; show it as a small list with kill and mute.

### P3.8: Activity board for projects and background jobs

An Activity view that lists every Project-mode agent, finished and aborted ones included. Each row shows an n-of-N checkpoint progress bar, elapsed time and the last message, with Pause, Resume, Abort, Extend budget, Silence and Revise plan, plus the files and artifacts produced. Extend GET /api/agents with progress fields. Add a global-grants editor over /api/agent_global_grants. Save _agent_jobs to ~/.codec and add a list endpoint, so crew jobs survive a dashboard restart (a known gap). Restyle the existing #agentStatusPills as the running-task rail above the composer.

### P3.9: Connections: Google, MCP clients, bridges, permissions

Add sections to the existing Connector tab rather than moving it:
- Google Workspace status (account, scopes, token expiry) with Reconnect. It works only from the Mac, because reauth_google.py uses a localhost redirect that cannot complete over the tunnel.
- AI apps connected to CODEC's MCP server: last used, and tools exposed, blocked or needing consent. Revoke removes only that client's tokens and never clears oauth_state.json.
- Telegram and iMessage bridge status, with a test-message button that sends only on an explicit click.
- A best-effort macOS permissions checklist with 'Open System Settings' links.

### P3.10: Side-by-side model compare from any reply

A 'Compare models' item in the reply's more menu sends the same prompt through codec_compare to the loaded local model and one other target. A cloud leg is added only by an explicit per-compare opt-in and must pass the capped codec_cloud_models path. Results show in columns (stacked on the phone) with time, tokens and tok/s. The token counts need adding, because _query_one returns only elapsed_ms. 'Keep this one' puts the winner into the chat. Only one local model is loaded at a time, so 'several local variants' is not possible.

### P3.11: Usage and cloud spend

A Usage section in Settings: this month's spend against monthly_cap_usd for each cloud model, straight from codec_cloud_models; whether CODEC is on the cloud right now (llm_auto_fallback_active); and a llm_auto_fallback picker listing only registered cloud entries, with a plain explanation of when it triggers. Add local against cloud call counts and the busiest skills over 7 days, which means extending audit get_stats beyond its fixed 24h. Tok/s over time needs new per-reply logging.

### P3.12: Learning page: review what CODEC proposes to learn

Add a read-only endpoint that lists ~/.codec/skill_proposals/, and a Learning page where Approve runs through the existing /api/skill/review and /api/skill/approve gates (replaces the scripts/promote_skill.py CLI step; nothing auto-deploys). Add a readable daily 'What CODEC learned' diary. Facts from fact_extract and auto_memorize keep saving as today; the page lists recent ones with Edit and Forget (expire_fact). No approval gate, so no behaviour change. Any nightly job must respect the image-job lock and model memory limits.

## Risks carried from the review

- Phone regressions: #344 (root zoom) and 1612bf0 (fixed composer) both broke scrolling. One 100dvh flex column
  with a single scroller; test at 375x812 and on a real iPhone in standalone PWA mode.
- The service worker (P2.14) caches only the static shell, never /api responses or anything tied to the session cookie.
- qchat.db changes are additive, with a backup first.
- The proactive check-in (P3.5) on a small local model can get noisy: strict NO_REPLY rule, daily cap, default off,
  every run audited.
- The skills on/off switch does nothing today (nothing reads cfg['skills']); no UI toggle before dispatch enforces it.
- Any 'always allow' for approvals must never widen the security lists above.
- Temporary chat still writes audit events: label it 'Not saved to history or memory', never 'no trace'.
