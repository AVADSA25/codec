"""Which supervisor runs CODEC's services on this Mac, and launchd's list in PM2's shape (P-032).

The Studio (a checkout) runs the fleet under PM2; a buyer's Mac (the .app) runs it as launchd
LaunchAgents `ai.avadigital.codec.<name>` (packaging/macos/launchd). `kind()` says which one, and
`launchd_jlist()` gives the LaunchAgents as `pm2 jlist` entries, so the heartbeat, Connections and
Cortex read either with the code they already have. Read only: `launchctl list` and `launchctl
print` only, nothing is loaded, started or stopped. Stdlib only. docs/LAUNCHD-STATUS-DESIGN.md
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

LABEL_PREFIX = "ai.avadigital.codec."
LAUNCHCTL = "/bin/launchctl"
PS = "/bin/ps"
AGENTS_DIR = os.path.expanduser("~/Library/LaunchAgents")
LOG_DIR = os.path.expanduser("~/Library/Logs/CODEC")
REPO = os.path.dirname(os.path.abspath(__file__))
SUPERVISORS = ("pm2", "launchd")
_TIMEOUT_S = 10
_PRINT_WORKERS = 8

try:
    from codec_config import CONFIG_PATH
except Exception:  # pragma: no cover - codec_config always imports in the repo and the .app
    CONFIG_PATH = os.path.expanduser("~/.codec/config.json")


def _config() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return {}
    return cfg if isinstance(cfg, dict) else {}


def kind(cfg: dict | None = None, repo: str | None = None) -> str:
    """'pm2' (the default) or 'launchd'. config.json `supervisor` ("pm2" / "launchd") wins; else a
    CODEC that runs from inside the .app bundle is launchd's (codec_doctor.install_kind's rule)."""
    cfg = _config() if cfg is None else cfg
    choice = cfg.get("supervisor") if isinstance(cfg, dict) else None
    if choice in SUPERVISORS:
        return choice
    try:
        import codec_doctor
        return "launchd" if codec_doctor.install_kind(repo or REPO) == "app" else "pm2"
    except Exception:
        return "pm2"


def logs_hint(name: str, supervisor: str | None = None) -> str:
    """Where to read a service's output, for an alert text."""
    if (supervisor or kind()) == "launchd":
        return f"its log ~/Library/Logs/CODEC/{name}.err"
    return f"pm2 logs {name}"


def _run(args: list) -> str | None:
    """stdout of a read-only command, or None (missing tool, error exit, timeout)."""
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return r.stdout if r.returncode == 0 else None


def parse_print(text: str) -> dict:
    """The one-tab-deep `key = value` lines of `launchctl print` (nested blocks are skipped)."""
    out: dict = {}
    for line in (text or "").splitlines():
        m = re.match(r"^\t([a-z][a-z ]*?) = (.*)$", line)
        if m and m.group(1) not in out:
            out[m.group(1)] = m.group(2).strip()
    return out


def _int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def status_of(info: dict | None) -> str:
    """PM2's status word for one `launchctl print` reading (None: a plist that is not loaded)."""
    if not info:
        return "stopped"
    state = info.get("state", "")
    if state == "running" and (_int(info.get("pid")) or 0) > 0:
        return "online"
    if state == "spawn scheduled":
        return "waiting restart"
    return "errored" if _keepalive(info) else "stopped"


def _keepalive(info: dict) -> bool:
    return "keepalive" in [p.strip() for p in info.get("properties", "").split("|")]


def _exit_code(info: dict) -> int | None:
    code = _int(info.get("last exit code"))
    if code is not None:
        return code
    m = re.search(r"(\d+)\s*$", info.get("last terminating signal", ""))
    return -int(m.group(1)) if m else None


def parse_etime(text: str) -> int | None:
    """Seconds from ps's elapsed time `[[dd-]hh:]mm:ss`, or None."""
    m = re.fullmatch(r"(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+)", (text or "").strip())
    if not m:
        return None
    d, h, mi, s = (int(g or 0) for g in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


def _labels(listing: str, agents_dir: str) -> list:
    labels = set()
    for line in listing.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[2].startswith(LABEL_PREFIX):
            labels.add(parts[2].strip())
    for path in glob.glob(os.path.join(glob.escape(agents_dir), LABEL_PREFIX + "*.plist")):
        labels.add(os.path.basename(path)[:-len(".plist")])
    return sorted(labels)


def _started_ms(pids: list, run, now: float) -> dict:
    """{pid: start time in epoch ms} for running pids, from one ps call."""
    if not pids:
        return {}
    out = run([PS, "-o", "pid=,etime=", "-p", ",".join(str(p) for p in pids)]) or ""
    started = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and _int(parts[0]) in pids:
            secs = parse_etime(parts[1])
            if secs is not None:
                started[_int(parts[0])] = int((now - secs) * 1000)
    return started


def launchd_jlist(*, run=None, uid: int | None = None, agents_dir: str | None = None,
                  now: float | None = None) -> list:
    """CODEC's LaunchAgents as `pm2 jlist` entries: name, pid and pm2_env {status, autorestart,
    restart_time, pm_uptime, exit_code}. [] when `launchctl list` cannot be read. Never raises."""
    run = run or _run
    uid = os.getuid() if uid is None else uid
    now = time.time() if now is None else now
    try:
        listing = run([LAUNCHCTL, "list"])
        if listing is None:
            return []
        labels = _labels(listing, agents_dir or AGENTS_DIR)
        if not labels:
            return []
        with ThreadPoolExecutor(max_workers=min(_PRINT_WORKERS, len(labels))) as pool:
            texts = list(pool.map(lambda label: run([LAUNCHCTL, "print", f"gui/{uid}/{label}"]), labels))
        infos = [parse_print(t) if t else None for t in texts]
        statuses = [status_of(i) for i in infos]
        pids = [_int(i.get("pid")) for i, s in zip(infos, statuses) if s == "online"]
        started = _started_ms(pids, run, now)
        procs = []
        for label, info, status in zip(labels, infos, statuses):
            info = info or {}
            pid = _int(info.get("pid")) if status == "online" else 0
            env = {"status": status, "autorestart": _keepalive(info),
                   "restart_time": max((_int(info.get("runs")) or 0) - 1, 0),
                   "pm_uptime": started.get(pid, 0) if pid else 0}
            code = _exit_code(info)
            if code is not None:
                env["exit_code"] = code
            procs.append({"name": label[len(LABEL_PREFIX):], "pid": pid or 0, "pm2_env": env})
        return procs
    except Exception:
        return []
