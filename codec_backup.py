"""CODEC backups and stated retention (docs/BACKUP-RETENTION-DESIGN.md; plan item P-018, owner decision D-38).

One writer for every SQLite backup (`backup_sqlite`): the database is copied through the sqlite backup API
(safe under WAL), the new copy is checked with `PRAGMA quick_check`, and only a copy that passes replaces
anything. A copy that fails is thrown away, the older copies are kept and the owner gets one alert
(`codec_alerts.alert_once`). Copies are 0600 in a 0700 folder.

One state tar (`backup_state`) holds the agents' state and the owner's settings files; the restore tool
(`tools/codec_restore.py`, never a skill) reads it back.

Retention (`run_retention`): conversations in memory.db, clipboard history and webcam photos are deleted
after the number of days in `config.json:retention` (Settings > Memory). memory.db is copied through
`backup_sqlite` before the first clean-up with a new number, and no clean-up runs without a checked copy
from the same day. Every count lands in `~/.codec/backup_status.json`, which backup status reads.

Paths hang off `CODEC_HOME` (`CODEC_BACKUP_HOME` overrides it, as `CODEC_NOTES_DIR` does for notes); the
test suite points it at a temp folder (tests/conftest.py).
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import sqlite3
import tarfile
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("codec_backup")

CODEC_HOME = os.path.expanduser(os.environ.get("CODEC_BACKUP_HOME", "").strip() or "~/.codec")

# (prefix, file in CODEC_HOME, copies kept). memory.db keeps 30: the nightly launchd copy it replaces kept 30.
MEMORY_KEEP = 30
DATABASES = (("memory", "memory.db", MEMORY_KEEP), ("qchat", "qchat.db", 7), ("vibe", "vibe.db", 7))
# Older names of the same series, rotated with it so nothing is orphaned (scripts/backup_memory.sh wrote memory-<date>.db).
LEGACY_NAMES = {"memory": ("memory-",)}
PRE_RETENTION_PREFIX = "memory_pre-retention"
PRE_RETENTION_KEEP = 3

# What the state tar holds, relative to CODEC_HOME. plugins.allowlist is saved but never restored.
STATE_ITEMS = ("agents", "config.json", "notes", "standing_rules.json", "schedules.json",
               "voices/codec-02", "plugins.allowlist")
NEVER_RESTORED = ("plugins.allowlist",)
STATE_KEEP = 7
STATE_PREFIX = "state"
STATE_LEGACY = ("agents_",)  # the agents-only tar the heartbeat wrote before

# Owner decision D-38, 29 Oct 2026: chats 90 days, clipboard and webcam photos 30 days. 0 keeps them forever.
RETENTION_DEFAULTS = {"chats_days": 90, "clipboard_days": 30, "photos_days": 30}
RETENTION_MIN = {"chats_days": 7, "clipboard_days": 1, "photos_days": 1}
RETENTION_MAX = 3650
RETENTION_LABELS = {"chats_days": "chats", "clipboard_days": "clipboard history", "photos_days": "webcam photos"}
DELETIONS_KEPT = 50

_DATE = r"(\d{4}-\d{2}-\d{2})"
_PHOTO_RE = re.compile(r"^webcam_(\d{8})_(\d{6})\.jpg$")


# ── Paths and the status file ────────────────────────────────────────────────
def home() -> Path:
    return Path(CODEC_HOME)


def backup_dir() -> Path:
    return home() / "backups"


def status_path() -> Path:
    return home() / "backup_status.json"


def load_status() -> Dict[str, Any]:
    import json
    try:
        with open(status_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _update_status(mutate) -> None:
    import codec_jsonstore

    def _apply(data):
        data = data if isinstance(data, dict) else {}
        mutate(data)
        return data
    try:
        codec_jsonstore.read_modify_write(status_path(), _apply)
    except Exception as e:  # the backup itself already succeeded or failed; never raise from bookkeeping
        log.warning("backup status write failed: %s", e)


def _ensure_dir() -> Path:
    d = backup_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def _now_iso(now: Optional[datetime] = None) -> str:
    return (now or datetime.now()).isoformat(timespec="seconds")


# ── Alerts and audit (metadata only) ─────────────────────────────────────────
def _alert_failed(key: str, label: str, reason: str) -> None:
    try:
        import codec_alerts
        codec_alerts.alert_once(f"backup_check:{key}", "warning",
                                f"CODEC backup of {label} failed its check ({reason}). The older copies are kept.")
    except Exception as e:
        log.warning("backup alert failed: %s", e)


def _alert_ok(key: str, label: str) -> None:
    try:
        import codec_alerts
        codec_alerts.alert_resolved(f"backup_check:{key}", f"CODEC backup of {label} passes its check again.")
    except Exception as e:
        log.warning("backup alert failed: %s", e)


def _audit(event: str, message: str, *, level: str = "info", **extra) -> None:
    try:
        from codec_audit import log_event
        log_event(event, "codec-backup", message, outcome="warning" if level == "warning" else "ok",
                  level=level, extra=extra)
    except Exception:
        pass


# ── Checks ───────────────────────────────────────────────────────────────────
def quick_check(path) -> str:
    """'ok' when `PRAGMA quick_check` passes on the file at `path`, otherwise a short reason."""
    path = str(path)
    if not os.path.isfile(path):
        return "missing"
    try:
        con = sqlite3.connect(path, timeout=5.0)
        try:
            rows = con.execute("PRAGMA quick_check").fetchall()
        finally:
            con.close()
    except sqlite3.Error as e:
        return f"error: {e}"[:200]
    if rows == [("ok",)]:
        return "ok"
    return ("; ".join(str(r[0]) for r in rows[:3]) or "no answer")[:200]


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def content_checksum(path) -> str:
    """sha256 over the SQL dump: equal when two databases hold the same rows, whatever their journal mode."""
    h = hashlib.sha256()
    con = sqlite3.connect(str(path), timeout=5.0)
    try:
        for line in con.iterdump():
            h.update(line.encode("utf-8"))
            h.update(b"\n")
    finally:
        con.close()
    return h.hexdigest()


def tar_manifest(path) -> Dict[str, str]:
    """{member name: sha256} for every regular file in a tar.gz; reading it all also checks the gzip CRC."""
    out: Dict[str, str] = {}
    with tarfile.open(path, "r:gz") as tf:
        for m in tf:
            if m.isfile():
                f = tf.extractfile(m)
                out[m.name] = hashlib.sha256(f.read() if f else b"").hexdigest()
    return out


# ── Rotation ─────────────────────────────────────────────────────────────────
def _rotate(names: tuple, suffix: str, keep: int) -> List[str]:
    """Keep the files of the newest `keep` dates in the series `names` (+ date + suffix); tighten them to 0600."""
    d = backup_dir()
    pat = re.compile(rf"^(?:{'|'.join(re.escape(n) for n in names)}){_DATE}{re.escape(suffix)}$")
    files = []
    for name in os.listdir(d):
        m = pat.match(name)
        if m and os.path.isfile(d / name) and not os.path.islink(d / name):
            files.append((m.group(1), name))
    keep_dates = set(sorted({date for date, _ in files})[-keep:]) if keep > 0 else set()
    removed = []
    for date, name in files:
        p = d / name
        if date in keep_dates:
            try:
                if p.stat().st_mode & 0o077:
                    os.chmod(p, 0o600)
            except OSError:
                pass
            continue
        try:
            p.unlink()
            removed.append(name)
        except OSError as e:
            log.warning("could not remove old backup %s: %s", name, e)
    # Our own half-written temp files from a crash, more than a day old.
    for name in os.listdir(d):
        if name.startswith(".") and name.endswith(".partial"):
            try:
                if datetime.now().timestamp() - (d / name).stat().st_mtime > 86400:
                    (d / name).unlink()
            except OSError:
                pass
    return removed


def _failed_recently(rec: Dict[str, Any]) -> bool:
    """A failed check is tried again after an hour, not at every 30-minute heartbeat."""
    if rec.get("last_check") in (None, "ok"):
        return False
    try:
        return (datetime.now() - datetime.fromisoformat(rec.get("last_check_at", ""))).total_seconds() < 3600
    except (TypeError, ValueError):
        return False


# ── The one SQLite backup writer ─────────────────────────────────────────────
def backup_sqlite(db_path, prefix: str, *, keep: int = 7, force: bool = False,
                  today: Optional[str] = None) -> Dict[str, Any]:
    """Copy `db_path` to backups/<prefix>_<today>.db, check the copy, then keep the newest `keep` dates.

    Without `force`, a copy already checked today is not made again. A copy that fails `quick_check` is
    removed, replaces nothing, leaves the older copies alone and raises one owner alert.
    """
    today = today or datetime.now().strftime("%Y-%m-%d")
    name = f"{prefix}_{today}.db"
    result: Dict[str, Any] = {"prefix": prefix, "file": name, "ok": False, "skipped": False,
                              "check": None, "bytes": 0, "removed": []}
    db_path = str(db_path)
    if not os.path.isfile(db_path):
        result.update(skipped=True, check="no database")
        return result
    d = _ensure_dir()
    dest = d / name
    rec = (load_status().get("databases") or {}).get(prefix) or {}
    if not force and dest.is_file() and rec.get("last_ok_file") == name and rec.get("last_check") == "ok":
        result.update(ok=True, skipped=True, check="ok", bytes=dest.stat().st_size)
        return result
    if not force and _failed_recently(rec):
        result.update(skipped=True, check=f"the last check failed less than an hour ago ({rec.get('last_check')})")
        return result

    fd, tmp = tempfile.mkstemp(dir=str(d), prefix=f".{prefix}_", suffix=".partial")  # 0600 from the start
    os.close(fd)
    try:
        src = sqlite3.connect(db_path, timeout=10.0)
        try:
            src.execute("PRAGMA busy_timeout=10000")
            dst = sqlite3.connect(tmp, timeout=10.0)
            try:
                src.backup(dst)
                dst.execute("PRAGMA journal_mode=DELETE")  # a self-contained file: no -wal or -shm beside it
            finally:
                dst.close()
        finally:
            src.close()
        check = quick_check(tmp)
    except Exception as e:
        check = f"error: {type(e).__name__}"
    result["check"] = check
    label = os.path.basename(db_path)
    if check != "ok":
        try:
            os.unlink(tmp)
        except OSError:
            pass

        def _failed(st):
            r = st.setdefault("databases", {}).setdefault(prefix, {})
            r.update(last_check=check, last_check_at=_now_iso(), keep=keep, source=label)
        _update_status(_failed)
        _alert_failed(prefix, label, check)
        _audit("backup_check_failed", f"Backup of {label} failed its check; older copies kept",
               level="warning", prefix=prefix, reason=check[:120])
        log.warning("%s backup rejected (%s); older copies kept", prefix, check)
        return result

    os.chmod(tmp, 0o600)
    os.replace(tmp, dest)
    size = dest.stat().st_size
    removed = _rotate((f"{prefix}_",) + LEGACY_NAMES.get(prefix, ()), ".db", keep)
    result.update(ok=True, bytes=size, removed=removed)

    def _ok(st):
        r = st.setdefault("databases", {}).setdefault(prefix, {})
        r.update(last_ok_at=_now_iso(), last_ok_file=name, last_ok_bytes=size, last_check="ok",
                 last_check_at=_now_iso(), keep=keep, source=label)
        if removed:
            r.update(last_removed=removed, last_removed_at=_now_iso())
    _update_status(_ok)
    if rec.get("last_check") not in (None, "ok"):  # only after a recorded failure: clear its alert
        _alert_ok(prefix, label)
    log.info("%s backup: %s (%.1f MB, check ok)", prefix, name, size / 1048576)
    return result


def backup_memory(*, force: bool = False, today: Optional[str] = None) -> Dict[str, Any]:
    return backup_sqlite(home() / "memory.db", "memory", keep=MEMORY_KEEP, force=force, today=today)


# ── The state tar ────────────────────────────────────────────────────────────
def _tar_filter(ti: tarfile.TarInfo) -> Optional[tarfile.TarInfo]:
    if not (ti.isfile() or ti.isdir()):
        return None  # no links, devices or sockets
    if ti.name.endswith((".lock", ".tmp", ".partial")):
        return None
    return ti


def backup_state(*, keep: int = STATE_KEEP, force: bool = False, today: Optional[str] = None) -> Dict[str, Any]:
    """backups/state_<today>.tar.gz: agents/, config.json, notes/, standing_rules.json, schedules.json,
    voices/codec-02 and plugins.allowlist (0600, the newest `keep` dates kept). Every member is read back
    before the tar replaces anything."""
    today = today or datetime.now().strftime("%Y-%m-%d")
    name = f"{STATE_PREFIX}_{today}.tar.gz"
    result: Dict[str, Any] = {"prefix": STATE_PREFIX, "file": name, "ok": False, "skipped": False,
                              "check": None, "bytes": 0, "items": [], "removed": []}
    h = home()
    present = [i for i in STATE_ITEMS if (h / i).exists() and not (h / i).is_symlink()]
    if not present:
        result.update(skipped=True, check="nothing to save")
        return result
    d = _ensure_dir()
    dest = d / name
    rec = load_status().get("state") or {}
    if not force and dest.is_file() and rec.get("last_ok_file") == name and rec.get("last_check") == "ok":
        result.update(ok=True, skipped=True, check="ok", bytes=dest.stat().st_size, items=rec.get("items", []))
        return result
    if not force and _failed_recently(rec):
        result.update(skipped=True, check=f"the last check failed less than an hour ago ({rec.get('last_check')})")
        return result

    fd, tmp = tempfile.mkstemp(dir=str(d), prefix=".state_", suffix=".partial")
    try:
        with os.fdopen(fd, "wb") as fh, tarfile.open(fileobj=fh, mode="w:gz") as tf:
            for item in present:
                tf.add(str(h / item), arcname=item, filter=_tar_filter)
        files = len(tar_manifest(tmp))
        check = "ok"
    except Exception as e:
        files, check = 0, f"error: {type(e).__name__}"
    result["check"] = check
    if check != "ok":
        try:
            os.unlink(tmp)
        except OSError:
            pass

        def _failed(st):
            st.setdefault("state", {}).update(last_check=check, last_check_at=_now_iso(), keep=keep)
        _update_status(_failed)
        _alert_failed("state", "the settings and agents", check)
        _audit("backup_check_failed", "State backup failed its check; older copies kept",
               level="warning", prefix=STATE_PREFIX, reason=check[:120])
        return result

    os.chmod(tmp, 0o600)
    os.replace(tmp, dest)
    size = dest.stat().st_size
    removed = _rotate((f"{STATE_PREFIX}_",) + STATE_LEGACY, ".tar.gz", keep)
    result.update(ok=True, bytes=size, items=present, removed=removed)

    def _ok(st):
        r = st.setdefault("state", {})
        r.update(last_ok_at=_now_iso(), last_ok_file=name, last_ok_bytes=size, last_check="ok",
                 last_check_at=_now_iso(), keep=keep, items=present, files=files)
        if removed:
            r.update(last_removed=removed, last_removed_at=_now_iso())
    _update_status(_ok)
    if rec.get("last_check") not in (None, "ok"):
        _alert_ok("state", "the settings and agents")
    log.info("state backup: %s (%d files, %.1f MB)", name, files, size / 1048576)
    return result


def daily_backups(today: Optional[str] = None) -> List[Dict[str, Any]]:
    """The heartbeat's daily run: the three SQLite stores and the state tar (each at most once a day)."""
    results = [backup_sqlite(home() / fname, prefix, keep=keep, today=today) for prefix, fname, keep in DATABASES]
    results.append(backup_state(today=today))
    return results


