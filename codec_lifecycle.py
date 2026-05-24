"""codec_lifecycle — uniform graceful-shutdown helper for PM2 daemons (H-1).

PM2 sends SIGTERM on `pm2 restart` / `reload` / max-memory-restart / watchdog
kill. Python's DEFAULT SIGTERM disposition terminates the process immediately —
it does NOT run `atexit` handlers or unwind `finally` blocks. (Only SIGINT does,
because it raises KeyboardInterrupt.) So a daemon with no SIGTERM handler is
hard-killed mid-work: in-flight state is dropped, subprocesses orphaned,
tempfiles leaked.

`install_handlers(cleanup_fn, name)` registers a uniform shutdown path — the
same shape `codec.py` (PR-4A/C-1) and `codec_dictate` already use by hand —
so every daemon exits cleanly within PM2's kill window:

    import codec_lifecycle
    def _cleanup():
        ...  # flush state, kill children, unlink temp files
    codec_lifecycle.install_handlers(_cleanup, name="codec-myservice")

Pure stdlib (no codec_* imports) so it can't create daemon import cycles.
"""
from __future__ import annotations

import atexit
import logging
import signal
import sys
import threading
from typing import Callable

log = logging.getLogger("codec.lifecycle")

__all__ = ["install_handlers"]


def install_handlers(cleanup_fn: Callable[[], None], name: str = "daemon",
                     exit_on_signal: bool = True) -> Callable[[], None]:
    """Install SIGTERM + SIGINT + atexit handlers that run ``cleanup_fn`` once.

    - **Idempotent:** a one-shot guard ensures ``cleanup_fn`` runs exactly once
      even if SIGTERM fires and then atexit runs (or a second signal arrives).
    - **Never raises:** exceptions from ``cleanup_fn`` are caught + logged — a
      shutdown hook must not crash shutdown.
    - **Signal path exits:** the SIGTERM/SIGINT handler runs cleanup then
      ``sys.exit(0)`` (raises SystemExit, which unwinds the daemon's ``while
      True`` loop and runs atexit). SystemExit is a BaseException, not
      Exception, so the daemons' inner ``except Exception`` loop-guards don't
      swallow it.
    - **Main-thread safe:** ``signal.signal`` only works on the main thread;
      the calls are wrapped so a non-main-thread caller degrades to atexit-only
      instead of raising at startup.

    Returns the wrapped run-once cleanup (handy for tests / manual invocation).
    """
    done = threading.Event()

    def _run_cleanup() -> None:
        if done.is_set():
            return
        done.set()
        try:
            cleanup_fn()
        except Exception as e:  # never let a teardown error crash shutdown
            log.warning("[%s] shutdown cleanup failed: %s", name, e)

    def _on_signal(signum, frame):  # noqa: ANN001 - signal handler signature
        try:
            signame = signal.Signals(signum).name
        except Exception:
            signame = str(signum)
        log.info("[%s] %s received — shutting down", name, signame)
        _run_cleanup()
        if exit_on_signal:
            sys.exit(0)

    # signal.signal raises ValueError off the main thread — degrade gracefully.
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _on_signal)
        except (ValueError, OSError, RuntimeError) as e:
            log.warning("[%s] could not install %s handler (%s); "
                        "relying on atexit only", name, sig, e)

    atexit.register(_run_cleanup)
    return _run_cleanup
