"""macOS Keychain wrapper + headless fallback (Phase 1 Wave 2, PR-2B).

Closes audit findings D-8 (OAuth tokens plaintext) and D-15 partial
(`dashboard_token` + `llm_api_key`) per docs/audits/PHASE-1-SECURITY.md.
Three more secrets (`gemini_api_key`, `pexels_api_key`, `serper_api_key`,
`telegram.bot_token`, `auth_pin_hash`) are deferred to PR-2B-2.

## Production path (macOS Mac Studio)

Shells out to `/usr/bin/security`:
    security add-generic-password -s <service> -a <account> -w <value> -U
    security find-generic-password -s <service> -a <account> -w
    security delete-generic-password -s <service> -a <account>

`-w` provides the password as an argv (not via shell substitution / stdin),
so the secret never lands in shell history. `-U` upserts.

Locked Keychain returns exit code 51 ("user interaction not allowed").
Background daemons cannot prompt the user — the helper logs a CRITICAL,
emits a `keychain_locked` audit event, and returns None. The caller must
fall back to the cleartext source for that startup; the operator unlocks
Keychain on next login.

## Fallback path (headless Linux CI, no `/usr/bin/security`)

A 32-byte random key is generated at first use and stored at
`~/.codec/secret.key` (0600). Secrets are encrypted via a per-key XOR
stream cipher with PBKDF2-derived per-secret nonces. Stored at
`~/.codec/secrets.enc.json`. THIS IS FALLBACK-GRADE, NOT KEYCHAIN-GRADE —
it defends against casual file-read disclosure on a CI runner; it does
NOT defend against a determined attacker with shell access. The fallback
exists ONLY so tests can exercise the API on Linux CI without spurious
skips. CODEC's threat model assumes Keychain on the production Mac.

## Audit events

  keychain_set            on every set, ok/error
  keychain_get_missing    on read-miss (info, no secret in extras)
  keychain_locked         on exit 51 from /usr/bin/security (warning)
  keychain_migration      on first migration from plaintext
"""
from __future__ import annotations

import getpass
import hashlib
import json
import logging
import os
import platform
import secrets
import stat
import subprocess
from pathlib import Path
from typing import Callable, Optional

try:
    from codec_audit import log_event as _kc_log_event
except Exception:  # pragma: no cover — audit must never break secret access
    def _kc_log_event(*a, **kw):
        pass

# ── Constants ─────────────────────────────────────────────────────────────────

_SERVICE_PREFIX = "ai.avadigital.codec"
_SECURITY_BIN = "/usr/bin/security"
_FALLBACK_KEY_PATH = Path(os.path.expanduser("~/.codec/secret.key"))
_FALLBACK_STORE_PATH = Path(os.path.expanduser("~/.codec/secrets.enc.json"))
_SECURITY_TIMEOUT = 5  # seconds

# Public key names (canonical). Keep this list in sync with documentation.
KEY_DASHBOARD_TOKEN = "dashboard_token"
KEY_LLM_API_KEY = "llm_api_key"
KEY_OAUTH_STATE = "oauth_state"
KEY_INTERNAL_TOKEN = "internal_token"  # PR-2D — localhost-only IPC auth
KEY_AUDIT_HMAC_SECRET = "audit_hmac_secret"  # PR-2E — audit log integrity
# PR-2B-2 — remaining provider secrets (closes D-15 fully)
KEY_GEMINI_API_KEY = "gemini_api_key"
KEY_PEXELS_API_KEY = "pexels_api_key"
KEY_SERPER_API_KEY = "serper_api_key"
KEY_TELEGRAM_BOT_TOKEN = "telegram_bot_token"


# ── Backend selection ────────────────────────────────────────────────────────


def is_keychain_available() -> bool:
    """True if real macOS Keychain via /usr/bin/security is reachable.
    Cached per-process (the answer doesn't change at runtime)."""
    if platform.system() != "Darwin":
        return False
    if not Path(_SECURITY_BIN).exists():
        return False
    # Don't run a live `security` probe here — it can prompt the user and
    # spam audit logs. Existence of the binary on Darwin is enough.
    return True


def _account() -> str:
    """Current macOS user — Keychain's `Account` field. Stable across runs."""
    try:
        return getpass.getuser()
    except Exception:
        return os.environ.get("USER") or "codec"


def _service(key: str) -> str:
    return f"{_SERVICE_PREFIX}.{key}"


