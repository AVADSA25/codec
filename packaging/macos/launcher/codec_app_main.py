#!/usr/bin/env python3
"""CODEC / Sovereign AI Workstation — .app entry point (W5-2).

Thin, **stdlib-only** bootstrap that the bundle launcher (``Contents/MacOS/codec``)
execs. It must run before any virtualenv / dependency wiring, so it imports
nothing from the CODEC engine and nothing third-party.

Establishes the bundle's runtime identity, logging, a SAFE ``--selftest``, and —
when launched from inside the .app — starts the CODEC service fleet.

2026-07: W5-3 (the PM2 -> launchd migration) shipped `launchd/` and `first_run.py`
and wired them to each other, but nothing ever called first_run, and build_app.sh
never copied it into the bundle. So this entry point kept logging "fleet start
deferred to W5-3; no services started" and exiting 0 — a buyer's app opened and
immediately quit. main() now runs first-run (idempotent, sentinel-guarded) and
thereafter re-bootstraps the fleet if launchd has dropped it.

SOURCE-TREE SAFETY IS PRESERVED: outside an .app bundle this still starts nothing,
because on a developer's machine the fleet runs under PM2 and double-running it
would be destructive. That was the right instinct in W5-2; it just also needs to
do something when it IS the shipped app.

See docs/W5-2-APP-BUNDLE-DESIGN.md, docs/W5-3-LAUNCHD-DESIGN.md.
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

MIN_PY = (3, 11)
FLEET_LABEL_PREFIX = "ai.avadigital.codec"
SENTINEL = ".first_run_complete"


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def _log_dir() -> Path:
    d = _home() / "Library" / "Logs" / "CODEC"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _codec_dir() -> Path:
    d = _home() / ".codec"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _bundle_contents() -> Path | None:
    """If running from inside an .app, return the Contents dir, else None.

    Layout: <App>.app/Contents/Resources/codec_app_main.py — so Contents is two
    parents up from this file.
    """
    here = Path(__file__).resolve()
    contents = here.parent.parent
    if contents.name == "Contents" and (contents / "Info.plist").exists():
        return contents
    return None


def _log(line: str) -> None:
    stamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    msg = f"{stamp}  {line}"
    try:
        with (_log_dir() / "launch.log").open("a", encoding="utf-8") as fh:
            fh.write(msg + "\n")
    except OSError:
        pass
    print(msg, flush=True)


def selftest() -> int:
    """Validate the runtime environment without starting anything. Returns an
    exit code (0 = healthy). Safe to run anywhere, anytime."""
    problems: list[str] = []

    if sys.version_info < MIN_PY:
        problems.append(f"python {sys.version_info.major}.{sys.version_info.minor} < required {MIN_PY[0]}.{MIN_PY[1]}")

    try:
        cd = _codec_dir()
        probe = cd / ".selftest_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        problems.append(f"~/.codec not writable: {e}")

    try:
        _log_dir()
    except OSError as e:
        problems.append(f"log dir not writable: {e}")

    contents = _bundle_contents()
    if contents is not None:
        if not (contents / "Info.plist").exists():
            problems.append("Info.plist missing from bundle Contents")
        if not (contents / "MacOS" / "codec").exists():
            problems.append("launcher (MacOS/codec) missing from bundle")

    location = "app-bundle" if contents else "source-tree"
    if problems:
        for p in problems:
            _log(f"SELFTEST FAIL [{location}]: {p}")
        return 1
    _log(f"SELFTEST OK [{location}] python={sys.version_info.major}.{sys.version_info.minor}")
    return 0


def _fleet_loaded() -> int:
    """How many CODEC LaunchAgents launchd currently knows about."""
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True,
                             timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return 0
    return sum(1 for line in out.splitlines() if FLEET_LABEL_PREFIX in line)


def _run_first_run(resources: Path) -> int:
    """Install the launchd fleet, fetch models, report permissions. Idempotent:
    first_run.py no-ops once ~/.codec/.first_run_complete exists."""
    script = resources / "first_run.py"
    if not script.exists():
        _log(f"FLEET ERROR: first_run.py missing from bundle ({script})")
        return 1
    proc = subprocess.run([sys.executable, str(script), "--home", str(_codec_dir())],
                          capture_output=True, text=True)
    for line in (proc.stdout or "").splitlines():
        _log(f"  first-run: {line}")
    if proc.returncode != 0:
        _log(f"FLEET ERROR: first-run exited {proc.returncode}: {(proc.stderr or '').strip()[:300]}")
    return proc.returncode


def _bootstrap_fleet(resources: Path) -> int:
    """Re-install the LaunchAgents when launchd has forgotten them (e.g. the user
    ran the uninstaller's launchctl bootout, or agents were pruned)."""
    script = resources / "launchd" / "install_launchagents.sh"
    if not script.exists():
        _log(f"FLEET ERROR: install_launchagents.sh missing from bundle ({script})")
        return 1
    interp = resources / "python" / "bin" / "python3"
    cmd = ["bash", str(script)]
    if interp.exists():
        cmd += ["--interpreter", str(interp)]
    if (resources / "app").is_dir():
        cmd += ["--workdir", str(resources / "app")]
    if (resources / "services.json").exists():
        cmd += ["--services-json", str(resources / "services.json")]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        _log(f"FLEET ERROR: bootstrap exited {proc.returncode}: {(proc.stderr or '').strip()[:300]}")
    return proc.returncode


def start_fleet(contents: Path) -> int:
    """Ensure the CODEC fleet is installed and running under launchd."""
    resources = contents / "Resources"
    home = _codec_dir()

    if not (home / SENTINEL).exists():
        _log("first launch — installing the CODEC fleet")
        rc = _run_first_run(resources)
        if rc != 0:
            return rc
    elif _fleet_loaded() == 0:
        _log("fleet not loaded in launchd — re-bootstrapping")
        rc = _bootstrap_fleet(resources)
        if rc != 0:
            return rc

    n = _fleet_loaded()
    if n == 0:
        _log("FLEET ERROR: no CODEC LaunchAgents are loaded after setup")
        return 1
    _log(f"fleet running: {n} launchd service(s)")
    return 0


def main(argv: list[str]) -> int:
    if "--selftest" in argv:
        return selftest()

    contents = _bundle_contents()
    where = str(contents) if contents else "source tree"
    _log(f"CODEC launched from {where}")

    if contents is None:
        # Developer machine: the fleet runs under PM2 (ecosystem.config.js).
        # Starting launchd agents here would double-run every service.
        _log("source tree — fleet is managed by PM2; not starting anything")
        return 0

    return start_fleet(contents)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
