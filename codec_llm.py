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
from typing import Any, Dict, Iterator, List, Optional

log = logging.getLogger("codec.llm")

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


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
) -> str:
    """POST `messages` to `<base_url>/chat/completions` and return the parsed,
    `<think>`-stripped assistant text (or "" on failure).

    `retries` includes the first attempt (retries=3 → up to 3 tries with
    exponential 2**n backoff between them, matching codec_session.qwen_call).
    Never raises — network/parse errors are logged and yield "".
    """
    import requests
    headers, payload = _build_request(
        messages, model=model, api_key=api_key, max_tokens=max_tokens,
        temperature=temperature, enable_thinking=enable_thinking,
        extra_kwargs=extra_kwargs,
    )

    attempts = max(1, retries)
    url = base_url.rstrip("/") + "/chat/completions"
    for attempt in range(attempts):
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=timeout)
            if r.status_code == 200:
                resp = extract_content(r.json())
                if resp:
                    return resp
                # 200 but empty/odd shape — don't retry, nothing more to get.
                return ""
            log.warning("LLM call %s returned %s: %s", url, r.status_code, r.text[:200])
        except Exception as e:
            log.warning("LLM call attempt %d/%d failed: %s", attempt + 1, attempts, e)
            if attempt < attempts - 1:
                time.sleep(2 ** attempt)
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
) -> Iterator[str]:
    """POST with `stream=True` and yield the RAW assistant content deltas in
    order. Centralizes the SSE plumbing: header/payload build (shared with
    call()), `data: ` framing, the `[DONE]` sentinel, `choices[0].delta.content`
    extraction, and per-chunk parse tolerance.

    Think-stripping is intentionally NOT done here — callers that show tokens
    live (e.g. codec_session.qwen_stream) strip `<think>` on the accumulated
    result, and the dashboard owns its own cross-chunk tag machine. Never
    raises: on connect/HTTP/parse error it logs and stops yielding, so the
    caller sees a short/empty stream and applies its own fallback.
    """
    import json as _json
    import requests
    headers, payload = _build_request(
        messages, model=model, api_key=api_key, max_tokens=max_tokens,
        temperature=temperature, enable_thinking=enable_thinking,
        extra_kwargs=extra_kwargs, stream=True,
    )
    url = base_url.rstrip("/") + "/chat/completions"
    try:
        with requests.post(url, json=payload, headers=headers,
                           timeout=timeout, stream=True) as r:
            if r.status_code != 200:
                log.warning("LLM stream %s returned %s: %s",
                            url, r.status_code, getattr(r, "text", "")[:200])
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
                    delta = (_json.loads(data).get("choices", [{}])[0]
                             .get("delta", {}).get("content", ""))
                except Exception as e:
                    log.warning("LLM stream chunk parse failed: %s", e)
                    continue
                if delta:
                    yield delta
    except Exception as e:
        log.warning("LLM stream call failed: %s", e)
        return
