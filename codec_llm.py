"""CODEC LLM call helper — the single canonical OpenAI-style chat/completions caller.

A-12 (PR-3E): before this, ~45 sites hand-rolled the same `chat/completions`
POST — build headers (`Authorization: Bearer …`, `Content-Type`), assemble the
payload (`model`/`messages`/`max_tokens`/`temperature`/
`chat_template_kwargs.enable_thinking=False`), parse `choices[0].message`
(content, with a `reasoning` fallback), and strip `<think>…</think>`. A model
upgrade or API-shape fix then meant editing 20+ places.

This module centralizes the **non-streaming** call. It is intentionally
config-agnostic — each caller passes its own `base_url` / `model` / `api_key`
/ tuning — so it's a pure "build payload → POST → parse" helper with no import
cycle into codec_config. (Streaming SSE + the remaining call sites are migrated
in later A-12 tranches; this PR covers the call() API + codec.py + codec_session.)

NOTE: `codec_llm_proxy` is a *priority queue* (semaphore), not an HTTP proxy —
orthogonal to this module. Callers that want prioritization still wrap the call
in `llm_queue_sync(...)`; behavior parity for the migrated sites means we do NOT
add queue acquisition here (none of them used it).
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, AsyncIterator, Dict, Iterator, List, Optional

log = logging.getLogger("codec.llm")


class LLMError(Exception):
    """Raised by ``call(raise_on_error=True)`` on any non-success outcome —
    non-200 (after retries), a request exception (after retries), or a 200 with
    empty/unparseable content. The default ``raise_on_error=False`` keeps the
    never-raise → "" contract that the streaming/best-effort callers rely on.
    Fail-loud callers (agent_plan/runner, textassist, the regen script) opt in
    and map this onto their own error handling."""


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

# Sentinel yielded by stream(keepalive=True) on empty "thinking" chunks so an SSE
# caller (e.g. the dashboard) can emit a transport keepalive to hold its tunnel
# open. Never yielded when keepalive=False (the default) — content-only callers
# (codec_session.qwen_stream) are unaffected.
KEEPALIVE = object()

# Sentinels yielded by stream(error_sentinel=True) — 2026-07 chat-visibility
# fix. stream() never raises by contract, so before these existed a mid-reply
# connection drop / non-200 / read timeout was indistinguishable from a clean
# finish: the dashboard rendered an empty or silently-truncated bubble.
#   STREAM_ERROR   — the stream died abnormally (connect/HTTP/read error).
#   FINISH_LENGTH  — the model stopped at the max_tokens cap
#                    (finish_reason == "length"), i.e. the reply is truncated.
# Only yielded when error_sentinel=True; all existing callers are unaffected.
STREAM_ERROR = object()
FINISH_LENGTH = object()


def strip_think(text: str) -> str:
    """Remove <think>…</think> reasoning blocks and surrounding whitespace."""
    if not text:
        return ""
    return _THINK_RE.sub("", text).strip()


def extract_content(response_json: Dict[str, Any]) -> str:
    """Pull the assistant text from an OpenAI-style response: prefer
    `choices[0].message.content`, fall back to `.reasoning` (some local
    servers put the answer there when content is empty). `<think>` stripped.
    Returns "" on any shape mismatch."""
    try:
        msg = response_json["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return ""
    content = (msg.get("content") or "").strip()
    if content:
        return strip_think(content)
    reasoning = (msg.get("reasoning") or "").strip()
    if reasoning:
        return strip_think(reasoning)
    return ""


def _build_request(
    messages: List[Dict[str, Any]],
    *,
    model: str,
    api_key: str,
    max_tokens: int,
    temperature: float,
    enable_thinking: bool,
    extra_kwargs: Optional[Dict[str, Any]],
    stream: bool = False,
) -> tuple[Dict[str, str], Dict[str, Any]]:
    """Build the (headers, payload) for an OpenAI-style chat/completions request.
    Shared by call() and stream() so headers/auth/payload shape never drift.
    The `stream` flag is applied LAST so extra_kwargs can't clobber it."""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "chat_template_kwargs": {"enable_thinking": enable_thinking},
    }
    if extra_kwargs:
        payload.update(extra_kwargs)
    if stream:
        payload["stream"] = True
    return headers, payload


