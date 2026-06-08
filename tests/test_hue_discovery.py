"""Tests for codec_hue_discovery — standard mDNS/cloud/scan bridge discovery + self-heal.

All network I/O is injected (`_get` / `methods` / `_discover`) so these are pure unit
tests with no real network. See docs/HUE-DISCOVERY-DESIGN.md.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import codec_hue_discovery as hd  # noqa: E402


class _Resp:
    def __init__(self, data):
        self._d = data

    def json(self):
        return self._d


# ── verify_bridge ──────────────────────────────────────────────────────────
def test_verify_bridge_match():
    get = lambda url, timeout=0: _Resp({"bridgeid": "ECB5FAFFFE9A05E2", "name": "Philips hue"})
    assert hd.verify_bridge("192.168.1.81", _get=get) == "ECB5FAFFFE9A05E2"
    # expected_id match is case-insensitive
    assert hd.verify_bridge("192.168.1.81", "ecb5faffFE9a05e2", _get=get) == "ECB5FAFFFE9A05E2"


def test_verify_bridge_id_mismatch():
    get = lambda url, timeout=0: _Resp({"bridgeid": "AAAA"})
    assert hd.verify_bridge("192.168.1.81", "BBBB", _get=get) is None


def test_verify_bridge_unreachable_never_raises():
    def boom(url, timeout=0):
        raise OSError("host down")
    assert hd.verify_bridge("192.168.1.81", _get=boom) is None


def test_verify_bridge_non_bridge_response():
    get = lambda url, timeout=0: _Resp({"not": "a bridge"})
    assert hd.verify_bridge("192.168.1.81", _get=get) is None


# ── discover_bridge (ladder) ────────────────────────────────────────────────
def test_discover_returns_first_verified_in_ladder_order():
    m_mdns = lambda: ["10.0.0.5"]       # candidate that will NOT verify
    m_cloud = lambda: ["192.168.1.81"]  # candidate that WILL verify
    m_scan = lambda: ["1.1.1.1"]        # must not be reached
    def get(url, timeout=0):
        if "192.168.1.81" in url:
            return _Resp({"bridgeid": "REAL"})
        raise OSError("nope")
    assert hd.discover_bridge(methods=[m_mdns, m_cloud, m_scan], _get=get) == {
        "ip": "192.168.1.81", "id": "REAL"}


def test_discover_matches_by_expected_id():
    m = lambda: ["192.168.1.50", "192.168.1.81"]
    def get(url, timeout=0):
        if ".50" in url:
            return _Resp({"bridgeid": "OTHER"})
        if ".81" in url:
            return _Resp({"bridgeid": "MINE"})
        raise OSError()
    assert hd.discover_bridge("MINE", methods=[m], _get=get) == {"ip": "192.168.1.81", "id": "MINE"}


def test_discover_none_when_nothing_verifies():
    m = lambda: ["10.0.0.9"]
    def get(url, timeout=0):
        raise OSError()
    assert hd.discover_bridge(methods=[m], _get=get) is None


def test_discover_method_that_raises_is_skipped():
    def boom():
        raise RuntimeError("dns-sd blew up")
    good = lambda: ["192.168.1.81"]
    get = lambda url, timeout=0: _Resp({"bridgeid": "REAL"})
    assert hd.discover_bridge(methods=[boom, good], _get=get) == {"ip": "192.168.1.81", "id": "REAL"}


# ── rediscover_and_update_config ────────────────────────────────────────────
def test_rediscover_updates_ip_and_preserves_other_keys(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"hue_bridge_ip": "192.168.1.81", "hue_bridge_id": "MINE", "keep": 1}))
    disc = lambda expected_id=None: {"ip": "192.168.1.99", "id": "MINE"}
    assert hd.rediscover_and_update_config(str(cfg), _discover=disc) == "192.168.1.99"
    saved = json.loads(cfg.read_text())
    assert saved["hue_bridge_ip"] == "192.168.1.99"
    assert saved["hue_bridge_id"] == "MINE"
    assert saved["keep"] == 1


def test_rediscover_backfills_missing_id(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"hue_bridge_ip": "192.168.1.81"}))
    disc = lambda expected_id=None: {"ip": "192.168.1.99", "id": "FOUND"}
    hd.rediscover_and_update_config(str(cfg), _discover=disc)
    assert json.loads(cfg.read_text())["hue_bridge_id"] == "FOUND"


def test_rediscover_returns_none_and_leaves_config_untouched(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"hue_bridge_ip": "192.168.1.81", "hue_bridge_id": "MINE"}))
    disc = lambda expected_id=None: None
    assert hd.rediscover_and_update_config(str(cfg), _discover=disc) is None
    assert json.loads(cfg.read_text())["hue_bridge_ip"] == "192.168.1.81"
