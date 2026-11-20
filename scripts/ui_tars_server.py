"""UI-TARS server that stops itself (docs/UI-TARS-SERVER-DESIGN.md).

Started by codec_ui_tars.endpoint() when mouse control needs UI-TARS. It runs mlx_vlm's
server through scripts/mlx_pinned_server.py (pinned to the one model it loads: a request
naming another one gets 409) and adds an idle timer around the ASGI app:

- the timer starts when the model has loaded (the lifespan startup is complete);
- it counts requests in flight and the time the last one finished (a streamed answer
  counts until its last byte);
- with nothing in flight for --idle-seconds, or --max-seconds after the start whatever
  happens, it sends itself SIGTERM; uvicorn shuts down and the model's memory is freed.
  If the process is still there 30 s later it exits hard.

If the timer cannot be put in place (mlx_vlm changed), the script exits with code 3
before loading anything: a UI-TARS server that never stops would hold about 7 GB.

Run with the model server's Python. Standard library only, apart from mlx_vlm.
"""
import os
import signal
import sys
import threading
import time

EXIT_NO_TIMER = 3
_HARD_EXIT_AFTER_S = 30


def split_args(argv):
    """(idle_seconds, max_seconds, the rest for mlx_vlm)."""
    idle, maximum, rest = 300, 7200, []
    it = iter(argv)
    for arg in it:
        if arg == "--idle-seconds":
            idle = int(next(it))
        elif arg == "--max-seconds":
            maximum = int(next(it))
        else:
            rest.append(arg)
    return idle, maximum, rest


def should_stop(now, started, ready, last, inflight, idle_s, max_s):
    """Why the server should stop now ("max", "idle"), or ""."""
    if now - started >= max_s:
        return "max"
    if ready and inflight == 0 and now - last >= idle_s:
        return "idle"
    return ""


class IdleApp:
    """ASGI wrapper that records when the server is ready, how many HTTP requests are in
    flight and when the last one finished."""

    def __init__(self, inner, clock=time.monotonic):
        self.inner = inner
        self.clock = clock
        self.lock = threading.Lock()
        self.started = clock()
        self.ready = False
        self.last = self.started
        self.inflight = 0

    async def __call__(self, scope, receive, send):
        kind = scope.get("type")
        if kind == "lifespan":
            async def watch(message):
                if message.get("type") == "lifespan.startup.complete":
                    with self.lock:
                        self.ready = True
                        self.last = self.clock()
                await send(message)
            return await self.inner(scope, receive, watch)
        if kind != "http":
            return await self.inner(scope, receive, send)
        with self.lock:
            self.inflight += 1
        try:
            return await self.inner(scope, receive, send)
        finally:
            with self.lock:
                self.inflight -= 1
                self.last = self.clock()

    def reason(self, idle_s, max_s):
        with self.lock:
            return should_stop(self.clock(), self.started, self.ready, self.last,
                               self.inflight, idle_s, max_s)


def _watch(app, idle_s, max_s, tick):
    while True:
        time.sleep(tick)
        why = app.reason(idle_s, max_s)
        if why:
            print(f"ui_tars_server: stopping ({why})", file=sys.stderr, flush=True)
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(_HARD_EXIT_AFTER_S)
            print("ui_tars_server: still running after SIGTERM, exiting", file=sys.stderr, flush=True)
            os._exit(0)


def install_timer(package):
    """Wrap `package.app` (the object uvicorn imports as "mlx_vlm.server:app").
    Returns the IdleApp, or None when the package has no ASGI app to wrap."""
    inner = getattr(package, "app", None)
    if inner is None or not callable(inner):
        return None
    app = IdleApp(inner)
    package.app = app
    return app


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    idle_s, max_s, rest = split_args(argv)
    try:
        import mlx_vlm.server as package
    except Exception as e:
        print(f"ui_tars_server: mlx_vlm not importable ({type(e).__name__}); not starting",
              file=sys.stderr, flush=True)
        return EXIT_NO_TIMER
    app = install_timer(package)
    if app is None:
        print("ui_tars_server: mlx_vlm has no app to wrap; not starting", file=sys.stderr, flush=True)
        return EXIT_NO_TIMER
    print(f"ui_tars_server: stops after {idle_s} s idle, {max_s} s at most",
          file=sys.stderr, flush=True)
    threading.Thread(target=_watch, args=(app, idle_s, max_s, min(5, max(1, idle_s // 6))),
                     daemon=True).start()
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import mlx_pinned_server
    mlx_pinned_server.main(rest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
