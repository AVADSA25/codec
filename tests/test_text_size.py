"""Text size is adjustable on every surface and defaults to medium.

Reported 2026-09-04: "the size of the text on the CODEC app is too small". The
fix scales the whole page rather than one font variable, because the surfaces
are px-sized throughout and a font-size change alone leaves buttons, inputs and
icons at the old size.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SURFACES = sorted(REPO.glob("codec_*.html"))


@pytest.mark.parametrize("path", SURFACES, ids=lambda p: p.name)
def test_every_surface_applies_text_size(path: Path):
    s = path.read_text()
    assert "codec-text-size" in s, f"{path.name} ignores the text-size setting"
    assert "applyTextSize();" in s, f"{path.name} never applies it on load"
    # Default must be medium, and medium must be larger than 1.0 — that is the fix.
    m = re.search(r"_TEXT_SIZES=\{small:([\d.]+),medium:([\d.]+),large:([\d.]+)\}", s)
    assert m, f"{path.name}: size map missing"
    small, medium, large = map(float, m.groups())
    assert small < medium < large
    assert medium > 1.0, "medium must enlarge the page — that is the whole point"
    assert "?v:'medium'" in s, f"{path.name}: default is not medium"
    # Storage access must go through the shim — it throws when blocked.
    assert "_lsGet('codec-text-size')" in s and "_lsSet('codec-text-size'" in s


def test_control_exists_where_users_look():
    for name in ("codec_dashboard.html", "codec_chat.html"):
        s = (REPO / name).read_text()
        assert 'id="textSizeGroup"' in s, f"{name} has no text-size control"
        for size in ("small", "medium", "large"):
            assert f"setTextSize('{size}')" in s, f"{name}: no {size} button"