def _cloud_blocked_msg(base_url: str) -> Optional[str]:
    """License gate for the 'cloud_proxy' feature (paid edition only).

    Returns a user-facing message if this is a CLOUD call (non-localhost
    base_url) that the current license doesn't permit; otherwise None.

    Local calls (localhost / 127.0.0.1 / 0.0.0.0) are NEVER gated. OSS/dev
    builds always return None (feature_allowed → True). Fail-open: any
    licensing fault returns None so transport is never broken by licensing.
    """
    try:
        bl = (base_url or "").lower()
        if "localhost" in bl or "127.0.0.1" in bl or "0.0.0.0" in bl:
            return None  # local model — always allowed
        import codec_license
        if codec_license.feature_allowed("cloud_proxy"):
            return None
        st = codec_license.license_state()
        return (f"\U0001F512 Cloud models require an active CODEC license — "
                f"{st.reason}. Activate in Settings, or switch to the local model.")
    except Exception:
        return None  # fail-open — licensing must never break the LLM transport


def call(
    messages: List[Dict[str, Any]],
    *,
    base_url: str,
    model: str,
    api_key: str = "",
    max_tokens: int = 500,
    temperature: float = 0.7,
    timeout: float = 120.0,
    retries: int = 1,
    enable_thinking: bool = False,
    extra_kwargs: Optional[Dict[str, Any]] = None,
    raise_on_error: bool = False,
) -> str:
    """POST `messages` to `<base_url>/chat/completions` and return the parsed,
    `<think>`-stripped assistant text.

    `retries` includes the first attempt (retries=3 → up to 3 tries with
    exponential 2**n backoff between them, matching codec_session.qwen_call).

    Error contract:
    - `raise_on_error=False` (default): never raises — network/parse errors and
      empty/unparseable 200s are logged and yield "".
    - `raise_on_error=True`: raises `LLMError` on EVERY non-success outcome
      (non-200 after retries, request exception after retries, or a 200 with
      empty/unparseable content). For fail-loud callers that must not silently
      proceed on an empty answer.
    """
    import requests
    _blocked = _cloud_blocked_msg(base_url)
    if _blocked is not None:
        if raise_on_error:
            raise LLMError(_blocked)
        return _blocked
    headers, payload = _build_request(
        messages, model=model, api_key=api_key, max_tokens=max_tokens,
        temperature=temperature, enable_thinking=enable_thinking,
        extra_kwargs=extra_kwargs,
    )

    attempts = max(1, retries)
    url = base_url.rstrip("/") + "/chat/completions"
    last_error: Optional[Exception] = None
    for attempt in range(attempts):
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=timeout)
            if r.status_code == 200:
                resp = extract_content(r.json())
                if resp:
                    return resp
                # 200 but empty/odd shape — nothing more to get; don't retry.
                if raise_on_error:
                    raise LLMError("LLM returned empty or unparseable content")
                return ""
            last_error = LLMError(f"LLM call returned {r.status_code}: {r.text[:200]}")
            log.warning("LLM call %s returned %s: %s", url, r.status_code, r.text[:200])
        except LLMError:
            raise  # empty-200 in raise mode — propagate, don't swallow as a retry
        except Exception as e:
            last_error = e
            log.warning("LLM call attempt %d/%d failed: %s", attempt + 1, attempts, e)
            if attempt < attempts - 1:
                time.sleep(2 ** attempt)
    if raise_on_error:
        raise LLMError(f"LLM call failed after {attempts} attempt(s): {last_error}")
    return ""


