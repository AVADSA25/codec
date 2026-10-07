"""Web Push for CODEC (UI phase 3, P3.13; docs/P3.13-DESIGN.md).

CODEC's own channel to the owner's phone for approvals, questions, the briefing
and agent results (owner decision 2026-09-29: no outside chat app). A push
carries no content: the payload is a fixed line per kind from KINDS, and
callers pass only the kind, so no question text, agent name or chat text can
reach a push service. The page shows the detail.

- VAPID (RFC 8292): a P-256 key pair. The private key lives in the Keychain as
  ``ai.avadigital.codec.vapid_private_key``; the dashboard creates it once
  (``ensure_key``), every other process only reads it.
- Payload encryption (RFC 8291, ``aes128gcm``) with the ``cryptography``
  package the repo already depends on.
- Devices and switches in ``~/.codec/push.json`` (0600, atomic write,
  cross-process lock). Endpoints must be https on a known push service, so a
  stored endpoint cannot make the Mac post anywhere else; redirects are never
  followed; a device the push service reports gone (404/410) is removed.
- ``notify(kind)`` is the fan-out. It never raises and never blocks the caller:
  the sends run on a daemon thread, at most one push per kind per
  THROTTLE_SECONDS per process.

Kill switch: ``PUSH_ENABLED=false`` stops all sending.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import struct
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import codec_jsonstore

log = logging.getLogger("codec_push")

STATE_PATH = Path(os.path.expanduser("~/.codec/push.json"))
KEYCHAIN_KEY = "vapid_private_key"
MAX_DEVICES = 10
THROTTLE_SECONDS = 60
SEND_TIMEOUT = 10
JWT_TTL = 12 * 3600
RECORD_SIZE = 4096
DEFAULT_SUBJECT = "https://github.com/AVADSA25/codec"

# The Settings switches, in display order.
SWITCHES = ("approvals", "questions", "briefing", "agents")

# kind -> switch (None = always sent), title, body, url, TTL seconds, urgency.
# These fixed strings are the ONLY text a push ever carries.
KINDS: Dict[str, Tuple[Optional[str], str, str, str, int, str]] = {
    "approval": ("approvals", "CODEC needs your approval", "Tap to see what it wants to do.", "/#inbox", 600, "high"),
    "question": ("questions", "CODEC has a question for you", "Tap to answer.", "/#inbox", 600, "high"),
    "briefing": ("briefing", "Your briefing is ready", "Tap to read it.", "/", 4 * 3600, "normal"),
    "agent_done": ("agents", "An agent finished its work", "Tap to see the result.", "/#inbox=agents",
                   86400, "normal"),
    "agent_blocked": ("agents", "An agent needs your go-ahead", "Tap to see what it needs.", "/#inbox",
                      86400, "high"),
    "agent_stopped": ("agents", "An agent stopped", "Tap to see why.", "/#inbox=agents", 86400, "normal"),
    "test": (None, "Notifications are on", "CODEC can reach this device.", "/", 300, "normal"),
}

# Push services the Mac may post to: exact hosts and domain suffixes.
_PUSH_HOSTS = frozenset({"fcm.googleapis.com"})
_PUSH_SUFFIXES = (".push.services.mozilla.com", ".push.apple.com", ".notify.windows.com")

_LOCK = threading.Lock()
_LAST_SENT: Dict[str, float] = {}
_KEY_CACHE: Dict[str, Any] = {"key": None, "at": 0.0}
_KEY_TTL = 300.0


# ── small helpers ─────────────────────────────────────────────────────────────
def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64u(text: str) -> bytes:
    text = (text or "").strip()
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def enabled() -> bool:
    return os.environ.get("PUSH_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")


def _audit(event: str, message: str, extra: Dict[str, Any], level: str = "info") -> None:
    try:
        from codec_audit import log_event
        log_event(event, "codec-push", message, extra=extra, level=level,
                  outcome="ok" if level == "info" else "warning")
    except Exception:
        pass


# ── VAPID key ─────────────────────────────────────────────────────────────────
def _keychain_get() -> Optional[str]:
    import codec_keychain
    return codec_keychain.keychain_get(KEYCHAIN_KEY)


def _keychain_set(value: str) -> bool:
    import codec_keychain
    return codec_keychain.keychain_set(KEYCHAIN_KEY, value)


def _key_from_secret(secret: str):
    from cryptography.hazmat.primitives.asymmetric import ec
    raw = _unb64u(secret)
    if len(raw) != 32:
        raise ValueError("VAPID private key must be 32 bytes")
    return ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())


def _point(public_key) -> bytes:
    from cryptography.hazmat.primitives import serialization
    return public_key.public_bytes(serialization.Encoding.X962,
                                   serialization.PublicFormat.UncompressedPoint)


def _private_key():
    """The VAPID private key, or None when it does not exist yet. Hits are
    cached for _KEY_TTL; a miss is not cached here (the Keychain layer has its
    own short negative cache)."""
    with _LOCK:
        key, at = _KEY_CACHE["key"], _KEY_CACHE["at"]
        if key is not None and time.monotonic() - at < _KEY_TTL:
            return key
    secret = _keychain_get()
    if not secret:
        return None
    try:
        key = _key_from_secret(secret)
    except Exception as e:
        log.warning("[push] stored VAPID key unreadable: %s", type(e).__name__)
        return None
    with _LOCK:
        _KEY_CACHE["key"], _KEY_CACHE["at"] = key, time.monotonic()
    return key


def public_key() -> Optional[str]:
    """The VAPID public key (base64url uncompressed point) or None."""
    key = _private_key()
    return _b64u(_point(key.public_key())) if key is not None else None


def ensure_key() -> Optional[str]:
    """Create the VAPID key pair once, then return the public key. Only the
    dashboard calls this; the file lock keeps two creators from racing."""
    existing = public_key()
    if existing:
        return existing
    from cryptography.hazmat.primitives.asymmetric import ec
    with codec_jsonstore.file_lock(STATE_PATH):
        existing = public_key()
        if existing:
            return existing
        key = ec.generate_private_key(ec.SECP256R1())
        secret = _b64u(key.private_numbers().private_value.to_bytes(32, "big"))
        if not _keychain_set(secret):
            log.warning("[push] could not store the VAPID key")
            return None
        with _LOCK:
            _KEY_CACHE["key"], _KEY_CACHE["at"] = key, time.monotonic()
        return _b64u(_point(key.public_key()))


def _subject() -> str:
    try:
        cfg = json.loads(Path(os.path.expanduser("~/.codec/config.json")).read_text())
        sub = str(((cfg.get("push") or {}).get("subject")) or "").strip()
        if sub.startswith(("mailto:", "https://")):
            return sub
    except Exception:
        pass
    return DEFAULT_SUBJECT


def vapid_header(endpoint: str, key=None, *, now: Optional[float] = None) -> str:
    """RFC 8292 Authorization header value for one push service origin."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    key = key or _private_key()
    if key is None:
        raise RuntimeError("no VAPID key")
    parts = urlsplit(endpoint)
    claims = {"aud": f"{parts.scheme}://{parts.netloc}",
              "exp": int((now if now is not None else time.time()) + JWT_TTL),
              "sub": _subject()}
    signing_input = (_b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
                     + "." + _b64u(json.dumps(claims, separators=(",", ":")).encode()))
    r, s = decode_dss_signature(key.sign(signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256())))
    token = signing_input + "." + _b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={token}, k={_b64u(_point(key.public_key()))}"


