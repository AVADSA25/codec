# W5-3 — PM2 → launchd migration (design)

> Wave 5 (Audit E). Closes **E-7** (supervisor choice + migrate the 16 services).
> Decision locked in `docs/APPLE-DISTRIBUTION.md`: the paid `.app` uses **launchd
> LaunchAgents**, not PM2/Node (a signed consumer app can't ship a Node runtime +
> `npm i -g pm2`). The OSS/dev build keeps `ecosystem.config.js` + PM2.

## Why a generator, not 16 hand-written plists

`ecosystem.config.js` stays the **single source of truth** for the service list.
W5-3 *derives* LaunchAgents from it, so the two never drift:

```
ecosystem.config.js ──(node -e JSON dump)──▶ generate_launchagents.py ──▶ ai.avadigital.codec.<svc>.plist × 16
```

The generator's core is a **pure, stdlib-only** function
(`plistlib` + `shlex`), unit-testable with a JSON fixture — no `node`, no
`launchctl` needed to test. The real install path uses `node -e` to dump the
ecosystem to JSON (node is present wherever PM2 ran).

## PM2 → launchd field mapping

| PM2 field | launchd key | Notes |
|---|---|---|
| `name` | `Label` = `ai.avadigital.codec.<name>` | unique per agent |
| `script` + `args` | `ProgramArguments` = `[script, *shlex.split(args)]` | `bash -c '…'` tokenises correctly via `shlex` |
| `cwd` (`__dirname`) | `WorkingDirectory` | repo-root cwds rewritten to `--workdir` (the app's `Resources/app`); absolute non-repo cwds (pilot-runner) preserved + flagged |
| `env` | `EnvironmentVariables` | merged as-is |
| `autorestart: true` | `KeepAlive` = `true` | PM2-equivalent "always restart" |
| `restart_delay` (ms) | `ThrottleInterval` = `max(10, ms/1000)` s | launchd min throttle |
| — | `RunAtLoad` = `true` | boot the service on load |
| — | `StandardOutPath`/`StandardErrorPath` | `~/Library/Logs/CODEC/<name>.{out,err}` (expanded — launchd does **not** expand `~`) |
| `max_memory_restart` | *(none)* | launchd has no direct equivalent; documented gap (a memory-watchdog wrapper is a later option). `codec-watchdog` already covers runaway RAM. |

Interpreters (`python3`, `/usr/local/bin/python3.13`, `bash`) are remapped via
`--interpreter` (→ the bundled `Python.framework` python from W5-4) and
`bash`→`/bin/bash`, so `ProgramArguments[0]` is absolute under launchd's minimal
PATH.

## Deliverables

- `packaging/macos/launchd/generate_launchagents.py` — pure mapping + CLI
  (`--from-json` / `--from-ecosystem`, `--out`, `--interpreter`, `--workdir`,
  `--log-dir`, `--dry-run`). stdlib only.
- `packaging/macos/launchd/install_launchagents.sh` — generate → write to
  `~/Library/LaunchAgents/` → `launchctl bootstrap gui/$UID`. **Opt-in**, has
  `--dry-run`, and **refuses if the PM2 codec fleet is running** unless `--force`
  (so it can't double-run services on a dev machine).
- `packaging/macos/launchd/uninstall_launchagents.sh` — `launchctl bootout` +
  remove the plists.

## Scope boundaries

- **Not wired into the app entry point.** `codec_app_main.py` (W5-2) still defers
  fleet start. First-run auto-install of the agents is **W5-6** (permissions
  wizard). W5-3 ships the *toolkit*; nothing auto-runs.
- **Does not touch the running PM2 fleet.** The install script is opt-in and
  guards against PM2 being live.
- Signing/notarization (W5-7/8) operate on the bundle, independent of these
  agents.

## Test plan (`tests/test_launchd.py`, portable)

- Pure-function mapping from a fixture (incl. a `bash -c '…'` service + an `env`
  service): parse the generated plist back with `plistlib` and assert `Label`,
  `ProgramArguments` tokenisation, `RunAtLoad`, `KeepAlive`, `EnvironmentVariables`,
  `StandardOutPath`.
- `--interpreter` remap replaces `python3`.
- `--from-json` front-end writes N plists to `--out`.
- install/uninstall scripts exist, are executable, reference `launchctl`, and
  support `--dry-run`; install script has the PM2-running guard.
- **darwin+node** smoke (skipped on Linux / no node): `--from-ecosystem
  ecosystem.config.js --dry-run` emits all 16 labels.

Added to the CI doc-guard list (alongside `test_app_bundle` from W5-2, now that
#97's doc-guard step is on `main`).

## Rollback

Net-new files under `packaging/macos/launchd/` + one test + CI list + audit/
handoff notes. No runtime/daemon code touched; `git revert` the commit.
