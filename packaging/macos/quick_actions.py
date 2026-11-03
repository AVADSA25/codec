#!/usr/bin/env python3
"""Write CODEC's eight Quick Actions (macOS Services) for the app (P-029).

Right-click selected text > Services > CODEC Proofread (Elevate, Explain, Prompt,
Read Aloud, Reply, Save, Translate). Each is a `CODEC <Name>.workflow` folder in
~/Library/Services: one Run Shell Script action that copies the selection and
runs the app's own interpreter on the app's own codec_textassist.py.

- A workflow carries the CODECQuickAction key in its Info.plist. A folder with
  that name and no marker (a hand-made one, or one re-saved in Automator) is never
  written, renamed or removed.
- If the app has moved or gone, the action shows one notification, prints nothing
  and exits 0, so Automator shows no shell error.
- Stdlib only: first_run.py loads it from the same folder (packaging/macos in the
  repo, Contents/Resources in the app). Tests always pass their own Services folder.

Design: docs/HOTKEYS-QUICK-ACTIONS-DESIGN.md.
"""
from __future__ import annotations

import argparse
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
import uuid

SERVICES = (
    ("Elevate", "elevate"),
    ("Explain", "explain"),
    ("Prompt", "prompt"),
    ("Proofread", "proofread"),
    ("Read Aloud", "read_aloud"),
    ("Reply", "reply"),
    ("Save", "save"),
    ("Translate", "translate"),
)
MARKER = "CODECQuickAction"
FORMAT = 1
SERVICES_DIR = os.path.expanduser("~/Library/Services")
PBS = "/System/Library/CoreServices/pbs"
TOOLS = {"pbcopy": "/usr/bin/pbcopy", "afplay": "/usr/bin/afplay", "osascript": "/usr/bin/osascript"}
LOG_DIR = '"$HOME/Library/Logs/CODEC"'  # expanded by the shell when the action runs
SOUND = "/System/Library/Sounds/Blow.aiff"
MOVED_NOTICE = "CODEC was moved or removed. Open CODEC again to repair its Quick Actions."
_UUID_NS = uuid.UUID("6b4f1f0e-2c1d-4c39-9a52-0c0dec0a2029")


def folder_name(title: str) -> str:
    return f"CODEC {title}.workflow"


def bundle_targets(contents: str) -> tuple[str, str]:
    """(interpreter, app source folder) inside an .app's Contents folder."""
    res = os.path.join(contents, "Resources")
    return os.path.join(res, "python", "bin", "python3"), os.path.join(res, "app")


def script(mode: str, interpreter: str, app_dir: str, *, tools: dict | None = None,
           log_dir: str | None = None) -> str:
    """The Run Shell Script body. Paths are quoted; the selection arrives on stdin."""
    t = {**TOOLS, **(tools or {})}
    q = shlex.quote
    logs = LOG_DIR if log_dir is None else q(log_dir)
    notice = 'display notification "' + MOVED_NOTICE + '" with title "CODEC"'
    return "\n".join([
        f"# CODEC Quick Action ({mode}). Written by the CODEC app, which rewrites it when the app moves.",
        f"PY={q(interpreter)}",
        f"APP={q(app_dir)}",
        'if [ ! -x "$PY" ] || [ ! -f "$APP/codec_textassist.py" ]; then',
        "  cat >/dev/null",
        f"  {q(t['osascript'])} -e {q(notice)} >/dev/null 2>&1",
        "  exit 0",
        "fi",
        f"LOGS={logs}",
        'if mkdir -p "$LOGS" 2>/dev/null; then LOG="$LOGS/quick-actions.log"; else LOG=/dev/null; fi',
        f"{q(t['pbcopy'])}",
        f"{q(t['afplay'])} {q(SOUND)} >/dev/null 2>&1",
        'cd "$APP" 2>/dev/null || exit 0',
        f'"$PY" "$APP/codec_textassist.py" {mode} >>"$LOG" 2>&1',
        "exit 0",
        "",
    ])