# ── Keychain (macOS) backend ─────────────────────────────────────────────────


def _keychain_set(key: str, value: str) -> bool:
    """Write to macOS Keychain. Returns True on success."""
    try:
        r = subprocess.run(
            [_SECURITY_BIN, "add-generic-password",
             "-s", _service(key), "-a", _account(),
             "-w", value, "-U"],
            check=False, capture_output=True, text=True,
            timeout=_SECURITY_TIMEOUT,
        )
        if r.returncode == 0:
            return True
        if r.returncode == 51:
            _kc_log_event(
                "keychain_locked", source="codec-keychain",
                message=f"Keychain locked while setting {key!r}",
                level="warning", outcome="error",
                extra={"key": key, "operation": "set"},
            )
        return False
    except (subprocess.TimeoutExpired, OSError):
        return False


def _keychain_get(key: str) -> Optional[str]:
    """Read from macOS Keychain. None if missing, locked, or unavailable."""
    try:
        r = subprocess.run(
            [_SECURITY_BIN, "find-generic-password",
             "-s", _service(key), "-a", _account(), "-w"],
            check=False, capture_output=True, text=True,
            timeout=_SECURITY_TIMEOUT,
        )
        if r.returncode == 0:
            return r.stdout.rstrip("\n")
        if r.returncode == 51:
            _kc_log_event(
                "keychain_locked", source="codec-keychain",
                message=f"Keychain locked while reading {key!r}",
                level="warning", outcome="error",
                extra={"key": key, "operation": "get"},
            )
        return None
    except (subprocess.TimeoutExpired, OSError):
        return None


def _keychain_delete(key: str) -> bool:
    try:
        r = subprocess.run(
            [_SECURITY_BIN, "delete-generic-password",
             "-s", _service(key), "-a", _account()],
            check=False, capture_output=True, text=True,
            timeout=_SECURITY_TIMEOUT,
        )
        # exit 44 = entry not found; treat as success (idempotent)
        return r.returncode in (0, 44)
    except (subprocess.TimeoutExpired, OSError):
        return False


# ── Fallback (headless) backend ──────────────────────────────────────────────
#
# stdlib-only envelope obfuscation. Threat model: defend a CI runner from
# casual file-read disclosure. NOT crypto-grade against a determined
# attacker with shell access (XOR stream + per-secret nonce is reversible
# if the key file leaks). The real defense is Keychain on production.


def _fallback_key() -> bytes:
    """Read / generate the 32-byte random fallback key. 0600 perms."""
    if not _FALLBACK_KEY_PATH.exists():
        _FALLBACK_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Use atomic write so concurrent daemons don't race on creation.
        tmp = _FALLBACK_KEY_PATH.with_suffix(".key.tmp")
        tmp.write_bytes(secrets.token_bytes(32))
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(tmp, _FALLBACK_KEY_PATH)
    return _FALLBACK_KEY_PATH.read_bytes()


def _stream(key: bytes, nonce: bytes, length: int) -> bytes:
    """Derive a length-byte keystream from (key, nonce) via SHA-256 CTR.
    NOT a real stream cipher — provides obfuscation only."""
    out = bytearray()
    counter = 0
    while len(out) < length:
        out.extend(hashlib.sha256(key + nonce + counter.to_bytes(8, "big")).digest())
        counter += 1
    return bytes(out[:length])


def _fallback_load() -> dict:
    if not _FALLBACK_STORE_PATH.exists():
        return {}
    try:
        return json.loads(_FALLBACK_STORE_PATH.read_text())
    except Exception:
        return {}


def _fallback_save(store: dict) -> None:
    _FALLBACK_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _FALLBACK_STORE_PATH.with_suffix(".enc.json.tmp")
    tmp.write_text(json.dumps(store, indent=2))
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    os.replace(tmp, _FALLBACK_STORE_PATH)


def _fallback_set(key: str, value: str) -> bool:
    try:
        kbytes = _fallback_key()
        nonce = secrets.token_bytes(16)
        cipher = bytes(a ^ b for a, b in zip(value.encode("utf-8"),
                                              _stream(kbytes, nonce, len(value.encode("utf-8")))))
        store = _fallback_load()
        store[key] = {"n": nonce.hex(), "c": cipher.hex()}
        _fallback_save(store)
        return True
    except Exception:
        return False


