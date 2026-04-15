"""Retry + exponential backoff helpers for CODEC external-service calls.

Use this when calling Qwen LLM, Kokoro TTS, Whisper STT, Google APIs, etc.
Skills that wrap their requests with @retryable get automatic resilience
against transient 5xx / connection errors without bloating individual skills.

Usage:
    from codec_retry import retryable, retry_post

    @retryable(max_attempts=3)
    def fetch(...):
        return requests.post(...)

    # Or one-shot:
    r = retry_post(url, json=payload, max_attempts=3)
"""
from __future__ import annotations

import functools
import random
import time
from typing import Callable, TypeVar

T = TypeVar("T")

# HTTP status codes worth retrying
RETRIABLE_STATUS = {408, 429, 500, 502, 503, 504}


def _sleep_backoff(attempt: int, base: float = 0.5, cap: float = 8.0) -> float:
    """Exponential backoff with full jitter."""
    t = min(cap, base * (2 ** attempt))
    return random.uniform(0, t)


def retryable(
    max_attempts: int = 3,
    base_delay: float = 0.5,
    exceptions: tuple = (Exception,),
):
    """Decorator: retry function on listed exceptions with exponential backoff.

    Does NOT retry on KeyboardInterrupt or generic `Exception` subclasses that
    indicate logic errors (ValueError, TypeError) — caller should pass the
    specific transient exception classes (ConnectionError, Timeout, etc.).
    """
    def deco(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapped(*args, **kwargs) -> T:
            last_exc = None
            for attempt in range(max_attempts):
                try:
                    return fn(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    if attempt == max_attempts - 1:
                        raise
                    delay = _sleep_backoff(attempt, base_delay)
                    time.sleep(delay)
            # Unreachable (either returned or raised)
            raise last_exc  # type: ignore[misc]
        return wrapped
    return deco


def retry_request(
    method: str,
    url: str,
    *,
    max_attempts: int = 3,
    base_delay: float = 0.5,
    timeout: float = 30.0,
    **kwargs,
):
    """One-shot HTTP request with retry on connection + retriable status codes."""
    import requests
    last_exc = None
    for attempt in range(max_attempts):
        try:
            r = requests.request(method, url, timeout=timeout, **kwargs)
            if r.status_code in RETRIABLE_STATUS and attempt < max_attempts - 1:
                time.sleep(_sleep_backoff(attempt, base_delay))
                continue
            return r
        except (requests.ConnectionError, requests.Timeout) as e:
            last_exc = e
            if attempt == max_attempts - 1:
                raise
            time.sleep(_sleep_backoff(attempt, base_delay))
    raise last_exc  # type: ignore[misc]


def retry_post(url: str, **kwargs):
    return retry_request("POST", url, **kwargs)


def retry_get(url: str, **kwargs):
    return retry_request("GET", url, **kwargs)
