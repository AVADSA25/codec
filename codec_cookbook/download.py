"""Cookbook model downloads — detached Hugging Face jobs + status polling.

Each download runs as a DETACHED subprocess (start_new_session) so it survives
across skill calls (every skill `run()` is a fresh, short-lived call). The child
writes its own per-repo status file with stdlib only (no dependency on this repo
being importable in the child), and status() reconciles a dead pid to
'interrupted'. Downloads land in the standard HF cache — they never touch the
running stack.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import time

log = logging.getLogger("codec_cookbook.download")

DL_DIR = os.path.expanduser("~/.codec/cookbook/downloads")
# Where snapshot_download puts files (the default Hugging Face cache, the folder
# codec_models.discover_local reads). bytes_on_disk() measures progress there.
HF_CACHE = os.path.expanduser("~/.cache/huggingface/hub")

# Self-contained child: writes {repo,state,pid,...} to argv[2] via tmp+replace.
# argv[3], when given, is the revision (commit) to download.
_CHILD = r"""
import sys, json, os, time, tempfile
repo, sf = sys.argv[1], sys.argv[2]
rev = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else None
def w(state, **kw):
    d = {"repo": repo, "state": state, "pid": os.getpid(), "updated_at": time.time()}
    d.update(kw)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(sf), suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(d, f)
    os.replace(tmp, sf)
w("running", started_at=time.time())
try:
    from huggingface_hub import snapshot_download
    p = snapshot_download(repo, revision=rev)
    w("done", path=p, finished_at=time.time())
except Exception as e:
    w("error", error=str(e)[:500], finished_at=time.time())
"""


def _slug(repo: str) -> str:
    return repo.replace("/", "__").replace(":", "_")


def _status_file(repo: str) -> str:
    return os.path.join(DL_DIR, _slug(repo) + ".json")


def _read_status(repo: str) -> dict | None:
    try:
        with open(_status_file(repo), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def _write_initial(repo: str, pid: int | None = None) -> None:
    os.makedirs(DL_DIR, exist_ok=True)
    sf = _status_file(repo)
    data = {"repo": repo, "state": "starting", "pid": pid, "updated_at": time.time()}
    fd, tmp = tempfile.mkstemp(dir=DL_DIR, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.replace(tmp, sf)


def start(repo: str, revision: str | None = None) -> dict:
    """Begin (or report) a download of `repo` (at `revision` when given, a
    commit pin). Idempotent: if a job is already running, returns its current
    status instead of spawning a duplicate."""
    if not repo:
        return {"state": "error", "error": "no repo given"}
    cur = status(repo)
    if cur.get("state") in ("starting", "running"):
        return cur  # already in flight
    os.makedirs(DL_DIR, exist_ok=True)
    _write_initial(repo)
    try:
        argv = [sys.executable, "-c", _CHILD, repo, _status_file(repo)]
        if revision:
            argv.append(str(revision))
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,   # detach: survives the parent skill call
        )
    except Exception as e:
        return {"repo": repo, "state": "error", "error": f"spawn failed: {e}"}
    _write_initial(repo, pid=proc.pid)
    return {"repo": repo, "state": "starting", "pid": proc.pid}


def status(repo: str) -> dict:
    """Current download state: not_started | starting | running | done | error |
    interrupted. Reconciles a dead pid (child crashed/killed) to 'interrupted'."""
    rec = _read_status(repo)
    if rec is None:
        return {"repo": repo, "state": "not_started"}
    state = rec.get("state")
    if state in ("starting", "running"):
        pid = rec.get("pid")
        if pid is not None and not _pid_alive(pid):
            return {**rec, "state": "interrupted",
                    "detail": "download process is no longer running"}
    return rec


def list_downloads() -> list[dict]:
    """All known download jobs (one per status file in DL_DIR)."""
    out = []
    try:
        for fn in os.listdir(DL_DIR):
            if fn.endswith(".json"):
                try:
                    with open(os.path.join(DL_DIR, fn), encoding="utf-8") as f:
                        rec = json.load(f)
                    out.append(status(rec.get("repo", "")))
                except (OSError, json.JSONDecodeError):
                    continue
    except OSError:
        pass
    return out


def bytes_on_disk(repo: str) -> int:
    """Bytes of `repo` in the Hugging Face cache so far: every file in its
    blobs/ folder, partial `.incomplete` files included, links not followed.
    0 when nothing is there yet. Never raises."""
    blobs = os.path.join(HF_CACHE, "models--" + repo.replace("/", "--"), "blobs")
    total = 0
    try:
        names = os.listdir(blobs)
    except OSError:
        return 0
    for fn in names:
        path = os.path.join(blobs, fn)
        try:
            if os.path.isfile(path) and not os.path.islink(path):
                total += os.path.getsize(path)
        except OSError:
            continue
    return total