def stream(
    messages: List[Dict[str, Any]],
    *,
    base_url: str,
    model: str,
    api_key: str = "",
    max_tokens: int = 500,
    temperature: float = 0.7,
    timeout: float = 120.0,
    enable_thinking: bool = False,
    extra_kwargs: Optional[Dict[str, Any]] = None,
    keepalive: bool = False,
    error_sentinel: bool = False,
) -> Iterator[Any]:
    """POST with `stream=True` and yield the RAW assistant content deltas in
    order. Centralizes the SSE plumbing: header/payload build (shared with
    call()), `data: ` framing, the `[DONE]` sentinel, `choices[0].delta.content`
    extraction, and per-chunk parse tolerance.

    Think-stripping is intentionally NOT done here — callers that show tokens
    live (e.g. codec_session.qwen_stream) strip `<think>` on the accumulated
    result, and the dashboard owns its own cross-chunk tag machine. Never
    raises: on connect/HTTP/parse error it logs and stops yielding, so the
    caller sees a short/empty stream and applies its own fallback.

    `keepalive=True` (default off): on an empty "thinking" chunk, yield the
    `KEEPALIVE` sentinel every 10th empty (1st, 11th, …) so an SSE caller can
    emit a transport keepalive. Content-only callers leave it off and only ever
    see `str` deltas.

    `error_sentinel=True` (default off): yield `STREAM_ERROR` when the stream
    dies abnormally (non-200, connect/read exception) and `FINISH_LENGTH` when
    the model stops at the max_tokens cap — so a UI caller can tell the user
    the reply was interrupted / truncated instead of rendering an empty or
    silently-cut bubble. Callers that leave it off see the old behavior
    (stream just ends).
    """
    import json as _json
    import requests
    _blocked = _cloud_blocked_msg(base_url)
    if _blocked is not None:
        yield _blocked
        return
    headers, payload = _build_request(
        messages, model=model, api_key=api_key, max_tokens=max_tokens,
        temperature=temperature, enable_thinking=enable_thinking,
        extra_kwargs=extra_kwargs, stream=True,
    )
    url = base_url.rstrip("/") + "/chat/completions"
    _empty = 0  # empty "thinking" chunks seen (drives keepalive)
    try:
        with requests.post(url, json=payload, headers=headers,
                           timeout=timeout, stream=True) as r:
            if r.status_code != 200:
                log.warning("LLM stream %s returned %s: %s",
                            url, r.status_code, getattr(r, "text", "")[:200])
                if error_sentinel:
                    yield STREAM_ERROR
                return
            for line in r.iter_lines():
                if not line:
                    continue
                if isinstance(line, (bytes, bytearray)):
                    line = line.decode("utf-8", "replace")
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data.strip() == "[DONE]":
                    return
                try:
                    choice = _json.loads(data).get("choices", [{}])[0]
                    delta = choice.get("delta", {}).get("content", "")
                except Exception as e:
                    log.warning("LLM stream chunk parse failed: %s", e)
                    continue
                if delta:
                    yield delta
                elif keepalive:
                    _empty += 1
                    if _empty % 10 == 1:   # 1st, 11th, 21st … (matches dashboard)
                        yield KEEPALIVE
                if error_sentinel and choice.get("finish_reason") == "length":
                    # Model hit the max_tokens cap — reply is truncated.
                    yield FINISH_LENGTH
    except Exception as e:
        log.warning("LLM stream call failed: %s", e)
        if error_sentinel:
            yield STREAM_ERROR
        return


