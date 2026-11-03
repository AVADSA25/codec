# Doctor: one read-only check of this Mac, with a redacted support report (P-022)

Plan item P-022 (25 Oct 2026 audit, GAP-601, GAP-602, GAP-614, GAP-416). Owner's word on 3 Nov 2026: "go go
build i trust ur judgment here". Builds on P-017 (health beats, merged in #527).

## What and why
The known leftovers on the Studio (keys in plain text in old copies of config.json, a readable
mcp_servers.json, PM2 logs other accounts can read, services listening on every network interface, code
merged and pulled but services not restarted, a service whose health beat went quiet, a missing macOS
permission) are found by hand today, one at a time. A buyer's Mac has the same blind spots and no one to
look. One read-only check finds them all and says what to do, and a redacted zip lets a buyer send the
result to support without sending a key, a file's contents or their home-folder path.

## Change
**`codec_doctor.py` (new; stdlib plus `codec_health`, which is stdlib too).** Top-level so it ships in the
app bundle with the other `*.py` files (`tools/` does not ship), so Settings can run it on a buyer's Mac.
`run(paths=None, now=None)` returns `{doctor: 1, generated, about, summary, checks, permissions_note}`; each
check is `{id, title, status, detail, fix, url?, data?}` with status FAIL, WARN, OK, INFO or N/A. It reads
only; it never writes a file, starts or stops a process, or asks macOS for a permission.

| id | reads | FAIL | WARN |
|---|---|---|---|
| `config_backups` | `~/.codec` and `~/.codec/backups`: files named `config*.json*` and `oauth_state*` other than the live `config.json` (JSON only, 5 MB cap, links skipped) | a copy holds a key in plain text (count and field names only, e.g. `telegram.bot_token`) | |
| `mcp_servers` | mode of `~/.codec/mcp_servers.json`, number of filled keys | others can write it, or read it while it holds a key | others can read it (no key yet) |
| `zshrc` | mode of `~/.zshrc`, number of lines that set a key or token | others can write it, or read it while it sets a key | |
| `logs` | modes of `~/.pm2/logs/*` and `~/Library/Logs/CODEC/*` | | some can be read by other accounts (counts only) |
| `listeners` | `lsof -nP -iTCP -sTCP:LISTEN` | | a CODEC port (dashboard 8090, MCP 8091, model, speech, voice, Vibe 5300-5399) listens on every interface; other programs are a count and ports |
| `code_version` | git installs only: the newest HEAD reflog line (time and short id; never the name or email in it) against each PM2 app's start time whose folder is this checkout, leaving out the apps that run an installed package (the model process, `kokoro-82m`); N/A on the app | | a service started before the code last changed |
| `health_beats` | `codec_health.read_beats(folder)` with the heartbeat's PM2 rules (not online = stopped, 5-minute start grace, an old stop mark checked by age) | a beat is silent | a beat is failing |
| `model_server` | `llm_base_url` from config.json, only when it is on this Mac (`localhost`, `127.0.0.1`, `::1`): GET `<base>/models`, 3 s, no proxy; never a cloud or LAN address | nothing answers | no answer in 3 s |
| `perm_<key>` x5 | Screen Recording, Accessibility, Full Disk Access, Microphone, Automation | | not allowed (with the System Settings link) |

- Permissions are read with calls that cannot prompt: `CGPreflightScreenCaptureAccess`, `AXIsProcessTrusted`,
  opening the Messages database, `[AVCaptureDevice authorizationStatusForMediaType:AVMediaTypeAudio]` and
  `AEDeterminePermissionToAutomateTarget(..., askUserIfNeeded=false)` for Messages, Notes, Reminders,
  Music, System Events, Google Chrome and Terminal (all through ctypes, stdlib). A state that cannot be
  read (an app that is not open, a macOS that does not answer, not a Mac) is INFO or N/A with the reason.
  A missing permission is WARN, not FAIL: the dashboard's own grant is not the voice or bridge process's.
- The report says the permissions are those of the process that ran the check (the dashboard from
  Settings) and that the voice call, the wake word and the bridges have their own, which the dashboard
  cannot read.
- `pm2 jlist` runs only when the PM2 daemon's pid file names a live process whose title is PM2's "God
  Daemon" (so the check never starts PM2, even when another program got a dead daemon's pid), and never
  on the app, whose services run under launchd; only name, pid, status, start time and folder are kept
  from it (it also carries environments).
- Every string of the report goes through `scrub()`: the home folder becomes `~` and token shapes
  (Anthropic, OpenAI, AWS, GitHub, Slack, Google, JWT, Bearer, CODEC OAuth, Telegram bot, long hex,
  private-key headers) become `<redacted>`. The checks themselves only report counts, modes, fixed paths
  and setting names; scrubbing is the second layer. Exit code 1 when any check is FAIL.
- `build_zip(report)`: a stdlib `zipfile` with `codec-doctor.json` and `codec-doctor.txt`.

**`tools/doctor.py` (new).** The command line: `python3 tools/doctor.py [--json] [--repo DIR]`. Text by
default, JSON with `--json`; `--repo` points the code-version check at another checkout (the live one).

**`codec_health.read_beats(folder=None)`.** An optional folder, so the doctor reads the folder it was
given; the heartbeat's call is unchanged.

**`routes/connections.py`.** `/api/connections/permissions` reads Microphone and Automation through the
doctor's probes instead of a fixed "unknown" (Automation also gets a `detail` per app); the note says the
bridge and voice processes' grants cannot be read from the dashboard. New `GET /api/connections/doctor`
(the report as JSON) and `GET /api/connections/doctor/report` (the zip, `Content-Disposition:
attachment`, `no-store`), both read-only and behind the dashboard's login like every other route.

**Settings > Connectors > macOS permissions.** Two new buttons under the existing list: "Check my Mac"
(runs the doctor and lists every FAIL and WARN with its fix, and the System Settings link in a Mac
browser, then one line of what is fine) and "Copy report" (downloads the zip). No second panel. The list
is drawn by one plain function (`connDoctorHtml`) so Node tests it without a browser; every value goes
through `escHtml`.

No audit event, config key, schema or skill is added; `skills/.manifest.json` does not change.

## Not checked, on purpose
The observer's beat (it comes from the audit log and the PM2 env, which the heartbeat already reads), the
state backup tar (it holds the live, blanked config.json), the owner's other shell files, and anything
that needs a prompt or admin rights.

## Tests
`tests/test_doctor.py`, every path in tmp (a `Paths` object, never a fake HOME, never the live ~/.codec):
each check's FAIL / WARN / OK case from fixture files and modes; field names reported and values never;
the reflog parsed without its name or email; PM2 never called without a live pid file; listeners from a
fixture `lsof` output; health beats with the PM2 rules; the model check refused / timeout / up / cloud
(no call made); the scrubber against planted token shapes (and the same shapes found in the raw
fixtures, the negative control); the zip unzipped and searched for token shapes and the home path (0);
the CLI's `--json` output and exit code; the probes on a Mac return a state without prompting.
`tests/test_connections.py`: the permission list with Microphone and Automation read, the two routes,
and the card's function under Node (Microphone missing and the model server stopped: both listed with
the link and a fix).

## Owner steps
`pm2 restart codec-dashboard` (the routes and the page). Then Settings > Connectors > Check my Mac. On the
Mac Air test: deny Microphone, stop the model server, Check my Mac (both listed with link and fix); then a
clean run reports 0 FAIL.

## Rollback
`git revert` the commit. The doctor writes nothing, so there is no state to undo; delete any downloaded
`codec-doctor-*.zip` by hand.
