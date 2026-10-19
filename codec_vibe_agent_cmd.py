"""Vibe coding agent, step 3c: run one approved command inside a macOS sandbox (docs/VIBE-AGENT-DESIGN.md, section 7).

The agent never starts a command by itself: the owner approves the exact text first (codec_vibe_agent.py).
This module only runs it, and runs it with the least it needs:

  reads    the system, the usual toolchains in the home folder, and this project; nothing else in the
           home folder, no other project, no keys, no CODEC state, not the project's own .vibe folder
  writes   this project (never .vibe) and one temporary folder made for the command
  network  none; or, when the owner ticked it, web ports and name lookups only, never this Mac
  apps     no Apple Events, no clipboard, no keychain service, no screen, no camera, no microphone
  limits   a time limit (the whole process group is stopped), no input, a size limit on files it writes,
           and the output kept as its first 4 KB and last 12 KB

The sandbox is `sandbox-exec` with a profile written for each command. Nothing runs outside it:
where it is not available, `available()` is false and the agent is not offered `run_check`.
"""
from __future__ import annotations

import os
import re
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass

SANDBOX_EXEC = "/usr/bin/sandbox-exec"
DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 300
HEAD_BYTES = 4 * 1024
TAIL_BYTES = 12 * 1024
MAX_COMMAND_CHARS = 500
STOP_GRACE_SECONDS = 2.0
_EXTRA_PATH = ("/opt/homebrew/bin", "/usr/local/bin")
# Toolchains that live in the home folder: readable, so node, python, ruby and the like start from there.
TOOLCHAINS = (".nvm", ".pyenv", ".volta", ".bun", ".deno", ".cargo", ".rustup", ".local", ".rbenv", ".asdf",
              "Library/Python", "go")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_DENIED_HINT = re.compile(r"operation not permitted|not permitted|permission denied", re.I)


class Refused(Exception):
    """The command is not one the agent may ask the owner about."""


def available() -> bool:
    return sys.platform == "darwin" and os.path.exists(SANDBOX_EXEC)


def check_command(command) -> str:
    """The same checks as step 2b's play button: text, one line, not obviously dangerous."""
    import codec_vibe_runner as vr
    try:
        return vr._check_command(command)
    except Exception as e:                      # VibeError carries the reason in .message
        raise Refused(getattr(e, "message", str(e))) from None


def _q(path: str) -> str:
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


def profile(project: str, vibe_root: str, home: str, temp: str, network: bool) -> str:
    """The sandbox profile for one command. Paths are real paths."""
    chains = "\n".join(f"    (subpath {_q(os.path.join(home, t))})" for t in TOOLCHAINS)
    net = ('(allow network-outbound (literal "/private/var/run/mDNSResponder") (remote udp "*:53") (remote tcp "*:53") '
           '(remote ip "*:80") (remote ip "*:443"))\n(deny network-outbound (remote ip "localhost:*"))'
           if network else "(deny network*)")
    return f'''(version 1)
(deny default)
(import "bsd.sb")

;; reads: the system and the toolchains, this project; not the rest of the home folder, other projects, keys or CODEC state
(allow file-read*)
(deny file-read* (subpath {_q(home)}))
(deny file-read* (subpath {_q(vibe_root)}))
(allow file-read*
{chains}
    (subpath {_q(project)})
    (subpath {_q(temp)}))
(deny file-read*
    (subpath {_q(os.path.join(home, ".ssh"))}) (subpath {_q(os.path.join(home, ".aws"))}) (subpath {_q(os.path.join(home, ".gnupg"))})
    (subpath {_q(os.path.join(home, ".config/gh"))}) (subpath {_q(os.path.join(home, ".codec"))})
    (subpath "/Library/Keychains") (subpath {_q(os.path.join(project, ".vibe"))}))
(allow file-read-metadata (subpath {_q(home)}))

;; writes: this project (never its .vibe folder) and this command's own temporary folder
(allow file-write*
    (subpath {_q(project)})
    (subpath {_q(temp)})
    (literal "/dev/null") (literal "/dev/tty") (regex #"^/dev/ttys[0-9]+$") (literal "/dev/dtracehelper"))
(deny file-write* (subpath {_q(os.path.join(project, ".vibe"))}))

;; programs it may start, and the few system services a runtime asks for
(allow process-exec*)
(allow process-fork)
(allow signal (target self))
(allow sysctl-read)
(allow file-ioctl)
(allow mach-lookup
    (global-name "com.apple.system.logger")
    (global-name "com.apple.system.notification_center")
    (global-name "com.apple.system.opendirectoryd.libinfo")
    (global-name "com.apple.system.opendirectoryd.membership")
    (global-name "com.apple.cfprefsd.daemon")
    (global-name "com.apple.cfprefsd.agent")
    (global-name "com.apple.trustd")
    (global-name "com.apple.trustd.agent")
    (global-name "com.apple.SystemConfiguration.configd")
    (global-name "com.apple.SystemConfiguration.DNSConfiguration"))
(allow ipc-posix-shm*)
(allow iokit-open (iokit-registry-entry-class "IOSurfaceRootUserClient"))

;; never: other apps, the clipboard, the keychain service, the screen, the camera, the microphone
(deny mach-lookup
    (global-name "com.apple.coreservices.appleevents")
    (global-name "com.apple.pasteboard.1")
    (global-name "com.apple.SecurityServer")
    (global-name "com.apple.securityd")
    (global-name "com.apple.screencapture.interactive")
    (global-name "com.apple.windowserver.active")
    (global-name "com.apple.CoreServices.coreservicesd")
    (global-name "com.apple.lsd.mapdb")
    (global-name-prefix "com.apple.tccd")
    (global-name-prefix "com.apple.accessibility"))
(deny device-camera)
(deny device-microphone)

;; network
{net}
'''


