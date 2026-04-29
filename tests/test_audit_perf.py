"""Performance + concurrency tests for the unified audit writer.

audit() runs on every MCP tool call. The unified envelope adds a few fields
and one dict merge — the budgets per design §4.4:

    Single-thread:           < 0.5  ms/call
    10-way contention stress: < 2.5 ms/call (also: no JSON corruption,
                                              no dropped writes)

Tests redirect codec_audit._AUDIT_LOG to a temp file so the real audit log
is never touched.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import codec_audit


@pytest.fixture
def temp_log(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
    tmp.close()
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", Path(tmp.name))
    yield Path(tmp.name)
    try:
        os.unlink(tmp.name)
    except OSError:
        pass


# Generous CI multiplier — flaky CI shouldn't block merges; the perf-baseline
# capture in docs/PHASE1-STEP1-BASELINE.md is the production guard.
_CI = bool(os.environ.get("CI"))
_BUDGET_SINGLE_MS = 0.5 if not _CI else 5.0
_BUDGET_CONCURRENT_MS = 2.5 if not _CI else 25.0


def test_audit_single_thread_perf(temp_log):
    n = 1000
    cid = secrets.token_hex(6)
    t0 = time.monotonic()
    for _ in range(n):
        codec_audit.audit("perf_test", event="tool_result", outcome="ok",
                          duration_ms=1.0, correlation_id=cid)
    elapsed = time.monotonic() - t0
    avg_ms = (elapsed / n) * 1000.0
    assert avg_ms < _BUDGET_SINGLE_MS, (
        f"audit() single-thread: {avg_ms:.3f}ms/call (budget {_BUDGET_SINGLE_MS}ms)"
    )


def test_log_event_single_thread_perf(temp_log):
    n = 1000
    t0 = time.monotonic()
    for _ in range(n):
        codec_audit.log_event("perf_test", "codec-test", "msg",
                              extra={"i": 1})
    elapsed = time.monotonic() - t0
    avg_ms = (elapsed / n) * 1000.0
    # log_event has one extra dict merge over audit() — allow ~20% headroom.
    assert avg_ms < _BUDGET_SINGLE_MS * 1.2, (
        f"log_event() single-thread: {avg_ms:.3f}ms/call "
        f"(budget {_BUDGET_SINGLE_MS * 1.2:.2f}ms)"
    )


def test_audit_concurrent_no_corruption(temp_log):
    """10 threads × 1000 writes each. Verify:
       1) Every line is parseable JSON.
       2) All 10,000 lines present.
       3) Avg latency under 2.5 ms/call.
       4) Every correlation_id we passed appears in the file.
    """
    N_THREADS = 10
    N_WRITES = 1000

    cid_pool = [
        f"{thread_id:08x}{j:04x}"  # 12-char hex, unique per (thread, write)
        for thread_id in range(N_THREADS)
        for j in range(N_WRITES)
    ]

    def worker(thread_id: int):
        for j in range(N_WRITES):
            codec_audit.audit(
                "stress",
                event="tool_result",
                outcome="ok",
                duration_ms=0.1,
                correlation_id=cid_pool[thread_id * N_WRITES + j],
            )

    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=N_THREADS) as ex:
        list(ex.map(worker, range(N_THREADS)))
    elapsed = time.monotonic() - t0

    total = N_THREADS * N_WRITES
    avg_ms = (elapsed / total) * 1000.0

    # 1. Latency
    assert avg_ms < _BUDGET_CONCURRENT_MS, (
        f"audit() under {N_THREADS}-way contention: {avg_ms:.3f}ms/call "
        f"(budget {_BUDGET_CONCURRENT_MS}ms)"
    )

    # 2. Line count + 3. JSON validity + required fields
    text = temp_log.read_text(encoding="utf-8")
    lines = [l for l in text.splitlines() if l.strip()]
    assert len(lines) == total, (
        f"expected {total} lines, got {len(lines)} — writes were lost"
    )
    seen_cids = set()
    for i, line in enumerate(lines):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            raise AssertionError(f"corrupt JSON at line {i}: {e!r}: {line[:200]!r}")
        for k in ("ts", "schema", "event", "source", "outcome"):
            assert k in obj, f"line {i} missing required field {k}: {line[:200]}"
        seen_cids.add(obj["extra"]["correlation_id"])

    # 4. All cids present
    assert seen_cids == set(cid_pool), (
        f"expected {len(cid_pool)} unique cids, got {len(seen_cids)} — drops?"
    )