# ── RFC 8291 payload encryption ───────────────────────────────────────────────
def _hkdf_one(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:length]


def encrypt(plaintext: bytes, p256dh: str, auth: str, *, salt: Optional[bytes] = None,
            server_key=None) -> bytes:
    """Encrypt one push message body (aes128gcm, a single record). `salt` and
    `server_key` are for the RFC test vector; normal calls leave them random."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    ua_public = _unb64u(p256dh)
    auth_secret = _unb64u(auth)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    server_key = server_key or ec.generate_private_key(ec.SECP256R1())
    as_public = _point(server_key.public_key())
    salt = salt or os.urandom(16)
    ecdh_secret = server_key.exchange(ec.ECDH(), ua_key)
    ikm = _hkdf_one(auth_secret, ecdh_secret, b"WebPush: info\x00" + ua_public + as_public, 32)
    cek = _hkdf_one(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf_one(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    record = AESGCM(cek).encrypt(nonce, plaintext + b"\x02", None)
    if len(record) + 86 > RECORD_SIZE:
        raise ValueError("payload too large for one record")
    return salt + struct.pack("!IB", RECORD_SIZE, len(as_public)) + as_public + record


# ── devices and switches ──────────────────────────────────────────────────────
def endpoint_allowed(endpoint: str) -> bool:
    try:
        parts = urlsplit(endpoint)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or parts.username or parts.password or not host:
        return False
    if parts.port not in (None, 443):
        return False
    return host in _PUSH_HOSTS or any(host.endswith(s) for s in _PUSH_SUFFIXES)


def device_id(endpoint: str) -> str:
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()[:12]


def label_from_agent(user_agent: str) -> str:
    ua = user_agent or ""
    if "iPhone" in ua:
        place = "iPhone"
    elif "iPad" in ua:
        place = "iPad"
    elif "Android" in ua:
        place = "Android"
    elif "Macintosh" in ua or "Mac OS X" in ua:
        place = "Mac"
    elif "Windows" in ua:
        place = "Windows"
    elif "Linux" in ua:
        place = "Linux"
    else:
        place = "Device"
    if "Edg/" in ua:
        browser = "Edge"
    elif "Firefox/" in ua or "FxiOS/" in ua:
        browser = "Firefox"
    elif "Chrome/" in ua or "CriOS/" in ua:
        browser = "Chrome"
    elif "Safari/" in ua:
        browser = "Safari"
    else:
        browser = "browser"
    return f"{place} {browser}"


def _empty_state() -> Dict[str, Any]:
    return {"schema": 1, "types": {s: True for s in SWITCHES}, "devices": []}


def _normalise(data: Any) -> Dict[str, Any]:
    state = _empty_state()
    if not isinstance(data, dict):
        return state
    types = data.get("types")
    if isinstance(types, dict):
        for s in SWITCHES:
            if isinstance(types.get(s), bool):
                state["types"][s] = types[s]
    for d in data.get("devices") or []:
        if (isinstance(d, dict) and isinstance(d.get("endpoint"), str) and isinstance(d.get("p256dh"), str)
                and isinstance(d.get("auth"), str)):
            state["devices"].append(d)
    return state


def read_state() -> Dict[str, Any]:
    try:
        return _normalise(json.loads(STATE_PATH.read_text()))
    except (FileNotFoundError, ValueError, OSError):
        return _empty_state()


def _update(mutate: Callable[[Dict[str, Any]], Any]) -> Dict[str, Any]:
    with codec_jsonstore.file_lock(STATE_PATH):
        state = read_state()
        mutate(state)
        codec_jsonstore.atomic_write_json(STATE_PATH, state)
        return state


def public_devices(state: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """The device list for the page: never the endpoint or the keys."""
    state = state or read_state()
    return [{"id": device_id(d["endpoint"]), "label": d.get("label") or "Device",
             "created": d.get("created"), "last_ok": d.get("last_ok")} for d in state["devices"]]


_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+={0,2}$")


def add_device(subscription: Dict[str, Any], user_agent: str = "") -> str:
    """Store (or refresh) one browser subscription. Raises ValueError when it
    is not a subscription CODEC will post to."""
    if not isinstance(subscription, dict):
        raise ValueError("subscription must be an object")
    endpoint = subscription.get("endpoint")
    keys = subscription.get("keys") or {}
    if not isinstance(endpoint, str) or len(endpoint) > 1024 or not endpoint_allowed(endpoint):
        raise ValueError("endpoint is not a known push service")
    p256dh, auth = keys.get("p256dh"), keys.get("auth")
    if not (isinstance(p256dh, str) and isinstance(auth, str) and _KEY_RE.match(p256dh) and _KEY_RE.match(auth)):
        raise ValueError("subscription keys missing")
    try:
        from cryptography.hazmat.primitives.asymmetric import ec
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), _unb64u(p256dh))
        if len(_unb64u(auth)) != 16:
            raise ValueError
    except Exception:
        raise ValueError("subscription keys invalid") from None
    label = label_from_agent(user_agent)

    def mutate(state):
        devices = [d for d in state["devices"] if d["endpoint"] != endpoint]
        devices.append({"endpoint": endpoint, "p256dh": p256dh, "auth": auth, "label": label,
                        "created": _now_iso(), "last_ok": None, "last_error": None})
        state["devices"] = devices[-MAX_DEVICES:]

    state = _update(mutate)
    _audit("push_subscribed", f"Push turned on for {label}", {"label": label, "devices": len(state["devices"])})
    return device_id(endpoint)


def remove_device(*, endpoint: Optional[str] = None, dev_id: Optional[str] = None) -> bool:
    removed: List[int] = []

    def mutate(state):
        keep = [d for d in state["devices"]
                if not (d["endpoint"] == endpoint or (dev_id and device_id(d["endpoint"]) == dev_id))]
        removed.append(len(state["devices"]) - len(keep))
        state["devices"] = keep

    state = _update(mutate)
    if removed and removed[0]:
        _audit("push_unsubscribed", "Push turned off for a device", {"devices": len(state["devices"])})
        return True
    return False


def set_types(changes: Dict[str, Any]) -> Dict[str, bool]:
    def mutate(state):
        for s in SWITCHES:
            if isinstance(changes.get(s), bool):
                state["types"][s] = changes[s]
    return dict(_update(mutate)["types"])


# ── sending ───────────────────────────────────────────────────────────────────
def payload(kind: str) -> Dict[str, str]:
    _switch, title, body, url, _ttl, _urgency = KINDS[kind]
    return {"kind": kind, "title": title, "body": body, "url": url}


def _post(endpoint: str, data: bytes, headers: Dict[str, str]) -> int:
    import requests
    return requests.post(endpoint, data=data, headers=headers, timeout=SEND_TIMEOUT,
                         allow_redirects=False).status_code


def send(kind: str) -> Dict[str, int]:
    """Send `kind` to every device now, if its switch is on. Returns counts."""
    result = {"devices": 0, "ok": 0, "failed": 0, "removed": 0}
    if kind not in KINDS or not enabled():
        return result
    state = read_state()
    switch = KINDS[kind][0]
    if switch is not None and not state["types"].get(switch, True):
        return result
    devices = state["devices"]
    if not devices:
        return result
    key = _private_key()
    if key is None:
        log.warning("[push] devices are subscribed but there is no VAPID key")
        return result
    _switch, _t, _b, _u, ttl, urgency = KINDS[kind]
    body = json.dumps(payload(kind), separators=(",", ":")).encode("utf-8")
    outcome: Dict[str, Tuple[str, Optional[str]]] = {}
    for d in devices:
        endpoint = d["endpoint"]
        result["devices"] += 1
        if not endpoint_allowed(endpoint):
            outcome[endpoint] = ("remove", None)
            continue
        try:
            status = _post(endpoint, encrypt(body, d["p256dh"], d["auth"]), {
                "Authorization": vapid_header(endpoint, key),
                "Content-Encoding": "aes128gcm",
                "Content-Type": "application/octet-stream",
                "TTL": str(ttl),
                "Urgency": urgency,
                "Topic": f"codec-{kind.replace('_', '-')}",
            })
        except Exception as e:
            outcome[endpoint] = ("error", type(e).__name__)
            continue
        if 200 <= status < 300:
            outcome[endpoint] = ("ok", None)
        elif status in (404, 410):
            outcome[endpoint] = ("remove", None)
        else:
            outcome[endpoint] = ("error", f"HTTP {status}")
    result["ok"] = sum(1 for v in outcome.values() if v[0] == "ok")
    result["removed"] = sum(1 for v in outcome.values() if v[0] == "remove")
    result["failed"] = result["devices"] - result["ok"] - result["removed"]

    def mutate(st):
        keep = []
        for d in st["devices"]:
            what, err = outcome.get(d["endpoint"], (None, None))
            if what == "remove":
                continue
            if what == "ok":
                d["last_ok"], d["last_error"] = _now_iso(), None
            elif what == "error":
                d["last_error"] = err
            keep.append(d)
        st["devices"] = keep

    try:
        _update(mutate)
    except Exception as e:
        log.warning("[push] could not record results: %s", e)
    _audit("push_sent", f"Push {kind}: {result['ok']} of {result['devices']} devices",
           {"kind": kind, **result}, level="info" if not result["failed"] else "warning")
    return result


def notify(kind: str) -> None:
    """The fan-out hook: send `kind` in the background. Never raises."""
    try:
        if kind not in KINDS or not enabled():
            return
        now = time.monotonic()
        with _LOCK:
            last = _LAST_SENT.get(kind)
            if last is not None and now - last < THROTTLE_SECONDS:
                return
            _LAST_SENT[kind] = now
        threading.Thread(target=_send_quietly, args=(kind,), name="codec-push", daemon=True).start()
    except Exception as e:
        log.debug("[push] notify(%s) failed: %s", kind, e)


def _send_quietly(kind: str) -> None:
    try:
        send(kind)
    except Exception as e:
        log.warning("[push] send(%s) failed: %s", kind, type(e).__name__)
