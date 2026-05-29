"""Pilot PP-5 — the Chromium CDP debug port must be randomized per launch, not a fixed
predictable 9223 that any local process can attach to. Closes audit P-8 (the CDP socket is
loopback-bound, so this is the local-process-hijack hardening, not the 0.0.0.0 case).

Reference: docs/PP5-CDP-PORT-DESIGN.md.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))  # ~/codec on sys.path

from pilot.pilot_chrome import PilotChrome  # noqa: E402


def test_cdp_port_is_randomized_per_instance():
    a = PilotChrome()
    b = PilotChrome()
    assert a.cdp_port != 9223 and b.cdp_port != 9223, "must not use the fixed predictable 9223 (P-8)"
    assert a.cdp_port != b.cdp_port, "each launch should get its own random port"
    assert 1024 < a.cdp_port < 65536 and 1024 < b.cdp_port < 65536


def test_explicit_cdp_port_still_respected():
    p = PilotChrome(cdp_port=12345)
    assert p.cdp_port == 12345, "an explicitly-passed port must be honored (back-compat)"
