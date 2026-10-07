"""The vision warmup does not block the dashboard (docs/known-issues.md, 2026-10-07).

`_warmup_vision()` ran `requests.post(..., timeout=60)` on the event loop 5 s after
startup, so while the local model was stuck no page or API call was answered until
the timeout: on 6 Oct the log shows startup at 23:25:31 and the warmup giving up at
23:26:36. The requests now run in a thread.
"""
from __future__ import annotations

import asyncio
import time


class _Resp:
    status_code = 200


def test_the_vision_warmup_leaves_the_event_loop_free(monkeypatch):
    import requests

    import codec_dashboard as dash
    monkeypatch.setattr(dash, "_WARMUP_DELAY_S", 0)

    def slow_post(*a, **k):
        time.sleep(0.6)  # a slow model
        return _Resp()

    monkeypatch.setattr(requests, "post", slow_post)

    async def main():
        ticks = []

        async def ticker():
            for _ in range(12):
                ticks.append(time.monotonic())
                await asyncio.sleep(0.05)

        t = asyncio.create_task(ticker())
        await dash._warmup_vision()
        await t
        return ticks

    ticks = asyncio.run(main())
    gaps = [b - a for a, b in zip(ticks, ticks[1:])]
    assert max(gaps) < 0.3, f"the loop stalled for {max(gaps):.2f} s during the warmup"


def test_the_keepalive_ping_is_off_the_event_loop_too():
    import inspect

    import codec_dashboard as dash
    src = inspect.getsource(dash._vision_keepalive)
    assert "asyncio.to_thread(rq.get" in src and "= rq.get(" not in src
