"""codec_doctor: one read-only check of this Mac's CODEC install (P-022, docs/DOCTOR-DESIGN.md).

Settings > Connectors runs it ("Check my Mac", "Copy report"); so does `python3 tools/doctor.py [--json]`.
It reads files, file modes, the PM2 list, the listening sockets and the local model server's answer, and
reports counts, modes, fixed paths and setting names only: never a secret value, a file's contents, or
the name of a file other than the fixed paths it checks. It writes nothing, starts and stops nothing,
and asks macOS for no permission (every permission read below is one that cannot show a prompt).

Stdlib only, plus codec_health (stdlib too), so it runs from the app bundle and from a checkout alike.
"""
from __future__ import annotations

import argparse
import ctypes
import io
import json
import os
import platform
import re
import shutil
import socket
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

FAIL, WARN, OK, INFO, NA = "FAIL", "WARN", "OK", "INFO", "N/A"
STATUSES = (FAIL, WARN, INFO, OK, NA)
REPORT_VERSION = 1
MODEL_TIMEOUT_S = 3
START_GRACE_S = 300          # PM2's start grace, as the heartbeat uses it (codec_models.pm2_app_loading)
MAX_FILE_BYTES = 5_000_000   # a copy of config.json bigger than this is not read
MAX_FIELDS = 20
APP_LABEL = "ai.avadigital.codec."
SETTINGS_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_"
PERMISSIONS_NOTE = ("These permissions are those of the process that ran this check: the dashboard when you "
                    "use Check my Mac. The voice call, the wake word and the bridges run in their own "
                    "processes, and the dashboard cannot read their grants.")

# Ports CODEC's own services use (the model, speech and voice ports also come from config.json).
CODEC_PORTS = {8090: "dashboard", 8091: "MCP server", 8083: "model server", 8084: "speech to text",
               8085: "voice (TTS)"}
VIBE_PORTS = range(5300, 5400)
ALL_INTERFACES = {"*", "0.0.0.0", "[::]", "::", "[::0]"}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

# What a missing permission breaks (packaging/macos/first_run.py says the same to a new buyer).
PERMISSIONS = [("screen", "Screen Recording", "ScreenCapture", "reading the screen when you ask"),
               ("accessibility", "Accessibility", "Accessibility", "clicking and typing for you"),
               ("full_disk", "Full Disk Access", "AllFiles", "the iMessage bridge (the Messages database)"),
               ("microphone", "Microphone", "Microphone", "voice commands, dictation and the wake word"),
               ("automation", "Automation", "Automation", "controlling apps such as Messages, Notes and Reminders")]
AUTOMATION_TARGETS = [("Messages", "com.apple.MobileSMS"), ("Notes", "com.apple.Notes"),
                      ("Reminders", "com.apple.reminders"), ("Music", "com.apple.Music"),
                      ("System Events", "com.apple.systemevents"), ("Google Chrome", "com.google.Chrome"),
                      ("Terminal", "com.apple.Terminal")]
AUTOMATION_STATES = ("allowed", "denied", "not asked", "not open", "unknown")
UNKNOWN_TEXT = {"microphone": "Not asked yet: macOS asks the first time CODEC listens.",
                "automation": "Not known until CODEC first drives one of these apps.",
                "full_disk": "Could not be checked: there is no Messages database here."}

# Token shapes: the scrubber replaces them, and a value of one of these shapes counts as a key wherever
# it sits. Our own list (codec_audit has a richer one, but this module stays stdlib only).
TOKEN_SHAPES = [re.compile(p) for p in (
    r"sk-ant-[A-Za-z0-9_-]{16,}",
    r"\bsk-[A-Za-z0-9_-]{20,}",
    r"\bAKIA[0-9A-Z]{16}\b",
    r"\bgh[pousr]_[A-Za-z0-9]{30,}",
    r"\bxox[abprs]-[A-Za-z0-9-]{10,}",
    r"\bAIza[0-9A-Za-z_-]{35}",
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}",
    r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}",
    r"\bcodec_(?:at|rt)_[0-9a-f]{16,}",
    r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b",
    r"\b[0-9a-fA-F]{32,}\b",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
)]
_LONG_HEX = TOKEN_SHAPES[10]
# Finding a key uses every shape but the long-hex one: a PIN hash, a commit id or a client id is no key.
DETECT_SHAPES = [p for p in TOKEN_SHAPES if p is not _LONG_HEX]
_SECRET_NAME = re.compile(r"(?i)(api_?key|token|secret|passw(?:or)?d|bearer|authorization|private_?key|"
                          r"access_?key|credential)")
_NOT_SECRET_NAME = re.compile(r"(?i)(_hash|_slot|_id|_ids|_path|_url|_file|_ttl|_count|_at|_seconds|tokens|"
                              r"_limit|_len|_name|_type|_uri|_method|_mode|_kind|_format|_scheme|_version)$")
