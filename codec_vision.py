"""CODEC Vision — the single canonical screen-vision helper (A-11, PR-3E).

Before this, the Gemini-Flash → local-Qwen-VL fallback was hand-rolled in three
places with drifting shapes: `codec.py` (sync), `codec_voice._analyze_screenshot`
(async), and `codec_session.screenshot_ctx` (sync, local-only). A model upgrade
or vision-API fix meant editing all three.

Canonical API:
    describe_sync(image_b64, prompt, *, mime, max_tokens)        -> str
    await describe_async(image_b64, prompt, *, mime, max_tokens, http) -> str

Both: try Gemini Flash first (when `vision_provider == "gemini"` and a key is
present), fall back to the local Qwen-VL `/chat/completions` endpoint. Return
the description text, or "" on failure. Config is read live from codec_config
(so provider/model/key changes + Keychain migration take effect without restart).
"""
from __future__ import annotations

import logging
from typing import Any, Optional, Tuple

log = logging.getLogger("codec.vision")

_GEMINI_MODEL = "gemini-2.0-flash"


def _vision_config() -> Tuple[str, str, str, str]:
    """(provider, gemini_key, local_url, local_model) read live from config.
    Falls back to safe defaults if codec_config can't be imported."""
    try:
        from codec_config import cfg, QWEN_VISION_URL, QWEN_VISION_MODEL, get_gemini_api_key
        gem = get_gemini_api_key() or ""
        provider = cfg.get("vision_provider", "gemini" if gem else "local")
        return provider, gem, QWEN_VISION_URL, QWEN_VISION_MODEL
    except Exception as e:  # pragma: no cover — defensive
        log.warning("vision config unavailable: %s", e)
        return "local", "", "http://localhost:8083/v1", "qwen-vl"


def _gemini_payload(image_b64: str, prompt: str, mime: str, max_tokens: int) -> dict:
    return {
        "contents": [{"parts": [
            {"inlineData": {"mimeType": mime, "data": image_b64}},
            {"text": prompt},
        ]}],
        "generationConfig": {"maxOutputTokens": max_tokens},
    }


def _gemini_url(api_key: str) -> str:
    return (f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{_GEMINI_MODEL}:generateContent?key={api_key}")


def _parse_gemini(rj: dict) -> str:
    try:
        parts = rj.get("candidates", [])[0].get("content", {}).get("parts", [])
        return (parts[0].get("text", "") if parts else "").strip()
    except (IndexError, AttributeError, TypeError):
        return ""


def _local_payload(image_b64: str, prompt: str, mime: str, model: str, max_tokens: int) -> dict:
    return {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
            {"type": "text", "text": prompt},
        ]}],
        "max_tokens": max_tokens,
    }


def _parse_local(rj: dict) -> str:
    try:
        return (rj["choices"][0]["message"].get("content") or "").strip()
    except (KeyError, IndexError, TypeError):
        return ""


def describe_sync(image_b64: str, prompt: str, *, mime: str = "image/png",
                  max_tokens: int = 800, timeout: float = 120.0) -> str:
    """Synchronous (requests) vision describe. Gemini Flash → local Qwen-VL."""
    import requests
    provider, gem_key, local_url, local_model = _vision_config()

    if provider == "gemini" and gem_key:
        try:
            r = requests.post(_gemini_url(gem_key),
                              json=_gemini_payload(image_b64, prompt, mime, max_tokens),
                              timeout=min(timeout, 30.0))
            if r.status_code == 200:
                txt = _parse_gemini(r.json())
                if txt:
                    return txt
            log.info("Gemini vision %s; falling back to local", r.status_code)
        except Exception as e:
            log.info("Gemini vision error (%s); falling back to local", e)

    try:
        r = requests.post(local_url.rstrip("/") + "/chat/completions",
                          json=_local_payload(image_b64, prompt, mime, local_model, max_tokens),
                          headers={"Content-Type": "application/json"}, timeout=timeout)
        if r.status_code == 200:
            return _parse_local(r.json())
        log.warning("Local vision returned %s: %s", r.status_code, r.text[:200])
    except Exception as e:
        log.warning("Local vision error: %s", e)
    return ""


async def describe_async(image_b64: str, prompt: str, *, mime: str = "image/jpeg",
                         max_tokens: int = 500, timeout: float = 120.0,
                         http: Optional[Any] = None) -> str:
    """Async (httpx) vision describe. Gemini Flash → local Qwen-VL. Reuses the
    caller's httpx client if given (e.g. VoicePipeline._http), else makes one."""
    import httpx
    provider, gem_key, local_url, local_model = _vision_config()
    own_client = http is None
    client = http or httpx.AsyncClient(timeout=timeout)
    try:
        if provider == "gemini" and gem_key:
            try:
                r = await client.post(_gemini_url(gem_key),
                                      json=_gemini_payload(image_b64, prompt, mime, max_tokens),
                                      timeout=min(timeout, 30.0))
                if r.status_code == 200:
                    txt = _parse_gemini(r.json())
                    if txt:
                        return txt
                log.info("Gemini vision %s; falling back to local", r.status_code)
            except Exception as e:
                log.info("Gemini vision error (%s); falling back to local", e)

        try:
            r = await client.post(local_url.rstrip("/") + "/chat/completions",
                                  json=_local_payload(image_b64, prompt, mime, local_model, max_tokens),
                                  headers={"Content-Type": "application/json"}, timeout=timeout)
            if r.status_code == 200:
                return _parse_local(r.json())
            log.warning("Local vision returned %s: %s", r.status_code, r.text[:200])
        except Exception as e:
            log.warning("Local vision error: %s", e)
        return ""
    finally:
        if own_client:
            await client.aclose()
