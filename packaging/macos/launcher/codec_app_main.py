#!/usr/bin/env python3
"""CODEC / Sovereign AI Workstation — .app entry point (W5-2).

Thin, **stdlib-only** bootstrap that the bundle launcher (``Contents/MacOS/codec``)
execs. It must run before any virtualenv / dependency wiring, so it imports
nothing from the CODEC engine and nothing third-party.

W5-2 scope: establish the bundle's runtime identity, logging, and a SAFE
``--selftest``. It deliberately does **not** start the 16-service fleet — under
the current architecture those run via PM2, and the launchd migration is W5-3.
Running this normally just records a launch line and exits 0; it never touches a
running fleet.

See docs/W5-2-APP-BUNDLE-DESIGN.md.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

MIN_PY = (3, 11)


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


def main(argv: list[str]) -> int:
    if "--selftest" in argv:
        return selftest()

    contents = _bundle_contents()
    where = str(contents) if contents else "source tree"
    _log(f"CODEC launched from {where}")
    # W5-2: fleet orchestration (the 16 PM2 services) is deferred to W5-3
    # (launchd migration). We intentionally do NOT start or touch a running
    # fleet here — that would interfere with a developer machine.
    _log("fleet start deferred to W5-3 (launchd); no services started")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