_PLACEHOLDER = re.compile(r"<[^>]*>|^\$\{?[A-Za-z_][A-Za-z0-9_]*\}?$|redacted|^(.)\1+$|^(not[-_ ]?needed|none|null|"
                          r"empty|dummy|changeme|placeholder|your[-_ ].*|sk-no-key-required|lm-studio|ollama)$", re.I)
_PLAIN_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
_ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
_ENV_SECRET = re.compile(r"(?i)(token|key|secret|passw(?:or)?d|credential)")
_ENV_NOT_SECRET = re.compile(r"(?i)(_path|_dir|_file|_home|_url|_host|_port|_timeout|_id|_name)$")


@dataclass
class Paths:
    """Every place the doctor reads. Tests build one in a temp folder; nothing else is read."""
    home: str          # only for the display and the scrubber: it becomes "~"
    codec_dir: str
    zshrc: str
    pm2_home: str
    app_logs: str
    repo: str
    health_dir: str
    messages_db: str


def default_paths(repo: Optional[str] = None) -> Paths:
    import codec_health
    home = os.path.expanduser("~")
    return Paths(home=home, codec_dir=os.path.join(home, ".codec"), zshrc=os.path.join(home, ".zshrc"),
                 pm2_home=os.environ.get("PM2_HOME") or os.path.join(home, ".pm2"),
                 app_logs=os.path.join(home, "Library", "Logs", "CODEC"),
                 repo=repo or os.path.dirname(os.path.abspath(__file__)), health_dir=codec_health.HEALTH_DIR,
                 messages_db=os.path.join(home, "Library", "Messages", "chat.db"))


def _check(cid: str, title: str, status: str, detail: str, fix: str = "", url: str = "",
           data: Optional[dict] = None) -> dict:
    out = {"id": cid, "title": title, "status": status, "detail": detail, "fix": fix}
    if url:
        out["url"] = url
    if data:
        out["data"] = data
    return out


def _run(cmd: list, timeout: float = 15) -> Optional[str]:
    """A command's standard output, whatever its exit code; None when it cannot run."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return r.stdout or ""


def _read_json(path: str) -> Any:
    try:
        if os.path.getsize(path) > MAX_FILE_BYTES:
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def _mode(path: str) -> Optional[int]:
    try:
        return stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        return None


def _shaped(text: str, shapes=DETECT_SHAPES) -> bool:
    return any(p.search(text) for p in shapes)


def _segment(key: Any) -> str:
    """A key as it may appear in the report: a setting name, or `*` for anything else (an id, a token)."""
    k = str(key)
    if _PLAIN_NAME.match(k) and sum(c.isdigit() for c in k) <= 3 and not _shaped(k, TOKEN_SHAPES):
        return k
    return "*"


def _real_value(value: Any) -> bool:
    return isinstance(value, str) and len(value.strip()) >= 8 and not _PLACEHOLDER.search(value.strip())


def key_fields(obj: Any, path: str = "") -> list:
    """The setting names (dotted, `[]` for a list, `*` for an id) that hold a key in plain text: a field
    named like a key with a real value, or any text of a token shape. Names only, never a value."""
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            here = (path + "." if path else "") + _segment(k)
            if _shaped(str(k)):
                found.append(path or "*")
            if isinstance(v, str):
                if _NOT_SECRET_NAME.search(str(k)):
                    continue  # a hash, an id, a path, a key's Keychain slot
                if (_SECRET_NAME.search(str(k)) and _real_value(v)) or _shaped(v):
                    found.append(here)
            else:
                found += key_fields(v, here)
    elif isinstance(obj, list):
        for v in obj:
            if isinstance(v, str):
                if _shaped(v):
                    found.append(path + "[]")
            else:
                found += key_fields(v, path + "[]")
    return found


def _names(fields: list) -> list:
    return sorted(set(fields))[:MAX_FIELDS]


def install_kind(repo: str) -> str:
    """'git' (a checkout), 'app' (inside the .app bundle) or 'other'."""
    if os.path.exists(os.path.join(repo, ".git")):
        return "git"
    if "/Contents/Resources/app" in os.path.realpath(repo):
        return "app"
    return "other"


def restart_hint(name: str, kind: str) -> str:
    if kind == "git":
        return f"pm2 restart {name}"
    if kind == "app":
        return f"launchctl kickstart -k gui/$(id -u)/{APP_LABEL}{name}"
    return f"restart {name}"


# ── PM2 (read only; never started) ────────────────────────────────────────────

def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _pm2_bin() -> Optional[str]:
    for c in (shutil.which("pm2"), "/opt/homebrew/bin/pm2", "/usr/local/bin/pm2"):
        if c and os.path.exists(c):
            return c
    return None


def pm2_procs(p: Paths) -> Optional[list]:
    """[{name, pid, status, started, cwd}] from `pm2 jlist`, or None when PM2 is not running here.
    `pm2 jlist` starts a PM2 daemon when none runs, so it is called only when the pid file names a live
    process. Only these five fields are kept: the list also carries each app's environment."""
    try:
        with open(os.path.join(p.pm2_home, "pm2.pid"), encoding="utf-8") as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return None
    pm2 = _pm2_bin()
    if pid <= 0 or not _alive(pid) or not pm2:
        return None
    out = _run([pm2, "jlist"], timeout=20)
    try:
        raw = json.loads(out or "")
    except ValueError:
        return None
    procs = []
    for x in raw if isinstance(raw, list) else []:
        if not isinstance(x, dict):
            continue
        env = x.get("pm2_env") if isinstance(x.get("pm2_env"), dict) else {}
        try:
            started = float(env.get("pm_uptime") or 0) / 1000.0
        except (TypeError, ValueError):
            started = 0.0
        procs.append({"name": str(x.get("name") or ""), "pid": x.get("pid"), "status": env.get("status"),
                      "started": started, "cwd": str(env.get("pm_cwd") or "")})
    return procs


