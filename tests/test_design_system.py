"""Every PWA surface runs one design system.

Three surfaces (auth, audit, tasks) were missed by the #318-#324 refresh and
kept serving the pre-2026 orange, Inter/JetBrains Mono, and emoji. The login
page — the first screen of the demo — was one of them.

Two lessons from that refresh are encoded here:

- Replacing the hex alone is not enough. The first pass on chat swapped only the
  `rgba()` spelling and the live page kept serving five `#E8711A` values, so both
  spellings are checked.
- A source grep is not proof that the SERVED page is clean, but it IS the cheap
  regression guard. The served check happens at deploy; this stops the drift
  from being reintroduced in the first place.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SURFACES = sorted(REPO.glob("codec_*.html"))

NEW_ACCENT = "#d97757"
NEW_ACCENT_LIGHT = "#b85a3a"
_OLD_HEX = re.compile(r"#E8711A", re.I)
_OLD_RGBA = re.compile(r"rgba\(\s*232\s*,\s*113\s*,\s*26")
_ACCENT_DECL = re.compile(r"--accent:\s*([^;}]+)")

# Smartphone emoji only. Geometric glyphs (✓ ✕ ● ▸ ⚠) are typography, not emoji,
# and are used deliberately across the surfaces.
_EMOJI = re.compile(r"[\U0001F300-\U0001FAFF]")
_EMOJI_ENTITY = re.compile(r"&#(1[0-9]{5});")

# `codec_tasks.html` strips a marker that `shift_report` puts in the report BODY.
# The emoji is content produced elsewhere, not UI chrome — editing it would break
# the strip and the marker would start rendering.
_CONTENT_EMOJI_EXEMPT = {"codec_tasks.html": 1}


def test_surfaces_are_discovered():
    assert len(SURFACES) >= 7, f"expected the PWA surfaces, found {SURFACES}"


@pytest.mark.parametrize("path", SURFACES, ids=lambda p: p.name)
def test_no_old_accent_in_either_spelling(path: Path):
    text = path.read_text()
    # The token comment documents what the value replaced; that mention is fine.
    live = "\n".join(l for l in text.splitlines() if "was pure #E8711A" not in l)
    assert not _OLD_HEX.search(live), f"{path.name} still hardcodes the pre-2026 orange"
    assert not _OLD_RGBA.search(live), (
        f"{path.name} still has the OLD accent in rgba() form — replacing the hex "
        f"alone is what let five values survive the first chat pass"
    )


@pytest.mark.parametrize("path", SURFACES, ids=lambda p: p.name)
def test_accent_token_is_the_shared_value(path: Path):
    declared = {m.strip() for m in _ACCENT_DECL.findall(path.read_text())}
    if not declared:
        pytest.skip(f"{path.name} declares no --accent")
    assert declared <= {NEW_ACCENT, NEW_ACCENT_LIGHT}, (
        f"{path.name} declares off-system accents: {declared - {NEW_ACCENT, NEW_ACCENT_LIGHT}}"
    )


@pytest.mark.parametrize("path", SURFACES, ids=lambda p: p.name)
def test_typeface_is_ibm_plex(path: Path):
    text = path.read_text()
    if "fonts.googleapis.com" not in text:
        pytest.skip(f"{path.name} loads no webfont")
    assert "IBM+Plex" in text, f"{path.name} still loads the pre-refresh typeface"
    assert "'Inter'" not in text and "'JetBrains Mono'" not in text, (
        f"{path.name} still names Inter/JetBrains Mono in a font stack"
    )


@pytest.mark.parametrize("path", SURFACES, ids=lambda p: p.name)
def test_no_emoji_in_ui(path: Path):
    text = path.read_text()
    found = _EMOJI.findall(text) + [chr(int(c)) for c in _EMOJI_ENTITY.findall(text)]
    allowed = _CONTENT_EMOJI_EXEMPT.get(path.name, 0)
    assert len(found) <= allowed, (
        f"{path.name} has {len(found)} emoji ({''.join(found)}) but only {allowed} "
        f"content exemption(s) — CODEC UI uses line SVG or plain text"
    )
