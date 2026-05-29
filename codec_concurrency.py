"""codec_concurrency — small, dependency-free concurrency helpers.

`run_with_timeout` runs a callable in a daemon thread with a hard wall-clock
timeout that ACTUALLY bounds wall-clock time.

Motivation (audit C4): the common idiom

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(fn)
        return fut.result(timeout=T)

defeats its own timeout. When `fut.result(timeout=T)` raises TimeoutError,
the `with` block's __exit__ calls `executor.shutdown(wait=True)`, which BLOCKS
until the runaway task finishes. So a 50ms "timeout" on a 5s task takes ~5s —
the MCP tool dispatch (codec_mcp) and the observer OCR call (codec_observer)
could hang on a slow skill / screencapture popup.

This helper uses a daemon thread + `join(timeout=...)` and never calls
shutdown(wait=True): on timeout it abandons the still-running worker and
raises promptly. daemon=True means an abandoned worker never blocks process
shutdown. Same shape as the proven `codec_hooks._run_hook_with_timeout`.
"""
import queue
import threading
from typing import Any, Callable

__all__ = ["run_with_timeout"]


def run_with_timeout(
    fn: Callable[..., Any],
    timeout: float,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Run ``fn(*args, **kwargs)`` in a daemon thread, bounded by ``timeout`` seconds.

    Returns ``fn``'s return value on success. Re-raises, in the calling thread,
    any exception ``fn`` raised (original type, message, and instance preserved).
    Raises ``TimeoutError`` if ``fn`` does not complete within ``timeout`` —
    promptly, without waiting for the (abandoned, still-running) worker to finish.

    On Python 3.11+ ``concurrent.futures.TimeoutError`` is an alias of the
    builtin ``TimeoutError``, so call sites catching either are satisfied.
    """
    result_q: "queue.Queue[Any]" = queue.Queue(maxsize=1)
    exc_q: "queue.Queue[BaseException]" = queue.Queue(maxsize=1)

    def _runner() -> None:
        try:
            result_q.put(fn(*args, **kwargs))
        except BaseException as e:  # noqa: BLE001 — propagate ANY error to the caller
            try:
                exc_q.put(e)
            except Exception:
                pass

    t = threading.Thread(target=_runner, daemon=True, name="codec-run-with-timeout")
    t.start()
    t.join(timeout=timeout)

    if t.is_alive():
        # Abandon the worker — daemon=True so it never blocks shutdown. No
        # shutdown(wait=True), so we return control to the caller immediately.
        raise TimeoutError(f"operation exceeded {timeout}s timeout")
    if not exc_q.empty():
        raise exc_q.get_nowait()
    if not result_q.empty():
        return result_q.get_nowait()
    # Thread finished without putting a result or exception — only reachable if
    # the result put itself failed. Treat as a None return.
    return None
