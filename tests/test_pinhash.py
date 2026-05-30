"""B8 / SR-31 — codec_pinhash regression tests.

Pins backward-compat with the SHA-256 era AND the new argon2id flow:
both formats verify; new hashes use argon2id when argon2-cffi is
installed.

Tests that require argon2-cffi are skipped when the package isn't
present (e.g. CI runners that install only the minimal dep set). The
backward-compat SHA-256 path is tested unconditionally — that's the
ship-critical invariant for operators with legacy hashes.
"""
import hashlib

import pytest

try:
    import argon2  # noqa: F401
    _ARGON2_INSTALLED = True
except ImportError:
    _ARGON2_INSTALLED = False

requires_argon2 = pytest.mark.skipif(
    not _ARGON2_INSTALLED,
    reason="argon2-cffi not installed in this environment",
)


@requires_argon2
def test_argon2_available_when_installed():
    """When argon2-cffi is installed, codec_pinhash should expose
    ARGON2_AVAILABLE=True. If this fails, argon2 is importable but
    codec_pinhash's import-check is broken."""
    import codec_pinhash
    assert codec_pinhash.ARGON2_AVAILABLE


@requires_argon2
def test_hash_pin_produces_argon2_format():
    from codec_pinhash import hash_pin
    h = hash_pin("1234")
    # argon2id encoded hashes start with $argon2id$
    assert h.startswith("$argon2id$"), f"expected argon2id-encoded hash, got: {h[:20]}..."


@requires_argon2
def test_verify_pin_argon2_match():
    from codec_pinhash import hash_pin, verify_pin
    h = hash_pin("4321")
    assert verify_pin("4321", h) is True


@requires_argon2
def test_verify_pin_argon2_mismatch():
    from codec_pinhash import hash_pin, verify_pin
    h = hash_pin("4321")
    assert verify_pin("9999", h) is False


def test_verify_pin_legacy_sha256_match():
    """Operators with a SHA-256 `auth_pin_hash` configured during the
    SHA-256 era must keep working."""
    from codec_pinhash import verify_pin
    pin = "5678"
    sha = hashlib.sha256(pin.encode()).hexdigest()
    assert verify_pin(pin, sha) is True


def test_verify_pin_legacy_sha256_mismatch():
    from codec_pinhash import verify_pin
    sha = hashlib.sha256(b"5678").hexdigest()
    assert verify_pin("0000", sha) is False


def test_verify_pin_empty_inputs_reject():
    from codec_pinhash import verify_pin
    assert verify_pin("", "abc") is False
    assert verify_pin("1234", "") is False
    assert verify_pin("", "") is False


def test_verify_pin_malformed_hash_rejects():
    """Unknown hash format (not argon2, not 64-hex-char SHA-256)
    returns False — no exception, no spurious accept."""
    from codec_pinhash import verify_pin
    assert verify_pin("1234", "not-a-hash") is False
    assert verify_pin("1234", "deadbeef") is False  # too short for SHA-256


@requires_argon2
def test_needs_rehash_signals_sha256_users():
    """needs_rehash flags SHA-256 hashes when argon2 is available so an
    admin/setup flow can opportunistically upgrade. Skipped when argon2
    isn't installed (needs_rehash returns False for all inputs in that
    case — by design)."""
    from codec_pinhash import needs_rehash, hash_pin
    sha = hashlib.sha256(b"abc").hexdigest()
    assert needs_rehash(sha) is True
    argon = hash_pin("abc")
    assert needs_rehash(argon) is False


def test_needs_rehash_returns_false_without_argon2():
    """Operator environments without argon2-cffi installed get
    needs_rehash=False for every input — there's no upgrade target."""
    if _ARGON2_INSTALLED:
        pytest.skip("argon2 is installed; tested above instead")
    from codec_pinhash import needs_rehash
    sha = hashlib.sha256(b"abc").hexdigest()
    assert needs_rehash(sha) is False
