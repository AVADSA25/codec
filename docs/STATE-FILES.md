# CODEC State-File Registry

> Canonical map of every `~/.codec/*.json` (and related) state file: who writes
> it, who reads it, and its persistence/locking policy. Introduced by Fix #9
> (audit C8) to make the `codec_jsonstore` convergence trackable.

## Persistence policy levels

- **SAFE** — writes via `codec_jsonstore.atomic_write_json` / `file_lock` /
  `read_modify_write` (tmp + fsync + atomic replace + 0600; cross-process lock
  for read-modify-write). This is the target for every state file.
- **OWN-ATOMIC** — a module-local hand-rolled tmp+fsync+replace helper
  (durable, but duplicates the primitive; Phase 3 converges these).
- **AD-HOC** — raw `json.dump(f)` / `write_text(json.dumps(...))`: no fsync,
  often no cross-process lock. Phase 1/2 migration targets.

## Registry

| File | Writer(s) | RMW? | Policy | Status |
|---|---|---|---|---|
| `notifications.json` | `routes/_shared._save_notification`, `_write_notifications`; `codec_ask_user` (question + mark-read); `codec_agent_messaging.post_message` | yes | **SAFE** | `_write_notifications` atomic (C-3); RMW under `file_lock` — `_save_notification` (Fix #9 P2), ask_user (Fix #5), messaging (B-11) |
| `pending_questions.json` | `codec_ask_user._save_pending_questions` | yes | OWN-ATOMIC (+`file_lock`) | RMW already under `file_lock` (C-4). Helper `_atomic_write_text` → converge in Phase 3. **Don't-touch zone.** |
| `agents/<id>/grants.json` | `codec_agent_plan.save_grants` (+ `grants_lock`) | yes | OWN-ATOMIC (+`file_lock`) | RMW under `grants_lock` (Fix #5). Helper `_atomic_write_json` → Phase 3. **Tamper-hashed.** |
| `agents/<id>/{plan,state,manifest}.json` | `codec_agent_plan.save_*` (manifest CAS under `_status_lock`) | manifest yes | OWN-ATOMIC | `_atomic_write_json` (0600/0700). Phase 3 convergence. **Don't-touch after approval.** |
| `agents/<id>/messages.jsonl` | `codec_agent_messaging.post_message` | append | n/a (append) | append-only JSONL, not a JSON doc |
| `agent_silence.json` | `codec_agent_messaging.set_silenced` | yes | review in Phase 2 | per-agent silence |
| `oauth_state.json` | `codec_oauth_provider._save` (fallback) | no | **SAFE** | `atomic_write_json` (Fix #1b). Keychain-primary. **Don't-touch zone.** |
| `audit.log` | `codec_audit` | append | bespoke (flock + HMAC) | PR-4E/PR-2E — its own contract, NOT json.dump. **Don't-touch zone.** |
| `config.json` | `codec_config`, `routes/auth`, `codec_dashboard` | yes | AD-HOC | **Phase 4** (don't-touch: holds migrated-out secrets, auth/TOTP). Per-file sign-off. |
| `custom_triggers.json` | `routes/skills.save_triggers` | yes | AD-HOC | Phase 1/2 |
| `triggers.json` / `triggers_killed.json` | `codec_triggers` | yes | review | Phase 2 |
| `schedules.json` | `codec_scheduler`, `codec_dashboard` | yes | AD-HOC | Phase 1/2 |
| `voice_session.json` | `codec_voice` | no | AD-HOC | Phase 1 |
| `<google token>.json` | `codec_google_auth` | no | AD-HOC | **Phase 4** (don't-touch: OAuth token). |
| `.marketplace.json` | `codec_marketplace` | yes | AD-HOC | Phase 1 |
| daemon state (`codec_alerts`, `codec_heartbeat`, `codec_imessage`, `codec_proactive`, `codec_agent_messaging`) | respective module | mixed | AD-HOC | Phase 1 |
| `pomodoro` state | `skills/pomodoro` | no | AD-HOC | Phase 1 |
| E2E keys / auth sessions (`routes/_shared`) | `_save_e2e_keys`, `_save_sessions` | no | AD-HOC (0600) | Phase 1 (sensitive — review) |

## Migration backlog (Fix #9 follow-on)

Done in the current PR: **Phase 0** (primitive hardening: `atomic_write_json`
`default=`/`sort_keys=`, new `read_modify_write`) and **Phase 2**
(`_save_notification` cross-process `file_lock`).

Remaining, tracked for follow-on commits:
- **Phase 1** — migrate the AD-HOC full-overwrite writers above to
  `atomic_write_json` (mechanical durability upgrade; one batch).
- **Phase 3** — converge the OWN-ATOMIC duplicate helpers
  (`codec_ask_user._atomic_write_text`, `codec_agent_plan._atomic_write_json`)
  onto the now-`default=`-aware `codec_jsonstore.atomic_write_json`, keeping the
  named helpers as thin shims. NOTE: these write don't-touch files
  (`pending_questions.json`, agent `grants/manifest`), so convergence is
  byte-output-preserving and reviewed per-helper.
- **Phase 4** — the **don't-touch** files (`config.json`, Google/OAuth tokens,
  auth writes): migrate last, one file per commit, with operator sign-off
  (same protocol as Fix #1b's `oauth_state.json` surfacing).

## Why no broad "raw json.dump" CI guard

Considered (the Fix #10 A-12 guard is the model) but rejected: `json.dump` has
many legitimate non-state uses, so a repo-wide ratchet is low-signal /
high-false-positive. Regression prevention instead relies on (a) this registry,
(b) the existing source-level guards `test_json_write_safety.test_notifications_writer_atomic`
and `test_ask_user_uses_file_lock`, and (c) per-RMW concurrency tests added with
each migration phase.
