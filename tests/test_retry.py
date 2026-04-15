"""Unit tests for codec_retry."""
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from codec_retry import retryable


def test_retries_and_succeeds():
    calls = {"n": 0}

    @retryable(max_attempts=3, base_delay=0.01, exceptions=(RuntimeError,))
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("boom")
        return "ok"

    assert flaky() == "ok"
    assert calls["n"] == 3


def test_exhausts_and_raises():
    @retryable(max_attempts=2, base_delay=0.01, exceptions=(RuntimeError,))
    def always_fails():
        raise RuntimeError("nope")

    try:
        always_fails()
        assert False, "should have raised"
    except RuntimeError as e:
        assert str(e) == "nope"


def test_no_retry_on_unlisted_exception():
    calls = {"n": 0}

    @retryable(max_attempts=3, base_delay=0.01, exceptions=(RuntimeError,))
    def fn():
        calls["n"] += 1
        raise ValueError("logic error")

    try:
        fn()
    except ValueError:
        pass
    assert calls["n"] == 1, "should not retry on unlisted exception"


if __name__ == "__main__":
    test_retries_and_succeeds()
    test_exhausts_and_raises()
    test_no_retry_on_unlisted_exception()
    print("retry tests passed.")
