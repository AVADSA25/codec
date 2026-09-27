"""Cloud models the owner switches to by hand, e.g. when the local Qwen is down.

WHY THIS EXISTS (2026-09-27)
---------------------------
When the local model server is stopped (a memory-heavy job, a crash, a model
that will not load), CODEC has no model and every chat fails. A cloud model in
the model picker keeps it answering. The switch is MANUAL: nothing in this
module ever moves CODEC to the cloud on its own. docs/CLOUD-FALLBACK-MODEL-DESIGN.md

THE REGISTRY
------------
An `extra_models` entry in ~/.codec/config.json with a non-local `base_url` is
served by that endpoint instead of CODEC's MLX server:

    {"id": "mimo-v2.6-pro", "label": "MiMo V2.6 Pro (cloud)",
     "base_url": "https://api.xiaomimimo.com/v1", "key_slot": "mimo_api_key",
     "kwargs": {"thinking": {"type": "disabled"}},
     "price_in_per_m": 0.435, "price_out_per_m": 0.87, "monthly_cap_usd": 10}

- `key_slot` names a Keychain secret (ai.avadigital.codec.<slot>). The key is
  never on disk and never copied into `llm_api_key`, so switching back to the
  local model (codec_setup stores an empty `llm_api_key`) cannot delete it.
- The two prices are required. CODEC cannot cap a model it cannot meter, so an
  entry without them is refused. Use 0 for a free endpoint.
- `monthly_cap_usd` defaults to DEFAULT_MONTHLY_CAP_USD. 0 blocks the model.

THE CHECK BEFORE EVERY CLOUD CALL
---------------------------------
codec_llm asks `route()` about every non-local base_url, in the same place the
licence gate sits, in call / stream / acall / astream. For a registered cloud
model it sends the entry's key, model id and kwargs, drops
`chat_template_kwargs` (a local-server setting), asks streamed replies for
their token usage, and refuses the call once this month's spend has reached the
cap. Each reply's cost is added to ~/.codec/cloud_spend.json.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import re
import threading
import time
import urllib.parse
from typing import Any, Dict, Iterator, List, Optional, Tuple

from codec_jsonstore import atomic_write_json

log = logging.getLogger("codec.cloud_models")

CONFIG_PATH = os.path.expanduser("~/.codec/config.json")
SPEND_PATH = os.path.expanduser("~/.codec/cloud_spend.json")

DEFAULT_MONTHLY_CAP_USD = 10.0

# A cloud model that has not sent its first words after this long is stalled:
# MiMo's normal first words arrive in 1-5 s, and a stalled request can sit in
# its queue for minutes (measured 2026-09-27). `first_reply_timeout_s` on an
# entry overrides it.
FIRST_REPLY_S = 60.0

# Where a switch back to local points when `llm_local_restore` is missing.
DEFAULT_LOCAL_BASE_URL = "http://localhost:8083/v1"

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}

# The key is read with `security`, a subprocess. Cache hits only, so a key added
# after a miss is picked up on the next call.
_KEY_TTL = 60.0
_key_cache: Dict[str, Tuple[float, str]] = {}

_spend_lock = threading.Lock()


def _load_config() -> Dict[str, Any]:
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        return {}


def is_local_url(url: str) -> bool:
    """True when `url` points at this Mac (the MLX server, a local proxy)."""
    try:
        host = (urllib.parse.urlparse(url or "").hostname or "").lower()
    except ValueError:
        return False
    return host in _LOCAL_HOSTS


def _norm(url: Any) -> str:
    return url.strip().rstrip("/") if isinstance(url, str) else ""


# ── The registry ─────────────────────────────────────────────────────────────

def entries(cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Registered cloud models: `extra_models` entries with a non-local base_url."""
    c = cfg if cfg is not None else _load_config()
    raw = c.get("extra_models")
    out: List[Dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        mid, base = item.get("id"), _norm(item.get("base_url"))
        if isinstance(mid, str) and mid and base and not is_local_url(base):
            out.append(item)
    return out


def entry_for_id(model_id: Any, cfg: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    for e in entries(cfg):
        if e["id"] == model_id:
            return e
    return None


def _entry_for_url(base_url: str, model: str, cfg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The entry served at `base_url`. If several share it, the one named `model`."""
    b = _norm(base_url)
    same = [e for e in entries(cfg) if _norm(e["base_url"]) == b]
    for e in same:
        if e["id"] == model:
            return e
    return same[0] if same else None


def active_entry(cfg: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """The cloud model CODEC is switched to right now, or None.

    Both `llm_model` and `llm_base_url` must point at the entry. A matching
    `llm_model` with a local base_url is not a cloud switch.
    """
    c = cfg if cfg is not None else _load_config()
    e = entry_for_id(c.get("llm_model"), c)
    if e is not None and _norm(c.get("llm_base_url")) == _norm(e["base_url"]):
        return e
    return None


def local_only(base_url: str, model: str,
               cfg: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
    """(base_url, model), with a registered cloud model replaced by the local
    model it stands in for.

    For callers that must never leave the Mac, even while the owner has
    switched chat to the cloud: the Project-mode planner and runner (Step 8 Q1,
    "no cloud fallback"). Anything that is not a registered cloud model (the
    local server, a LAN Mac, an AVA or custom provider) comes back unchanged.
    """
    if is_local_url(base_url):
        return base_url, model
    c = cfg if cfg is not None else _load_config()
    if _entry_for_url(base_url, model, c) is None:
        return base_url, model
    restore = c.get("llm_local_restore")
    restore = restore if isinstance(restore, dict) else {}
    local_model = restore.get("llm_model")
    if not isinstance(local_model, str) or not local_model:
        import codec_models
        local_model = codec_models.DEFAULT_MODEL
    return (_norm(restore.get("llm_base_url")) or DEFAULT_LOCAL_BASE_URL), local_model


# ── Key, prices, cap ─────────────────────────────────────────────────────────

def key_slot(entry: Dict[str, Any]) -> str:
    slot = entry.get("key_slot")
    return slot if isinstance(slot, str) else ""


def get_key(entry: Dict[str, Any]) -> str:
    slot = key_slot(entry)
    if not slot:
        return ""
    now = time.monotonic()
    hit = _key_cache.get(slot)
    if hit is not None and now - hit[0] < _KEY_TTL:
        return hit[1]
    try:
        from codec_keychain import keychain_get
        val = keychain_get(slot) or ""
    except Exception:
        val = ""
    if val:
        _key_cache[slot] = (now, val)
    return val


def _number(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
        return None
    return float(v)


def prices(entry: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    """(USD per million input tokens, USD per million output tokens), or None."""
    p_in, p_out = _number(entry.get("price_in_per_m")), _number(entry.get("price_out_per_m"))
    if p_in is None or p_out is None:
        return None
    return p_in, p_out


def cap_usd(entry: Dict[str, Any]) -> float:
    cap = _number(entry.get("monthly_cap_usd", DEFAULT_MONTHLY_CAP_USD))
    return DEFAULT_MONTHLY_CAP_USD if cap is None else cap


# ── The spend ledger ─────────────────────────────────────────────────────────
#
# {"2026-09": {"mimo-v2.6-pro": {"usd": 0.0421, "prompt_tokens": 81234,
#   "completion_tokens": 7310, "calls": 57, "estimated_calls": 1}}}
#
# Several PM2 processes (dashboard, telegram, agents) can write it, so updates
# hold an flock on a sibling .lock file, not only a thread lock.

def _month(now: Optional[float] = None) -> str:
    return time.strftime("%Y-%m", time.localtime(now))


def _read_spend() -> Dict[str, Any]:
    try:
        with open(SPEND_PATH) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


@contextlib.contextmanager
def _ledger_lock() -> Iterator[None]:
    os.makedirs(os.path.dirname(SPEND_PATH) or ".", exist_ok=True)
    fd = os.open(SPEND_PATH + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def spent_usd(model_id: str, month: Optional[str] = None) -> float:
    rec = (_read_spend().get(month or _month()) or {}).get(model_id)
    if not isinstance(rec, dict):
        return 0.0
    return _number(rec.get("usd")) or 0.0


def record(entry: Dict[str, Any], usage: Any, *, estimated: bool = False) -> float:
    """Add one reply's cost to this month's ledger. Returns the cost in USD.

    Never raises: a ledger that cannot be written must not lose the reply the
    owner already paid for. Cached input tokens are charged at the full input
    price, so the ledger errs high, never low.
    """
    pr = prices(entry)
    if pr is None or not isinstance(usage, dict):
        return 0.0
    try:
        p_tok = max(0, int(usage.get("prompt_tokens") or 0))
        c_tok = max(0, int(usage.get("completion_tokens") or 0))
    except (TypeError, ValueError):
        return 0.0
    if not p_tok and not c_tok:
        return 0.0
    cost = p_tok * pr[0] / 1e6 + c_tok * pr[1] / 1e6
    month, mid, cap = _month(), entry["id"], cap_usd(entry)
    try:
        with _spend_lock, _ledger_lock():
            data = _read_spend()
            by_model = data.get(month) if isinstance(data.get(month), dict) else {}
            rec = by_model.get(mid) if isinstance(by_model.get(mid), dict) else {}
            before = _number(rec.get("usd")) or 0.0
            rec["usd"] = round(before + cost, 8)
            rec["prompt_tokens"] = int(rec.get("prompt_tokens") or 0) + p_tok
            rec["completion_tokens"] = int(rec.get("completion_tokens") or 0) + c_tok
            rec["calls"] = int(rec.get("calls") or 0) + 1
            if estimated:
                rec["estimated_calls"] = int(rec.get("estimated_calls") or 0) + 1
            by_model[mid] = rec
            data[month] = by_model
            atomic_write_json(SPEND_PATH, data)
    except Exception as e:
        log.warning("cloud spend ledger update failed: %s", e)
        return cost
    if before < cap <= rec["usd"]:
        _audit_cap_reached(entry, month, rec["usd"], cap)
    return cost


def estimate_usage(messages: Any, output_text: str) -> Dict[str, int]:
    """About 4 characters per token. Used only when a stream ended without the
    provider's usage chunk. It counts the JSON framing too, so it errs high."""
    try:
        prompt_chars = len(json.dumps(messages, ensure_ascii=False))
    except (TypeError, ValueError):
        prompt_chars = sum(len(str(m)) for m in (messages or []))
    return {"prompt_tokens": max(1, prompt_chars // 4),
            "completion_tokens": len(output_text or "") // 4}


def _audit_cap_reached(entry: Dict[str, Any], month: str, spent: float, cap: float) -> None:
    try:
        from codec_audit import audit
        audit(event="cloud_spend_cap_reached", source="codec-cloud-models",
              outcome="warning", level="warning",
              message=f"{entry['id']} reached its monthly cap (${spent:.2f} of ${cap:.2f})",
              extra={"model": entry["id"], "month": month,
                     "spent_usd": round(spent, 4), "cap_usd": cap})
    except Exception:
        pass


# ── The check before each call ───────────────────────────────────────────────

def block_message(entry: Dict[str, Any]) -> Optional[str]:
    """Why a call to this cloud model must not be sent, or None."""
    label = entry.get("label") or entry["id"]
    if prices(entry) is None:
        return (f"{label} has no price set, so CODEC cannot cap its spend. Add "
                f"price_in_per_m and price_out_per_m to its extra_models entry "
                f"(0 for a free endpoint).")
    cap, spent = cap_usd(entry), spent_usd(entry["id"])
    if spent >= cap:
        return (f"{label}: monthly spend cap reached (${spent:.2f} of ${cap:.2f} "
                f"this month). Switch back to a local model in the model picker, "
                f"or raise monthly_cap_usd.")
    if not get_key(entry):
        return (f"{label}: no API key found in the Keychain "
                f"(ai.avadigital.codec.{key_slot(entry) or '<key_slot>'}).")
    return None


class Route:
    """How codec_llm sends one call to a registered cloud model."""

    __slots__ = ("entry", "model", "api_key", "blocked", "first_reply_s")

    def __init__(self, entry: Dict[str, Any], blocked: Optional[str]):
        self.entry = entry
        self.model = entry["id"]
        self.api_key = "" if blocked else get_key(entry)
        self.blocked = blocked
        self.first_reply_s = _number(entry.get("first_reply_timeout_s")) or FIRST_REPLY_S

    def kwargs(self, extra: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        merged = {k: v for k, v in (extra or {}).items() if k != "chat_template_kwargs"}
        own = self.entry.get("kwargs")
        if isinstance(own, dict):
            merged.update(own)
        return merged

    def shape(self, payload: Dict[str, Any], *, stream: bool) -> None:
        """Final edits to the request body codec_llm built."""
        payload.pop("chat_template_kwargs", None)
        if stream:
            payload["stream_options"] = {"include_usage": True}

    def record(self, usage: Any, messages: Any = None, output_text: str = "") -> float:
        """Charge one reply: the provider's usage, or an estimate when it sent none."""
        if isinstance(usage, dict) and (usage.get("prompt_tokens") or usage.get("completion_tokens")):
            return record(self.entry, usage)
        return record(self.entry, estimate_usage(messages, output_text), estimated=True)


def route(base_url: str, model: str,
          cfg: Optional[Dict[str, Any]] = None) -> Optional[Route]:
    """The registered cloud model served at `base_url`, or None when there is
    none (then codec_llm sends the request unchanged)."""
    if is_local_url(base_url):
        return None
    c = cfg if cfg is not None else _load_config()
    e = _entry_for_url(base_url, model, c)
    if e is None:
        return None
    return Route(e, block_message(e))


# Failures that waiting cannot fix: the call was refused before it was sent
# (cap, key, price), or the provider rejected the request itself (4xx but 429).
_DEFINITIVE = re.compile(r"monthly spend cap reached|no API key|no price set|(?:returned|HTTP) 4(?!29)\d\d")


def is_transient_failure(detail: str) -> bool:
    """True when a failed call may succeed later: slow, overloaded, unreachable."""
    return not _DEFINITIVE.search(detail or "")


def probe(entry: Dict[str, Any], timeout: float = 30.0) -> Tuple[bool, str]:
    """One tiny real request, so a switch is kept only when the model answers."""
    import codec_llm
    t0 = time.time()
    try:
        codec_llm.call([{"role": "user", "content": "Reply with the single word: READY"}],
                       base_url=entry["base_url"], model=entry["id"],
                       max_tokens=8, temperature=0, timeout=timeout,
                       retries=1, raise_on_error=True)
    except codec_llm.LLMError as e:
        return False, str(e)[:240]
    return True, f"answered in {time.time() - t0:.1f}s"
