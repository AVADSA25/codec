"""Vibe: show or run a project on this Mac (docs/VIBE-PROJECTS-DESIGN.md, step 2b).

Two things can be live, one at a time:

  serve  a read-only file server on 127.0.0.1:<port> for the project's folder.
         Nothing is run. Pages, images, scripts and fetched files work as on a real site.
  run    a command the owner typed or confirmed, started in the project folder with
         PORT and HOST=127.0.0.1 set (node, python, ...). Its output is kept for the page.

The command that runs is the string in the owner's request. A file the AI wrote (vibe.json,
package.json) only fills in the text of the dialog, so the AI cannot start anything by itself.

Both stop on request, when something else is started, or after IDLE_SECONDS without the page
asking about them. A run lives in its own process group and is stopped as a group. A process
left behind by a dashboard restart is recorded in <project>/.vibe/run.json and stopped the
next time anything is started.

Callers see VibeError (status + message) for anything the owner can fix.
"""
from __future__ import annotations

import atexit
import collections
import http.server
import mimetypes
import os
import posixpath
import re
import resource
import signal
import socket
import subprocess
import threading
import time
import urllib.parse
from pathlib import Path

import codec_vibe_projects as vp
from codec_vibe_projects import VibeError

PORT_RANGE = range(5300, 5400)
IDLE_SECONDS = 30 * 60
WATCH_INTERVAL = 5.0
STOP_GRACE_SECONDS = 3.0
MAX_COMMAND_CHARS = 500
LOG_LINES = 400
LOG_LINE_CHARS = 500
SERVE_MAX_BYTES = 20 * 1024 * 1024
RUN_FILE = "run.json"

_LOCK = threading.RLock()
_TARGET = None          # the one thing that is live: a _Serve or a _Run
_WATCHER = None
_WAKE = threading.Event()   # set when something starts, so the watcher re-reads its interval at once
_REAPED = False

_EXTRA_PATH = ("/opt/homebrew/bin", "/usr/local/bin")


def _audit(event: str, message: str, extra: dict) -> None:
    """Ids, ports, kinds and counts only. Never the command, a path or any output."""
    try:
        from codec_audit import log_event
        log_event(event, "codec-vibe", message, extra=extra)
    except Exception:
        pass


# ── ports ────────────────────────────────────────────────────────────────

def _free_port() -> int:
    for p in PORT_RANGE:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", p))
            return p
        except OSError:
            continue
        finally:
            s.close()
    raise VibeError(503, f"No free port between {PORT_RANGE.start} and {PORT_RANGE.stop - 1}")


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False


def _check_port(port) -> int | None:
    if port in (None, "", 0):
        return None
    try:
        n = int(port)
    except (TypeError, ValueError):
        raise VibeError(400, "The port must be a number")
    if not 1024 <= n <= 65535:
        raise VibeError(400, "The port must be between 1024 and 65535")
    return n


# ── the read-only file server ────────────────────────────────────────────

_TEXTY = ("application/javascript", "application/json", "image/svg+xml", "application/xml")


class _Serve:
    kind = "serve"

    def __init__(self, project: str, root: Path, port: int):
        self.project, self.port = project, port
        self.started = self.last_seen = time.time()
        self.command_chars = 0
        self.real_root = os.path.realpath(root)
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), _handler_for(self))
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
        self.thread.start()

    def touch(self) -> None:
        self.last_seen = time.time()

    def describe(self, since: int = 0) -> dict:
        return {"state": "ready", "kind": "serve", "port": self.port, "url": f"http://127.0.0.1:{self.port}/",
                "lines": [], "next": 0, "exit_code": None, "seconds": int(time.time() - self.started)}

    def stop(self) -> int | None:
        try:
            self.httpd.shutdown()
        finally:
            self.httpd.server_close()
        return None


