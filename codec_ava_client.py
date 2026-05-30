"""AVA proxy client — opt-in cloud LLM path for CODEC.

This module is ADDITIVE. It does not modify any existing CODEC paths.
The default local Qwen3.6 pipeline keeps working exactly as before. Callers
that explicitly want to route a query through AVA's hosted cloud proxy
(Gemini, OpenAI, Claude) use `ava_chat()` from this module.

Config lives under `ava:` in ~/.codec/config.json:

  "ava": {
    "enabled": true,
    "proxy_url": "https://ava-proxy.lucyvpa.com",
    "license_key": "eyJhbGci...",
    "default_cloud_model": "gemini-2.5-flash-lite",
    "available_cloud_models": [ ... ]
  }

When you're ready to migrate dashboard / skills to support a model picker,
import `ava_chat` from here and branch on user selection. Nothing in this
file auto-wires anything.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional

import requests

log = logging.getLogger("codec.ava")

CONFIG_PATH = Path(os.path.expanduser("~/.codec/config.json"))


# ── Config helpers ──

@dataclass
class AvaConfig:
    enabled: bool
    proxy_url: str
    license_key: str
    default_cloud_model: str
    available_cloud_models: list[dict[str, str]]

    @property
    def is_ready(self) -> bool:
        return bool(self.enabled and self.proxy_url and self.license_key)


def load_config() -> AvaConfig | None:
    """Load AVA config from ~/.codec/config.json.

    Returns None if the file is missing or `ava` block isn't present.
    """
    try:
        data = json.loads(CONFIG_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    ava = data.get("ava")
    if not ava or not isinstance(ava, dict):
        return None
    return AvaConfig(
        enabled=bool(ava.get("enabled", False)),
        proxy_url=(ava.get("proxy_url") or "").rstrip("/"),
        license_key=ava.get("license_key", ""),
        default_cloud_model=ava.get("default_cloud_model", "gemini-2.5-flash-lite"),
        available_cloud_models=list(ava.get("available_cloud_models", [])),
    )


# ── License status check (called at startup) ──

def verify_license(cfg: Optional[AvaConfig] = None, timeout: float = 5.0) -> dict[str, Any] | None:
    """Hit /api/v1/status on the AVA license server. Returns status dict or None on failure.

    Doesn't raise — CODEC should keep working if proxy is unreachable (local Qwen still fine).
    """
    cfg = cfg or load_config()
    if not cfg or not cfg.is_ready:
        return None
    # License server sits on a SIBLING subdomain (same Cloudflare tunnel)
    # ava-proxy.lucyvpa.com → ava-license.lucyvpa.com
    license_url = cfg.proxy_url.replace("ava-proxy", "ava-license")
    try:
        r = requests.get(
            f"{license_url}/api/v1/status",
            params={"license_jwt": cfg.license_key},
            timeout=timeout,
        )
        if r.ok:
            return r.json()
        log.warning("ava license check %s: %s", r.status_code, r.text[:200])
        return {"status": "error", "http": r.status_code, "detail": r.text[:200]}
    except requests.RequestException as e:
        log.warning("ava license unreachable: %s", e)
        return None


# ── Cloud chat (OpenAI-compatible shape) ──

class AvaProxyError(Exception):
    pass


def _tag_messages_for_anthropic_cache(messages: list[dict]) -> list[dict]:
    """B7 / SR-30: rewrite each message that should be cached by Anthropic.

    Anthropic accepts `cache_control` on individual `content` blocks. For
    a system message that's a plain string, lift it into the rich-content
    format so the cache_control marker can attach. Same for the FIRST
    user message (memory injection / [MEMORY] block lives there). Later
    user messages are uncached because they're the actual turn content.

    Idempotent: if `cache_control` is already present, leave the message
    untouched.
    """
    out = []
    cached_user = False
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role == "system" and isinstance(content, str):
            out.append({
                "role": "system",
                "content": [
                    {"type": "text", "text": content,
                     "cache_control": {"type": "ephemeral"}},
                ],
            })
        elif role == "user" and isinstance(content, str) and not cached_user:
            cached_user = True
            out.append({
                "role": "user",
                "content": [
                    {"type": "text", "text": content,
                     "cache_control": {"type": "ephemeral"}},
                ],
            })
        else:
            out.append(m)
    return out


def ava_chat(
    messages: list[dict],
    model: str | None = None,
    stream: bool = False,
    max_tokens: int | None = None,
    temperature: float = 0.7,
    timeout: float = 60.0,
    cfg: Optional[AvaConfig] = None,
    **extra,
) -> dict | Iterator[dict]:
    """Send a chat-completion request through the AVA proxy.

    Returns the parsed JSON dict when `stream=False`.
    Returns an iterator over parsed SSE delta dicts when `stream=True`.

    Raises `AvaProxyError` on config / transport / auth problems so the caller
    can fall back to local Qwen if desired.
    """
    cfg = cfg or load_config()
    if not cfg:
        raise AvaProxyError("AVA config missing in ~/.codec/config.json")
    if not cfg.is_ready:
        raise AvaProxyError("AVA config present but incomplete "
                            "(enabled/proxy_url/license_key)")

    model = model or cfg.default_cloud_model

    # B7 / SR-30: Anthropic prompt-caching for Claude models. When the
    # caller routes a chat to Claude, mark the system message + (optional)
    # injected memory block as ephemeral cache breakpoints. The cache
    # block lifetimes Anthropic enforces are 5 minutes (default) — well
    # within a single chat session — and yield 50-75% input-token cost
    # savings on repeat turns of the same session (identity + memory
    # prelude is the largest reusable chunk).
    #
    # The proxy forwards `cache_control` as-is per the OpenAI-compatible
    # passthrough contract; non-Claude models that don't honor the field
    # simply ignore it.
    cache_messages = messages
    if model and "claude" in model.lower():
        cache_messages = _tag_messages_for_anthropic_cache(messages)
    payload: dict[str, Any] = {
        "model": model,
        "messages": cache_messages,
        "stream": stream,
        "temperature": temperature,
        **extra,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    headers = {
        "Authorization": f"Bearer {cfg.license_key}",
        "Content-Type": "application/json",
    }
    url = f"{cfg.proxy_url}/v1/chat/completions"

    t0 = time.monotonic()

    if not stream:
        r = requests.post(url, json=payload, headers=headers, timeout=timeout)
        if not r.ok:
            raise AvaProxyError(f"ava proxy {r.status_code}: {r.text[:500]}")
        data = r.json()
        log.info("ava non-stream %s %dms tokens=%s",
                 model, int((time.monotonic() - t0) * 1000),
                 data.get("usage", {}).get("total_tokens"))
        return data

    # Streaming — yield parsed deltas
    def _stream():
        with requests.post(url, json=payload, headers=headers, stream=True, timeout=timeout) as r:
            if not r.ok:
                raise AvaProxyError(f"ava proxy {r.status_code}: {r.text[:500]}")
            for line in r.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                try:
                    yield json.loads(data)
                except json.JSONDecodeError:
                    continue
    return _stream()


def ava_chat_simple(prompt: str, system: str | None = None, **kwargs) -> str:
    """Convenience wrapper: take a plain string prompt, return a plain string answer."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    data = ava_chat(messages, stream=False, **kwargs)
    return data["choices"][0]["message"]["content"]


