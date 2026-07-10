#!/usr/bin/env python3
"""First-run orchestrator for the Sovereign AI Workstation (W5-6, E-9).

Headless core the native onboarding wizard (W5-11) drives. On first launch it:
installs the launchd fleet (W5-3), fetches the bundled model set (W5-5), and
reports TCC permission status with deep links to the right System Settings pane.
Idempotent via a ~/.codec/.first_run_complete sentinel.

macOS can't grant TCC permissions programmatically (by design), so this guides
the user; status checks are best-effort via ctypes and degrade to "unknown"
where an API isn't available (e.g. Linux CI). stdlib only.

See docs/W5-6-FIRST-RUN-DESIGN.md. Closes E-9 (headless layer).
"""
from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SENTINEL = ".first_run_complete"


def bundle_contents() -> str | None:
    """The .app's Contents dir when running from inside the bundle, else None.

    In the bundle build_app.sh copies this file to Contents/Resources/first_run.py,
    so HERE == .../Contents/Resources and Contents is one parent up. In the repo
    HERE == packaging/macos and this returns None.
    """
    contents = os.path.dirname(HERE)
    if os.path.basename(contents) == "Contents" and os.path.exists(
        os.path.join(contents, "Info.plist")
    ):
        return contents
    return None


def launchagent_args(contents: str | None) -> list[str]:
    """Extra args for install_launchagents.sh when running inside the .app.

    A buyer's Mac has no node and no PM2, and no `python3` on PATH that has our
    dependencies — so point the installer at the bundled interpreter, the bundled
    source tree, and the services.json that build_app.sh generated at build time.
    """
    if not contents:
        return []
    resources = os.path.join(contents, "Resources")
    args: list[str] = []
    interp = os.path.join(resources, "python", "bin", "python3")
    if os.path.exists(interp):
        args += ["--interpreter", interp]
    workdir = os.path.join(resources, "app")
    if os.path.isdir(workdir):
        args += ["--workdir", workdir]
    services = os.path.join(resources, "services.json")
    if os.path.exists(services):
        args += ["--services-json", services]
    return args

_BASE = "x-apple.systempreferences:com.apple.preference.security?Privacy_"
PERMISSIONS = [
    {"key": "accessibility", "label": "Accessibility", "deep_link": _BASE + "Accessibility",
     "reason": "Move the mouse and type for you (vision-driven control).", "degrades": "voice clicking"},
    {"key": "microphone", "label": "Microphone", "deep_link": _BASE + "Microphone",
     "reason": "Hear voice commands, dictation, and the wake word.", "degrades": "voice + wake word"},
    {"key": "screen_recording", "label": "Screen Recording", "deep_link": _BASE + "ScreenCapture",
     "reason": "Read the screen when you ask ('check my screen').", "degrades": "screen OCR"},
    {"key": "input_monitoring", "label": "Input Monitoring", "deep_link": _BASE + "ListenEvent",
     "reason": "Detect global hotkeys (F13/F18, double-tap).", "degrades": "global hotkeys"},
    {"key": "full_disk_access", "label": "Full Disk Access", "deep_link": _BASE + "AllFiles",
     "reason": "Read the Messages database for the iMessage bridge.", "degrades": "iMessage"},
    {"key": "automation", "label": "Automation", "deep_link": _BASE + "Automation",
     "reason": "Control apps like Messages, Notes, Reminders on request.", "degrades": "app control"},
]


def is_first_run(home: str) -> bool:
    return not os.path.exists(os.path.join(home, SENTINEL))


def mark_complete(home: str) -> None:
    os.makedirs(home, exist_ok=True)
    with open(os.path.join(home, SENTINEL), "w", encoding="utf-8") as fh:
        fh.write("ok\n")


# ---- best-effort TCC status (never raises) --------------------------------

def _framework(path: str):
    try:
        return ctypes.cdll.LoadLibrary(path)
    except OSError:
        return None