def _handler_for(target: _Serve):
    real_root = target.real_root

    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "CodecVibePreview"
        sys_version = ""
        protocol_version = "HTTP/1.0"

        def log_message(self, *args):       # no console noise
            pass

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").strip().lower()
            name, _, port = host.partition(":")
            return name in ("127.0.0.1", "localhost") and port == str(target.port)

        def _origin_ok(self) -> bool:
            """A page on another site must not read the project through the browser: only the
            sandboxed frame itself (Origin: null) and pages on this machine may ask with an Origin."""
            origin = (self.headers.get("Origin") or "").strip().lower()
            if not origin or origin == "null":
                return True
            parts = urllib.parse.urlsplit(origin)
            return parts.scheme in ("http", "https") and parts.hostname in ("127.0.0.1", "localhost")

        def _common(self) -> None:
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            # Even opened on its own, the page stays sandboxed (no cookies, no storage).
            self.send_header("Content-Security-Policy", "sandbox allow-scripts allow-forms allow-modals allow-popups")
            # Read-only project files: lets the sandboxed page fetch its own files (a request from
            # a foreign site never gets this far: see _origin_ok).
            self.send_header("Access-Control-Allow-Origin", "*")

        def _plain(self, code: int, text: str = "", extra: dict | None = None) -> None:
            body = (text or http.server.BaseHTTPRequestHandler.responses.get(code, ("",))[0]).encode()
            self.send_response(code)
            self._common()
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _serve(self) -> None:
            if not self._host_ok() or not self._origin_ok():
                return self._plain(403, "Forbidden")
            target.touch()
            try:
                path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path, errors="strict")
            except (UnicodeDecodeError, ValueError):
                return self._plain(400)
            if "\x00" in path or "\\" in path:
                return self._plain(400)
            segs = [s for s in posixpath.normpath("/" + path.lstrip("/")).split("/") if s]
            if any(s.startswith(".") for s in segs):
                return self._plain(404)
            real = os.path.realpath(os.path.join(real_root, *segs))
            if real != real_root and not real.startswith(real_root + os.sep):
                return self._plain(404)
            rel = os.path.relpath(real, real_root)
            if any(s.startswith(".") for s in rel.split(os.sep) if s != "."):
                return self._plain(404)       # a link that points into .vibe or another dot folder
            if os.path.isdir(real):
                if not path.endswith("/") and segs:      # built from the cleaned segments, never from the raw path
                    return self._plain(301, "", {"Location": "/" + "/".join(urllib.parse.quote(x) for x in segs) + "/"})
                real = os.path.join(real, "index.html")
            if not os.path.isfile(real):
                return self._plain(404)
            try:
                size = os.path.getsize(real)
                if size > SERVE_MAX_BYTES:
                    return self._plain(413)
                ctype = mimetypes.guess_type(real)[0] or "application/octet-stream"
                if (ctype.startswith("text/") or ctype in _TEXTY) and "charset" not in ctype:
                    ctype += "; charset=utf-8"
                f = open(real, "rb")
            except OSError:
                return self._plain(404)
            with f:
                self.send_response(200)
                self._common()
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(size))
                self.end_headers()
                if self.command != "HEAD":
                    while True:
                        chunk = f.read(65536)
                        if not chunk:
                            break
                        self.wfile.write(chunk)

        def do_GET(self):
            self._serve()

        def do_HEAD(self):
            self._serve()

        def _refuse(self):
            self._plain(405, "Read only", {"Allow": "GET, HEAD"})

        do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _refuse

    return Handler


# ── a command ────────────────────────────────────────────────────────────

def _check_command(command) -> str:
    if not isinstance(command, str):
        raise VibeError(400, "Send the command as text")
    cmd = command.strip()
    if not cmd:
        raise VibeError(400, "The command is empty")
    if len(cmd) > MAX_COMMAND_CHARS:
        raise VibeError(400, f"The command is longer than {MAX_COMMAND_CHARS} characters")
    if "\n" in cmd or "\r" in cmd or "\x00" in cmd:
        raise VibeError(400, "The command must be one line")
    try:
        from codec_config import is_dangerous
    except Exception:
        raise VibeError(500, "The safety check is not available, so nothing was started")
    if is_dangerous(cmd):
        raise VibeError(403, "Blocked: that command looks dangerous")
    return cmd