def info_plist(title: str, mode: str) -> bytes:
    return plistlib.dumps({
        "NSServices": [{
            "NSBackgroundColorName": "background",
            "NSIconName": "NSActionTemplate",
            "NSMenuItem": {"default": f"CODEC {title}"},
            "NSMessage": "runWorkflowAsService",
            "NSSendTypes": ["public.utf8-plain-text"],
        }],
        MARKER: {"format": FORMAT, "mode": mode},
    })


def _arg(uid: str, name: str, default) -> dict:
    return {"default value": default, "name": name, "required": "0", "type": "0", "uuid": uid}


def document(mode: str, command: str) -> bytes:
    """document.wflow: the Automator workflow, the shape the Studio's eight have."""
    def u(part):
        return str(uuid.uuid5(_UUID_NS, f"{mode}:{part}")).upper()
    action = {
        "AMAccepts": {"Container": "List", "Optional": True, "Types": ["com.apple.cocoa.string"]},
        "AMActionVersion": "2.0.3",
        "AMApplication": ["Automator"],
        "AMParameterProperties": {k: {} for k in
                                  ("COMMAND_STRING", "CheckedForUserDefaultShell", "inputMethod", "shell", "source")},
        "AMProvides": {"Container": "List", "Types": ["com.apple.cocoa.string"]},
        "ActionBundlePath": "/System/Library/Automator/Run Shell Script.action",
        "ActionName": "Run Shell Script",
        "ActionParameters": {
            "COMMAND_STRING": command,
            "CheckedForUserDefaultShell": True,
            "inputMethod": 0,          # the selection is passed on stdin
            "shell": "/bin/bash",
            "source": "",
        },
        "BundleIdentifier": "com.apple.RunShellScript",
        "CFBundleVersion": "2.0.3",
        "CanShowSelectedItemsWhenRun": False,
        "CanShowWhenRun": True,
        "Category": ["AMCategoryUtilities"],
        "Class Name": "RunShellScriptAction",
        "InputUUID": u("input"),
        "Keywords": ["Shell", "Script", "Command", "Run", "Unix"],
        "OutputUUID": u("output"),
        "UUID": u("action"),
        "UnlocalizedApplications": ["Automator"],
        "arguments": {
            "0": _arg("0", "inputMethod", 0),
            "1": _arg("1", "CheckedForUserDefaultShell", False),
            "2": _arg("2", "source", ""),
            "3": _arg("3", "COMMAND_STRING", ""),
            "4": _arg("4", "shell", "/bin/sh"),
        },
        "isViewVisible": True,
        "location": "505.500000:305.000000",
        "nibPath": "/System/Library/Automator/Run Shell Script.action/Contents/Resources/Base.lproj/main.nib",
    }
    return plistlib.dumps({
        "AMApplicationBuild": "534",
        "AMApplicationVersion": "2.10",
        "AMDocumentVersion": "2",
        "AMWorkflowSchemeVersion": "2.0",
        "actions": [{"action": action, "isViewVisible": True}],
        "connectors": {},
        "workflowMetaData": {
            "applicationBundleIDsByPath": {},
            "applicationPaths": [],
            "inputTypeIdentifier": "com.apple.Automator.text",
            "outputTypeIdentifier": "com.apple.Automator.nothing",
            "presentationMode": 11,
            "processesInput": False,
            "serviceInputTypeIdentifier": "com.apple.Automator.text",
            "serviceOutputTypeIdentifier": "com.apple.Automator.nothing",
            "serviceProcessesInput": False,
            "systemImageName": "NSActionTemplate",
            "useAutomaticInputType": False,
            "workflowTypeIdentifier": "com.apple.Automator.servicesMenu",
        },
    })


def files_for(title: str, mode: str, interpreter: str, app_dir: str, **kw) -> dict[str, bytes]:
    return {
        "Contents/Info.plist": info_plist(title, mode),
        "Contents/document.wflow": document(mode, script(mode, interpreter, app_dir, **kw)),
    }


