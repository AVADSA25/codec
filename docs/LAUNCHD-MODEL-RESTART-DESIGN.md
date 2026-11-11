# Buyer Mac: restart the model LaunchAgent on a model switch (P-039 part 2, E-15)

Status: built 2026-11-11, PR left open for the owner's word. It changes how a
model switch restarts the model server (AGENTS.md section 10: the model server
launcher, `config.json:llm_model`, `model_pm2_process`), and it needs a Mac Air
test before a release.

## What and why

A model switch (`codec_models.set_active`, the Settings picker and the "Best model
for this Mac" Use button) writes `llm_model` to config.json, then restarts the
model server so the launcher preloads the new model and the old model's memory is
given back. In-place swapping does not give it back (52 GB measured against a
19 GB model, docs/MODEL-SWITCHING-DESIGN.md).

On a buyer's Mac the fleet runs as launchd LaunchAgents (docs/W5-3-LAUNCHD-DESIGN.md)
and there is no PM2, so `restart_server` answers "pm2 not on PATH, switching in
place": the probe makes the running server load the new model in place, and the
old model's memory stays held until the job happens to restart.

## Change

**`codec_models.restart_for_switch()` (new): the restart a switch asks for.**
`set_active`'s two restart calls (after writing the new model, and again on the
revert after a failed load) go through it:

- `codec_supervisor.kind(cfg) == "pm2"` (the Studio, any checkout, CI): it calls
  `restart_server()` with no arguments, the same function and the same body as
  before (pm2 stop, wait for release, pm2 restart --update-env, wait for the
  port). Byte for byte unchanged; a test pins the body against origin/main.
- `kind(cfg) == "launchd"` (CODEC running from the `.app`, or
  `config.json:supervisor` = `"launchd"`): `restart_launchd()`.

`kind()` is P-032's rule: config.json `supervisor` wins, otherwise a CODEC that
runs from inside the `.app` bundle is launchd's. Without a usable answer it is PM2.

**`codec_models.restart_launchd()` (new).**

1. **Which label.** Exactly one: the model server's own label, built from the
   shipped services list `Contents/Resources/services.json` (build_app.sh writes
   it from `ecosystem.config.js`): the one service whose `script` is
   `scripts/start_model_server.sh`, prefixed `ai.avadigital.codec.`. The result
   must equal the fixed pattern `ai.avadigital.codec.qwen3.6` (prefix plus the
   known name) or nothing runs. `config.json:model_pm2_process` is not read on
   this path, and no other label can ever be built: a renamed, missing or
   doubled model service in the list is refused.
2. **Is it loaded.** `launchctl print gui/<uid>/<label>` (read only). A label
   launchd does not know is refused; this is also what a Studio with
   `"supervisor": "launchd"` in its config hits.
3. **The command.** `launchctl kickstart -k gui/<uid>/<label>`, one argv list,
   no shell, 60 s limit. `-k` makes launchd stop the running copy before it starts
   the job again; launchd never runs two copies of one job, so the old model's
   memory is released before the new one loads. Never `bootout`, `unload`,
   `remove`, `bootstrap`, `kill` or `stop`: the plist stays loaded and KeepAlive
   stays in charge.
   The P-039 part 1 proposal (`launchctl kill SIGTERM`, wait, then `kickstart`)
   was dropped: the model job has KeepAlive, so launchd restarts it by itself
   about 10 s after a kill and races the second command.
4. **Wait.** Up to 30 s (`_RESTART_RESPAWN_TIMEOUT`, as on PM2) for launchd to
   report the job running with a pid other than the one before the kickstart, then
   up to 300 s (`_RESTART_READY_TIMEOUT`) for the port in `llm_base_url` to accept
   a connection (uvicorn binds only after the preload, so that means "loaded").
   `time.sleep` between reads, `launchctl print` each time.