def _run_env(port: int) -> dict:
    try:
        from routes.vibe_exec import _hardened_run_env
        env = _hardened_run_env()
    except Exception:       # the same rule, if the route module cannot be loaded
        pat = re.compile(r"(?i)(secret|password|passwd|token|credential|api[_-]?key|access[_-]?key|"
                         r"private[_-]?key|bearer|session[_-]?token|auth[_-]?token|_key$|^key$)")
        env = {k: v for k, v in os.environ.items() if k == "SSH_AUTH_SOCK" or not pat.search(k)}
    path = [p for p in env.get("PATH", "").split(os.pathsep) if p]
    for extra in _EXTRA_PATH:
        if extra not in path:
            path.append(extra)
    env["PATH"] = os.pathsep.join(path)
    env.update(PORT=str(port), HOST="127.0.0.1", BROWSER="none")
    return env


def _limits() -> None:          # runs in the child between fork and exec: no imports, no locks
    try:
        resource.setrlimit(resource.RLIMIT_FSIZE, (512 * 1024 ** 2, 512 * 1024 ** 2))
    except (ValueError, OSError):
        pass


def _ps_start(pid: int) -> str:
    try:
        out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, timeout=5)
        return out.stdout.strip()
    except Exception:
        return ""


class _Run:
    kind = "run"

    def __init__(self, project: str, pdir: Path, command: str, listen_port: int, proc: subprocess.Popen):
        self.project, self.pdir, self.port, self.proc = project, pdir, listen_port, proc
        self.command_chars = len(command)
        self.started = self.last_seen = time.time()
        self.lines: collections.deque = collections.deque(maxlen=LOG_LINES)
        self.seq = 0
        self.lines_lock = threading.Lock()
        self.exit_code: int | None = None
        self.exited_at: float | None = None
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self) -> None:
        try:
            # A bounded read, so a command that prints without ever ending a line cannot fill memory.
            for raw in iter(lambda: self.proc.stdout.readline(LOG_LINE_CHARS * 4), b""):
                text = raw.decode("utf-8", errors="replace").rstrip("\r\n")[:LOG_LINE_CHARS]
                with self.lines_lock:
                    self.seq += 1
                    self.lines.append((self.seq, text))
        except (OSError, ValueError):
            pass
        finally:
            self.exit_code = self.proc.wait()
            self.exited_at = time.time()

    def touch(self) -> None:
        self.last_seen = time.time()

    def describe(self, since: int = 0) -> dict:
        alive = self.proc.poll() is None
        state = ("ready" if _port_open(self.port) else "starting") if alive else "exited"
        with self.lines_lock:
            lines = [t for s, t in self.lines if s > since]
            nxt = self.seq
        return {"state": state, "kind": "run", "port": self.port, "url": f"http://127.0.0.1:{self.port}/",
                "lines": lines, "next": nxt, "exit_code": None if alive else self.proc.returncode,
                "seconds": int(time.time() - self.started)}

    def _signal(self, sig: int) -> None:
        try:
            os.killpg(self.proc.pid, sig)       # its own process group (start_new_session)
        except (ProcessLookupError, PermissionError):
            pass

    def stop(self) -> int | None:
        if self.proc.poll() is None:
            self._signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=STOP_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                self._signal(signal.SIGKILL)
                try:
                    self.proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
        elif self.exited_at is not None and time.time() - self.exited_at <= 10:
            self._signal(signal.SIGKILL)         # children the command left behind, if it only just ended
            # (later, the number could belong to another process, so nothing is signalled)
        try:
            self.proc.stdout.close()
        except Exception:
            pass
        self.reader.join(timeout=1)
        return self.proc.returncode


# ── run.json: remember a process so a restart can find it ────────────────

def _run_file(pdir: Path) -> Path:
    return pdir / vp.META_DIR / RUN_FILE


def _write_run_file(pdir: Path, run: _Run) -> None:
    import json
    try:
        _run_file(pdir).write_text(json.dumps({"pid": run.proc.pid, "lstart": _ps_start(run.proc.pid),
                                               "port": run.port, "started": run.started}), encoding="utf-8")
    except OSError:
        pass


def _clear_run_file(pdir: Path) -> None:
    try:
        _run_file(pdir).unlink()
    except OSError:
        pass