def is_ours(path: str) -> bool:
    """True only when the folder's Info.plist carries the CODECQuickAction marker."""
    try:
        with open(os.path.join(path, "Contents", "Info.plist"), "rb") as fh:
            data = plistlib.load(fh)
    except Exception:
        return False
    return isinstance(data, dict) and isinstance(data.get(MARKER), dict)


def _same(path: str, files: dict[str, bytes]) -> bool:
    try:
        for rel, data in files.items():
            with open(os.path.join(path, rel), "rb") as fh:
                if fh.read() != data:
                    return False
    except OSError:
        return False
    return True


def _write(services_dir: str, dest: str, files: dict[str, bytes], replace: bool) -> None:
    """Build the folder beside its place, then rename it in. `replace` is only ever
    True for a folder is_ours() just approved."""
    name = os.path.basename(dest)
    tmp = os.path.join(services_dir, f".{name}.tmp-{os.getpid()}")
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        for rel, data in files.items():
            p = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as fh:
                fh.write(data)
        if replace:
            old = os.path.join(services_dir, f".{name}.old-{os.getpid()}")
            shutil.rmtree(old, ignore_errors=True)
            os.rename(dest, old)
            try:
                os.rename(tmp, dest)
            except OSError:
                os.rename(old, dest)  # put CODEC's previous version back
                raise
            shutil.rmtree(old, ignore_errors=True)
        else:
            os.rename(tmp, dest)  # fails if something appeared there meanwhile: nothing is overwritten
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def install(services_dir: str, interpreter: str, app_dir: str, *, create: bool = True,
            dry_run: bool = False, **kw) -> list[tuple[str, str]]:
    """Write the eight workflows. Returns (folder name, what happened) per service:
    written, updated, unchanged, kept (not CODEC's), skipped (repair mode: absent),
    would write / would update (dry run), failed: <reason>.
    create=False is repair mode: only CODEC's own folders are rewritten."""
    results = []
    if not dry_run:
        os.makedirs(services_dir, exist_ok=True)
    for title, mode in SERVICES:
        name = folder_name(title)
        dest = os.path.join(services_dir, name)
        files = files_for(title, mode, interpreter, app_dir, **kw)
        if os.path.lexists(dest):
            if not is_ours(dest):
                results.append((name, "kept"))
                continue
            if _same(dest, files):
                results.append((name, "unchanged"))
                continue
            action, replace = "updated", True
        elif not create:
            results.append((name, "skipped"))
            continue
        else:
            action, replace = "written", False
        if dry_run:
            results.append((name, {"written": "would write", "updated": "would update"}[action]))
            continue
        try:
            _write(services_dir, dest, files, replace)
            results.append((name, action))
        except OSError as e:
            results.append((name, f"failed: {e.strerror or e}"))
    return results


def changed(results: list[tuple[str, str]]) -> bool:
    return any(action in ("written", "updated") for _name, action in results)


def refresh_menu(run=subprocess.run) -> None:
    """Ask macOS to re-read the Services folders. Best effort; never raises."""
    if not os.path.exists(PBS):
        return
    try:
        run([PBS, "-update"], capture_output=True, timeout=20, check=False)
    except Exception:
        pass


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Write CODEC's eight Quick Actions.")
    ap.add_argument("--interpreter", required=True, help="the Python that runs codec_textassist.py")
    ap.add_argument("--app-dir", required=True, help="the folder that holds codec_textassist.py")
    ap.add_argument("--services-dir", default=SERVICES_DIR)
    ap.add_argument("--repair", action="store_true", help="only rewrite CODEC's own existing workflows")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    results = install(a.services_dir, a.interpreter, a.app_dir, create=not a.repair, dry_run=a.dry_run)
    for name, action in results:
        print(f"  {action:<13} {name}")
    if not a.dry_run and changed(results) and os.path.realpath(a.services_dir) == os.path.realpath(SERVICES_DIR):
        refresh_menu()
    return 1 if any(action.startswith("failed") for _n, action in results) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