async def acall(
    messages: List[Dict[str, Any]],
    *,
    base_url: str,
    model: str,
    api_key: str = "",
    max_tokens: int = 500,
    temperature: float = 0.7,
    timeout: float = 120.0,
    enable_thinking: bool = False,
    extra_kwargs: Optional[Dict[str, Any]] = None,
    http: Optional[Any] = None,
    raise_on_error: bool = False,
) -> str:
    """Async sibling of call() — a single non-streaming POST via an httpx
    AsyncClient. Reuses the caller's client when `http` is given (e.g. agents'
    module `_async_http`), else makes + closes its own. When a client is
    injected we do NOT pass a per-request timeout — the client's configured
    timeout applies (exact parity with the inline sites). `raise_on_error`
    mirrors call(): raise `LLMError` on non-200 / exception / empty, else "".
    The queue (codec_llm_proxy) stays at the call site — never owned here.
    """
    import httpx
    _blocked = _cloud_blocked_msg(base_url)
    if _blocked is not None:
        if raise_on_error:
            raise LLMError(_blocked)
        return _blocked
    headers, payload = _build_request(
        messages, model=model, api_key=api_key, max_tokens=max_tokens,
        temperature=temperature, enable_thinking=enable_thinking,
        extra_kwargs=extra_kwargs,
    )
    url = base_url.rstrip("/") + "/chat/completions"
    own_client = http is None
    client = http or httpx.AsyncClient(timeout=timeout)
    try:
        try:
            r = await client.post(url, json=payload, headers=headers)
            if r.status_code == 200:
                resp = extract_content(r.json())
                if resp:
                    return resp
                if raise_on_error:
                    raise LLMError("LLM returned empty or unparseable content")
                return ""
            if raise_on_error:
                raise LLMError(f"async LLM call returned {r.status_code}")
            log.warning("async LLM call %s returned %s", url, r.status_code)
            return ""
        except LLMError:
            raise
        except Exception as e:
            if raise_on_error:
                raise LLMError(f"async LLM call failed: {e}") from e
            log.warning("async LLM call failed: %s", e)
            return ""
    finally:
        if own_client:
            await client.aclose()


async def astream(
    messages: List[Dict[str, Any]],
    *,
    base_url: str,
    model: str,
    api_key: str = "",
    max_tokens: int = 500,
    temperature: float = 0.7,
    timeout: float = 120.0,
    enable_thinking: bool = False,
    extra_kwargs: Optional[Dict[str, Any]] = None,
    http: Optional[Any] = None,
    keepalive: bool = False,
) -> AsyncIterator[Any]:
    """Async sibling of stream() — yields the RAW assistant content deltas (and
    the `KEEPALIVE` sentinel on empty chunks when `keepalive=True`) over httpx
    streaming. Reuses the caller's client when `http` is given (e.g. voice's
    `self._http`), else makes + closes its own.

    Contract difference vs sync stream(): astream **propagates** exceptions —
    it does NOT swallow connect/stream errors — because its consumer
    (codec_voice._stream_qwen) wraps the loop in try/except to speak a failure
    and a silent stream would be a UX regression. The queue stays at the call
    site. `<think>` stripping is the caller's job (voice strips per-token).
    """
    import json as _json
    import httpx
    _blocked = _cloud_blocked_msg(base_url)
    if _blocked is not None:
        yield _blocked
        return
    headers, payload = _build_request(
        messages, model=model, api_key=api_key, max_tokens=max_tokens,
        temperature=temperature, enable_thinking=enable_thinking,
        extra_kwargs=extra_kwargs, stream=True,
    )
    url = base_url.rstrip("/") + "/chat/completions"
    own_client = http is None
    client = http or httpx.AsyncClient(timeout=timeout)
    _empty = 0
    try:
        async with client.stream("POST", url, json=payload, headers=headers) as resp:
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data = line[6:]
                if data.strip() == "[DONE]":
                    return
                try:
                    delta = (_json.loads(data).get("choices", [{}])[0]
                             .get("delta", {}).get("content", ""))
                except (ValueError, KeyError, IndexError, TypeError):
                    continue
                if delta:
                    yield delta
                elif keepalive:
                    _empty += 1
                    if _empty % 10 == 1:
                        yield KEEPALIVE
    finally:
        if own_client:
            await client.aclose()