def _proc(procs: Optional[list], name: str) -> Optional[dict]:
    for x in procs or []:
        if x.get("name") == name:
            return x
    return None


# ── File checks ───────────────────────────────────────────────────────────────

def _backup_files(p: Paths) -> list:
    out = []
    for folder in (p.codec_dir, os.path.join(p.codec_dir, "backups")):
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for n in names:
            if n in ("config.json", "oauth_state.json") or n.endswith((".lock", ".tmp")):
                continue
            if not ((n.startswith("config") and ".json" in n) or n.startswith("oauth_state")):
                continue
            full = os.path.join(folder, n)
            if os.path.islink(full) or not os.path.isfile(full):
                continue
            out.append(full)
    return sorted(out)


def check_config_backups(p: Paths, **_) -> dict:
    title = "Old copies of config.json and oauth_state.json"
    files = _backup_files(p)
    with_keys, readable, unreadable, fields = 0, 0, 0, []
    for path in files:
        data = _read_json(path)
        if data is None:
            unreadable += 1
            continue
        found = key_fields(data)
        if found:
            with_keys += 1
            fields += found
            if (_mode(path) or 0) & 0o044:
                readable += 1
    data = {"files": len(files), "with_keys": with_keys, "readable_by_others": readable,
            "not_json": unreadable, "fields": _names(fields)}
    if not files:
        return _check("config_backups", title, OK, "No old copies in ~/.codec or ~/.codec/backups.", data=data)
    if not with_keys:
        return _check("config_backups", title, OK, f"{len(files)} old copies; none holds a key in plain text.",
                      data=data)
    others = f"; other accounts can read {readable} of them" if readable else ""
    return _check("config_backups", title, FAIL,
                  f"{with_keys} of {len(files)} old copies hold keys or tokens in plain text "
                  f"({', '.join(data['fields'])}){others}. A copy keeps them on disk after the live settings "
                  "moved them to the Keychain or changed them.",
                  fix="List them with ls -la ~/.codec ~/.codec/backups and delete the old config and "
                      "oauth_state copies you no longer need.", data=data)


def check_mcp_servers(p: Paths, **_) -> dict:
    title = "~/.codec/mcp_servers.json"
    path = os.path.join(p.codec_dir, "mcp_servers.json")
    mode = _mode(path)
    if mode is None:
        return _check("mcp_servers", title, OK, "Not created yet.")
    data = _read_json(path)
    keys = len(key_fields(data)) if data is not None else 0
    info = {"mode": f"{mode:04o}", "keys": keys}
    fix = "chmod 600 ~/.codec/mcp_servers.json"
    if mode & 0o022:
        return _check("mcp_servers", title, FAIL, f"Other accounts can change it (mode {mode:04o}): a changed file "
                      "can point CODEC at another server.", fix=fix, data=info)
    if mode & 0o044 and keys:
        return _check("mcp_servers", title, FAIL, f"It holds {keys} connector keys and other accounts can read it "
                      f"(mode {mode:04o}).", fix=fix, data=info)
    if mode & 0o044:
        return _check("mcp_servers", title, WARN, f"Other accounts can read it (mode {mode:04o}). It holds no key "
                      "yet, but your connector keys go here.", fix=fix, data=info)
    return _check("mcp_servers", title, OK, f"Only you can read it (mode {mode:04o}); it holds {keys} keys.",
                  data=info)


