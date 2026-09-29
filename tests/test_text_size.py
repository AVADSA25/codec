"""Text size is adjustable on every surface and defaults to medium.

Reported 2026-09-04: "the size of the text on the CODEC app is too small".
UI phase 1 PR-C moved the size onto the type tokens (--fs-scale) instead of a
root zoom; UI phase 2 P2.1 moved the setting into the shared shell
(static/codec-shell.js), which every app page loads. Auth, the login page, has
no shell and applies the same setting itself.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SHELL = (REPO / "static" / "codec-shell.js").read_text()
APP_PAGES = sorted(p for p in REPO.glob("codec_*.html") if p.name != "codec_auth.html")


@pytest.mark.parametrize("path", APP_PAGES, ids=lambda p: p.name)
def test_every_app_surface_gets_text_size_from_the_shell(path: Path):
    s = path.read_text()
    assert re.search(r'<script src="/static/codec-shell\.js(\?v=[0-9a-f]+)?" data-page=', s), \
        f"{path.name} does not load the shell, so it ignores the text-size setting"
    assert "style.zoom" not in s, f"{path.name}: zooms the root"


def test_shell_applies_text_size_on_load_with_medium_default():
    m = re.search(r"SIZES = \{ small: ([\d.]+), medium: ([\d.]+), large: ([\d.]+) \}", SHELL)
    assert m, "size map missing"
    small, medium, large = map(float, m.groups())
    assert small < medium == 1.0 < large, "medium is the token sizes; S and L scale them"
    assert "root.style.setProperty('--fs-scale'" in SHELL and "zoom" not in SHELL.replace("zoomed", "")
    assert "return SIZES[s] ? s : 'medium'" in SHELL, "default is not medium"
    assert "\n  applyTextSize();\n" in SHELL, "the shell never applies it on load"
    # Storage access goes through the guarded helpers; it throws when blocked.
    assert "lsGet('codec-text-size')" in SHELL and "lsSet('codec-text-size'" in SHELL
    assert "try { var v = localStorage.getItem(k)" in SHELL


def test_auth_applies_the_same_setting():
    s = (REPO / "codec_auth.html").read_text()
    assert "_lsGet('codec-text-size')" in s and "small:0.93,medium:1,large:1.12" in s
    assert "setProperty('--fs-scale'" in s and "style.zoom" not in s


def test_control_exists_where_users_look():
    # The quick-settings panel the shell builds on every app page.
    assert 'id="textSizeGroup"' in SHELL, "the shell has no text-size control"
    for size in ("small", "medium", "large"):
        assert f"setTextSize(\\'{size}\\')" in SHELL, f"no {size} button"