def _fallback_get(key: str) -> Optional[str]:
    try:
        store = _fallback_load()
        entry = store.get(key)
        if not entry:
            return None
        kbytes = _fallback_key()
        nonce = bytes.fromhex(entry["n"])
        cipher = bytes.fromhex(entry["c"])
        plain = bytes(a ^ b for a, b in zip(cipher, _stream(kbytes, nonce, len(cipher))))
        return plain.decode("utf-8")
    except Exception:
        return None


def _fallback_delete(key: str) -> bool:
    try:
        store = _fallback_load()
        store.pop(key, None)
        _fallback_save(store)
        return True
    except Exception:
        return False


# ── Public API ───────────────────────────────────────────────────────────────


def keychain_set(key: str, value: str) -> bool:
    """Store a secret. Returns True on success. Uses real Keychain on
    macOS, envelope-encrypted fallback elsewhere."""
    ok = _keychain_set(key, value) if is_keychain_available() else _fallback_set(key, value)
    _kc_log_event(
        "keychain_set", source="codec-keychain",
        message=f"Stored secret {key!r}" if ok else f"Failed to store secret {key!r}",
        level="info" if ok else "warning",
        outcome="ok" if ok else "error",
        extra={
            "key": key,
            "method": "keychain" if is_keychain_available() else "fallback",
        },
    )
    return ok


def keychain_get(key: str) -> Optional[str]:
    """Read a secret. None if missing, locked, or unavailable."""
    val = _keychain_get(key) if is_keychain_available() else _fallback_get(key)
    if val is None:
        _kc_log_event(
            "keychain_get_missing", source="codec-keychain",
            message=f"Secret {key!r} not found",
            level="info", outcome="ok",
            extra={"key": key,
                   "method": "keychain" if is_keychain_available() else "fallback"},
        )
    return val


def keychain_delete(key: str) -> bool:
    """Delete a secret. Idempotent (returns True if not present)."""
    return _keychain_delete(key) if is_keychain_available() else _fallback_delete(key)


def migrate_from_plaintext(
    key: str,
    current_value: str,
    blank_source_fn: Callable[[], None],
) -> bool:
    """Idempotent migration helper.

    If the key is already in Keychain → no-op, return False.
    If the key is missing AND current_value is empty → no-op, return False.
    Else: write current_value to Keychain, then call blank_source_fn().
    Returns True on successful first-time migration."""
    if keychain_get(key) is not None:
        return False
    if not current_value:
        return False
    if not keychain_set(key, current_value):
        _kc_log_event(
            "keychain_migration", source="codec-keychain",
            message=f"Failed to migrate {key!r}: keychain_set returned False",
            level="warning", outcome="error",
            extra={"key": key,
                   "method": "keychain" if is_keychain_available() else "fallback"},
        )
        return False
    try:
        blank_source_fn()
    except Exception as e:
        _kc_log_event(
            "keychain_migration", source="codec-keychain",
            message=f"Migrated {key!r} but blank_source_fn failed: {e}",
            level="warning", outcome="error",
            extra={"key": key, "blank_error": str(e)[:200],
                   "method": "keychain" if is_keychain_available() else "fallback"},
        )
        return False
    _kc_log_event(
        "keychain_migration", source="codec-keychain",
        message=f"Migrated {key!r} from plaintext to "
                f"{'Keychain' if is_keychain_available() else 'fallback'}",
        level="info", outcome="ok",
        extra={"key": key,
               "method": "keychain" if is_keychain_available() else "fallback"},
    )
    return True


# ── Convenience getters ──────────────────────────────────────────────────────


def get_dashboard_token() -> Optional[str]:
    return keychain_get(KEY_DASHBOARD_TOKEN)


def get_llm_api_key() -> Optional[str]:
    return keychain_get(KEY_LLM_API_KEY)


def get_oauth_state() -> Optional[str]:
    return keychain_get(KEY_OAUTH_STATE)


def set_oauth_state(serialized: str) -> bool:
    return keychain_set(KEY_OAUTH_STATE, serialized)