def _reap_stale_once() -> None:
    """Stop a command that an earlier dashboard process started and did not get to stop."""
    global _REAPED
    if _REAPED:
        return
    _REAPED = True
    import json
    root = vp.vibe_root()
    try:
        names = os.listdir(root)
    except OSError:
        return
    for name in names:
        pdir = root / name
        f = _run_file(pdir)
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
            pid = int(rec["pid"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        try:
            os.kill(pid, 0)
            alive = True
        except ProcessLookupError:
            alive = False
        except PermissionError:
            alive = False           # not ours
        if alive and rec.get("lstart") and _ps_start(pid) == rec["lstart"]:
            try:
                os.killpg(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            _audit("vibe_project_stop", "Vibe stopped a leftover process", {"project": name, "kind": "run", "reason": "reaped"})
        _clear_run_file(pdir)


# ── start, status, stop ─────────────────────────────────────────────────

def _stop_locked(reason: str) -> bool:
    global _TARGET
    t = _TARGET
    if t is None:
        return False
    _TARGET = None
    code = None
    try:
        code = t.stop()
    finally:
        if t.kind == "run":
            _clear_run_file(t.pdir)
        _audit("vibe_project_stop", "Vibe stopped what was running", {
            "project": t.project, "kind": t.kind, "reason": reason, "seconds": int(time.time() - t.started),
            "exit_code": code})
    return True


def _ensure_watcher() -> None:
    global _WATCHER
    _WAKE.set()
    if _WATCHER is not None and _WATCHER.is_alive():
        return

    def watch() -> None:
        while True:
            _WAKE.wait(WATCH_INTERVAL)
            _WAKE.clear()
            with _LOCK:
                t = _TARGET
                if t is not None and time.time() - t.last_seen > IDLE_SECONDS:
                    _stop_locked("idle")

    _WATCHER = threading.Thread(target=watch, daemon=True, name="vibe-runner-watch")
    _WATCHER.start()


def start_serve(pid: str) -> dict:
    pdir = vp.project_dir(pid)
    global _TARGET
    with _LOCK:
        _reap_stale_once()
        _stop_locked("replaced")
        last = None
        for _ in range(5):          # a port can be taken between the check and the bind
            port = _free_port()
            try:
                _TARGET = _Serve(pid, pdir, port)
                break
            except OSError as e:
                last = e
        else:
            raise VibeError(503, f"Could not open a port: {last}")
        _ensure_watcher()
    _audit("vibe_project_run", "Vibe showed a project on localhost", {"project": pid, "kind": "serve", "port": port, "command_chars": 0})
    return status(pid)


def start_run(pid: str, command, port=None) -> dict:
    pdir = vp.project_dir(pid)
    cmd = _check_command(command)
    listen = _check_port(port)
    global _TARGET
    with _LOCK:
        _reap_stale_once()
        _stop_locked("replaced")
        env_port = listen or _free_port()
        try:
            proc = subprocess.Popen(["/bin/bash", "-c", cmd], cwd=str(pdir), env=_run_env(env_port),
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    start_new_session=True, close_fds=True, preexec_fn=_limits)
        except OSError as e:
            raise VibeError(500, f"Could not start the command: {e.strerror or e}")
        run = _Run(pid, pdir, cmd, env_port, proc)
        _TARGET = run
        _write_run_file(pdir, run)
        _ensure_watcher()
    _audit("vibe_project_run", "Vibe started a command", {"project": pid, "kind": "run", "port": env_port, "command_chars": len(cmd)})
    return status(pid)


def status(pid: str, since: int = 0) -> dict:
    vp.project_dir(pid)
    try:
        since = max(0, int(since))
    except (TypeError, ValueError):
        since = 0
    with _LOCK:
        t = _TARGET
        if t is None or t.project != pid:
            return {"state": "none", "kind": None, "lines": [], "next": 0}
        t.touch()
        return t.describe(since)


def stop(pid: str) -> dict:
    vp.project_dir(pid)
    with _LOCK:
        if _TARGET is None or _TARGET.project != pid:
            return {"stopped": False}
        return {"stopped": _stop_locked("user")}


def stop_all(reason: str = "exit") -> None:
    with _LOCK:
        _stop_locked(reason)


atexit.register(stop_all)
