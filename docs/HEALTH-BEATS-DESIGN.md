# Health beats: a bridge that is online but failing raises an alert (P-017)

Plan item P-017 (25 Oct 2026 audit, findings BO-2, GP11-4, BC-5). Owner's word on 3 Nov 2026: "go go build
i trust ur judgment here". Builds on P-058 (iMessage backoff) and P-059 (boot grace), both merged.

## What and why
PM2 shows a bridge "online" while every round of its loop fails: codec-imessage logged a refused Messages
database every 3 s for weeks, codec-telegram's `get_updates` swallows every error and returns `[]`, and
codec-autopilot logs a tick error and sleeps. The heartbeat only probes HTTP services, so none of this
reached the owner. A bridge that hangs inside a call (no timeout) looks the same: online, silent.

## Change
**`codec_health.py` (new, stdlib plus `codec_jsonstore`, which is stdlib too).**
- `beat(name, *, cadence_s, error=None)`, called once per loop round. `error` is the round's exception, an
  exception class or a short class name, or None. The module counts failed rounds in a row and writes
  `<HEALTH_DIR>/<name>.json` atomically (`codec_jsonstore.atomic_write_json`, 0600, folder 0700):
  `{ts, ok, consecutive_errors, last_error_class, cadence_s, stopped}`. `cadence_s` is the expected gap
  between writes, `max(loop cadence, 60)`. It writes at most once a minute unless what the file says
  changes (ok flips, a new error class, the cadence, the 20-errors mark). It never raises.