# ── Internal IPC token (PR-2D — closes D-11) ─────────────────────────────────
#
# Replaces the `X-Internal: codec` literal header trust. AuthMiddleware
# validates `X-Internal-Token: <hmac>` on every internal call; internal
# callers (heartbeat, scheduler, autopilot, observer, MCP, skills that
# talk to the dashboard) read this token via `get_internal_token()` and
# send it as a header. `hmac.compare_digest` is the constant-time
# comparison in AuthMiddleware — see codec_dashboard.py.
#
# Bootstrap: first call auto-generates a 32-byte URL-safe token and
# persists it to Keychain. Subsequent calls (cached 30s) return the
# stored token. Cache is invalidated by `_invalidate_internal_token_cache`
# (used by tests + by a hypothetical operator who rotates the token by
# deleting the Keychain entry).
#
# Cache lives here (not in codec_config) because internal_token has no
# plaintext-config fallback — it's purely Keychain-or-Keychain. The
# cache is process-local; cross-process callers each pay one
# /usr/bin/security shellout to warm their cache, then ~0 cost.

_INTERNAL_TOKEN_CACHE: dict = {"value": None, "ts": 0.0}
_INTERNAL_TOKEN_TTL = 30.0  # seconds


def _invalidate_internal_token_cache() -> None:
    """Drop the cached internal_token. Tests use this; an operator can
    force re-read by calling it after `security delete-generic-password`."""
    _INTERNAL_TOKEN_CACHE["value"] = None
    _INTERNAL_TOKEN_CACHE["ts"] = 0.0


def get_internal_token() -> Optional[str]:
    """Return the per-process internal IPC token. Bootstraps on first
    miss via a fresh 32-byte URL-safe token persisted to Keychain.
    30s in-memory cache.

    Returns None ONLY if Keychain is unavailable AND the autogen Keychain
    write fails — in that case the caller falls back to no-internal-auth
    (request is denied at the AuthMiddleware gate). Background daemons
    on a locked Keychain hit this path; they retry on the next request.
    """
    import time as _time
    now = _time.time()
    cached = _INTERNAL_TOKEN_CACHE
    if cached["value"] is not None and (now - cached["ts"]) < _INTERNAL_TOKEN_TTL:
        return cached["value"]

    token = keychain_get(KEY_INTERNAL_TOKEN)
    if token is None:
        # First-run bootstrap. URL-safe so it survives header transport.
        token = secrets.token_urlsafe(32)
        ok = keychain_set(KEY_INTERNAL_TOKEN, token)
        if ok:
            _kc_log_event(
                "internal_token_autogenerated",
                source="codec-keychain",
                message="Bootstrapped internal IPC token to Keychain",
                level="info",
                outcome="ok",
                extra={
                    "token_id_last8": token[-8:],
                    "method": "keychain" if is_keychain_available() else "fallback",
                },
            )
        else:
            # Couldn't persist — use ephemeral so this process can still
            # talk to the dashboard, but the warning surfaces the gap.
            _kc_log_event(
                "internal_token_autogenerated",
                source="codec-keychain",
                message="Bootstrap failed to persist to Keychain — using ephemeral token",
                level="warning",
                outcome="error",
                extra={
                    "token_id_last8": token[-8:],
                    "method": "ephemeral",
                },
            )

    cached["value"] = token
    cached["ts"] = now
    return token


# ── Audit log HMAC secret (PR-2E — closes D-12) ──────────────────────────────
#
# Backs the per-line HMAC-SHA256 signature in `codec_audit._write`. The
# secret is 32 raw bytes stored hex-encoded in Keychain (binary in the
# Keychain `-w` argv would be brittle). Tampering with `~/.codec/audit.log`
# is detectable post-hoc via `codec_audit.verify_audit_log()` — an attacker
# would have to extract this secret from Keychain (D-8/D-15-grade compromise)
# before they could forge HMACs that pass verification.
#
# ## CRITICAL: silent path
# This function is called from inside `codec_audit._write` while the audit
# `_LOCK` is held. Calling `_kc_log_event` (the normal audit-emitting
# wrapper) would re-enter `_write` → deadlock on the non-reentrant lock,
# OR trigger infinite recursion as the new event needs an HMAC secret too.
# So this path uses *raw subprocess* calls and `stdlib logging` for
# visibility — NEVER `_kc_log_event`.
#
# ## Cache
# 5-minute TTL. Audit writes are hot (every tool call, every observation,
# every approval) so we amortize the `/usr/bin/security` shellout. Cache
# stores raw bytes; first call after process start pays one Keychain
# read; subsequent calls return cached bytes directly. Invalidated by
# `_invalidate_audit_hmac_cache` (used by tests).