# ── Retention ────────────────────────────────────────────────────────────────
def normalize_retention(raw: Any) -> tuple:
    """(settings, errors). Missing keys take the D-38 default; a bad value is an error and keeps its default."""
    raw = raw if isinstance(raw, dict) else {}
    out, errors = dict(RETENTION_DEFAULTS), []
    for key, default in RETENTION_DEFAULTS.items():
        if key not in raw:
            continue
        v = raw[key]
        if isinstance(v, str) and v.strip().isdigit():
            v = int(v.strip())
        if isinstance(v, bool) or not isinstance(v, int) or not (v == 0 or RETENTION_MIN[key] <= v <= RETENTION_MAX):
            errors.append(f"{RETENTION_LABELS[key]}: 0 (keep forever) or {RETENTION_MIN[key]} to {RETENTION_MAX} days")
            continue
        out[key] = v
    return out, errors


def _read_config() -> Dict[str, Any]:
    import json
    try:
        with open(home() / "config.json", encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        return {}


def retention_settings(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, int]:
    cfg = _read_config() if cfg is None else cfg
    return normalize_retention(cfg.get("retention"))[0]


def _checked_memory_copy_today(today: str) -> bool:
    rec = (load_status().get("databases") or {}).get("memory") or {}
    return rec.get("last_check") == "ok" and str(rec.get("last_ok_file", "")) == f"memory_{today}.db"


def _retain_chats(days: int, now: datetime) -> Dict[str, Any]:
    if days == 0:  # recorded as applied, so a later number takes a fresh copy first
        return {"days": 0, "deleted": 0, "skipped": "kept forever", "applied": True}
    db = home() / "memory.db"
    if not db.is_file():
        return {"days": days, "deleted": 0, "skipped": "no database"}
    today = now.strftime("%Y-%m-%d")
    if not _checked_memory_copy_today(today) and not backup_memory(today=today)["ok"]:
        return {"days": days, "deleted": 0, "skipped": "no checked copy of memory.db today"}
    applied = (load_status().get("retention") or {}).get("chats_days_applied")
    if applied != days:
        pre = backup_sqlite(db, PRE_RETENTION_PREFIX, keep=PRE_RETENTION_KEEP, force=True, today=today)
        if not pre["ok"]:
            return {"days": days, "deleted": 0, "skipped": "the copy before the new setting failed its check"}
    from codec_memory import CodecMemory
    before = _count_rows(db)
    mem = CodecMemory(str(db))
    try:
        res = mem.cleanup(retention_days=days)
    except Exception as e:  # e.g. VACUUM found the file busy after the delete was committed: keep the count
        return {"days": days, "deleted": max(0, before - _count_rows(db)), "applied": True,
                "error": type(e).__name__}
    finally:
        mem.close()
    return {"days": days, "deleted": int(res.get("deleted", 0)), "applied": True}


def _count_rows(db: Path) -> int:
    con = sqlite3.connect(str(db), timeout=10.0)
    try:
        return con.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
    finally:
        con.close()


def _retain_clipboard(days: int, now: datetime) -> Dict[str, Any]:
    if days == 0:
        return {"days": 0, "deleted": 0, "skipped": "kept forever"}
    db = home() / "clipboard.db"
    if not db.is_file():
        return {"days": days, "deleted": 0, "skipped": "no history"}
    cutoff = (now - timedelta(days=days)).isoformat()
    con = sqlite3.connect(str(db), timeout=10.0)
    try:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='clips'").fetchone():
            return {"days": days, "deleted": 0, "skipped": "no history"}
        n = max(0, con.execute("DELETE FROM clips WHERE timestamp < ?", (cutoff,)).rowcount)
        con.commit()
        if n:
            try:
                con.execute("VACUUM")  # the deleted text leaves the file, not just the table
            except sqlite3.Error as e:
                return {"days": days, "deleted": n, "error": f"VACUUM: {type(e).__name__}"}
    finally:
        con.close()
    return {"days": days, "deleted": n}


def _retain_photos(days: int, now: datetime) -> Dict[str, Any]:
    if days == 0:
        return {"days": 0, "deleted": 0, "skipped": "kept forever"}
    folder = home() / "photos"
    if not folder.is_dir():
        return {"days": days, "deleted": 0, "skipped": "no photos"}
    cutoff = now - timedelta(days=days)
    n = 0
    for entry in os.scandir(folder):
        m = _PHOTO_RE.match(entry.name)  # only the files CODEC's webcam routes write
        if not m or not entry.is_file(follow_symlinks=False):
            continue
        try:
            taken = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
        except ValueError:
            continue
        if taken < cutoff:
            try:
                os.unlink(entry.path)
                n += 1
            except OSError as e:
                log.warning("could not delete an old webcam photo: %s", e)
    return {"days": days, "deleted": n}


def run_retention(*, cfg: Optional[Dict[str, Any]] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Delete what is older than the stated retention; record every count in backup status."""
    now = now or datetime.now()
    settings = retention_settings(cfg)
    out: Dict[str, Any] = {}
    for kind, key, fn in (("chats", "chats_days", _retain_chats), ("clipboard", "clipboard_days", _retain_clipboard),
                          ("photos", "photos_days", _retain_photos)):
        try:
            out[kind] = fn(settings[key], now)
        except Exception as e:
            log.warning("%s clean-up failed: %s", kind, e)
            out[kind] = {"days": settings[key], "deleted": 0, "error": type(e).__name__}
    at = _now_iso(now)

    def _record(st):
        r = st.setdefault("retention", {})
        r["settings"] = settings
        r["last_run"] = {k: {"at": at, **v} for k, v in out.items()}
        if out["chats"].get("applied"):
            r["chats_days_applied"] = settings["chats_days"]
        dels = st.setdefault("deletions", [])
        for k, v in out.items():
            if v.get("deleted"):
                dels.insert(0, {"at": at, "what": k, "count": v["deleted"], "days": v["days"]})
        del dels[DELETIONS_KEPT:]
    _update_status(_record)
    for k, v in out.items():
        if v.get("deleted"):
            _audit("retention_cleanup", f"Deleted {v['deleted']} {k} older than {v['days']} days",
                   kind=k, deleted=v["deleted"], days=v["days"])
    return out


# ── Backup status (shared by skills/backup_status.py and Settings > Memory) ──
def _age(iso: Optional[str]) -> str:
    if not iso:
        return "never"
    try:
        delta = datetime.now() - datetime.fromisoformat(iso)
    except ValueError:
        return iso
    if delta.days > 0:
        return f"{delta.days}d ago"
    return f"{delta.seconds // 3600}h ago" if delta.seconds >= 3600 else f"{delta.seconds // 60}m ago"


def _removed_note(r: Dict[str, Any]) -> str:
    gone = r.get("last_removed") or []
    return f"; removed {len(gone)} older cop{'y' if len(gone) == 1 else 'ies'} {_age(r.get('last_removed_at'))} ({', '.join(gone[:3])})" if gone else ""


def _mb(n) -> str:
    return f"{(n or 0) / 1048576:.1f} MB"


def describe_backup(result: Dict[str, Any]) -> str:
    if result.get("ok") and result.get("skipped"):
        return f"Today's backup {result['file']} is already written and checked ({_mb(result.get('bytes'))})."
    if result.get("ok"):
        return f"Backup {result['file']} written and checked ({_mb(result.get('bytes'))}, quick_check ok)."
    if result.get("skipped"):
        return f"No backup made: {result.get('check')}."
    return f"Backup failed its check ({result.get('check')}). The older copies are kept."


def status_report() -> str:
    """Plain text: last backups and their check result, the state tar, retention settings, recent deletions."""
    st = load_status()
    dbs = st.get("databases") or {}
    lines = ["CODEC backups (~/.codec/backups, on this Mac's disk)"]
    for prefix, fname, keep in DATABASES:
        r = dbs.get(prefix) or {}
        if r.get("last_ok_file"):
            line = (f"  {fname}: last copy {r['last_ok_file']} ({_mb(r.get('last_ok_bytes'))}, "
                    f"{_age(r.get('last_ok_at'))}), check {r.get('last_check')}, {keep} days kept")
            if r.get("last_check") != "ok":
                line += f"; the last attempt failed {_age(r.get('last_check_at'))}"
            line += _removed_note(r)
        elif r.get("last_check"):
            line = f"  {fname}: no checked copy yet (last check: {r['last_check']})"
        else:
            line = f"  {fname}: no checked copy yet"
        lines.append(line)
    pre = dbs.get(PRE_RETENTION_PREFIX) or {}
    if pre.get("last_ok_file"):
        lines.append(f"  memory.db before a retention change: {pre['last_ok_file']} ({_age(pre.get('last_ok_at'))})")
    s = st.get("state") or {}
    if s.get("last_ok_file"):
        lines.append(f"  Settings and agents: {s['last_ok_file']} ({_mb(s.get('last_ok_bytes'))}, "
                     f"{_age(s.get('last_ok_at'))}), {s.get('files', 0)} files: {', '.join(s.get('items', []))}; "
                     f"{s.get('keep', STATE_KEEP)} days kept" + _removed_note(s))
    else:
        lines.append("  Settings and agents: no state backup yet")
    settings = retention_settings()
    keep = ", ".join(f"{RETENTION_LABELS[k]} {'forever' if v == 0 else f'{v} days'}" for k, v in settings.items())
    lines += ["", f"Kept for: {keep} (Settings > Memory)."]
    last = (st.get("retention") or {}).get("last_run") or {}
    if last:
        parts = []
        for kind in ("chats", "clipboard", "photos"):
            v = last.get(kind) or {}
            note = v.get("skipped") or v.get("error")
            parts.append(f"{kind} {v.get('deleted', 0)}" + (f" ({note})" if note else ""))
        any_at = next((v.get("at") for v in last.values() if v.get("at")), None)
        lines.append(f"Last clean-up {_age(any_at)}: deleted " + ", ".join(parts) + ".")
    dels = st.get("deletions") or []
    if dels:
        lines.append("Recent deletions:")
        for d in dels[:10]:
            lines.append(f"  {str(d.get('at', ''))[:16].replace('T', ' ')}  {d.get('what')}: {d.get('count')} "
                         f"older than {d.get('days')} days")
    else:
        lines.append("Recent deletions: none recorded.")
    lines += ["", "Restore on this Mac: python3 tools/codec_restore.py list (a live restore asks you first)."]
    return "\n".join(lines)


def _cli(argv: List[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="[backup] %(message)s")
    what = argv[0] if argv else "memory"
    if what == "memory":
        r = backup_memory()
        print(f"[backup] {describe_backup(r)}")
        return 0 if r["ok"] or r["skipped"] else 1
    if what == "status":
        print(status_report())
        return 0
    print("usage: python3 -m codec_backup [memory|status]")
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(_cli(sys.argv[1:]))
