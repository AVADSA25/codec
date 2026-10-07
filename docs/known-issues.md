# Known issues — deferred, not fixed

Intentionally-deferred bugs and test failures with documented status, so we don't lose track. Each entry: file path / symbol or test, what's broken, why we deferred it, and the revisit-when target.

---

## Pre-existing test failures from main (Phase 1 Step 1 audit)

The 20 pre-existing failures from `pytest tests/ --ignore=tests/test_smoke.py` were classified in [`docs/PHASE1-STEP1-PREMERGE-AUDIT.md`](PHASE1-STEP1-PREMERGE-AUDIT.md). All are pre-existing on `main` and unrelated to the audit-unification work. They remain failing on `main` after the merge of PR #3.

The single most likely-to-bite-us-soon entry from that list is the `_safe_task` regex bug below; the others are either documentation drift, references to functions/symbols that no longer exist, or test/implementation mismatches in unrelated subsystems.

### `codec.py:255` — `_safe_task` osascript variable name fails the regex sanitizer test

| field | value |
|---|---|
| **file** | `codec.py` |
| **line** | 255 (after PR #3 merge — was 253 on the parent commit; shifted by 2 lines from the import-block edit) |
| **symbol** | local variable `_safe_task` inside `_dispatch_inner()` |
| **failing test** | `tests/test_security.py::test_osascript_inputs_sanitized` |
| **what's broken** | The test reads `codec.py`, regex-finds every variable name interpolated into a `display notification "..."` osascript call, and asserts the variable's name `startswith("safe_")`. The variable in question (`_safe_task`) is in fact properly sanitized — it runs `task[:50].replace('\\', '\\\\').replace('"', '\\"')` before interpolation. The bug is in the **naming convention**: the test wants the prefix `safe_` (no leading underscore), the implementation uses `_safe_` (leading underscore for module-private convention). |
| **status** | **deferred-not-fixed** |
| **why deferred** | (a) the variable IS sanitized — it's a naming-convention disagreement between the test and the implementation, not a real escape vector. (b) Renaming `_safe_task` is an unrelated change and would have padded PR #3 with noise outside its scope. (c) The test has been failing on `main` since at least the 20-pre-existing snapshot — not a regression introduced by audit-unification. |
| **revisit when** | Phase 1 Step 2 or any future PR that legitimately edits `_dispatch_inner`. Rename the local to `safe_task` (drop the leading underscore) and re-run the test — should flip green with no other changes. |
| **risk if left unfixed** | Low. The escape pattern is correct; the test's strictness on naming gives a false-negative warning that's already documented as known-failing. Anyone adding a new osascript interpolation should still use the `safe_` prefix per the test's intent. |

---

## Phase 1 Step 1 sign-off

> **Phase 1 Step 1 — production-stable as of 2026-05-01T09:48:43+02:00 (T+24h post-merge).** Merge commit: `45d4aa7`.

**Samples captured:** T+0 (09:23 GMT+2, ok), T+8h (17:42 GMT+2, ok), T+24h (09:48 GMT+2 next day, ok). T+4h / T+12h / T+16h / T+20h were missed (operator asleep). Each captured sample showed status=ok per the §5.4 rubric. Trailing-30m windows had `with_duration=0` in every captured sample (no claude.ai → MCP traffic in the sample windows; this is a low-traffic personal workstation), so latency comparison vs the 987.96 ms / 1907.78 ms anchor never had a quantitative match — but service health stayed green for the full 24-hour period and no production incidents were reported.

**Sign-off rationale:**
- All captured samples within the §5.4 `ok` flag rubric.
- Zero `test_audit_concurrent_no_corruption` failures observed; no audit log corruption surfaced.
- Zero orphan-cid spikes.
- 24h elapsed without a revert event; no operator intervention required.
- The `service_down` lifecycle emits visible at T+0 (Whisper / Kokoro / Vision intermittents) are previously-hidden events now visible per design intent (§0), not a regression.

**Methodology gap acknowledged:** the missed T+12h / T+16h / T+20h sample slots are a process gap — the user was asleep, no automated capture was scheduled. The Apple Reminders that fired at those local times did not auto-trigger captures; they pinged the user. For Phase 1 Step 2's post-merge watch, consider an autopilot trigger or PM2 cron skill if missed samples become a pattern.

**Phase 1 Step 2 work:** unblocked.

---

## Phase 1 Step 2 sign-off

> **Phase 1 Step 2 — production-stable as of 2026-05-01T09:55:00+02:00 (T+0 post-merge).** Merge commit: `15c6f70`.

**Samples captured:** T+0 (status=ok). T+4h through T+20h were skipped after operator decision: Step 1's 24h watch had already proven the audit envelope was stable, Step 2's hook layer with zero plugins is a passthrough wrapper that adds <1 ms overhead (validated by `tests/test_hook_audit_perf.py`), and the production state at merge time had `~/.codec/plugins/` empty. No new audit-error spike, no service degradation observed in the 4 hours of casual monitoring before sign-off was decided.

**Sign-off rationale:** zero plugins installed = zero hook side effects. The wrapper itself was extensively tested pre-merge (16 hook lifecycle tests, all passing). Deferred-watch decision documented here.

**Phase 1 Step 3 work:** unblocked.

---

## Phase 1 Step 3 sign-off (retroactive)

> **Phase 1 Step 3 — production-stable as of 2026-05-01T15:47:00+02:00 (T+0 post-merge).** Merge commit: `59bfbda`.

**Samples captured:** T+0 (status=ok, 0 Step 3 audit events emitted in window — all features dormant until invoked, which matches design). T+4h through T+20h **explicitly skipped per user instruction**.

**Why the watch was skipped:** Step 1 + Step 2 24h watches both came back clean with no production incidents. The pattern was established. More importantly, the previous attempts to run a structured 24h cadence via Apple Reminders + repeated pytest invocations caused the **2026-05-01 incident** (5 Apple Reminders fired by Claude Code via stdio MCP; cascade of Terminal popups from `memory_search`/`clipboard` skills triggered by repeated test_mcp_all_tools.py runs; 11 leaked AskUserQuestion notifications from un-monkeypatched test fixtures). Documented in `docs/INCIDENT-2026-05-01-spurious-skill-fires.md`. Test pollution made the cadence noisy and value-low.

**Sign-off rationale:**
- Step 3 introduces 89 new passing tests with 0 new failures (711 passed / 20 failed / 73 skipped — exactly the Step 1 + Step 2 baseline).
- All 6 new audit event types (`ask_user_question_emit`/`_answer`/`_timeout`, `stuck_warning`/`_escalated`, `step_budget_exhausted`) are dormant until invoked — no impact on idle traffic.
- 3 per-feature kill switches (`ASKUSER_ENABLED`/`STUCK_DETECTION_ENABLED`/`STEP_BUDGET_ENABLED`) for instant disable without code change.
- Pre-merge audit (`docs/PHASE1-STEP3-PREMERGE-AUDIT.md`) classified 0 of 20 baseline failures as Step-3-caused.

**Phase 1 Step 4 work:** unblocked.

---

## Phase 1 Step 4 sign-off

> **Phase 1 Step 4 — production-stable as of 2026-05-01T16:13:59+02:00 (T+0 post-merge + plugin install + PM2 restart).** Merge commit: `9858934`.

**Samples captured:** T+0 (status=ok). End-to-end verified by triggering a synthetic `weather` skill call after PM2 restart — `audit.log` immediately recorded `hook_fired` event with `extra.plugin_name="self_improve"` and `extra.hook_name="post_tool"`. Plugin is **live** and observable.

**Why no extended watch:** plugin is observe-only (post_tool/on_error) plus an on_operation_end snapshot that spawns at most one daemon thread per operation. The drafter thread calls the same `_draft_skill`/`_validate`/`_write_proposal` flow that was previously invoked nightly via `codec_self_improve.run_once()` — same code path, same proposal output dir, same dangerous-pattern gate. Behavior delta from "nightly polling" to "event-driven" is bounded and tested (21/21 `tests/test_self_improve_plugin.py`).

**Sign-off rationale:**
- Plugin file copied to `~/.codec/plugins/self_improve.py` and AST-discovered at PM2 restart (1 plugin discovered, name=`self_improve`, hooks=`['post_tool', 'on_error', 'on_operation_end']`).
- First real-traffic `hook_fired` audit event captured at 2026-05-01T14:13:59Z (= 16:13 CEST) confirming the post_tool hook fires on real skill execution.
- `codec_self_improve.run_once()` legacy path unchanged — both triggers coexist.
- Per-feature kill switch available (`SELF_IMPROVE_PLUGIN_ENABLED=false`).
- Per-tool throttle (30 min) prevents Qwen spam if same gap fires repeatedly.
- Self-recursion guard (`_SELF_TOOLS = {"self_improve", ""}`) prevents the plugin from firing on its own emits.

**Phase 1 status: COMPLETE.** All 4 steps merged + production-stable. See `docs/PHASE1-COMPLETE.md` for the consolidated state report.

---

## Phase 2 Step 5 sign-off

> **Phase 2 Step 5 — production-stable as of 2026-05-02T16:42:00+02:00 (T+0 post-merge + observer install + PM2 restart).** Merge commit: `824a52f`. Hotfix landed during sign-off window: PR #10 (`26e6add`) added `observer.ocr_enabled` config flag.

**Samples captured:** T+0 (status=ok). End-to-end verified by tailing `~/.codec/audit.log` after `pm2 restart codec-observer` — `observation_tick` (or `observation_tick_slow` when OCR disabled) emits every 5 s as designed. As of Phase 2 close: 65 `observation_tick` + 97 `observation_tick_slow` emits captured.

**Hotfix context:** initial Step 5 deploy with `ocr_enabled=true` (default) triggered a macOS Screen Recording permission popup-storm — over 100 popups while the system retried screencapture inside a `ThreadPoolExecutor` whose `with` block was blocking on `shutdown(wait=True)`. Root cause: the "100 ms" timeout inside the executor was actually waiting ~5 s for the popup-blocked screencapture to complete; the retry then triggered a SECOND popup. Hotfix PR #10 added `observer.ocr_enabled` (default `true`) with a graceful-degraded path that skips screencapture entirely when `false`. User runtime config patched to `observer.ocr_enabled: false` until Screen Recording is explicitly granted to both `python3.13` and `node` (PM2 parent).

**Sign-off rationale:**
- Observer daemon online and stable for 3+ minutes after restart with the hotfix applied.
- All 5 Step 5 audit event constants live in code; 2 of 5 (`observation_tick`, `observation_tick_slow`) directly observable in production audit log.
- `observation_summary_injected` is dormant until a chat / voice handler invokes the injection contract — which fires only when `transport=local` (always) or when cloud transport hits possessive-pronoun / continuation-phrase / `SKILL_NEEDS_OBSERVATION` flag.
- Per-feature kill switch via PM2 `pm2 stop codec-observer` + `observer.enabled: false`.
- Graceful-degraded code path proves `ocr_enabled=false` mode works as designed (97 `observation_tick_slow` emits without a single screencapture attempt or popup).
- Pre-merge tests: 33 passing (`tests/test_observer.py`).

**Phase 2 Step 6 work:** unblocked.

---

## Phase 2 Step 6 sign-off

> **Phase 2 Step 6 — production-stable as of 2026-05-02T18:25:00+02:00 (T+0 post-merge + PM2 restart of codec-observer + codec-dashboard).** Merge commit: `2d2ff3f`.

**Samples captured:** T+0 (status=ok, 0 trigger emits in window — dormant by design because no installed skill currently declares `SKILL_OBSERVATION_TRIGGER`). T+4h through T+20h skipped per the same rationale used in Phase 1 Step 3 — repeated cadence sampling adds noise without value when the feature is dormant on idle traffic.

**Sign-off rationale:**
- 35 new passing tests (`tests/test_triggers.py`, all mocking `codec_dispatch.run_skill`); 0 new failures.
- 3 audit event constants (`trigger_fired`, `trigger_skipped`, `trigger_killed`) live in `codec_audit.PHASE2_STEP6_EVENTS` frozenset; emit-on-fire wired through `codec_observer._eval_triggers`.
- `routes/triggers.py` mounted: `GET /api/triggers` returns 401 for unauthenticated requests (correct gate behavior); authenticated PWA requests return the AST-discovered trigger list.
- Per-trigger kill via `POST /api/triggers/{key}/kill` writes atomic tmp+rename to `~/.codec/triggers_killed.json`; full-system kill via `TRIGGERS_ENABLED=false` env var.
- Stable `sha8` key per `(skill_name, trigger_type, params_hash)` ensures kill state survives skill rename.
- `_eval_triggers(snapshot)` runs in `try/except` inside the observation loop; a broken trigger module never breaks the daemon.

**Why no T+24h watch:** trigger events stay dormant until a skill declares a `SKILL_OBSERVATION_TRIGGER` constant. Until then, `_eval_triggers` runs every tick and returns the empty list — measured zero overhead. Sampling for events that can't fire is process noise.

**Phase 2 Step 7 work:** unblocked.

---

## Phase 2 Step 7 sign-off

> **Phase 2 Step 7 — production-stable as of 2026-05-02T20:49:40+02:00 (T+0 post-merge + skill install + PM2 restart of codec-observer).** Merge commit: `0e40687`.

**Samples captured:** T+0 (status=ok). End-to-end verified by direct invocation `python3 -c "import shift_report; shift_report.run('shift report')"` immediately after deployment. Result captured in `~/.codec/audit.log`:

```
2026-05-02T18:49:40.547+00:00  shift_report_started    cid=5f188e5485e5  trigger_kind=manual
2026-05-02T18:49:40.555+00:00  shift_report_completed  cid=5f188e5485e5  sections_included=2  word_count=69  audit_records_scanned=305  duration_ms=8.28
```

Notification posted to `~/.codec/notifications.json` with `type="shift_report"`, `title="CODEC Shift Report — 2026-05-02"`, full markdown body.

**Sign-off rationale:**
- 20 new passing tests (`tests/test_shift_report.py`, all filesystem-mocked to `tmp_path`); 0 new failures.
- Both audit event constants (`shift_report_started`, `shift_report_completed`) emit paired with shared `correlation_id` per Step 1 §1.4 contract.
- Manual trigger path bypasses per-day dedup so user can always re-run on demand; `time` and `idle` trigger paths honor `~/.codec/shift_report_state.json` to enforce one-report-per-local-day.
- Skill installed at `~/.codec/skills/shift_report.py` (22151 bytes); discovered by `codec_skill_registry`; exposed via MCP per `SKILL_MCP_EXPOSE = True`.
- `_maybe_fire_shift_report(idle_seconds)` integrated inside `codec_observer.run_daemon` loop; called every observation tick after `_eval_triggers`.
- Per-feature kill switches: `SHIFT_REPORT_ENABLED=false` env var, `shift_report.enabled: false` config, OR remove `~/.codec/skills/shift_report.py` (skill not discovered → not callable).

**Why no T+24h watch:** `time` and `idle` trigger paths fire at most once per local-date. The first scheduled `time` trigger after merge would be tomorrow at the configured `daily_at_hour:daily_at_minute`. Manual path verified live at T+0 with full audit + notification + state-file proof. The dedup mechanism is unit-tested in `tests/test_shift_report.py::test_per_day_dedup_blocks_second_idle_fire`.

**Phase 2 status: COMPLETE.** All 3 steps merged + production-stable. See `docs/PHASE2-COMPLETE.md` for the consolidated state report.

---

*Last updated: 2026-05-02 (Step 7 sign-off; Phase 2 complete).*

## 2026-06-09 — fact_extract silently no-ops on fact storage
`skills/fact_extract.py:93-95` calls `mem.store_fact(...)` inside a swallowed
try/except AttributeError — but `CodecMemory` has no `store_fact` method (it lives in
`codec_memory_upgrade`). Extracted facts are therefore never written to the facts table.
Found during the Daybreak audit (2026-06-09); out of Daybreak scope. Fix: route to
`codec_memory_upgrade.store_fact` and add a round-trip test.

## Pilot e2e skill files fail skill-contract tests (2026-07-02)

`skills/pilot_full_test_e2e.py` and `skills/pilot_e2e_test_fetch_example_title.py`
lack the required `SKILL_NAME` / `SKILL_TRIGGERS` module attrs, so 4
`tests/test_skills.py` contract tests fail on every run (pre-existing on main,
confirmed 2026-07-02 during the log-review PRs). They look like leftover Pilot
e2e scratch files, not real skills. Fix: either add the required metadata +
regenerate `skills/.manifest.json`, or delete both files.

**RESOLVED 2026-07-03:** they were auto-generated Pilot trace recordings
(May 13) living in `~/.codec/skills/`, not repo files. Moved to
`~/.codec/pilot_archive/` — recordings preserved, skills dir clean,
`tests/test_skills.py` fully green (160/160).

## Auth routes write unsigned plaintext into the HMAC-signed audit log (2026-08-21)

**RESOLVED 2026-09-05:** `routes/_shared._audit_event` routes every route-level audit
through `codec_audit.audit()` (HMAC, redaction, JSON). All 10 call sites (auth 8, media,
vision) converted; `_audit_write` survives only as a shim that converts a legacy string
into an envelope line, so plaintext can never reach the log again. The live log had
rotated by then: `verify_audit_log()` → integrity_ok=True.

`routes/_shared._audit_write` appends raw strings straight to `~/.codec/audit.log`,
bypassing `codec_audit.audit()` and therefore the whole PR-2E envelope — no HMAC,
no secret redaction, no JSON. Nine call sites in `routes/auth.py`, all
security-relevant, several carrying client IPs:

    [2026-08-21T12:15:12.532832] TOTP_SETUP: 2FA enabled

Three consequences, in order of severity:

1. **`audit_verify` reports the operator's log as tampered, permanently.**
   Measured on the live log: `total_lines 599, signed 597, broken 2,
   integrity_ok False` — and both broken lines are these auth writes. The
   tamper-detection feature built in PR-2E currently cries wolf at CODEC's own
   auth route, which trains the operator to ignore it.
2. **The events most worth signing are the only unsigned ones.** AUTH_SUCCESS /
   AUTH_FAILED / TOTP_* are exactly what an intruder would want to edit, and
   they are the lines with no HMAC over them.
3. **No redaction pass**, so anything that ends up interpolated into these
   f-strings lands in the log verbatim.

Found 2026-08-21 while measuring test pollution of the audit log; out of that
PR's scope. Fix: replace `_audit_write` with `codec_audit.audit()` calls using
proper event names (`auth_success`, `auth_failed`, `totp_enabled`, ...), then
re-run `verify_audit_log()` to confirm `integrity_ok` goes true. The two
existing broken lines stay — §6 forbids hand-editing the log, and per-line HMAC
cannot detect a deletion anyway.

## `_bootstrap_fleet` bypasses the ephemeral-location guard (2026-09-03)

`packaging/macos/first_run.py` now refuses to write LaunchAgents when the app runs
from a removable or translocated location (#335). But
`launcher/codec_app_main.py:_bootstrap_fleet` — the "launchd forgot the agents,
re-install them" path — calls `launchd/install_launchagents.sh` DIRECTLY, skipping
`first_run.py` and therefore the guard. An app launched from a mounted DMG with
zero agents loaded would bake `/Volumes/...` paths again. Fix: move
`refuse_if_ephemeral` into `install_launchagents.sh` (or call it from
`_bootstrap_fleet`) so every writer of a plist is covered. Also note: on a machine
running the PM2 dev fleet, `_bootstrap_fleet` starts the same services on the same
ports — the packaged app cannot be launched on the dev box without a port fight,
so packaged-app testing belongs on the Mac Air.

## 40 `test_llm_*` / stream / vision / agent_plan tests fail locally on main (2026-09-04)

On main @ cfd8b1e, `pytest` reports 40 failures across tests/test_llm_stream.py (15),
test_llm_async.py (8), test_stream_usage.py (5), test_llm_vision_dedup.py (4),
test_agent_plan.py (3), test_llm_raise_mode.py (2), test_skill_loader_unification.py,
test_security.py. Sample: `assert ['🔒 Cloud mod...local model.'] == ['Hello', ...]` —
codec_llm now returns a cloud-mode lock message where the tests expect a streamed
reply, i.e. a behaviour change in the persona/cloud-gating commits (8d72204..aca9270)
landed without updating these tests. CI's `smoke` job stays green, so it does not
exercise them. Confirmed pre-existing by diffing failing IDs on main vs a clean
branch: zero unique to the branch. Fix: decide whether the cloud-mode lock is the
intended default in tests (then update expectations) or gate it behind config in the
test fixture.

## `_fleet_loaded()` counts the app's own LaunchServices registration (2026-09-04)

`launcher/codec_app_main.py:_fleet_loaded()` counts `launchctl list` entries matching
`ai.avadigital.codec`. LaunchServices registers the running app itself as
`application.ai.avadigital.codec.<pid>.<n>`, which matches, so an app launched via
`open`/double-click sees "1 loaded" and skips `_bootstrap_fleet` even when zero fleet
agents exist — it then reports "fleet running: 1 launchd service(s)" with nothing
running. Launched directly from a shell the LS entry is absent, the count is 0, and
bootstrap runs (and on a dev box is correctly refused by the PM2 guard). Fix: match
`ai.avadigital.codec.<service>` labels only, excluding the `application.` prefix.
Found while diffing `open` vs direct-exec behaviour of the Mach-O launcher.

## `ava.license_key` is stored as plain text in `config.json` (2026-09-27)

`~/.codec/config.json:ava.license_key` holds the buyer's licence key (a signed JWT)
in plain text. PR-2B/2B-2 moved every provider secret to the Keychain, and
`codec_setup` notes that config.json is backed up and pasted into support threads,
yet this key stayed on disk. It is also the bearer `codec_setup.set_provider("ava")`
sends to the AVA proxy. Found while reviewing LLM config for the cloud fallback
model (docs/CLOUD-FALLBACK-MODEL-DESIGN.md). Fix: migrate it to a Keychain slot with
the same first-read migration as the PR-2B-2 getters and blank the field.

## `codec_sandbox` shares one profile file between callers (2026-09-28)

`codec_sandbox._write_sandbox_profile(allow_network=...)` rewrites the single
file `~/.codec/sandbox.sb` on every call. Two sandboxed runs that start at the
same time with different `allow_network` values race, and the last writer wins
for both. Found while reviewing `/api/run_code` for the 27 Sep audit follow-up.
Fix: write a per-call profile (a temp file, or one file per network mode).

## Sandbox docstrings claim "no process spawning" (2026-09-28)

`codec_sandbox.py` (module docstring), `skills/python_exec.py` (module docstring),
the comment at the top of `routes/vibe_exec.py`, and AGENTS.md §7 (`python_exec`
hardening) say the sandbox profile denies spawning processes. The profile
actually allows `process-fork`, and `process-exec` from `/usr`, `/opt/homebrew`
and the Python framework. What it does restrict is writes (to
`~/.codec/skill_output`, `/private/tmp`, `/private/var/folders`) and, when asked,
the network. Fix: correct the four descriptions, or tighten the profile and test
it.

## `codec_telegram.py` config example says an empty allowlist allows everyone (2026-09-28)

The example config at the top of `codec_telegram.py` says
`"allowed_chat_ids": []  // empty = allow all`. The code is fail-closed: an empty
or missing list denies every chat (`is_chat_allowed`, C2). Only the comment is
wrong. Fix: change it to "empty = deny all".

## `/ws/voice` does not check the handshake Origin (2026-09-28)

`routes/websocket.py:voice_websocket` authorizes the handshake with
`_ws_authorized` only. With no login configured (no `dashboard_token`, no Touch
ID / PIN), that allows any request from this Mac (`not _is_remote_request`), and
nothing checks the `Origin` header. So any web page open in the Mac's browser,
and the Vibe preview frame (opaque origin, `Origin: null`), can open
`ws://127.0.0.1:8090/ws/voice`, send audio or control frames into the
voice-to-skill pipeline and read the replies. `HostAllowlistMiddleware` checks
only `Host`, which is `127.0.0.1:8090` here. HTTP POSTs are covered by the
cross-site block in `AuthMiddleware` (`codec_dashboard.py`, Origin vs Host or
`TRUSTED_ORIGIN_HOSTS`, `null` refused); the WebSocket has no equivalent. Found
in the UI Phase 1 PR-A review. Fix (own PR): before `accept()`, close with 4403
when an `Origin` header is present and its host is neither the request `Host`
nor in `AuthMiddleware.TRUSTED_ORIGIN_HOSTS`, treating `null` as untrusted.

## Found during UI Phase 1 PR-C (2026-09-28)

- **Home question panels can vanish.** Question panels are inserted into
  `#chatList` (`codec_dashboard.html`, the ask_user panel code), and
  `loadChat()` replaces that list. A pending question can disappear while
  `_activePanels` still marks it as shown, so it is not drawn again. Fix: keep
  question panels in their own container outside `#chatList`.
- **Train-of-thought text contrast.** The collapsible reasoning text is 12px
  `--text-dim`, 4.18:1 on `--surface-2` in light. Fix: `--text-muted`.
- **`codec_mcp_http.py` reads `request.url.path`.** Same pattern as the
  dashboard's Host-header bypass fixed in #397; there a crafted Host only
  skips the rate limit, not the login. Fix: read `request.scope["path"]`.
- **Home Flash refuses any message with a backtick or `$(`.** `/api/command`
  runs `codec_config.is_dangerous` on the chat text, and that check treats a
  backtick as shell command substitution. "Reply with one inline `code` word"
  gets "Command blocked: matches a dangerous pattern". Deep Chat does not run
  this check; it relies on the skill-level gates (strict consent, the MCP
  block lists). Fix: decide whether Flash text needs this check at all, since
  it goes to the LLM, not to a shell.
- **Flash keeps polling after an error.** In `sendCmd` (`codec_dashboard.html`)
  an error from `/api/command` is shown, but the poll loop still starts and
  overwrites the error with "Processing... <message>" for up to 5 minutes. Fix:
  return after showing the error.

## Sidebar chat actions on the Audit page send no CSRF header (2026-09-29) — FIXED in P2.15 (the page is retired; /audit opens Settings > Audit)

`codec_audit.html` has no page fetch wrapper (the other six app pages add
`x-csrf-token` to every non-GET `fetch`). The shell's history actions
(`static/codec-shell.js`: rename, pin, archive via `PATCH`, delete via `DELETE`,
Save to Google Doc via `POST`) rely on that wrapper, so on the Audit page, with a
login configured, `AuthMiddleware` answers 403 "CSRF token mismatch". Found
while adding P3.13's push calls, which set the header themselves (`postJSON` in
the shell). Fix: route the history calls through the same helper, or add the
wrapper to `codec_audit.html`. Revisit: next UI queue item that touches the shell.

## The Vibe page's Screenshot quick setting does nothing (2026-09-29) — FIXED in P2.6

`codec_vibe.html`'s `takeScreenshot()` sends `POST /api/screenshot`, but the route
(`routes/media.py`) is GET-only and returns the PNG itself, so the request gets a
405 and the `alert()` it waits for never shows. The voice page had the same code;
P2.9 moved it to `GET` plus the shell toast (`CodecShell.toast`), showing the image
in the page's viewer. Fix Vibe the same way. Revisit: P2.6, which routes the
pages' `alert()` calls through toasts.

## A crew that needs no text cannot start from an empty Chat box (2026-09-29) — FIXED in P3.8

`codec_chat.html`'s `sendMessage` returns early when the box is empty and no
file is attached (it only releases a queued message then). The Agents branch
further down lists `daily_briefing` and `email_handler` as crews that run
without text, but it is never reached with an empty box, so picking one (in the
Agents sheet, or with '@' since P2.3) and pressing Send does nothing; any typed
word works. Fix: let the early return through when chatMode is 'research' and
the crew needs no text. Revisit: P3.8 (Activity board), which touches agent runs.

## Deleting a saved custom agent does not ask first (2026-09-29) — FIXED in P3.8

In Chat's Agents sheet (Custom Agent), the red Delete next to "Load saved"
(`deleteSelectedAgent` in `codec_chat.html`) removes the selected agent at once,
with no question and no undo. It never used the browser's `confirm()`, so P2.6
left it as it was. Fix: ask with `CodecShell.ask({danger: true})` first, like
the other deletes. Revisit: P3.8 (Activity board), which touches agents.

## `test_shell_mounts_and_switches_on_desktop` expects the old shell order (2026-09-29) — FIXED in P2.13

`tests/test_ui_shell.py::test_shell_mounts_and_switches_on_desktop` (jsdom only, so skipped in CI) asserts the
shell mounts `csSide, csScrim, csTop, csTabs` first. Since P2.8 the reading strip `csPlayer` is mounted before
`csTabs`, so the test fails wherever jsdom is available (the Mac with NODE_PATH set). The shell is right; the
expected list is stale. Found while running the suite with jsdom for P2.7. **Revisit when:** the next item that edits
tests/test_ui_shell.py (P2.13 accessibility): add `csPlayer` to the expected order.

## A missing notifications file is refilled with four made-up reports (2026-10-06)

`routes/_shared._load_notifications` writes four sample reports ("Daily Morning
Briefing", "Security Scan", "AI News Digest", "Weekly Code Review Summary") to
`~/.codec/notifications.json` when the file is missing or cannot be parsed, and
returns them. On a fresh install, or after a damaged file, they show in the Inbox
(P3.2) as real reports, as they did in Tasks' old Reports tab. P3.4's Today reads
the file directly and is not affected. Fix: return `[]` instead of seeding (and drop
the samples). Revisit: P3.11 or any item that touches notifications.

## fact_extract writes a fixed user_id (2026-10-06)

`skills/fact_extract.py`'s `_save` stores each learned fact's conversation row with `user_id="mickael"`, a
personal name in the public repo, while every other memory path uses the default user id. Found while fixing its
structured write in P3.12 (Learning page), which left this line as it was. Fix: use the default user id (or
`config.json`'s), regenerate the skill manifest, and decide whether the existing rows need it changed.

## The iMessage and Telegram bridges write plain text into the audit log (2026-10-07)

`codec_imessage.py` and `codec_telegram.py` each have their own `audit(msg)` that
appends a plain line such as `[2026-10-07T08:54:09] IMESSAGE: SERVICE_START`
straight to `~/.codec/audit.log`, with no JSON, HMAC or redaction (six call
sites, including service start). `verify_audit_log()` counts every such line as
broken, so Settings > Audit says the log failed its integrity check after each
bridge restart (2 broken lines today, 2 on 6 Oct). The route-level writer had the
same problem and was fixed on 2026-09-05 (entry above). Fix: send these through
`codec_audit.log_event` like the other services. Found in the Mac merge pass of
#418-#439. Revisit: the next item that touches either bridge.

## Inbox actions show "HTTP 400" instead of the reason (2026-10-07) — FIXED (the shell reads {error} or {detail})

When an Inbox action is refused (for example a grant that is not allowed), the
toast says "HTTP 400" and drops the server's `error` / `detail` text, so the
owner cannot tell why. Fix: read the JSON body's `error` or `detail` before
falling back to the status code. Found in the Mac
merge pass (P3.2). Revisit: any item that touches the Inbox drawer.

## The dashboard waits on the local model at startup (2026-10-07)

After a restart, `codec-dashboard` took 90-129 s to answer while the local model
was stuck, and 1 s once the model answered again, so a startup step most likely
waits on a model call (not traced yet). Pages are unreachable until then. Fix:
find that call and run it after startup, in the background, with a short timeout. Found in the Mac merge pass. Revisit:
the next item that touches dashboard startup.

## Automatic triggers show their raw pattern as the title (2026-10-07)

The 'CODEC is watching' menu's Automatic triggers list (P3.7) titles each trigger
with `codec_triggers`' summary, which is the raw match rule, for example
`clipboard~https?://[^\s<>'"]+`. Fix: give each trigger a plain sentence ("When
you copy a web link") from the skill's `SKILL_OBSERVATION_TRIGGER`, falling back
to the skill's name. Found in the Mac merge pass. Revisit: the next item that
touches triggers.

## The Activity board says "running for" a project that is waiting (2026-10-07)

Tasks > Activity (P3.8) shows "running for N min" under every project, including
one waiting for approval or paused, where it is the time since it started. Fix:
"started N min ago" unless the project is running. Found in the Mac merge pass.
Revisit: the next item that touches the Activity board.

## Approved skills are written into the repo's `skills/` folder (2026-10-07) — FIXED (user skills folder, docs/USER-SKILLS-DIR-DESIGN.md: approve writes to ~/.codec/skills and every registry reads it)

`POST /api/skill/approve` (the review-and-approve flow, and Settings > Learning's
Approve and install since P3.12) writes to `routes._shared._get_skills_dir()`,
which is `codec_config.SKILLS_DIR`: `config.json:skills_dir`, or the repo's own
`skills/` folder when that key is not set. This Mac's config has no `skills_dir`,
so an approved skill lands as an untracked file in `~/codec-repo/skills/` (not in
`~/.codec/skills/`, the user folder AGENTS.md describes), next to the hash-pinned
built-ins, where git operations on the live tree can trip over it. The load-time
gate still checks it (not in the manifest, so the AST check runs). Fix: write
approved skills to `~/.codec/skills/` (or set `skills_dir` there) and say so on
the page. Found in the Mac merge pass (P3.12 check). Revisit: before anyone
approves a skill from the Learning page.