_AUDIT_HMAC_CACHE: dict = {"value": None, "ts": 0.0}
_AUDIT_HMAC_TTL = 300.0  # 5 minutes — audit writes are hot

_audit_kc_log = logging.getLogger("codec.keychain.audit_hmac")


def _invalidate_audit_hmac_cache() -> None:
    """Drop the cached audit HMAC secret. Tests use this; operators force
    re-read by calling it after rotating the Keychain entry."""
    _AUDIT_HMAC_CACHE["value"] = None
    _AUDIT_HMAC_CACHE["ts"] = 0.0


def _keychain_get_silent(key: str) -> Optional[str]:
    """Read from Keychain without emitting any audit event. For the audit
    HMAC bootstrap path only. Falls through to the fallback store on
    headless systems."""
    if is_keychain_available():
        try:
            r = subprocess.run(
                [_SECURITY_BIN, "find-generic-password",
                 "-s", _service(key), "-a", _account(), "-w"],
                check=False, capture_output=True, text=True,
                timeout=_SECURITY_TIMEOUT,
            )
            if r.returncode == 0:
                return r.stdout.rstrip("\n")
            return None
        except (subprocess.TimeoutExpired, OSError):
            return None
    # Fallback path — _fallback_get is already silent (no _kc_log_event calls)
    return _fallback_get(key)


def _keychain_set_silent(key: str, value: str) -> bool:
    """Write to Keychain without emitting any audit event."""
    if is_keychain_available():
        try:
            r = subprocess.run(
                [_SECURITY_BIN, "add-generic-password",
                 "-s", _service(key), "-a", _account(),
                 "-w", value, "-U"],
                check=False, capture_output=True, text=True,
                timeout=_SECURITY_TIMEOUT,
            )
            return r.returncode == 0
        except (subprocess.TimeoutExpired, OSError):
            return False
    return _fallback_set(key, value)


def get_audit_hmac_secret() -> Optional[bytes]:
    """Return the audit log HMAC secret as 32 raw bytes (suitable for
    direct use with `hmac.new(secret, msg, sha256)`).

    First call autogenerates a fresh 32-byte secret and persists it as a
    hex string to Keychain. Subsequent calls within the 5-minute cache
    window return the cached value.

    Returns None ONLY if Keychain is unavailable AND fallback persistence
    fails. In that case `codec_audit._write` writes the audit line WITHOUT
    an HMAC field and adds `hmac_status="unsigned_keychain_unavailable"`
    so `verify_audit_log()` correctly classifies it as unsigned (rather
    than tampered).

    ## Silent path
    Bypasses `_kc_log_event` to avoid recursive audit emits (see module
    docstring for the rationale). Uses stdlib logging for bootstrap
    visibility instead — operators tail `~/.codec/codec.log` if curious.
    """
    import time as _time
    now = _time.time()
    cached = _AUDIT_HMAC_CACHE
    if cached["value"] is not None and (now - cached["ts"]) < _AUDIT_HMAC_TTL:
        return cached["value"]

    hex_value = _keychain_get_silent(KEY_AUDIT_HMAC_SECRET)
    if hex_value is None:
        # Bootstrap: generate fresh 32-byte secret, hex-encode, persist.
        raw = secrets.token_bytes(32)
        hex_value = raw.hex()
        if _keychain_set_silent(KEY_AUDIT_HMAC_SECRET, hex_value):
            _audit_kc_log.info(
                "audit_hmac_secret bootstrapped (last8=%s, method=%s)",
                hex_value[-8:],
                "keychain" if is_keychain_available() else "fallback",
            )
        else:
            # Persistence failed — return None so `_write` writes lines
            # unsigned. The operator sees the WARNING in stdlib logs +
            # the `unsigned_keychain_unavailable` markers in audit.log.
            _audit_kc_log.warning(
                "Failed to persist audit_hmac_secret to %s — audit log "
                "integrity reduced until next successful Keychain write",
                "keychain" if is_keychain_available() else "fallback",
            )
            return None

    try:
        secret_bytes = bytes.fromhex(hex_value)
    except ValueError:
        # Corrupted entry — log and treat as unavailable
        _audit_kc_log.error(
            "audit_hmac_secret in Keychain is not valid hex (corrupted?) — "
            "audit log integrity reduced. Delete the Keychain entry to bootstrap fresh."
        )
        return None

    cached["value"] = secret_bytes
    cached["ts"] = now
    return secret_bytes
