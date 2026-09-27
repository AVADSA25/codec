"""Create image: local Qwen-Image 2.1 jobs for the chat's Image mode.

WHY (2026-09-28)
----------------
Qwen-Image 2.1 runs on the owner's Mac Studio in its own venv. This module lets
the chat use it, with up to 3 reference images, without the dashboard importing
torch or diffusers. docs/CHAT-CREATE-IMAGE-DESIGN.md

THE RULES IT KEEPS
------------------
- One job at a time. A single worker thread runs each job as its own process
  (scripts/qwen_image_run.py under config `image.python`). The process exits
  when the job ends, which frees its ~35 GB. There is no resident server.
- Memory guard before a job: it does not start with less than `min_free_gb`
  free (vm_stat free + inactive). A loaded 35B model leaves less than that.
- Memory guard during a job: ~/.codec/image_job.lock names the runner's pid,
  and codec_models.set_active refuses to load a local model while it is alive
  (busy_message()). A switch to a cloud model is still allowed.
- Files live in ~/.codec/images/YYYY-MM-DD/<job id>/ (references, results,
  job.json, runner.log) until the owner deletes them. Nothing leaves the Mac.
"""
from __future__ import annotations

import base64
import binascii
import collections
import glob
import json
import logging
import os
import re
import secrets
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from codec_jsonstore import atomic_write_json

log = logging.getLogger("codec.image")

CONFIG_PATH = os.path.expanduser("~/.codec/config.json")
IMAGES_DIR = os.path.expanduser("~/.codec/images")
LOCK_PATH = os.path.expanduser("~/.codec/image_job.lock")
RUNNER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", "qwen_image_run.py")

# Overridable in config.json under "image".
DEFAULTS: Dict[str, Any] = {
    "python": "~/models/qwen-image-venv/bin/python",
    "model_path": "~/models/Qwen-Image-2.1",
    "min_free_gb": 36,          # a run holds ~35 GB
    "steps": 20,                # 512 px at 20 steps: ~46 s per image (measured 26 Sep)
    "sizes": [512, 768, 1024],
    "max_refs": 3,
    "max_ref_mb": 10,
    "max_count": 4,
    "job_timeout_s": 1800,
}
MAX_PROMPT = 2000

