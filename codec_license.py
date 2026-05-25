"""
CODEC — Client-side license enforcement (paid edition)
=======================================================

Design (operator-approved 2026-05-25):
  • Single paid tier. The free OSS build has NO license and is NEVER enforced —
    full local features. Enforcement only activates for a *paid* build, which is
    signalled by `edition: "paid"` in ~/.codec/config.json (written by the paid
    installer). This guarantees dev / OSS machines are untouched (fail-open).
  • Invalid / expired license → DEGRADE TO READ-ONLY: the UI loads but gated
    features (skill execution, agents, Pilot, Project) are disabled with an
    "activate to unlock" reason. Never a hard lockout.
  • 7-day OFFLINE GRACE: if the license can't be re-verified (server down, no
    network) we keep working for 7 days from the last good verification, then
    fall back to read-only.

Verification is RS256 over the license JWT, checked against the license server's
RSA public key (fetched from /public-key, cached to disk for offline use). Uses
`cryptography` directly — no PyJWT dependency.

Public API:
    state = license_state()              # LicenseState
    if feature_allowed("agents"): ...    # bool gate for a named feature
    require("pilot")                      # raises LicenseError if not allowed
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.exceptions import InvalidSignature

# ─── Paths / constants ──────────────────────────────────────────────────────────

CODEC_HOME       = Path.home() / ".codec"
CONFIG_PATH      = CODEC_HOME / "config.json"
PUBKEY_CACHE     = CODEC_HOME / "license_pubkey.pem"
GRACE_STATE_PATH = CODEC_HOME / ".license_state.json"

GRACE_SECONDS    = 7 * 24 * 3600            # 7-day offline grace (operator choice)
PUBKEY_URL_DEFAULT = "https://ava-license.lucyvpa.com/public-key"

# Features that require a valid paid license. Everything not listed is always
# allowed (the app shell, settings, viewing, etc. — read-only never locks out).
GATED_FEATURES = frozenset({
    "skill_exec",   # running a skill / command
    "agents",       # crew agents
    "project",      # autonomous Project mode
    "pilot",        # browser automation
    "cloud_proxy",  # AVA cloud proxy (Gemini/Claude/GPT)
})


class LicenseError(RuntimeError):
    """Raised by require() when a gated feature is used without a valid license."""


@dataclass
class LicenseState:
    mode: str            # "oss" | "ok" | "grace" | "readonly"
    reason: str          # human-readable explanation
    tier: str = ""       # JWT tier claim ("" for oss)
    expires_at: str = "" # ISO date if known
    grace_days_left: Optional[int] = None

    @property
    def enforced(self) -> bool:
        """True only when we should restrict gated features."""
        return self.mode == "readonly"

    def to_dict(self) -> dict:
        return {
            "mode": self.mode, "reason": self.reason, "tier": self.tier,
            "expires_at": self.expires_at, "grace_days_left": self.grace_days_left,
            "enforced": self.enforced,
        }


# ─── Config helpers ─────────────────────────────────────────────────────────────

def _load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _is_paid_edition(cfg: dict) -> bool:
    """Paid builds set edition=paid (installer-written). Absent → OSS, unenforced."""
    return str(cfg.get("edition", "")).lower() == "paid"


def _license_token(cfg: dict) -> str:
    ava = cfg.get("ava") or {}
    return cfg.get("license_token") or ava.get("license_token") or ava.get("license_key") or ""


def _pubkey_url(cfg: dict) -> str:
    base = cfg.get("license_base_url") or "https://ava-license.lucyvpa.com"
    return base.rstrip("/") + "/public-key"


# ─── JWT (RS256) verification — no PyJWT ─────────────────────────────────────────

def _b64url(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _fetch_pubkey(cfg: dict, timeout: float = 4.0) -> Optional[bytes]:
    """Fetch the server's PEM public key; cache to disk. Falls back to cache offline."""
    try:
        with urllib.request.urlopen(_pubkey_url(cfg), timeout=timeout) as r:
            pem = r.read()
        if b"BEGIN PUBLIC KEY" in pem:
            try:
                PUBKEY_CACHE.write_bytes(pem)
            except Exception:
                pass
            return pem
    except Exception:
        pass
    # Offline: use cached key
    try:
        return PUBKEY_CACHE.read_bytes()
    except Exception:
        return None


