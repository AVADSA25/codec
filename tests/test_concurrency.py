"""Tests for codec_concurrency.run_with_timeout (Fix #4 / C4).

The bug being fixed: `with ThreadPoolExecutor() as ex: fut.result(timeout=T)`
defeats its own timeout — the context-manager __exit__ calls
shutdown(wait=True), which blocks until the runaway task finishes. So a 50ms
"timeout" on a 5s task actually takes ~5s.

run_with_timeout must surface the timeout PROMPTLY and never block on the
abandoned worker (daemon thread, no shutdown(wait=True)).
"""
import concurrent.futures
import threading
import time

import pytest

import codec_concurrency


def test_returns_result_for_fast_fn():
    assert codec_concurrency.run_with_timeout(lambda: 42, 1.0) == 42


def test_passes_through_args_and_kwargs():
    def add(a, b, c=0):
        return a + b + c

    assert codec_concurrency.run_with_timeout(add, 1.0, 2, 3, c=4) == 9


def test_timeout_raises_TimeoutError():
    def slow():
        time.sleep(5)
        return "should-not-return"

    with pytest.raises(TimeoutError):
        codec_concurrency.run_with_timeout(slow, 0.05)


def test_timeout_returns_promptly_does_not_block_on_runaway():
    # THE C4 regression guard: a 5s task with a 50ms timeout must surface the
    # timeout in well under the task duration. If run_with_timeout blocked on
    # shutdown(wait=True) like the old ThreadPoolExecutor pattern, this would
    # take ~5s and the assertion would fail.
    started = threading.Event()

    def slow():
        started.set()
        time.sleep(5)

    t0 = time.monotonic()
    with pytest.raises(TimeoutError):
        codec_concurrency.run_with_timeout(slow, 0.05)
    elapsed = time.monotonic() - t0
    assert started.is_set(), "worker thread should have started"
    assert elapsed < 1.0, (
        f"timeout took {elapsed:.2f}s — it blocked on the runaway task; "
        "C4 (shutdown(wait=True)) not actually fixed"
    )


def test_reraises_fn_exception_with_type_and_message():
    def boom():
        raise ValueError("kaboom")

    with pytest.raises(ValueError, match="kaboom"):
        codec_concurrency.run_with_timeout(boom, 1.0)


def test_timeouterror_is_caught_as_concurrent_futures_timeout():
    # The migrated call sites catch concurrent.futures.TimeoutError. On
    # py3.11+ that is an alias of builtin TimeoutError, so a helper raising
    # builtin TimeoutError is still caught. Guard that contract explicitly.
    assert concurrent.futures.TimeoutError is TimeoutError

    def slow():
        time.sleep(5)

    with pytest.raises(concurrent.futures.TimeoutError):
        codec_concurrency.run_with_timeout(slow, 0.05)