def _secret_lines(text: str) -> int:
    n = 0
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        m = _ENV_LINE.match(line)
        if m and _ENV_SECRET.search(m.group(1)) and not _ENV_NOT_SECRET.search(m.group(1)):
            value = m.group(2).strip().strip("'\"")
            if len(value) >= 8 and not value.startswith(("$", "/", "~")) and not _PLACEHOLDER.search(value):
                n += 1
                continue
        if _shaped(line):
            n += 1
    return n


def check_zshrc(p: Paths, **_) -> dict:
    title = "~/.zshrc"
    mode = _mode(p.zshrc)
    if mode is None:
        return _check("zshrc", title, OK, "There is no ~/.zshrc.")
    try:
        with open(p.zshrc, encoding="utf-8", errors="replace") as f:
            lines = _secret_lines(f.read(MAX_FILE_BYTES))
    except OSError:
        lines = 0
    info = {"mode": f"{mode:04o}", "key_lines": lines}
    fix = "chmod 600 ~/.zshrc"
    if mode & 0o022:
        return _check("zshrc", title, FAIL, f"Other accounts can change it (mode {mode:04o}), and whatever it says "
                      "runs in every new Terminal window.", fix=fix, data=info)
    if mode & 0o044 and lines:
        return _check("zshrc", title, FAIL, f"{lines} lines set a key or token, and other accounts can read it "
                      f"(mode {mode:04o}).", fix=fix, data=info)
    if mode & 0o044:
        return _check("zshrc", title, OK, f"Other accounts can read it (mode {mode:04o}), but it sets no key or "
                      "token.", data=info)
    return _check("zshrc", title, OK, f"Only you can read it (mode {mode:04o}).", data=info)


def _log_counts(folder: str) -> tuple:
    try:
        names = os.listdir(folder)
    except OSError:
        return 0, 0
    files = readable = 0
    for n in names:
        full = os.path.join(folder, n)
        if os.path.islink(full) or not os.path.isfile(full):
            continue
        files += 1
        if (_mode(full) or 0) & 0o044:
            readable += 1
    return files, readable


def check_logs(p: Paths, **_) -> dict:
    title = "Service logs"
    pm2_files, pm2_read = _log_counts(os.path.join(p.pm2_home, "logs"))
    app_files, app_read = _log_counts(p.app_logs)
    data = {"pm2_files": pm2_files, "pm2_readable_by_others": pm2_read,
            "app_files": app_files, "app_readable_by_others": app_read}
    if not pm2_files and not app_files:
        return _check("logs", title, OK, "No service log files yet.", data=data)
    readable, files = pm2_read + app_read, pm2_files + app_files
    if not readable:
        return _check("logs", title, OK, f"Only you can read the {files} log files.", data=data)
    fixes = []
    if pm2_read:
        fixes.append("chmod 600 ~/.pm2/logs/*")
    if app_read:
        fixes.append("chmod 600 ~/Library/Logs/CODEC/*")
    return _check("logs", title, WARN, f"Other accounts on this Mac can read {readable} of the {files} log files; "
                  "logs hold chat text and file paths. New log files start readable again.",
                  fix=" and ".join(fixes), data=data)


# ── Network ───────────────────────────────────────────────────────────────────

def listeners() -> Optional[list]:
    """[(pid, host, port)] for every listening TCP socket of this user's processes; None without lsof."""
    lsof = shutil.which("lsof") or ("/usr/sbin/lsof" if os.path.exists("/usr/sbin/lsof") else None)
    if not lsof:
        return None
    out = _run([lsof, "-nP", "-iTCP", "-sTCP:LISTEN", "-Fpn"], timeout=15)
    if out is None:
        return None
    rows, pid = [], None
    for line in out.splitlines():
        if line.startswith("p"):
            pid = int(line[1:]) if line[1:].isdigit() else None
        elif line.startswith("n"):
            host, _, port = line[1:].rpartition(":")
            if port.isdigit():
                rows.append((pid, host, int(port)))
    return rows


def _url_port(url: Any) -> Optional[int]:
    try:
        u = urllib.parse.urlsplit(str(url))
        return u.port or (443 if u.scheme == "https" else 80 if u.scheme == "http" else None)
    except ValueError:
        return None


def codec_ports(cfg: dict) -> dict:
    ports = dict(CODEC_PORTS)
    for key, label in (("llm_base_url", "model server"), ("stt_url", "speech to text"), ("tts_url", "voice (TTS)")):
        port = _url_port(cfg.get(key)) if cfg.get(key) else None
        if port:
            ports.setdefault(port, label)
    return ports


