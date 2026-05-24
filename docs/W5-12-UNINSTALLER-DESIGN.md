# W5-12 — macOS uninstaller (design)

> Wave 5 (Audit E). Closes **E-14** (no clean-removal path). Depends on W5-3
> (launchd is the supervisor to tear down). Reference: `docs/audits/PHASE-1-APPLE-APP.md`.

## Principle: destructive tool → safe by default

An uninstaller deletes things. This one is built so it **cannot** nuke a user's
data by accident and is **fully testable in a sandbox**:

- **Dry-run is the default.** With no `--yes`, it only *lists* what it would
  remove (and prints the manual residue steps). Deletes nothing.
- **User data is double-gated.** `~/.codec/` (config, memory.db, audit.log,
  skills, agents) is removed **only** with `--yes` **and** `--purge-data`.
  Without `--purge-data`, a `--yes` run removes the app/agents/logs but
  *preserves* user data (the "reinstall to fix X" support case).
- **`--home DIR`** overrides the base dir (default `$HOME`) so tests exercise
  even the destructive path entirely inside a temp dir. Keychain deletion is
  gated to real-`$HOME` runs, so tests never touch the real Keychain.
- Every `rm -rf` is guarded (non-empty + expected-suffix check) — no
  `rm -rf "$VAR/"` with an unset `VAR`.

## What it removes automatically (CODEC-owned, safe)

| Target | When |
|---|---|
| launchd agents `ai.avadigital.codec.*` (bootout + plist rm) | `--yes` |
| The `.app` bundle (`--app`, default `/Applications/Sovereign AI Workstation.app`) | `--yes` |
| `~/Library/Logs/CODEC/` | `--yes` |
| Keychain items `ai.avadigital.codec.*` (audit HMAC, internal token, oauth_state, dashboard/llm/gemini/pexels/serper/telegram keys, license) | `--yes`, real-`$HOME` only |
| `~/.codec/` (all user state) | `--yes` **and** `--purge-data` |

## What it does NOT auto-delete (printed as guided manual steps)

Apple/sharing realities make these unsafe to auto-remove:

- **TCC privacy grants** — Apple intentionally forbids an app from revoking its
  own Accessibility / Microphone / Screen-Recording entries. We print the exact
  System Settings path for the user to clear them.
- **`~/.cloudflared/config.yml`** — may hold non-CODEC tunnels; we print it for
  manual review rather than delete.
- **Model cache** (HuggingFace / `~/.codec/models` once W5-5 lands) — large; we
  print the path and size, delete only under `--purge-data`.
- **Source checkout** (`~/codec-repo`) — the developer's working tree; never touched.

## Files

- `packaging/macos/uninstall_codec.sh` — the uninstaller (flags above).
- `tests/test_uninstaller.py` — dry-run-preserves-everything, `--yes` removes
  app/agents/logs but keeps data, `--yes --purge-data` removes data too — all in
  a temp `--home`; plus the rm-guard + TCC-instructions asserts. Wired into CI.

## Rollback

Net-new `packaging/macos/uninstall_codec.sh` + one test + CI line + audit/handoff
notes. No runtime/daemon code; `git revert` the commit.