- `stopped(name)` marks a stop on purpose (called from each daemon's SIGTERM cleanup), so a `pm2 stop` or a
  launchd unload is not reported as silent.
- `HEALTH_DIR` is `~/.codec/health`, or `CODEC_HEALTH_DIR` (tests/conftest.py points it at a temp folder).
- `verdict(beat, now)`: `silent` when the beat is older than 3x its cadence, never less than 15 minutes
  (the heartbeat looks every 30 minutes, so the floor costs no detection time, and one long round, a deep
  report or a slow model reply, must not page him); `failing` when `consecutive_errors >= 20`; else `ok`.
- `observer_beat(audit_path, now)`: the observer gets no new call. Its beat is the newest
  `observation_tick` / `observation_tick_slow` line in the last 2 MB of `audit.log` (about four days of
  log), with that line's `cadence_used_s`. No tick in the tail counts as silent only when the tail itself
  is older than the stale window; otherwise it is unknown (just after the daily rotation) and nothing is
  sent or cleared.

**Daemons, one `beat` call in each loop (plus `stopped` in the existing cleanup):**
- codec-imessage: after each poll, before the sleep; cadence = the next sleep (3 s, or 300 s once slowed by
  P-058); error = the poll-cycle exception, or `DbReadHealth.last_error` (`PermissionDenied` for a refused
  read, else the exception class), a new attribute set in `failed()` and cleared in `succeeded()`.
- codec-telegram: after each `get_updates`; cadence 40 s (30 s long poll + 10 s); error =
  `TelegramBot.last_error`, a new attribute: the exception class, or `TelegramApiError` when Telegram
  answers `ok: false`. A long-poll timeout is the normal case and is no error.
- codec-autopilot: after each tick; cadence 30 s; error = the tick exception, or `StateFileCorrupt` while
  autopilot is on and its state file cannot be parsed (it then refuses to fire every trigger, PR-4I L-2).
- codec_agent_runner.py: no beat (AGENTS.md section 10; it would need its own design and his word).

**Heartbeat (`codec_heartbeat.check_health_beats(procs=None, now_ts=None)`).** Called in `heartbeat()`
with the one `pm2 jlist` list, before the daily status. Since D-29 the heartbeat runs inside
codec-dashboard (`_bg_heartbeat`, every 30 minutes); `run_daemon` is unchanged. For each beat:
- With a PM2 list: an app that is not listed or not `online` is skipped as stopped (errored apps already
  alert through `check_errored_codec_apps`); an app in its 5-minute start grace
  (`codec_models.pm2_app_loading`, P-059) is skipped this round.
- `silent` -> `alert_once("silent:<name>", "critical", ...)`; `failing` -> `alert_once("failing:<name>",
  "warning", ...)`; ok -> `alert_resolved` for both; stopped -> `alert_resolved` with "no longer checked".
- The observer is checked only when the PM2 list shows codec-observer online, its PM2 env does not say
  `OBSERVER_ENABLED=false`, `config.json:observer.enabled` is not false, and watching is not paused.
- Messages carry the service name, minutes, a count and an exception class name only: no message text,
  sender, path or user data. The daily status labels the new problems "<name> silent" / "<name> failing".

No audit event, config key or schema is added. `codec_alerts.py` is not changed.

## Outside monitor (parts 3 and 4): proposed, not done (needs his word)
The Worker `studio-watchdog` (`~/Documents/Claude/Obsidian/_system/macs/watchdog/`) already lists
`https://codec-mcp.avadigital.ai/health` (checked 3 Nov: 200). `https://codec.avadigital.ai/api/health`
answers 302 to `lucy-vpa.cloudflareaccess.com` (checked 3 Nov), and the Worker counts a 302 as up, so the
dashboard entry is useful only after an Access bypass. Nothing here was edited or deployed.

1. Access bypass, paths only (recommended; no Worker code change; `/api/health` is already public in the
   dashboard and returns only `{status, service, timestamp}`): Zero Trust > Access > Applications > Add a
   self-hosted application "CODEC dashboard health", domain `codec.avadigital.ai`, path `api/health`;
   one policy "watchdog health probe", action **Bypass**, include **Everyone**. Access applies the most
   specific path, so every other path keeps the current login. Check: `curl -s -o /dev/null -w "%{http_code}"
   https://codec.avadigital.ai/api/health` prints 200, not 302. (Service-token alternative: a token
   "studio-watchdog" plus a Service Auth policy on the existing app, and a Worker change to send
   `CF-Access-Client-Id` / `CF-Access-Client-Secret` for that target from two new secrets.)
2. Then add one line to `targets.json` and deploy (`npx wrangler deploy` in that folder):
   `{"name": "CODEC dashboard", "url": "https://codec.avadigital.ai/api/health"}`
3. Dry run (part 4): add `{"name": "TEST dead host (P-017, remove after)", "url": "https://p017-watchdog-test.invalid/"}`
   (`.invalid` never resolves), deploy, wait for two 5-minute checks: Telegram shows "TEST dead host ...
   down since ... (HTTP no answer); the rest of the Studio answers." Remove the line and deploy again.
   Nothing on the Studio is stopped for this test.

## Owner steps (part 5)
`pm2 restart codec-telegram codec-autopilot codec-dashboard` (the dashboard holds the heartbeat; there is
no codec-heartbeat app since D-29). codec-imessage is stopped on the Studio; restart it only if he runs
it. `alerts.telegram.chat_id` is already set in his config (checked 3 Nov, value not read out).

## Tests
`tests/test_health_beats.py`, every path in tmp (never a fake HOME): the file and its modes, the
once-a-minute write, the error count and class names, `beat` never raising; with a fixture folder, one
alert for a stale beat and none for a fresh one, one for 20 errors and none for 19, a recovery, skips for
a stopped mark, a PM2-stopped app and the start grace; the observer from fixture audit lines (old tick,
recent tick, paused, a short tail, no PM2 app); each daemon's loop writes its beat (main() run with fakes).
Existing: test_imessage_backoff, test_remote_alerts, test_boot_grace, test_heartbeat, test_lifecycle,
test_small_reliability.

## Rollback
`git revert` the commit; delete `~/.codec/health/`. If parts 3 and 4 were applied: remove the
"CODEC dashboard" line from targets.json and deploy; he removes the Access bypass application.