def check_listeners(p: Paths, cfg: dict, procs: Optional[list], **_) -> dict:
    title = "Listening on every network interface"
    rows = listeners()
    if rows is None:
        return _check("listeners", title, INFO, "lsof is not available here, so this was not checked.")
    ports = codec_ports(cfg)
    names = {x.get("pid"): x.get("name") for x in procs or [] if os.path.realpath(x.get("cwd") or "/") ==
             os.path.realpath(p.repo)}
    codec, other = {}, set()
    for pid, host, port in rows:
        if host not in ALL_INTERFACES:
            continue
        label = ports.get(port) or ("Vibe preview" if port in VIBE_PORTS else None) or names.get(pid)
        if label:
            codec[port] = label
        else:
            other.add(port)
    data = {"codec": [{"service": codec[k], "port": k} for k in sorted(codec)], "other_count": len(other),
            "other_ports": sorted(other)[:MAX_FIELDS]}
    others = f" {len(other)} other ports listen on every interface (other programs)." if other else ""
    if not codec:
        return _check("listeners", title, OK, "No CODEC service listens on every interface." + others, data=data)
    names_txt = ", ".join(f"{codec[k]} ({k})" for k in sorted(codec))
    return _check("listeners", title, WARN, f"Other devices on your network can reach: {names_txt}." + others,
                  fix="Unless you use them from another device on purpose, make them listen on 127.0.0.1 only "
                      "(the dashboard: dashboard_host in ~/.codec/config.json).", data=data)


# ── Code and services ─────────────────────────────────────────────────────────

def _git_dir(repo: str) -> Optional[str]:
    dot = os.path.join(repo, ".git")
    if os.path.isdir(dot):
        return dot
    try:
        with open(dot, encoding="utf-8") as f:
            text = f.read(4096).strip()
    except OSError:
        return None
    if text.startswith("gitdir:"):
        g = text[7:].strip()
        return g if os.path.isabs(g) else os.path.normpath(os.path.join(repo, g))
    return None