_JOB_ID = re.compile(r"^[0-9a-f]{12}$")
_RESULT = re.compile(r"^\d{2}\.png$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ImageError(ValueError):
    """A request the owner can fix: shown to them as is."""


# ── Settings and availability ────────────────────────────────────────────────

def settings() -> Dict[str, Any]:
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        cfg = {}
    own = cfg.get("image") if isinstance(cfg, dict) and isinstance(cfg.get("image"), dict) else {}
    s = dict(DEFAULTS)
    s.update({k: v for k, v in own.items() if k in DEFAULTS})
    s["python"] = os.path.expanduser(str(s["python"]))
    s["model_path"] = os.path.expanduser(str(s["model_path"]))
    return s


def unavailable_reason(s: Optional[Dict[str, Any]] = None) -> str:
    """Why Create image cannot run on this Mac, or "" when it can."""
    s = s or settings()
    if not os.access(s["python"], os.X_OK):
        return "The image model's Python is not installed on this Mac."
    if not os.path.isfile(os.path.join(s["model_path"], "model_index.json")):
        return "Qwen-Image 2.1 is not downloaded on this Mac."
    if not os.path.isfile(RUNNER):
        return "The image runner is missing from this CODEC install."
    return ""


def free_memory_gb() -> float:
    """Free plus inactive memory from vm_stat, in GB. 0.0 when it cannot be
    read, so the guard refuses rather than guesses."""
    try:
        out = subprocess.run(["/usr/bin/vm_stat"], capture_output=True, text=True,
                             timeout=5).stdout
        page = int(re.search(r"page size of (\d+)", out).group(1))
        pages = sum(int(re.search(rf"{k}:\s+(\d+)", out).group(1))
                    for k in ("Pages free", "Pages inactive"))
        return pages * page / 1e9
    except Exception:
        return 0.0


# ── The lock other modules read ──────────────────────────────────────────────

def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def busy_message() -> Optional[str]:
    """Why a local model must not load right now, or None.

    A lock left by a runner that is no longer alive (CODEC restarted, a crash)
    is ignored."""
    try:
        with open(LOCK_PATH) as f:
            lock = json.load(f)
        pid = int(lock.get("pid") or 0)
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    if pid <= 0 or not _alive(pid):
        return None
    return ("An image is being generated right now and needs the memory a local "
            "model would use. Try again when it finishes, or pick MiMo.")


def _write_lock(pid: int, job_id: str) -> None:
    atomic_write_json(LOCK_PATH, {"pid": pid, "job": job_id, "since": time.time()})


def _clear_lock(pid: int) -> None:
    try:
        with open(LOCK_PATH) as f:
            if int(json.load(f).get("pid") or 0) != pid:
                return
        os.remove(LOCK_PATH)
    except (OSError, ValueError, TypeError, AttributeError):
        pass


# ── Reference images ─────────────────────────────────────────────────────────

def _image_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return ""


def _decode_ref(ref: Any, max_mb: float) -> Tuple[bytes, str]:
    if not isinstance(ref, str):
        raise ImageError("A reference image could not be read.")
    if ref.startswith("data:"):
        ref = ref.split(",", 1)[-1]
    if len(ref) > max_mb * 1024 * 1024 * 4 / 3 + 16:
        raise ImageError(f"A reference image is larger than {max_mb:g} MB.")
    try:
        data = base64.b64decode(ref, validate=True)
    except (binascii.Error, ValueError):
        raise ImageError("A reference image could not be read.") from None
    if len(data) > max_mb * 1024 * 1024:
        raise ImageError(f"A reference image is larger than {max_mb:g} MB.")
    ext = _image_type(data)
    if not ext:
        raise ImageError("Reference images must be PNG, JPEG or WebP.")
    return data, ext


# ── Jobs ─────────────────────────────────────────────────────────────────────

def _save(job: Dict[str, Any]) -> None:
    try:
        atomic_write_json(os.path.join(job["dir"], "job.json"), job)
    except OSError as e:
        log.warning("image job %s: could not save job.json: %s", job.get("id"), e)


def _find_dir(job_id: str) -> Optional[str]:
    for d in sorted(glob.glob(os.path.join(IMAGES_DIR, "*", job_id)), reverse=True):
        if _DAY.match(os.path.basename(os.path.dirname(d))) and os.path.isdir(d):
            return d
    return None


def _tail_error(job_dir: str) -> str:
    """The last meaningful line the runner wrote to stderr."""
    try:
        with open(os.path.join(job_dir, "runner.log"), "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 4000))
            lines = f.read().decode("utf-8", "replace").strip().splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        line = line.strip()
        if line and not line.startswith(("File ", "^", "~")):
            return line[:300]
    return ""


def _audit(job: Dict[str, Any]) -> None:
    outcome, level = {"done": ("ok", "info"), "failed": ("error", "warning"),
                      "refused": ("denied", "info")}.get(job["state"], ("warning", "info"))
    try:
        from codec_audit import audit
        audit(event="image_generated", source="codec-image", outcome=outcome, level=level,
              message=f"image job {job['id']} {job['state']}",
              extra={"size": job["size"], "steps": job["steps"], "count": job["count"],
                     "refs": len(job["refs"]), "results": len(job["results"]),
                     "seconds": job.get("seconds"), "state": job["state"]})
    except Exception:
        pass


def public(job: Dict[str, Any], position: int = 0) -> Dict[str, Any]:
    """What the page sees: no filesystem paths."""
    out = {k: job.get(k) for k in (
        "id", "state", "stage", "prompt", "size", "steps", "count", "seed", "rgba",
        "step", "image", "error", "created", "started", "finished", "seconds",
        "width", "height")}
    out["refs"] = len(job.get("refs") or [])
    out["results"] = [f"/api/image/file/{job['id']}/{n}" for n in job.get("results") or []]
    if position:
        out["position"] = position
    return out


class ImageQueue:
    """The single-worker queue. One instance per dashboard process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._queue: collections.deque = collections.deque()
        self._procs: Dict[str, subprocess.Popen] = {}
        self._running: Optional[str] = None
        self._wake = threading.Event()
        self._worker: Optional[threading.Thread] = None

    # -- public API ----------------------------------------------------------

    def submit(self, prompt: str, refs: Optional[List[Any]] = None, size: Any = None,
               count: Any = 1, seed: Any = None, rgba: bool = False) -> Dict[str, Any]:
        s = settings()
        reason = unavailable_reason(s)
        if reason:
            raise ImageError(reason)
        prompt = (prompt or "").strip() if isinstance(prompt, str) else ""
        if not prompt:
            raise ImageError("Describe the image you want.")
        if len(prompt) > MAX_PROMPT:
            raise ImageError(f"Keep the description under {MAX_PROMPT} characters.")
        sizes = [int(x) for x in s["sizes"]]
        try:
            size = int(size) if size not in (None, "") else sizes[0]
            count = int(count) if count not in (None, "") else 1
            seed = int(seed) if seed not in (None, "") else secrets.randbelow(2 ** 31)
        except (TypeError, ValueError):
            raise ImageError("Size, count and seed must be numbers.") from None
        if size not in sizes:
            raise ImageError(f"Size must be one of {', '.join(map(str, sizes))}.")
        if not 1 <= count <= int(s["max_count"]):
            raise ImageError(f"Create between 1 and {s['max_count']} images at a time.")
        refs = list(refs or [])
        if len(refs) > int(s["max_refs"]):
            raise ImageError(f"Attach at most {s['max_refs']} reference images.")
        decoded = [_decode_ref(r, float(s["max_ref_mb"])) for r in refs]

        job_id = secrets.token_hex(6)
        job_dir = os.path.join(IMAGES_DIR, time.strftime("%Y-%m-%d"), job_id)
        os.makedirs(job_dir, mode=0o700, exist_ok=True)
        names = []
        for i, (data, ext) in enumerate(decoded, 1):
            name = f"ref{i}.{ext}"
            fd = os.open(os.path.join(job_dir, name), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            names.append(name)
        job = {"id": job_id, "dir": job_dir, "created": time.time(), "state": "queued",
               "stage": "queued", "prompt": prompt, "size": size, "steps": int(s["steps"]),
               "count": count, "seed": seed, "rgba": bool(rgba), "refs": names,
               "results": [], "step": 0, "image": 0, "error": ""}
        _save(job)
        with self._lock:
            self._jobs[job_id] = job
            self._queue.append(job_id)
            position = len(self._queue) + (1 if self._running else 0)
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._work, name="codec-image",
                                                 daemon=True)
                self._worker.start()
        self._wake.set()
        return public(job, position)

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        if not isinstance(job_id, str) or not _JOB_ID.match(job_id):
            return None
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                pos = 0
                if job["state"] == "queued" and job_id in self._queue:
                    pos = list(self._queue).index(job_id) + 1 + (1 if self._running else 0)
                return public(job, pos)
        d = _find_dir(job_id)
        if d is None:
            return None
        try:
            with open(os.path.join(d, "job.json")) as f:
                job = json.load(f)
        except (OSError, ValueError):
            return None
        if job.get("state") in ("queued", "running"):
            # Not in this process: CODEC restarted while it waited or ran.
            job.update(state="failed", error="Stopped when CODEC restarted.")
        return public(job)

    def cancel(self, job_id: str) -> Optional[Dict[str, Any]]:
        proc = None
        with self._lock:
            job = self._jobs.get(job_id) if isinstance(job_id, str) else None
            if job is None:
                return None
            if job["state"] == "queued":
                try:
                    self._queue.remove(job_id)
                except ValueError:
                    pass
                self._finish(job, "cancelled")
                return public(job)
            if job["state"] == "running":
                job["state"] = "cancelled"          # the worker finishes it
                proc = self._procs.get(job_id)
        if proc is not None:
            _stop(proc)
        return public(job)

    def status(self) -> Dict[str, Any]:
        s = settings()
        reason = unavailable_reason(s)
        with self._lock:
            queued = len(self._queue)
            running = self._running
        return {"available": not reason, "reason": reason, "queued": queued,
                "running": running, "sizes": [int(x) for x in s["sizes"]],
                "max_refs": int(s["max_refs"]), "max_count": int(s["max_count"]),
                "min_free_gb": s["min_free_gb"]}

    # -- worker --------------------------------------------------------------

    def _work(self) -> None:
        while True:
            with self._lock:
                job_id = self._queue.popleft() if self._queue else None
                job = self._jobs.get(job_id) if job_id else None
                if job is not None:
                    self._running = job_id
            if job_id is None:
                self._wake.wait()
                self._wake.clear()
                continue
            try:
                if job is not None and job["state"] == "queued":
                    self._run(job)
            except Exception as e:               # never let the worker die
                log.exception("image job %s crashed", job_id)
                if job is not None and job["state"] not in ("done", "failed", "cancelled", "refused"):
                    self._finish(job, "failed", f"Internal error: {e}")
            finally:
                with self._lock:
                    self._running = None

    def _run(self, job: Dict[str, Any]) -> None:
        s = settings()
        free = free_memory_gb()
        with self._lock:
            if job["state"] != "queued":        # cancelled after it left the queue
                return
            if free < float(s["min_free_gb"]):
                self._finish(job, "refused", (
                    f"Not enough free memory: {free:.0f} GB free, and the image model needs "
                    f"about {s['min_free_gb']} GB. A large local model is probably loaded. "
                    f"Switch chat to MiMo or pick a smaller local model, then try again."))
                return
            job.update(state="running", stage="loading", started=time.time())
        _save(job)
        spec = {"model_path": s["model_path"], "out_dir": job["dir"], "prompt": job["prompt"],
                "refs": [os.path.join(job["dir"], n) for n in job["refs"]],
                "size": job["size"], "steps": job["steps"], "count": job["count"],
                "seed": job["seed"], "rgba": job["rgba"]}
        env = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                   PYTHONUNBUFFERED="1")
        with open(os.path.join(job["dir"], "runner.log"), "ab") as err:
            try:
                proc = subprocess.Popen([s["python"], RUNNER], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=err, env=env,
                                        cwd=job["dir"])
            except OSError as e:
                self._finish(job, "failed", f"Could not start the image model: {e}")
                return
            with self._lock:
                self._procs[job["id"]] = proc
                cancelled = job["state"] == "cancelled"
            _write_lock(proc.pid, job["id"])
            timed_out = threading.Event()

            def _timeout() -> None:
                timed_out.set()
                _stop(proc)

            timer = threading.Timer(float(s["job_timeout_s"]), _timeout)
            timer.daemon = True
            timer.start()
            try:
                if cancelled:
                    _stop(proc)
                else:
                    try:
                        proc.stdin.write(json.dumps(spec).encode())
                        proc.stdin.close()
                    except OSError:
                        pass
                    for raw in proc.stdout:
                        try:
                            event = json.loads(raw)
                        except ValueError:
                            continue
                        if isinstance(event, dict):
                            self._apply(job, event)
                rc = proc.wait()
            finally:
                timer.cancel()
                _clear_lock(proc.pid)
                with self._lock:
                    self._procs.pop(job["id"], None)
        if job["state"] == "cancelled":
            self._finish(job, "cancelled")
        elif timed_out.is_set():
            self._finish(job, "failed", f"The job took longer than {s['job_timeout_s']} s and was stopped.")
        elif rc == 0 and len(job["results"]) == job["count"]:
            self._finish(job, "done")
        else:
            self._finish(job, "failed", _tail_error(job["dir"]) or f"The image model stopped (exit code {rc}).")

    def _apply(self, job: Dict[str, Any], event: Dict[str, Any]) -> None:
        stage = event.get("stage")
        if stage == "generating":
            try:
                job.update(stage="generating", image=int(event.get("image") or 1),
                           step=int(event.get("step") or 0),
                           steps=int(event.get("steps") or job["steps"]))
            except (TypeError, ValueError):
                pass
            return
        if stage == "loaded":
            job["stage"] = "generating"
            job["load_seconds"] = event.get("seconds")
        elif stage == "image":
            name = str(event.get("file") or "")
            if _RESULT.match(name) and os.path.isfile(os.path.join(job["dir"], name)):
                job["results"].append(name)
                job["width"], job["height"] = event.get("width"), event.get("height")
        else:
            return
        _save(job)

    def _finish(self, job: Dict[str, Any], state: str, error: str = "") -> None:
        now = time.time()
        job.update(state=state, stage=state, error=error, finished=now)
        if job.get("started"):
            job["seconds"] = round(now - job["started"], 1)
        _save(job)
        _audit(job)


def _stop(proc: subprocess.Popen) -> None:
    """Stop our own runner process: terminate, then kill if it hangs."""
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass
    except OSError:
        pass


_Q = ImageQueue()


def submit(*a: Any, **kw: Any) -> Dict[str, Any]:
    return _Q.submit(*a, **kw)


def get(job_id: str) -> Optional[Dict[str, Any]]:
    return _Q.get(job_id)


def cancel(job_id: str) -> Optional[Dict[str, Any]]:
    return _Q.cancel(job_id)


def status() -> Dict[str, Any]:
    return _Q.status()


def file_path(job_id: str, name: str) -> Optional[str]:
    """Absolute path of a result image, only inside that job's folder."""
    if not (isinstance(job_id, str) and _JOB_ID.match(job_id)
            and isinstance(name, str) and _RESULT.match(name)):
        return None
    d = _find_dir(job_id)
    if d is None:
        return None
    base = os.path.realpath(d)
    p = os.path.realpath(os.path.join(base, name))
    if not p.startswith(base + os.sep) or not os.path.isfile(p):
        return None
    return p
