"""B8 / SR-31 — PIN hashing helpers.

Migrates `auth_pin_hash` from SHA-256 (which is GPU-trivial to brute-
force) to argon2id (memory-hard, GPU-resistant) while preserving
backward compatibility with operators who configured SHA-256 hashes
during the SHA-256 era. Either format verifies; new hashes use argon2id
when the library is available.

PIN brute-force protection on the auth handler (5-strike escalating
lockout) is independent of this change; this is defense in depth on the
hash itself.
"""
from __future__ import annotations

import hashlib
import hmac
import logging

log = logging.getLogger("codec_pinhash")

# argon2-cffi is an OPTIONAL runtime dependency. If absent, we fall back
# to SHA-256 hashing for new hashes (with a one-line warning at first
# use) and continue to verify both formats. To enable argon2id hashing,
# `pip install argon2-cffi` and restart the dashboard.
try:
    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError, InvalidHashError, VerificationError
    _HASHER = PasswordHasher(
        time_cost=3,        # OWASP 2023 recommendation
        memory_cost=64_000,  # 64 MiB — fits desktop/dashboard process budget
        parallelism=1,
    )
    ARGON2_AVAILABLE = True
except ImportError:
    _HASHER = None
    VerifyMismatchError = type("VerifyMismatchError", (Exception,), {})
    InvalidHashError = type("InvalidHashError", (Exception,), {})
    VerificationError = type("VerificationError", (Exception,), {})
    ARGON2_AVAILABLE = False
    log.warning(
        "argon2-cffi not installed — PIN hashing will use SHA-256. "
        "Run `pip install argon2-cffi` for memory-hard hashing.")


def _is_argon2(stored: str) -> bool:
    return stored.startswith("$argon2")


def _is_sha256(stored: str) -> bool:
    # 64 lowercase hex characters.
    if len(stored) != 64:
        return False
    try:
        int(stored, 16)
        return True
    except ValueError:
        return False


def hash_pin(pin: str) -> str:
    """Hash a PIN for storage. Returns an argon2id encoded string when
    argon2-cffi is available; falls back to SHA-256 hex otherwise."""
    if not isinstance(pin, str):
        raise TypeError("pin must be str")
    if not pin:
        raise ValueError("pin must not be empty")
    if ARGON2_AVAILABLE:
        return _HASHER.hash(pin)
    return hashlib.sha256(pin.encode("utf-8")).hexdigest()


def verify_pin(pin: str, stored_hash: str) -> bool:
    """Constant-time PIN verification.

    Recognizes both `$argon2id$...` encoded hashes and 64-char SHA-256
    hex hashes. Returns False on any unexpected format or empty input.
    Never raises.
    """
    if not pin or not stored_hash:
        return False
    if not isinstance(pin, str) or not isinstance(stored_hash, str):
        return False
    if _is_argon2(stored_hash):
        if not ARGON2_AVAILABLE:
            log.error("argon2id-encoded auth_pin_hash present but argon2-cffi"
                      " is not installed — install with `pip install argon2-cffi`.")
            return False
        try:
            _HASHER.verify(stored_hash, pin)
            return True
        except (VerifyMismatchError, InvalidHashError, VerificationError):
            return False
        except Exception:
            return False
    if _is_sha256(stored_hash):
        candidate = hashlib.sha256(pin.encode("utf-8")).hexdigest()
        return hmac.compare_digest(candidate, stored_hash)
    return False


def needs_rehash(stored_hash: str) -> bool:
    """True if the stored hash should be migrated to argon2id.

    Used by an admin/setup flow to opportunistically upgrade SHA-256
    hashes to argon2id when the operator next sets or rotates a PIN.
    Not called on the verify path to avoid mid-request config writes.
    """
    if not ARGON2_AVAILABLE:
        return False
    return _is_sha256(stored_hash)
