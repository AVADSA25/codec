"""Tests for PR-5A (Wave 5 / W5-1) — the signed-bundle metadata under
packaging/macos/ is well-formed and carries the keys the Developer ID +
notarization path requires.

These are config-asset validation/regression guards (the analog of a
source-invariant): they pin the required Info.plist usage strings, the
hardened-runtime entitlements (and the ABSENCE of app-sandbox), and the
local-first privacy manifest, so a later edit can't silently drop one.

Reference: docs/APPLE-DISTRIBUTION.md, docs/audits/PHASE-1-APPLE-APP.md (E-1/E-5/E-16).
"""
from __future__ import annotations

import plistlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "packaging" / "macos"


def _load(name):
    with open(PKG / name, "rb") as f:
        return plistlib.load(f)


# ── Info.plist ────────────────────────────────────────────────────────────────


def test_info_plist_parses_and_has_identity():
    p = _load("Info.plist")
    assert p["CFBundleIdentifier"] == "ai.avadigital.codec", (
        "bundle ID must match the ai.avadigital.codec.* Keychain prefix (PR-2B/2D/2E)"
    )
    for key in ("CFBundleName", "CFBundleExecutable", "CFBundleVersion",
                "CFBundleShortVersionString", "LSMinimumSystemVersion"):
        assert p.get(key), f"Info.plist missing {key}"


def test_info_plist_has_all_tcc_usage_strings():
    p = _load("Info.plist")
    for key in ("NSMicrophoneUsageDescription",
                "NSAppleEventsUsageDescription",
                "NSScreenCaptureUsageDescription",
                "NSDesktopFolderUsageDescription",
                "NSDocumentsFolderUsageDescription",
                "NSDownloadsFolderUsageDescription"):
        val = p.get(key)
        assert isinstance(val, str) and len(val) > 10, (
            f"Info.plist needs a meaningful {key} (shown to the user in the TCC prompt)"
        )


# ── entitlements ──────────────────────────────────────────────────────────────


def test_entitlements_hardened_runtime_and_no_sandbox():
    e = _load("codec.entitlements")
    for key in ("com.apple.security.cs.allow-jit",
                "com.apple.security.cs.disable-library-validation",
                "com.apple.security.cs.allow-unsigned-executable-memory"):
        assert e.get(key) is True, f"hardened-runtime entitlement {key} must be true (bundled CPython/PyObjC)"
    assert e.get("com.apple.security.network.client") is True
    assert e.get("com.apple.security.network.server") is True
    # Direct distribution — sandbox would break 5 core capabilities (see §2).
    assert "com.apple.security.app-sandbox" not in e, (
        "must NOT be sandboxed — App Store is ruled out (E-4)"
    )


# ── PrivacyInfo.xcprivacy ─────────────────────────────────────────────────────


def test_privacy_manifest_is_local_first():
    pm = _load("PrivacyInfo.xcprivacy")
    assert pm.get("NSPrivacyTracking") is False, "CODEC does not track"
    assert pm.get("NSPrivacyTrackingDomains") == [], "no tracking domains"
    assert pm.get("NSPrivacyCollectedDataTypes") == [], "local-first — collects nothing"


def test_privacy_manifest_declares_required_reasons():
    pm = _load("PrivacyInfo.xcprivacy")
    apis = {a["NSPrivacyAccessedAPIType"]: a.get("NSPrivacyAccessedAPITypeReasons", [])
            for a in pm.get("NSPrivacyAccessedAPITypes", [])}
    assert "NSPrivacyAccessedAPICategoryFileTimestamp" in apis, "observer/scheduler mtime checks"
    assert "C617.1" in apis["NSPrivacyAccessedAPICategoryFileTimestamp"]
    assert "NSPrivacyAccessedAPICategoryDiskSpace" in apis, "heartbeat disk-space check"
    assert "E174.1" in apis["NSPrivacyAccessedAPICategoryDiskSpace"]


# ── decision doc present ──────────────────────────────────────────────────────


def test_distribution_decision_doc_exists():
    doc = (REPO / "docs" / "APPLE-DISTRIBUTION.md")
    assert doc.exists(), "W5-1 decision record must exist"
    text = doc.read_text()
    assert "Mac App Store" in text and "Developer ID" in text
    assert "ai.avadigital.codec" in text