def head_moved(repo: str) -> Optional[tuple]:
    """(short id, epoch seconds) of the newest HEAD reflog line: when the checked-out code last changed.
    Only those two fields are read from the line (it also names the person and their email)."""
    gd = _git_dir(repo)
    if not gd:
        return None
    try:
        with open(os.path.join(gd, "logs", "HEAD"), "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 8192))
            tail = f.read().decode("utf-8", "replace")
    except OSError:
        return None
    for line in reversed(tail.splitlines()):
        head = line.partition("\t")[0].split(" ")
        if len(head) >= 4 and re.fullmatch(r"[0-9a-f]{7,64}", head[1]) and head[-2].isdigit():
            return head[1][:7], int(head[-2])
    return None


def check_code_version(p: Paths, procs: Optional[list], kind: str, cfg: Optional[dict] = None, **_) -> dict:
    title = "Services running the newest code"
    model = str((cfg or {}).get("model_pm2_process") or "qwen3.6")  # runs its own package, not this code
    if kind != "git":
        return _check("code_version", title, NA, "Not a git checkout: the app updates as a whole.")
    moved = head_moved(p.repo)
    if moved is None:
        return _check("code_version", title, INFO, "Could not tell when the code last changed (no HEAD reflog).")
    sha, ts = moved
    if procs is None:
        return _check("code_version", title, INFO, f"PM2 is not running, so the services could not be compared "
                      f"with the code ({sha}).", data={"head": sha})
    here = os.path.realpath(p.repo)
    mine = [x for x in procs if x.get("status") == "online" and x.get("name") != model
            and os.path.realpath(x.get("cwd") or "/") == here]
    old = sorted(x["name"] for x in mine if x.get("started") and x["started"] < ts - 1)
    data = {"head": sha, "services": len(mine), "started_before": old[:MAX_FIELDS]}
    if not mine:
        return _check("code_version", title, INFO, f"No running service uses this checkout ({sha}).", data=data)
    if not old:
        return _check("code_version", title, OK, f"All {len(mine)} services started after the code last changed "
                      f"({sha}).", data=data)
    return _check("code_version", title, WARN, f"{len(old)} of {len(mine)} services started before the code last "
                  f"changed ({sha}), so they still run the older code: {', '.join(old)}.",
                  fix="pm2 restart " + " ".join(old), data=data)


def check_health_beats(p: Paths, procs: Optional[list], kind: str, now: float, **_) -> dict:
    """The heartbeat's rules (codec_heartbeat.check_health_beats) without its alerts."""
    import codec_health
    title = "Service health beats"
    beats = codec_health.read_beats(p.health_dir)
    if not beats:
        return _check("health_beats", title, INFO, "No service has written a health beat yet.")
    silent, failing, fine, stopped = [], [], [], []
    for name, beat in sorted(beats.items()):
        try:
            verdict = codec_health.verdict(beat, now)
            if procs is not None:
                x = _proc(procs, name)
                if not x or x.get("status") != "online":
                    verdict = "stopped"
                elif x.get("started") and now - x["started"] < START_GRACE_S:
                    continue
                elif verdict == "stopped":
                    verdict = codec_health.verdict(dict(beat, stopped=False, consecutive_errors=0), now)
        except Exception:
            continue
        {"silent": silent, "failing": failing, "stopped": stopped}.get(verdict, fine).append(name)
    data = {"silent": silent, "failing": failing, "ok": fine, "stopped": stopped}
    if silent:
        return _check("health_beats", title, FAIL, f"{', '.join(silent)} stopped reporting: it may be stuck."
                      + (f" Failing: {', '.join(failing)}." if failing else ""),
                      fix="; ".join(restart_hint(n, kind) for n in silent) + ", then check its log.", data=data)
    if failing:
        return _check("health_beats", title, WARN, f"{', '.join(failing)} runs but its last rounds failed.",
                      fix="Check its log (pm2 logs " + failing[0] + ")." if kind == "git" else
                      "Check its log in ~/Library/Logs/CODEC.", data=data)
    return _check("health_beats", title, OK, f"{len(fine)} services report on time"
                  + (f"; {len(stopped)} stopped." if stopped else "."), data=data)


def model_answers(base: str, timeout: float = MODEL_TIMEOUT_S) -> str:
    """'up', 'refused', 'timeout' or 'error' for GET <base>/models, without any proxy."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(base.rstrip("/") + "/models", timeout=timeout):
            return "up"
    except urllib.error.HTTPError:
        return "up"  # it answered
    except urllib.error.URLError as e:
        reason = e.reason
        if isinstance(reason, ConnectionRefusedError):
            return "refused"
        if isinstance(reason, (socket.timeout, TimeoutError)):
            return "timeout"
        return "error"
    except (socket.timeout, TimeoutError):
        return "timeout"
    except (OSError, ValueError):
        return "error"


def check_model_server(p: Paths, cfg: dict, procs: Optional[list], kind: str, now: float, **_) -> dict:
    title = "Model server"
    base = str(cfg.get("llm_base_url") or "http://localhost:8083/v1")
    try:
        u = urllib.parse.urlsplit(base)
        host, port = (u.hostname or "").lower(), u.port or (443 if u.scheme == "https" else 80)
    except ValueError:
        return _check("model_server", title, INFO, "llm_base_url in config.json is not a valid address.")
    if host not in LOCAL_HOSTS:
        return _check("model_server", title, NA, "Your model runs at another address (the cloud or another "
                      "computer), so it is not checked from here.")
    name = str(cfg.get("model_pm2_process") or "qwen3.6")
    x = _proc(procs, name)
    if x and x.get("status") == "online" and x.get("started") and now - x["started"] < START_GRACE_S:
        return _check("model_server", title, INFO, f"It is still loading (started {int(now - x['started'])} s ago).")
    state = model_answers(base)
    data = {"port": port, "answer": state}
    if state == "up":
        return _check("model_server", title, OK, f"It answers on port {port}.", data=data)
    fix = restart_hint(name, kind) + " (loading the model takes a few minutes)"
    if kind == "app":
        fix += ", or quit CODEC and open it again"
    if state == "timeout":
        return _check("model_server", title, WARN, f"No answer on port {port} within {MODEL_TIMEOUT_S} s: busy "
                      "with a long reply, or stuck.", fix="If it stays like this: " + fix, data=data)
    stopped = f" PM2 shows {name} {x.get('status')}." if x and x.get("status") != "online" else ""
    return _check("model_server", title, FAIL, f"Nothing answers on port {port}: chat, voice and agents cannot "
                  f"reply.{stopped}", fix=fix, data=data)


# ── macOS permissions (calls that never prompt) ───────────────────────────────

_FW = "/System/Library/Frameworks/{0}.framework/{0}"
_CACHE: dict = {}


def _cfunc(lib_path: str, name: str, restype, *argtypes):
    """A fresh ctypes function (its own argtypes, so nothing else in the process is changed)."""
    key = (lib_path, name)
    if key not in _CACHE:
        lib = ctypes.CDLL(lib_path)
        _CACHE[key] = ctypes.CFUNCTYPE(restype, *argtypes)((name, lib))
    return _CACHE[key]


def _bool_call(framework: str, fn: str) -> Optional[bool]:
    if sys.platform != "darwin":
        return None
    try:
        return bool(_cfunc(_FW.format(framework), fn, ctypes.c_bool)())
    except (OSError, AttributeError):
        return None


def screen_recording_ok() -> Optional[bool]:
    return _bool_call("CoreGraphics", "CGPreflightScreenCaptureAccess")


def accessibility_ok() -> Optional[bool]:
    return _bool_call("ApplicationServices", "AXIsProcessTrusted")


def full_disk_ok(messages_db: str) -> Optional[bool]:
    """Can this process open the Messages database (Full Disk Access never prompts)."""
    if sys.platform != "darwin":
        return None
    try:
        fd = os.open(messages_db, os.O_RDONLY)
    except PermissionError:
        return False
    except OSError:
        return None
    os.close(fd)
    return True


def microphone_ok() -> Optional[bool]:
    """AVCaptureDevice's authorization status for audio: a read (only asking for access would prompt).
    None when macOS has not asked yet, or off a Mac."""
    if sys.platform != "darwin":
        return None
    try:
        objc = "/usr/lib/libobjc.A.dylib"
        avf = ctypes.CDLL(_FW.format("AVFoundation"))
        media = ctypes.c_void_p.in_dll(avf, "AVMediaTypeAudio").value
        cls = _cfunc(objc, "objc_getClass", ctypes.c_void_p, ctypes.c_char_p)(b"AVCaptureDevice")
        sel = _cfunc(objc, "sel_registerName", ctypes.c_void_p, ctypes.c_char_p)(b"authorizationStatusForMediaType:")
        if not media or not cls or not sel:
            return None
        status = _cfunc(objc, "objc_msgSend", ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(
            cls, sel, media)
    except (OSError, AttributeError, ValueError):
        return None
    return {3: True, 2: False, 1: False}.get(status)  # authorized, denied, restricted; 0 = not asked yet


class _AEDesc(ctypes.Structure):
    _fields_ = [("descriptorType", ctypes.c_uint32), ("dataHandle", ctypes.c_void_p)]


def _automation_one(bundle_id: str) -> str:
    cs = _FW.format("CoreServices")
    create = _cfunc(cs, "AECreateDesc", ctypes.c_int16, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_ssize_t,
                    ctypes.POINTER(_AEDesc))
    dispose = _cfunc(cs, "AEDisposeDesc", ctypes.c_int16, ctypes.POINTER(_AEDesc))
    determine = _cfunc(cs, "AEDeterminePermissionToAutomateTarget", ctypes.c_int32, ctypes.POINTER(_AEDesc),
                       ctypes.c_uint32, ctypes.c_uint32, ctypes.c_bool)
    desc, raw = _AEDesc(), bundle_id.encode()
    if create(0x62756E64, raw, len(raw), ctypes.byref(desc)) != 0:  # typeApplicationBundleID 'bund'
        return "unknown"
    try:
        err = determine(ctypes.byref(desc), 0x2A2A2A2A, 0x2A2A2A2A, False)  # typeWildCard, askUserIfNeeded=false
    finally:
        dispose(ctypes.byref(desc))
    return {0: "allowed", -1743: "denied", -1744: "not asked", -600: "not open"}.get(err, "unknown")


def automation_states() -> dict:
    """{app name: allowed | denied | not asked | not open | unknown} for the apps CODEC drives."""
    out = {}
    for name, bid in AUTOMATION_TARGETS:
        if sys.platform != "darwin":
            out[name] = "unknown"
            continue
        try:
            out[name] = _automation_one(bid)
        except (OSError, AttributeError, ValueError):
            out[name] = "unknown"
    return out


def automation_ok(states: Optional[dict] = None) -> Optional[bool]:
    states = automation_states() if states is None else states
    if any(v == "denied" for v in states.values()):
        return False
    if any(v == "allowed" for v in states.values()):
        return True
    return None


def automation_detail(states: dict) -> str:
    parts = []
    for label, key in (("Not allowed", "denied"), ("Allowed", "allowed"), ("Not asked yet", "not asked"),
                       ("Checked when open", "not open")):
        names = [n for n, v in states.items() if v == key]
        if names:
            parts.append(f"{label}: {', '.join(names)}")
    return ". ".join(parts) + "." if parts else ""


def permission_states(p: Paths) -> dict:
    auto = automation_states()
    return {"screen": screen_recording_ok(), "accessibility": accessibility_ok(),
            "full_disk": full_disk_ok(p.messages_db), "microphone": microphone_ok(),
            "automation": automation_ok(auto), "_automation": auto}


def _on_mac() -> bool:
    return sys.platform == "darwin"


def check_permissions(p: Paths, **_) -> list:
    if not _on_mac():
        return [_check("perm_" + k, name, NA, "Not a Mac.") for k, name, _a, _w in PERMISSIONS]
    states = permission_states(p)
    out = []
    for key, name, anchor, breaks in PERMISSIONS:
        v, url = states[key], SETTINGS_URL + anchor
        extra = (" " + automation_detail(states["_automation"])) if key == "automation" else ""
        if v is True:
            out.append(_check("perm_" + key, name, OK, "Allowed." + extra, url=url))
        elif v is False:
            out.append(_check("perm_" + key, name, WARN, f"Not allowed, so {breaks} does not work." + extra,
                              fix=f"In System Settings > Privacy & Security > {name}, turn it on for CODEC.",
                              url=url))
        else:
            out.append(_check("perm_" + key, name, INFO, UNKNOWN_TEXT.get(key, "Could not be read on this Mac.")
                              + extra, url=url))
    return out


# ── The report ────────────────────────────────────────────────────────────────

CHECKS = [("config_backups", "Old copies of config.json and oauth_state.json", check_config_backups),
          ("mcp_servers", "~/.codec/mcp_servers.json", check_mcp_servers),
          ("zshrc", "~/.zshrc", check_zshrc),
          ("logs", "Service logs", check_logs),
          ("listeners", "Listening on every network interface", check_listeners),
          ("code_version", "Services running the newest code", check_code_version),
          ("health_beats", "Service health beats", check_health_beats),
          ("model_server", "Model server", check_model_server),
          ("permissions", "macOS permissions", check_permissions)]


def _codec_version(repo: str) -> str:
    try:
        with open(os.path.join(repo, "VERSION"), encoding="utf-8") as f:
            return f.read(40).strip()
    except OSError:
        return "unknown"


def scrub(obj: Any, home: str) -> Any:
    """Every string with the home folder as `~` and every token shape as `<redacted>`."""
    if isinstance(obj, dict):
        return {scrub(k, home): scrub(v, home) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub(v, home) for v in obj]
    if not isinstance(obj, str):
        return obj
    text = obj
    homes = {h for h in (home, os.path.realpath(home) if home else "") if h and len(h) > 1}
    for h in sorted(homes, key=len, reverse=True):
        text = text.replace(h, "~")
    for pat in TOKEN_SHAPES:
        text = pat.sub("<redacted>", text)
    return text


def run(paths: Optional[Paths] = None, now: Optional[float] = None) -> dict:
    """Run every check. Never raises: a check that breaks is reported as INFO with its error class."""
    p = paths or default_paths()
    now = time.time() if now is None else float(now)
    cfg = _read_json(os.path.join(p.codec_dir, "config.json"))
    cfg = cfg if isinstance(cfg, dict) else {}
    procs = pm2_procs(p)
    kind = install_kind(p.repo)
    checks = []
    for cid, title, fn in CHECKS:
        try:
            got = fn(p, cfg=cfg, procs=procs, kind=kind, now=now)
            checks += got if isinstance(got, list) else [got]
        except Exception as e:  # one broken check must not hide the others
            checks.append(_check(cid, title, INFO, f"This check could not run ({type(e).__name__})."))
    summary = {s: sum(1 for c in checks if c["status"] == s) for s in STATUSES}
    report = {"doctor": REPORT_VERSION, "generated": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
              "about": {"codec_version": _codec_version(p.repo), "install": kind, "pm2": procs is not None,
                        "macos": platform.mac_ver()[0] or "", "arch": platform.machine(),
                        "python": platform.python_version()},
              "summary": summary, "checks": checks, "permissions_note": PERMISSIONS_NOTE}
    return scrub(report, p.home)


def text_report(report: dict) -> str:
    a, s = report.get("about") or {}, report.get("summary") or {}
    lines = [f"CODEC doctor  {a.get('codec_version', '')}  {a.get('install', '')} install  "
             f"{str(report.get('generated', ''))[:16].replace('T', ' ')} UTC",
             "   ".join(f"{k} {s.get(k, 0)}" for k in STATUSES), ""]
    order = {k: i for i, k in enumerate(STATUSES)}
    for c in sorted(report.get("checks") or [], key=lambda c: order.get(c.get("status"), 9)):
        lines.append(f"{c.get('status', ''):<5} {c.get('title', '')}: {c.get('detail', '')}")
        if c.get("fix"):
            lines.append(f"      Fix: {c['fix']}")
        if c.get("url") and c.get("status") in (FAIL, WARN):
            lines.append(f"      Open: {c['url']}")
    lines += ["", str(report.get("permissions_note") or "")]
    return "\n".join(lines) + "\n"


def build_zip(report: dict, home: str = "") -> bytes:
    """The support report: a stdlib zip of the JSON and the text, scrubbed once more."""
    report = scrub(report, home or os.path.expanduser("~"))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("codec-doctor.json", json.dumps(report, indent=2, sort_keys=True) + "\n")
        z.writestr("codec-doctor.txt", text_report(report))
    return buf.getvalue()


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(prog="doctor.py", description="Read-only check of this Mac's CODEC install. "
                                 "Prints counts and setting names, never a key or a file's contents.")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    ap.add_argument("--repo", help="the CODEC checkout to compare running services with (default: this one)")
    args = ap.parse_args(argv)
    report = run(default_paths(os.path.abspath(os.path.expanduser(args.repo)) if args.repo else None))
    sys.stdout.write(json.dumps(report, indent=2) + "\n" if args.json else text_report(report))
    return 1 if report["summary"].get(FAIL) else 0


if __name__ == "__main__":
    raise SystemExit(main())
