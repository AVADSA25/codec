# Buyer launchd fleet: status, restarts and log size (P-032)

## What and why

On a buyer's Mac the CODEC fleet runs as launchd LaunchAgents
(`ai.avadigital.codec.<name>`, docs/W5-3-LAUNCHD-DESIGN.md), and there is no
PM2. Everything that reports on the fleet asked PM2 only:

| Reader | What it showed on a launchd Mac |
|---|---|
| `codec_heartbeat._pm2_jlist` (restart storms, errored apps, health beats, start grace, memory check, daily status) | `[]`: "0/0 apps online", no restart or crash report |
| `routes/connections.pm2_status` (Settings > Connectors, bridges) | no running state for Telegram or iMessage |
| `routes/cortex.cortex_health` | only the four HTTP probes, no service list |

launchd's `KeepAlive` already restarts a killed service (the generator sets it
from `autorestart`), so the missing parts are reading the state and keeping the
log files small: launchd appends to `~/Library/Logs/CODEC/<name>.out|.err` for
ever. The heartbeat also wrote up to 100 characters of saved chat text, the
auto-task text and an email preview into its log lines.

## Changes

**`codec_supervisor.py` (new, stdlib only): the supervisor adapter.**

- `kind(cfg=None, repo=None)` returns `"pm2"` or `"launchd"`.
  `config.json:supervisor` (`"pm2"` or `"launchd"`) wins; any other value or
  none means automatic: a CODEC that runs from inside the `.app` bundle
  (`codec_doctor.install_kind(repo) == "app"`, the doctor's own rule) is
  launchd's, everything else (the Studio's checkout, CI) is PM2's. **PM2 stays
  the default path**, and on the Studio nothing changes.
- `launchd_jlist()` returns CODEC's LaunchAgents as `pm2 jlist` entries, so
  every existing reader works unchanged:
  `{"name", "pid", "pm2_env": {"status", "autorestart", "restart_time",
  "pm_uptime", "exit_code"}}`. Nothing else is read (no environment).
  - Labels: `launchctl list` lines that start with `ai.avadigital.codec.`
    plus the plists of that name in `~/Library/LaunchAgents` (a plist that is
    not loaded is `stopped`). If `launchctl list` fails, the answer is `[]`, as
    a failed `pm2 jlist` was.
  - Each label: `launchctl print gui/<uid>/<label>`, its one-tab-deep
    `key = value` lines only. `state = running` -> `online` (with `pid`);
    `spawn scheduled` -> `waiting restart` (PM2's word for the same moment);
    any other state with `keepalive` in `properties` -> `errored` (launchd is
    not starting it again); otherwise `stopped`.
    `restart_time` = `runs` - 1. `autorestart` = `keepalive` in `properties`.
    `exit_code` = `last exit code`, or minus the signal number of
    `last terminating signal`.
  - `pm_uptime` (start time, epoch ms, for the 5-minute start grace) from one
    `ps -o pid=,etime= -p <pids>` call.
  - Read only: `launchctl list` and `launchctl print` only. Nothing is loaded,
    unloaded, started or killed.
- `logs_hint(name)`: `pm2 logs <name>` on PM2, `its log
  ~/Library/Logs/CODEC/<name>.err` on launchd, for the alert texts.

**Readers.** `codec_heartbeat._pm2_jlist`, `routes/connections.pm2_status` and
`routes/cortex.cortex_health` ask `kind()` first and read `launchd_jlist()` on
launchd; their PM2 code is unchanged.

**Heartbeat reports on launchd** (one heartbeat cycle = codec-dashboard's
30-minute loop since P-083):

- A killed service launchd brought back: `runs` grew by 1 to 4 since the last
  heartbeat -> one warning log line (`launchd restarted <name> 1x since the
  last heartbeat (last exit code -9)`). 5 or more is the existing restart-storm
  alert, worded `launchd restart storm`.
- A KeepAlive service launchd is not starting again (`errored`): the existing
  errored-app alert, worded for launchd, and its recovery message.
- The alert texts point at the launchd log file instead of `pm2 logs`. On PM2
  every text is byte for byte what it was.
- The HTTP probes and health beats are unchanged and now see real states.

**Log size: `codec_log_trim.py` (new, stdlib only).** `trim_folder(folder)`
looks at every `.out`, `.err` and `.log` file directly in the folder (no
links, no sub-folders). One over 10,000,000 bytes (10 MB, also under 10 MiB)
keeps its last 5,000,000 bytes, cut at a line start, in place: the same file
(same inode and mode), opened `O_APPEND`, tail read, re-read (up to three
times) while it grew meanwhile, truncated to 0 and the tail appended. launchd opens the log
with `O_APPEND`, so the service keeps writing into the trimmed file. A line
written in the microseconds between the last re-read and the truncate can be
lost (diagnostics only). The heartbeat runs it every cycle **on launchd only**
(`trim_service_logs`), and `python3 codec_log_trim.py [--dir DIR]` runs it by
hand. It logs names and sizes only.

**Heartbeat log lines carry lengths.** The pending-task scan, the auto-task
lines and the unread-mail line log counts and lengths, never message text. The
alert itself (the owner's notification) is unchanged.

## Not changed

- `codec_models.restart_server`, `codec_alerts._try_restart` and Cortex's
  restart and log buttons: restarting under launchd belongs to the model-picker
  item P-039 (part 2, the model switch only: docs/LAUNCHD-MODEL-RESTART-DESIGN.md). On launchd the heartbeat's restart attempt still calls PM2, which
  is absent, and KeepAlive does the restart.
- PM2 process names, `ecosystem.config.js`, the generator, the plists.
- **Per-service crash-cap wrapper: not built, not needed now.** It waits for
  the Mac Air sheet; build it only if that sheet records a crash loop.
  launchd's `ThrottleInterval` (10 s minimum) already caps a loop at 6 starts a
  minute, and the restart-storm alert reports it within one heartbeat cycle.

## Approval

New architecture next to a section 10 zone (AGENTS.md section 11). Mickael's
word, 3 Nov 2026, on finishing the 25 Oct audit plan:
"go go build i trust ur judgment here".

## Test plan

- `tests/test_supervisor.py`: `kind()` (config override, the `.app` rule, the
  checkout); `launchd_jlist()` on fixture `launchctl list` / `print` / `ps`
  outputs (running, spawn scheduled, KeepAlive not running, plain stopped, a
  plist that is not loaded, labels that only look like CODEC's, launchctl
  missing); the three readers on launchd and on PM2 (the same `pm2` command as
  before); the heartbeat's restart line, storm and errored texts on both.
- `tests/test_log_trim.py`: a 12 MB fixture in a temp folder ends at 5 MB or
  less, starts at a line, ends with the old last line, keeps its inode, and a
  writer holding it open with `O_APPEND` still lands in it; small files, other
  suffixes and links are left alone; the heartbeat trims on launchd only.
- `tests/test_heartbeat.py`: the three log lines carry lengths, not the text.
- Existing: `tests/test_model_switching.py`, the heartbeat, connections and
  cortex tests stay green.
- On the Mac Air (owner): Connections and Cortex match `launchctl print`, a
  killed service comes back and the next heartbeat logs it, a 12 MB fixture log
  in `~/Library/Logs/CODEC` is trimmed.

## Rollback

Revert the PR. PM2 stays the default path, so the Studio is unaffected. On a
buyer Mac, `"supervisor": "pm2"` in `config.json` turns the launchd reading off
without a revert. The trim only removes the oldest lines of log files, which
hold diagnostics only.