# ── Model picker helpers for future dashboard UI ──

def list_cloud_models() -> list[dict[str, str]]:
    cfg = load_config()
    return cfg.available_cloud_models if cfg else []


def choose_model(user_preference: str | None) -> str:
    """Resolve user preference to a proxy model id. Falls back to default."""
    cfg = load_config()
    if not cfg:
        return "gemini-2.5-flash-lite"
    if user_preference:
        # Allow shorthand like "fast" / "pro" / "balanced"
        shortcuts = {
            "fast": "gemini-2.5-flash-lite",
            "cheap": "gemini-2.5-flash-lite",
            "balanced": "gemini-2.5-flash",
            "pro": "gemini-2.5-pro",
            "quality": "gemini-2.5-pro",
        }
        if user_preference.lower() in shortcuts:
            return shortcuts[user_preference.lower()]
        # exact model id
        valid = {m["id"] for m in cfg.available_cloud_models}
        if user_preference in valid:
            return user_preference
    return cfg.default_cloud_model


if __name__ == "__main__":
    # `python codec_ava_client.py` → quick smoke test
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    print("Loading AVA config…")
    cfg = load_config()
    if not cfg:
        print("❌ no ava block in ~/.codec/config.json"); raise SystemExit(1)
    print(f"  proxy: {cfg.proxy_url}")
    print(f"  license: {cfg.license_key[:30]}…")
    print(f"  default model: {cfg.default_cloud_model}")

    print("\nChecking license status…")
    status = verify_license(cfg)
    print(f"  → {status}")

    print("\nAsking Gemini a quick question…")
    try:
        answer = ava_chat_simple(
            "Hello CODEC. Confirm you're alive and name yourself in under 10 words.",
            max_tokens=40,
        )
        print(f"\n🟢 Gemini says:\n  {answer}")
    except AvaProxyError as e:
        print(f"❌ {e}")