def _check_accessibility() -> str:
    fw = _framework("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
    if not fw:
        return "unknown"
    try:
        fw.AXIsProcessTrusted.restype = ctypes.c_bool
        return "granted" if fw.AXIsProcessTrusted() else "denied"
    except Exception:
        return "unknown"


def _check_screen_recording() -> str:
    fw = _framework("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    if not fw:
        return "unknown"
    try:
        fn = fw.CGPreflightScreenCaptureAccess
        fn.restype = ctypes.c_bool
        return "granted" if fn() else "denied"
    except AttributeError:
        return "unknown"
    except Exception:
        return "unknown"


def _check_full_disk() -> str:
    p = os.path.expanduser("~/Library/Messages/chat.db")
    if not os.path.exists(p):
        return "unknown"
    try:
        with open(p, "rb") as fh:
            fh.read(16)
        return "granted"
    except PermissionError:
        return "denied"
    except OSError:
        return "unknown"


_CHECKS = {
    "accessibility": _check_accessibility,
    "screen_recording": _check_screen_recording,
    "full_disk_access": _check_full_disk,
}


def permission_report() -> dict[str, str]:
    return {p["key"]: _CHECKS.get(p["key"], lambda: "unknown")() for p in PERMISSIONS}


def plan() -> list[str]:
    return [
        "Install launchd LaunchAgents (the CODEC service fleet)",
        "Fetch the bundled model set (Whisper + Kokoro + 7B LLM)",
        "Check & guide TCC permissions (Accessibility, Mic, Screen Recording, ...)",
    ]


def _run(cmd: list[str], dry_run: bool) -> None:
    if dry_run:
        print("  [dry-run] " + " ".join(cmd))
    else:
        subprocess.run(cmd, check=False)


def _print_permissions(report: dict[str, str]) -> None:
    print("TCC permissions:")
    for p in PERMISSIONS:
        status = report.get(p["key"], "unknown")
        mark = {"granted": "[ok]", "denied": "[DENIED]", "unknown": "[?]"}[status]
        print(f"  {mark:<9} {p['label']:<17} — {p['reason']}")
        if status != "granted":
            print(f"            grant: {p['deep_link']}")
            print(f"            (without it: {p['degrades']} is disabled)")


def run(home: str, *, dry_run: bool = False, yes: bool = False,
        force: bool = False, permissions_only: bool = False) -> int:
    if permissions_only:
        _print_permissions(permission_report())
        return 0
    if not force and not is_first_run(home):
        print("first run already complete (use --force to re-run, --permissions-only to re-check).")
        return 0

    print("==> CODEC first-run setup" + (" (DRY RUN)" if dry_run else ""))
    for i, step in enumerate(plan(), 1):
        print(f"-- {i}/{len(plan())} {step} --")

    os.makedirs(home, exist_ok=True)
    os.makedirs(os.path.expanduser("~/Library/Logs/CODEC"), exist_ok=True)

    install = ["bash", os.path.join(HERE, "launchd", "install_launchagents.sh")]
    install += launchagent_args(bundle_contents())
    _run(install + (["--dry-run"] if dry_run else []), dry_run)

    fetch = [sys.executable, os.path.join(HERE, "fetch_models.py"), "--tier", "bundled"]
    _run(fetch + (["--dry-run"] if dry_run else (["--yes"] if yes else ["--dry-run"])), dry_run)

    _print_permissions(permission_report())

    if not dry_run:
        mark_complete(home)
        print("==> first-run setup complete.")
    else:
        print("[dry-run] nothing changed; sentinel not written.")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="CODEC first-run setup.")
    ap.add_argument("--home", default=os.path.expanduser("~/.codec"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes", action="store_true", help="consent to the real model download")
    ap.add_argument("--force", action="store_true", help="re-run even if already completed")
    ap.add_argument("--permissions-only", action="store_true", help="just re-check + report TCC")
    args = ap.parse_args(argv)
    return run(args.home, dry_run=args.dry_run, yes=args.yes,
               force=args.force, permissions_only=args.permissions_only)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