5. **Answer.** `(True, "<label> kickstart -k, serving in N s")`, or `(False, why)`.
   Never raises. A failure leaves the switch to the probe, which loads the model in
   place exactly as today, and `model_switched` records `restarted: false` and
   the reason, as it does on PM2.

**When it runs.** Only where `set_active` restarts under PM2 today: a verified
switch to another local model (also back from a cloud model), and the revert after
a failed load. Not for an unverified switch, a switch to a cloud model, the
automatic cloud fallback or its switch back (none restart on PM2 either).

**Refused before any launchctl call:** a switch while an image job holds
`~/.codec/image_job.lock` (unchanged: `set_active` checks it before writing
config), an unknown model, the model already active.

**Not changed:**

- `restart_server()` itself, so `codec_setup.set_provider` (first-run
  "Use a local model") keeps its in-place behaviour on launchd. Its route runs on
  the event loop, and on a first run the server is still loading the bundled
  model; holding the setup page for a restart there is a separate decision.
- `scripts/start_model_server.sh`, the plists, the generator, services.json,
  `ecosystem.config.js`, PM2 process names, `codec_supervisor` (still read only),
  Cortex's and the heartbeat's restart paths.

## Audit

One line per launchd restart attempt, metadata only:
`model_server_restarted` (source `codec-models`; info when it worked, warning
otherwise; outcome `ok`, `warning` for a refusal, `error` for a failure), extra
`supervisor` (`launchd`), `label` (empty when no label was accepted), `result`
(`ok`, `refused`, `failed`), `reason` (`not_model_label`, `no_service_list`,
`not_loaded`, `kickstart_failed`, `no_new_process`, `not_ready`, or empty) and
`seconds`. No model ids, paths or command output. The switch itself keeps its
`model_switched` line.

## Tests

`tests/test_launchd_model_restart.py`. Every launchctl answer is a fixture: an
injected runner that records argv, or a fake `launchctl` script in a temp folder
for the real subprocess path. An autouse guard fails any test that would run
`/bin/launchctl` or `pm2`. Config is a temp file; nothing reads or writes the
owner's `~/.codec` or `~/Library/LaunchAgents`, and HOME is never faked.

- The label comes from the services list, equals `ai.avadigital.codec.qwen3.6`,
  matches what `ecosystem.config.js` and the plist generator give, and is refused
  when the list is missing, broken, names no or two model servers, or names the
  model server something else; `model_pm2_process` in config never changes it.
- A restart runs `print`, then exactly one
  `["/bin/launchctl", "kickstart", "-k", "gui/501/ai.avadigital.codec.qwen3.6"]`,
  then `print` until a new pid, and reports ok once the port opens; no other
  launchctl verb appears.
- Refusals and failures: not loaded (no kickstart), kickstart exits non-zero, no
  new process within the limit, the port never opens; one audit line each, with
  the fixed fields only.
- `set_active` on launchd restarts through `restart_launchd` (config written
  first, revert restarts again with the old model) and never through PM2; on PM2
  it calls `restart_server()` exactly as before, and the PM2 argv
  (`pm2 stop qwen3.6`, `pm2 restart qwen3.6 --update-env`) is pinned; an image job
  stops the switch before any launchctl call; `restart_server()` on launchd is
  still the in-place answer (setup).
- `restart_server`'s body equals origin/main's at the commit this branched from.
- Existing suites stay green: model switching, fit, supervisor, launchd, fleet
  start, cloud fallback, image, setup, doctor.

## Mac Air test (owner, before a release)

See the PR body: switch models in Settings, watch `launchctl print` show a new pid,
`footprint` show one model's memory, the audit line, and the launcher's log line
naming the new model.

## Rollback

Revert the PR. On a buyer Mac without a revert, `"supervisor": "pm2"` in
`~/.codec/config.json` sends switches back to `restart_server()`, which answers
"pm2 not on PATH, switching in place" as before. The Studio is on PM2 and is not
affected either way.