def command_env(temp: str) -> dict:
    """The environment of a command: secret-looking variables removed, tools told to stay quiet and local."""
    try:
        from routes.vibe_exec import _hardened_run_env
        env = _hardened_run_env()
    except Exception:
        pat = re.compile(r"(?i)(secret|password|passwd|token|credential|api[_-]?key|access[_-]?key|private[_-]?key|bearer|"
                         r"session[_-]?token|auth[_-]?token|_key$|^key$)")
        env = {k: v for k, v in os.environ.items() if not pat.search(k)}
    env.pop("SSH_AUTH_SOCK", None)
    path = [p for p in env.get("PATH", "").split(os.pathsep) if p]
    for extra in _EXTRA_PATH:
        if extra not in path:
            path.append(extra)
    env.update(PATH=os.pathsep.join(path), TMPDIR=temp, CI="1", BROWSER="none",
               GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0",
               npm_config_cache=os.path.join(temp, "npm-cache"), npm_config_update_notifier="false",
               npm_config_fund="false", npm_config_audit="false", PIP_DISABLE_PIP_VERSION_CHECK="1")
    return env


def _limits() -> None:          # runs in the child between fork and exec: no imports, no locks
    try:
        resource.setrlimit(resource.RLIMIT_FSIZE, (256 * 1024 ** 2, 256 * 1024 ** 2))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (ValueError, OSError):
        pass


@dataclass
class Result:
    exit_code: int | None       # None when it was stopped or timed out
    output: str
    seconds: float
    timed_out: bool = False
    stopped: bool = False
    cut_bytes: int = 0          # how much of the output was left out
    denied: bool = False        # the sandbox said "not permitted" somewhere in the output


class _Collector:
    """Keeps the first HEAD_BYTES and the last TAIL_BYTES of what a process prints, and counts the rest."""

    def __init__(self):
        self.head = bytearray()
        self.tail = bytearray()
        self.total = 0

    def add(self, chunk: bytes) -> None:
        self.total += len(chunk)
        room = HEAD_BYTES - len(self.head)
        if room > 0:
            self.head += chunk[:room]
            chunk = chunk[room:]
        if chunk:
            self.tail += chunk
            if len(self.tail) > TAIL_BYTES:
                del self.tail[:len(self.tail) - TAIL_BYTES]

    def text(self) -> tuple[str, int]:
        cut = max(0, self.total - len(self.head) - len(self.tail))
        body = bytes(self.head) + (f"\n... ({cut} bytes left out) ...\n".encode() if cut else b"") + bytes(self.tail)
        return _ANSI.sub("", body.decode("utf-8", errors="replace")).replace("\r\n", "\n"), cut


def _stop_group(proc: subprocess.Popen) -> None:
    for sig, wait in ((signal.SIGTERM, STOP_GRACE_SECONDS), (signal.SIGKILL, 2.0)):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue


def run(project, vibe_root, command: str, *, network: bool = False, timeout: float = DEFAULT_TIMEOUT,
        stop_event: threading.Event | None = None) -> Result:
    """Run one command in the sandbox, in the project folder. Never raises for what the command does."""
    if not available():
        raise Refused("The sandbox is not available on this computer, so no command was run.")
    timeout = max(1.0, min(float(timeout), MAX_TIMEOUT))
    proj = os.path.realpath(str(project))
    root = os.path.realpath(str(vibe_root))
    home = os.path.realpath(os.path.expanduser("~"))
    temp = os.path.realpath(tempfile.mkdtemp(prefix="vibe-agent-"))
    prof_file = None
    t0 = time.monotonic()
    try:
        fd, prof_file = tempfile.mkstemp(prefix="vibe-agent-", suffix=".sb", dir=temp)
        with os.fdopen(fd, "w") as f:
            f.write(profile(proj, root, home, temp, bool(network)))
        proc = subprocess.Popen([SANDBOX_EXEC, "-f", prof_file, "/bin/bash", "-c", command], cwd=proj, env=command_env(temp),
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                start_new_session=True, preexec_fn=_limits)
        box = _Collector()

        def pump():
            try:
                while True:
                    chunk = os.read(proc.stdout.fileno(), 65536)
                    if not chunk:
                        break
                    box.add(chunk)
            except (OSError, ValueError):
                pass

        reader = threading.Thread(target=pump, daemon=True)
        reader.start()
        timed_out = stopped = False
        while proc.poll() is None:
            if stop_event is not None and stop_event.is_set():
                stopped = True
                break
            if time.monotonic() - t0 > timeout:
                timed_out = True
                break
            time.sleep(0.05)
        if timed_out or stopped:
            _stop_group(proc)
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)     # what the command left behind
            except (ProcessLookupError, PermissionError):
                pass
        reader.join(timeout=2)
        try:
            proc.stdout.close()
        except Exception:
            pass
        text, cut = box.text()
        return Result(None if (timed_out or stopped) else proc.returncode, text, round(time.monotonic() - t0, 1), timed_out, stopped, cut,
                      bool(_DENIED_HINT.search(text)))
    finally:
        shutil.rmtree(temp, ignore_errors=True)
