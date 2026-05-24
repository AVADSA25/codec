# W5-6 — first-run orchestration (design)

> Wave 5 (Audit E). Closes **E-9** (permissions UX assumes a terminal walkthrough)
> at the *headless* level. The native Swift wizard window is **W5-11**; this PR
> ships the logic that window drives. Builds on W5-3 (launchd) + W5-5 (models).

## What ships

`packaging/macos/first_run.py` — the first-launch orchestrator, run once on first
app open (and re-checkable every launch):

1. **Ensure dirs** — `~/.codec/`, `~/Library/Logs/CODEC/`.
2. **Install the fleet** — invoke `install_launchagents.sh` (W5-3) to register +
   load the launchd agents.
3. **Fetch the bundled model set** — invoke `fetch_models.py --tier bundled
   --yes` (W5-5) so the app works offline.
4. **Permission report** — for each TCC permission CODEC needs, report
   `granted | denied | unknown` and, for anything not granted, print the
   "why we need this" reason + a **deep link** to the exact System Settings pane.
5. **Mark complete** — sentinel `~/.codec/.first_run_complete` (idempotent;
   `--force` re-runs; `--permissions-only` just re-checks).

## TCC permissions + deep links (the data the GUI needs)

| Key | Pane deep link (`x-apple.systempreferences:com.apple.preference.security?…`) | Why |
|---|---|---|
| accessibility | `Privacy_Accessibility` | vision mouse/keyboard control |
| microphone | `Privacy_Microphone` | voice + wake word |
| screen_recording | `Privacy_ScreenCapture` | "check my screen" OCR |
| input_monitoring | `Privacy_ListenEvent` | global hotkeys |
| full_disk_access | `Privacy_AllFiles` | iMessage DB read |
| automation | `Privacy_Automation` | controlling Messages/Notes/etc. |

**Status checks are best-effort** via `ctypes` into the system frameworks
(`AXIsProcessTrusted` for Accessibility, `CGPreflightScreenCaptureAccess` for
Screen Recording; Full Disk inferred by a probe read of `~/Library/Messages/chat.db`).
When an API isn't available (e.g. Linux CI), the status is `unknown` — never an
error. macOS can't *grant* TCC programmatically (by design), so the flow guides
the user; the GUI (W5-11) opens the deep links.

## Graceful degradation

The report flags which features degrade when a permission is denied (wake word
off without mic, screen OCR off without Screen Recording, etc.) — the runtime
already tolerates missing capabilities; this just surfaces it up front.

## Test plan (`tests/test_first_run.py`, hermetic)

- Sentinel: `is_first_run` true on a fresh temp `--home`, false after
  `mark_complete`.
- `PERMISSIONS` covers the 6 panes; every `deep_link` is an
  `x-apple.systempreferences:` URL.
- `permission_report()` returns all keys with a value in
  `{granted, denied, unknown}`.
- `plan()` lists install-agents → fetch-models → permissions in order.
- CLI `--dry-run --home <tmp>` prints the plan, runs nothing, and does **not**
  write the sentinel.

Wired into the CI packaging step.

## Scope

- Not wired into the launcher yet (the launcher calls it on first run once the
  GUI lands in W5-11). It's invocable today (`first_run.py --dry-run`).
- The actual install + 6 GB model fetch only run with `--yes`.

## Rollback

Net-new `packaging/macos/first_run.py` + one test + CI line + audit/handoff
notes. No runtime/daemon code; `git revert`.
