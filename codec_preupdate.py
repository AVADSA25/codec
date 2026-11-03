#!/usr/bin/env python3
"""A copy of CODEC's state taken just before an update is applied (docs/SAFE-UPDATE-DESIGN.md; plan item P-027).

    python3 codec_preupdate.py [--src DIR] [--dest DIR] [--label TEXT]

writes <dest>/<YYYYmmdd-HHMMSS>/ (default dest: <src>/pre_update, src: ~/.codec):

- every top-level ``*.db`` that is a SQLite file, copied through the sqlite backup API (safe while CODEC writes,
  WAL included) and checked with ``PRAGMA quick_check``, the steps of ``codec_backup.backup_sqlite``;
- ``state.tar.gz`` with ``codec_backup.STATE_ITEMS`` (agents, settings, notes, voice 02 ...), read back;
- ``manifest.json`` with each copy's size, check and sha256.

The copy is built in a hidden ``.partial`` folder and renamed into place only when every part passed, so a failed
run leaves nothing that looks complete. Folders are 0700 and files 0600: the copy holds secrets. Nothing here
deletes an older snapshot, and ``codec_backup``'s rotation only reads ``backups/``; old snapshots are deleted by
hand. Called by install.sh --update and update.sh (before the new code runs) and by
``codec_update.download_and_verify`` (before a verified DMG is handed over).

Exit codes: 0 copied (or no state folder to copy), 1 the copy failed, 2 usage.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import codec_backup

DIRNAME = "pre_update"
STATE_TAR = "state.tar.gz"
MANIFEST = "manifest.json"
_SQLITE_HEADER = b"SQLite format 3\x00"


def default_src() -> Path:
    return codec_backup.home()


def _private_dir(path: Path) -> None:
    """Create `path` 0700; a folder that already exists keeps its mode (it may be one the caller named)."""
    if not path.is_dir():
        path.mkdir(parents=True, mode=0o700)
        os.chmod(path, 0o700)


def _is_sqlite(path: Path) -> bool:
    """An empty file is a valid empty database to sqlite; anything else must carry the header."""
    if path.stat().st_size == 0:
        return True
    with open(path, "rb") as f:
        return f.read(16) == _SQLITE_HEADER


def _copy_db(src: Path, dest: Path) -> str:
    """Copy one database through the backup API into `dest` (0600) and return its quick_check answer."""
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    try:
        con = sqlite3.connect(str(src), timeout=10.0)
        try:
            con.execute("PRAGMA busy_timeout=10000")
            out = sqlite3.connect(str(dest), timeout=10.0)
            try:
                con.backup(out)
                out.execute("PRAGMA journal_mode=DELETE")  # one self-contained file, no -wal or -shm beside it
            finally:
                out.close()
        finally:
            con.close()
        # Some SQLite builds (3.51 on the Studio) leave the copy's -shm behind after the switch to
        # DELETE mode. The copy is ours and closed, so its side files hold nothing.
        for side in ("-shm", "-wal"):
            try:
                os.unlink(f"{dest}{side}")
            except FileNotFoundError:
                pass
    except Exception as e:
        return f"error: {type(e).__name__}: {e}"[:200]
    return codec_backup.quick_check(dest)


def _new_name(root: Path, now: datetime) -> str:
    base = now.strftime("%Y%m%d-%H%M%S")
    name, n = base, 2
    while (root / name).exists():
        name, n = f"{base}-{n}", n + 1
    return name


def snapshot(src=None, dest_root=None, *, label: str = "", now: Optional[datetime] = None) -> Dict[str, Any]:
    """Copy the state in `src` into `dest_root`/<stamp>/ and return what was done.

    Result keys: ok, path (None when there was nothing to copy), databases {name: {bytes, check, sha256}},
    state {items, files, bytes}, skipped [str], error (str, only when ok is False).
    """
    src = Path(src) if src else default_src()
    result: Dict[str, Any] = {"ok": False, "path": None, "databases": {}, "state": {}, "skipped": [],
                              "error": ""}
    if not src.is_dir():
        result.update(ok=True, skipped=[f"no state folder at {src}"])
        return result
    root = Path(dest_root) if dest_root else src / DIRNAME
    now = now or datetime.now()
    try:
        _private_dir(root)
        work = Path(tempfile.mkdtemp(dir=str(root), prefix=".", suffix=".partial"))  # 0700 from mkdtemp
    except OSError as e:
        result["error"] = f"cannot create {root}: {e}"
        return result

    try:
        for entry in sorted(src.iterdir()):
            if entry.suffix != ".db" or entry.is_symlink() or not entry.is_file():
                continue
            if not _is_sqlite(entry):
                result["skipped"].append(f"{entry.name}: not a SQLite database")
                continue
            dest = work / entry.name
            check = _copy_db(entry, dest)
            if check != "ok":
                result["error"] = f"{entry.name}: {check}"
                return result
            result["databases"][entry.name] = {"bytes": dest.stat().st_size, "check": check,
                                               "sha256": codec_backup.sha256_file(dest)}

        present = [i for i in codec_backup.STATE_ITEMS if (src / i).exists() and not (src / i).is_symlink()]
        if present:
            tar = work / STATE_TAR
            fd = os.open(tar, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh, tarfile.open(fileobj=fh, mode="w:gz") as tf:
                for item in present:
                    tf.add(str(src / item), arcname=item, filter=codec_backup._tar_filter)
            files = len(codec_backup.tar_manifest(tar))  # every member read back, gzip CRC checked
            result["state"] = {"items": present, "files": files, "bytes": tar.stat().st_size}

        manifest = {"created": now.isoformat(timespec="seconds"), "label": label, "source": str(src),
                    "databases": result["databases"], "state": result["state"], "skipped": result["skipped"]}
        fd = os.open(work / MANIFEST, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
        final = root / _new_name(root, now)
        os.rename(work, final)
        work = None
        result.update(ok=True, path=str(final))
        return result
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"[:300]
        return result
    finally:
        if work is not None:
            shutil.rmtree(work, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Copy CODEC's state before an update.")
    ap.add_argument("--src", help="state folder (default ~/.codec)")
    ap.add_argument("--dest", help="where the snapshot folders go (default <src>/pre_update)")
    ap.add_argument("--label", default="", help="a note kept in manifest.json, e.g. the commit being replaced")
    args = ap.parse_args(argv)
    res = snapshot(args.src or None, args.dest or None, label=args.label)
    if not res["ok"]:
        print(f"Could not save a copy of CODEC's data before the update: {res['error']}", flush=True)
        return 1
    if res["path"] is None:
        print("No CODEC data folder yet: nothing to copy before the update.", flush=True)
        return 0
    n = len(res["databases"])
    files = res["state"].get("files", 0)
    print(f"Saved a copy of CODEC's data before the update: {res['path']} "
          f"({n} database(s), {files} settings file(s)). Old copies can be deleted by hand.", flush=True)
    for s in res["skipped"]:
        print(f"  skipped {s}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
