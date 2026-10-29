#!/usr/bin/env python3
"""Restore a CODEC backup (docs/BACKUP-RETENTION-DESIGN.md; plan item P-018).

A tool for the owner at this Mac, never a skill: no chat, voice, agent or MCP path reaches it.

    python3 tools/codec_restore.py list
    python3 tools/codec_restore.py check  BACKUP
    python3 tools/codec_restore.py restore BACKUP --to DIR          # into a new or empty folder, outside ~/.codec
    python3 tools/codec_restore.py restore BACKUP --live [--only ITEM ...] [--replace-damaged]

BACKUP is a file name in ~/.codec/backups (or a path, for --to and check).

--to writes nothing live: it copies a database, or unpacks the state tar, into DIR and proves the copy
by checksum (sha256 of every file equals the backup's) and, for a database, PRAGMA quick_check.

--live writes into ~/.codec (memory.db, config.json, voices/codec-02 ... are AGENTS.md section 10 files),
so it first asks the owner in CODEC (Inbox, on the Mac or the phone) and goes ahead only on the literal
answer 'restore'. What it replaces is kept first: a database through codec_backup's checked copy
(<name>_before-restore_<date>.db), files and folders moved to backups/before-restore_<time>/. A database
is restored with the sqlite backup API, so CODEC can stay running; settings files are read at start, so
restart CODEC afterwards (your action).

plugins.allowlist is never restored, in either mode: a restored allowlist would grant plugins trust.
Approve plugins again with the plugin_approve skill.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import tarfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import codec_backup  # noqa: E402

VERB = "restore"
# Database backups by name: series prefix -> the live file it restores.
DB_SERIES = (("memory_pre-retention_", "memory.db"), ("memory_before-restore_", "memory.db"),
             ("qchat_before-restore_", "qchat.db"), ("vibe_before-restore_", "vibe.db"),
             ("memory_", "memory.db"), ("memory-", "memory.db"), ("qchat_", "qchat.db"), ("vibe_", "vibe.db"))
STATE_SERIES = ("state_", "agents_")


def _say(msg: str) -> None:
    print(msg, flush=True)


def _audit(message: str, **extra) -> None:
    try:
        from codec_audit import log_event
        log_event("backup_restored", "codec-restore", message, extra=extra)
    except Exception:
        pass


def _resolve(arg: str) -> Path:
    p = Path(arg).expanduser()
    if not p.is_absolute() and not p.exists():
        p = codec_backup.backup_dir() / arg
    return p


def _in_backups(p: Path) -> bool:
    return p.resolve().parent == codec_backup.backup_dir().resolve()


def _kind(p: Path) -> str:
    if p.name.endswith(".tar.gz"):
        return "state"
    with open(p, "rb") as f:
        return "db" if f.read(16) == b"SQLite format 3\x00" else "unknown"


def _live_db_name(p: Path) -> str | None:
    for prefix, live in DB_SERIES:
        if p.name.startswith(prefix) and p.name.endswith(".db"):
            return live
    return None


def _inside(child: Path, parent: Path) -> bool:
    c, par = os.path.realpath(child), os.path.realpath(parent)
    return c == par or c.startswith(par + os.sep)


def _item_of(name: str) -> str:
    """The STATE_ITEMS entry a tar member belongs to (voices/codec-02/x.wav -> voices/codec-02)."""
    return next((i for i in codec_backup.STATE_ITEMS if name == i or name.startswith(i + "/")), name.split("/")[0])


def _members(tf: tarfile.TarFile, only=None):
    """Members to restore: never plugins.allowlist; with `only`, just those items."""
    return [m for m in tf.getmembers()
            if _item_of(m.name) not in codec_backup.NEVER_RESTORED and (not only or _item_of(m.name) in only)]


def _items(members) -> list:
    found = []
    for m in members:
        if _item_of(m.name) not in found:
            found.append(_item_of(m.name))
    return found


def _ask(question: str) -> bool:
    """Strict consent through codec_ask_user: the answer must hold the literal verb 'restore'."""
    import codec_ask_user
    _say("Waiting for your answer in CODEC (Inbox, on the Mac or the phone): reply 'restore' to go ahead.")
    answer = codec_ask_user.ask(question, options=["Restore", "Cancel"], destructive=True,
                                destructive_verb=VERB, agent="Restore tool", asked_from="chat",
                                tool_name="codec_restore")
    return codec_ask_user.answer_consents(answer, VERB)


# ── list / check ────────────────────────────────────────────────────────────
def cmd_list() -> int:
    d = codec_backup.backup_dir()
    if not d.is_dir():
        _say(f"No backups folder at {d}.")
        return 1
    names = sorted((n for n in os.listdir(d) if n.endswith((".db", ".tar.gz")) and not n.startswith(".")),
                   reverse=True)
    if not names:
        _say("No backups yet.")
        return 1
    for n in names:
        _say(f"  {n}  {(d / n).stat().st_size / 1048576:.1f} MB")
    return 0


def cmd_check(p: Path) -> int:
    if not p.is_file():
        _say(f"No such backup: {p}")
        return 1
    if _kind(p) == "db":
        qc = codec_backup.quick_check(p)
        _say(f"{p.name}: quick_check {qc}; sha256 {codec_backup.sha256_file(p)}")
        return 0 if qc == "ok" else 1
    if _kind(p) == "state":
        for name, digest in sorted(codec_backup.tar_manifest(p).items()):
            _say(f"  {digest}  {name}")
        _say(f"{p.name}: every member read back; gzip check ok.")
        return 0
    _say(f"{p.name}: not a CODEC backup.")
    return 1


# ── restore into a folder (writes nothing live) ─────────────────────────────
def restore_to(p: Path, target: Path) -> int:
    if _inside(target, codec_backup.home()):
        _say(f"Refused: {target} is inside {codec_backup.home()}. Use --live to restore CODEC's own files.")
        return 1
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        _say(f"Refused: {target} is not an empty folder.")
        return 1
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    kind = _kind(p)
    if kind == "db":
        dest = target / (_live_db_name(p) or p.name)
        shutil.copyfile(p, dest)
        os.chmod(dest, 0o600)
        same = codec_backup.sha256_file(dest) == codec_backup.sha256_file(p)
        qc = codec_backup.quick_check(dest)
        _say(f"  {dest.name}: sha256 {'equal' if same else 'DIFFERENT'}, quick_check {qc}")
        ok = same and qc == "ok"
        files = 1
    elif kind == "state":
        expected = codec_backup.tar_manifest(p)
        with tarfile.open(p, "r:gz") as tf:
            members = _members(tf)
            tf.extractall(target, members=members, filter="data")
        files, ok = 0, True
        for m in members:
            if not m.isfile():
                continue
            files += 1
            same = codec_backup.sha256_file(target / m.name) == expected[m.name]
            ok = ok and same
            if not same:
                _say(f"  {m.name}: sha256 DIFFERENT")
        skipped = [n for n in expected if n in codec_backup.NEVER_RESTORED]
        if skipped:
            _say("  plugins.allowlist left out: it is never restored (approve plugins again with plugin_approve).")
    else:
        _say(f"{p.name}: not a CODEC backup.")
        return 1
    _audit(f"Backup {p.name} restored into a folder", backup=p.name, mode="folder", files=files, ok=ok)
    if ok:
        _say(f"Restore check passed: {files} file(s) in {target}, checksums equal.")
        return 0
    _say("Restore check FAILED: see the lines above.")
    return 1


# ── live restore (strict consent) ───────────────────────────────────────────
def restore_live_db(p: Path, replace_damaged: bool) -> int:
    live_name = _live_db_name(p)
    if not live_name:
        _say(f"Refused: {p.name} is not a database backup name CODEC knows.")
        return 1
    qc = codec_backup.quick_check(p)
    if qc != "ok":
        _say(f"Refused: the backup {p.name} fails its own check ({qc}).")
        return 1
    live = codec_backup.home() / live_name
    stem = live_name[:-3]
    question = (f"Restore {live_name} from the backup {p.name}? CODEC first keeps a checked copy of the "
                f"current {live_name} in backups ({stem}_before-restore_<date>.db). Reply 'restore' to go ahead.")
    if not _ask(question):
        _say("Not restored: the owner did not answer 'restore'.")
        _audit(f"Restore of {p.name} refused", backup=p.name, mode="live", decision="refused")
        return 1
    if live.exists():
        before = codec_backup.backup_sqlite(live, f"{stem}_before-restore", keep=3, force=True)
        if not before["ok"] and not replace_damaged:
            _say(f"Stopped: the current {live_name} could not be copied with a passing check ({before['check']}). "
                 "Run again with --replace-damaged to restore over it without a copy.")
            return 1
        if before["ok"]:
            _say(f"  current {live_name} kept as backups/{before['file']}")
    src = sqlite3.connect(str(p), timeout=10.0)
    dst = sqlite3.connect(str(live), timeout=30.0)
    try:
        dst.execute("PRAGMA busy_timeout=30000")
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    try:
        os.chmod(live, 0o600)
    except OSError:
        pass
    qc_live = codec_backup.quick_check(live)
    same = codec_backup.content_checksum(live) == codec_backup.content_checksum(p)
    ok = qc_live == "ok" and same
    _say(f"  {live_name}: quick_check {qc_live}, content {'equal to the backup' if same else 'DIFFERENT'}")
    _audit(f"Backup {p.name} restored into {live_name}", backup=p.name, mode="live", items=[live_name], ok=ok)
    _say("Restored." if ok else "Restore check FAILED: see the line above.")
    return 0 if ok else 1


def restore_live_state(p: Path, only) -> int:
    home = codec_backup.home()
    expected = codec_backup.tar_manifest(p)
    with tarfile.open(p, "r:gz") as tf:
        members = _members(tf, only)
        items = _items(members)
        if not items:
            _say("Nothing to restore (plugins.allowlist is never restored).")
            return 1
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        aside = codec_backup.backup_dir() / f"before-restore_{stamp}"
        question = (f"Restore {', '.join(items)} from the backup {p.name}? The current files move to "
                    f"backups/{aside.name} first. Reply 'restore' to go ahead.")
        if not _ask(question):
            _say("Not restored: the owner did not answer 'restore'.")
            _audit(f"Restore of {p.name} refused", backup=p.name, mode="live", decision="refused")
            return 1
        staging = codec_backup.backup_dir() / f".restore-staging_{stamp}"
        staging.mkdir(mode=0o700)
        tf.extractall(staging, members=members, filter="data")
    for m in members:
        if m.isfile() and codec_backup.sha256_file(staging / m.name) != expected[m.name]:
            shutil.rmtree(staging, ignore_errors=True)  # only this run's own staging copy
            _say(f"Stopped before touching anything: {m.name} does not match the backup.")
            return 1
    aside.mkdir(mode=0o700)
    for item in items:
        live, staged = home / item, staging / item
        if live.exists() or live.is_symlink():
            (aside / item).parent.mkdir(parents=True, exist_ok=True)
            os.replace(live, aside / item)
        live.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged, live)
        _say(f"  restored {item}" + (f" (the old one is in backups/{aside.name})" if (aside / item).exists() else ""))
    ok = all(codec_backup.sha256_file(home / m.name) == expected[m.name] for m in members if m.isfile())
    shutil.rmtree(staging, ignore_errors=True)  # only the emptied staging folder this run made
    if "plugins.allowlist" in expected:
        _say("  plugins.allowlist is never restored (approve plugins again with plugin_approve).")
    _audit(f"Backup {p.name} restored into {', '.join(items)}", backup=p.name, mode="live", items=items, ok=ok)
    _say("Restored, checksums equal. Restart CODEC so it reads the restored settings (your action)." if ok
         else "Restore check FAILED.")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="codec_restore", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    c = sub.add_parser("check")
    c.add_argument("backup")
    r = sub.add_parser("restore")
    r.add_argument("backup")
    where = r.add_mutually_exclusive_group(required=True)
    where.add_argument("--to", help="a new or empty folder outside ~/.codec")
    where.add_argument("--live", action="store_true", help="into ~/.codec, after the owner answers 'restore'")
    r.add_argument("--only", nargs="+", choices=[i for i in codec_backup.STATE_ITEMS if i not in codec_backup.NEVER_RESTORED],
                   help="state items to restore (default: all but plugins.allowlist)")
    r.add_argument("--replace-damaged", action="store_true",
                   help="restore a database even when the current one cannot be copied with a passing check")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        return cmd_list()
    p = _resolve(a.backup)
    if a.cmd == "check":
        return cmd_check(p)
    if not p.is_file():
        _say(f"No such backup: {p}")
        return 1
    if a.to:
        return restore_to(p, Path(a.to).expanduser())
    if not _in_backups(p):
        _say(f"Refused: a live restore reads only from {codec_backup.backup_dir()}.")
        return 1
    if _kind(p) == "db":
        return restore_live_db(p, a.replace_damaged)
    if _kind(p) == "state":
        return restore_live_state(p, a.only)
    _say(f"{p.name}: not a CODEC backup.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