def verify_license_token(token: str, pubkey_pem: bytes) -> tuple[bool, dict, str]:
    """
    Verify an RS256 license JWT against the RSA public key.
    Returns (valid, claims, reason). `valid` is True only if signature AND
    expiry both pass.
    """
    parts = token.split(".")
    if len(parts) != 3:
        return False, {}, "malformed token"
    header_b64, payload_b64, sig_b64 = parts
    try:
        header = json.loads(_b64url(header_b64))
        claims = json.loads(_b64url(payload_b64))
        sig = _b64url(sig_b64)
    except Exception as e:
        return False, {}, f"unparseable token: {e}"

    alg = header.get("alg")
    if alg != "RS256":
        return False, claims, f"unsupported alg {alg!r} (expected RS256)"

    try:
        pubkey = load_pem_public_key(pubkey_pem)
        pubkey.verify(
            sig,
            (header_b64 + "." + payload_b64).encode("ascii"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except InvalidSignature:
        return False, claims, "bad signature"
    except Exception as e:
        return False, claims, f"verify error: {e}"

    # Expiry — accept `exp` (epoch) or `expires_at` (ISO or epoch)
    now = time.time()
    exp = claims.get("exp")
    if exp is None:
        ea = claims.get("expires_at")
        if isinstance(ea, (int, float)):
            exp = ea
        elif isinstance(ea, str) and ea:
            try:
                from datetime import datetime
                exp = datetime.fromisoformat(ea.replace("Z", "+00:00")).timestamp()
            except Exception:
                exp = None
    if exp is not None and now > float(exp):
        return False, claims, "expired"

    return True, claims, "ok"


# ─── Offline grace tracking ──────────────────────────────────────────────────────

def _read_grace() -> dict:
    try:
        return json.loads(GRACE_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_grace(last_good_ts: float, tier: str, expires_at: str) -> None:
    try:
        GRACE_STATE_PATH.write_text(json.dumps({
            "last_good_ts": last_good_ts, "tier": tier, "expires_at": expires_at,
        }), encoding="utf-8")
    except Exception:
        pass


# ─── Public: state machine ───────────────────────────────────────────────────────

def license_state(cfg: Optional[dict] = None, *, _now: Optional[float] = None) -> LicenseState:
    """
    Resolve the current enforcement state. Order:
      1. Not a paid edition          → "oss"  (never enforced)
      2. No token                    → "readonly" (paid build, not activated)
      3. Verify token vs pubkey:
           valid                     → "ok"   (refresh grace timestamp)
           can't verify (offline)    → "grace" if within window, else "readonly"
           invalid/expired           → "readonly"
    """
    cfg = cfg if cfg is not None else _load_config()
    now = _now if _now is not None else time.time()

    if not _is_paid_edition(cfg):
        return LicenseState(mode="oss", reason="open-source build — no license required")

    token = _license_token(cfg)
    if not token:
        return LicenseState(mode="readonly", reason="paid build not activated — enter a license key")

    pubkey = _fetch_pubkey(cfg)
    if pubkey is None:
        # Can't get the key at all → fall back to grace window
        return _grace_or_readonly(now, "license server unreachable and no cached key")

    valid, claims, reason = verify_license_token(token, pubkey)
    tier = str(claims.get("tier", "pro"))
    expires_at = str(claims.get("expires_at", ""))
    if valid:
        _write_grace(now, tier, expires_at)
        return LicenseState(mode="ok", reason="licensed", tier=tier, expires_at=expires_at)

    if reason in ("verify error: ", "license server unreachable") or "unreachable" in reason:
        return _grace_or_readonly(now, reason)

    # Hard invalid (bad signature / expired / malformed) → read-only immediately
    return LicenseState(mode="readonly", reason=f"license invalid: {reason}",
                        tier=tier, expires_at=expires_at)


def _grace_or_readonly(now: float, reason: str) -> LicenseState:
    g = _read_grace()
    last = g.get("last_good_ts")
    if isinstance(last, (int, float)) and (now - last) < GRACE_SECONDS:
        days_left = int((GRACE_SECONDS - (now - last)) // 86400)
        return LicenseState(mode="grace",
                            reason=f"offline — {days_left}d grace left ({reason})",
                            tier=str(g.get("tier", "pro")),
                            expires_at=str(g.get("expires_at", "")),
                            grace_days_left=days_left)
    return LicenseState(mode="readonly", reason=f"offline grace expired ({reason})")


# ─── Public: feature gates ───────────────────────────────────────────────────────

def feature_allowed(feature: str, cfg: Optional[dict] = None) -> bool:
    """True if `feature` may run. OSS + ok + grace allow everything; readonly
    denies the GATED_FEATURES set."""
    if feature not in GATED_FEATURES:
        return True
    return not license_state(cfg).enforced


def require(feature: str, cfg: Optional[dict] = None) -> None:
    """Raise LicenseError if `feature` is gated and the license isn't valid."""
    if not feature_allowed(feature, cfg):
        st = license_state(cfg)
        raise LicenseError(
            f"'{feature}' requires an active CODEC license — {st.reason}. "
            f"Activate in Settings to unlock."
        )
