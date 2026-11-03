# Boot grace: no false "down" or model restart in the first 5 minutes (P-059)

Plan item P-059 (25 Oct 2026 audit, finding BO-5). Owner's word on 3 Nov 2026: "go go build i trust ur
judgment here". Status: built, merged with the PR that carries this file.

## What and why
After every reboot or deploy the heartbeat checked the services at once, while they were still starting:
- `codec_alerts.check_services_and_alert` counted the model server's closed port as a failure and, on the
  first failure, ran `pm2 restart qwen3.6`. The server binds its port only after its model is loaded
  (`scripts/start_model_server.sh`), so the restart threw away a load in progress and started a second full
  one (the 36 GB reload and the double-load memory peak).
- `codec_heartbeat.check_system_health` logged a `service_down` audit event for every service that did not
  answer yet, the dashboard included (the first tick ran before uvicorn had opened its port). 185 false
  `service_down` events in 30 days.

`codec_models._server_loading` already treats a PM2 app that is online and started less than 5 minutes ago
as loading, but only the cloud fallback used it.

Since 29 Oct (D-29) the heartbeat has no PM2 entry of its own: it runs as `_bg_heartbeat` inside
codec-dashboard. `codec_heartbeat.run_daemon` remains for a hand start (`python3 codec_heartbeat.py`).

## Change
- **`codec_models.pm2_app_loading(procs, name, now=None)` (new, pure).** The rule `_server_loading` used,
  taken out so it can run on a `pm2 jlist` list someone already holds: the app is `online` and
  `now - pm_uptime / 1000 < 300`. `_server_loading` now calls it after its own `pm2 jlist`; its answer is the
  same. Bad entries give False instead of an exception.
- **`heartbeat()`** runs `pm2 jlist` once, before the probes, and hands the list to `check_system_health`,
  the PM2 checks (unchanged) and `check_services_and_alert`.
- **`check_services_and_alert(procs=None, now_ts=None)`.** A failed probe of a built-in service whose PM2
  app (`_PM2_NAMES`) is still in its 5-minute grace is not counted, sets no "down since", restarts nothing
  and alerts nothing; it logs one line. Everything else is as before: after the grace one failed probe
  restarts the app (5-minute cooldown unchanged), two alert. `extra_services` have no PM2 app and are
  unaffected. Without `procs` nothing is known about start times and every failed probe counts, as before.
- **`check_system_health(procs=None, now_ts=None)`.** A service in its grace that does not answer reads
  `starting` in the log and logs no `service_down` event.
- **First tick 120 s after start** (`codec_heartbeat.FIRST_TICK_DELAY_S`): in the dashboard's
  `_bg_heartbeat` and in `run_daemon`. `python3 codec_heartbeat.py once` still runs at once.
- **Deleted:** the `pm2 show codec` exec-cwd block at the end of `check_services_and_alert`. On the
  Studio there is no PM2 app named `codec` (the main one is `open-codec`), so `check_output` always raised
  and the `except` swallowed it: the check could never alert there. Only an `install.sh` install, which
  starts `ecosystem.config.js`, has an app named `codec`; there the check compared its folder with a fixed
  `~/codec-repo`, so a clone anywhere else got a false warning.

No audit event, config key, file or schema is added or changed.

## What is not touched
`scripts/start_model_server.sh`, the model switch (`set_active`, `restart_server`), the cloud fallback's
decisions, the PM2 restart-storm and errored-app checks, `_HTTP_BLOCKED`, the audit envelope.

## Tests
`tests/test_boot_grace.py`, with a fake clock and a fake `pm2 jlist`: the grace rule and its edges; every
service failing 10 s after a boot gives no restart, no failure count, no alert and no `service_down`; one
failed probe at 400 s restarts `qwen3.6` once, and a second failure 120 s after that restart is held by the
unchanged cooldown; `heartbeat()` hands one jlist to both checks; both loops wait 120 s before the first
tick. Existing: `tests/test_watchdog.py`, `tests/test_remote_alerts.py`, `tests/test_cloud_auto_fallback.py`.

By hand (owner, at his next planned restart, or on the Mac Air): the first 300 s of the dashboard log hold
no `Auto-restarting` line and the audit log no `service_down` event. Nothing is stopped to check it.

## Rollback
Revert the PR and restart codec-dashboard. No state changes shape: `~/.codec/alert_state.json` keeps the
same keys.

## Risks
- A service that crashes again and again inside its first 5 minutes is not counted by this check while it
  shows `online`. A crash inside 60 s counts toward PM2's restart cap (`min_uptime` 60 s, `max_restarts`):
  PM2 then marks the app `errored`, which the heartbeat counts and alerts as before. A crash later than
  that does not count toward the cap, but a loop short enough to stay inside the grace restarts at least 6
  times in the dashboard's 30-minute interval, and the restart-storm check alerts on 5 or more restarts
  between two ticks.
- The first tick after a dashboard restart comes 120 s later than before, so a server that is really dead
  at that moment is restarted 2 minutes later than today. Later ticks keep their interval.
