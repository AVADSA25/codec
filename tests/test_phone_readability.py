"""Phone readability and stability (docs/PHONE-READABILITY-DESIGN.md).

On a phone the type scale sits on a 12.5% larger base, Today opens on its tiles
instead of the end of the Flash history, a pull past the end of a list does not
reload the page or carry on into it, and every page asks Android Chrome to
shrink the layout when the keyboard opens.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SHELL = (REPO / "static" / "codec-shell.js").read_text(encoding="utf-8")
CSS = (REPO / "static" / "codec.css").read_text(encoding="utf-8")
DASH = (REPO / "codec_dashboard.html").read_text(encoding="utf-8")
PAGES = ["codec_dashboard.html", "codec_chat.html", "codec_tasks.html", "codec_voice.html",
         "codec_vibe.html", "codec_cortex.html", "codec_auth.html"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_text_scale_is_larger_on_a_phone_and_unchanged_on_the_desktop():
    block = SHELL[SHELL.index("// text-scale:begin"):SHELL.index("// text-scale:end")]
    script = block + """
    var out = {};
    ['small', 'medium', 'large'].forEach(function (s) {
      out[s] = { desktop: effectiveScale(s, false), phone: effectiveScale(s, true) };
    });
    console.log(JSON.stringify(out));
    """
    run = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    got = json.loads(run.stdout.strip())
    # Desktop keeps the old S / M / L steps exactly.
    assert [got[s]["desktop"] for s in ("small", "medium", "large")] == [0.93, 1, 1.12]
    # Phone: medium is 18px reply text (16 x 1.125), large about 20px.
    assert got["medium"]["phone"] == 1.125
    assert got["large"]["phone"] == 1.26
    assert got["small"]["phone"] == 1.046
    assert all(got[s]["phone"] > got[s]["desktop"] for s in got)


def test_shell_applies_the_phone_scale_and_follows_a_resize_across_the_phone_width():
    assert "effectiveScale(textSize(), isPhone())" in SHELL
    assert re.search(r"window\.addEventListener\('resize', function \(\) \{\s*var now = isPhone\(\);\s*"
                     r"if \(now !== wasPhone\) \{ wasPhone = now; applyTextSize\(\); \}", SHELL)
    # The three steps stay as they were: other tests and the auth page rely on them.
    assert "small: 0.93, medium: 1, large: 1.12" in SHELL


@pytest.mark.parametrize("name", PAGES)
def test_every_page_asks_chrome_to_resize_for_the_keyboard_and_allows_pinch_zoom(name):
    src = (REPO / name).read_text(encoding="utf-8")
    meta = re.search(r'<meta name="viewport" content="([^"]*)"', src)
    assert meta, f"{name} has no viewport meta"
    assert "interactive-widget=resizes-content" in meta.group(1), name
    assert "user-scalable=no" not in meta.group(1) and "maximum-scale" not in meta.group(1), name


def test_touch_screens_do_not_pull_to_refresh_or_chain_scrolling_into_the_page():
    m = re.search(r"@media \(pointer: coarse\), \(max-width: 767px\) \{(.*?)\n\}", CSS, re.S)
    assert m, "no touch media query for the overscroll guard"
    body = m.group(1)
    assert re.search(r"html, body \{ overscroll-behavior-y: none; \}", body)
    lists = re.search(r"([^{}]+)\{ overscroll-behavior-y: contain; \}", body)
    assert lists, "inner lists must not hand their scroll to the page"
    for sel in (".cs-main", ".content", ".messages", ".cs-hist"):
        assert sel in lists.group(1), f"{sel} can still chain its scroll into the page"
    # Vertical only: a sideways swipe at the screen edge keeps going back.
    assert "overscroll-behavior-x" not in body and "overscroll-behavior: none" not in body
    assert re.search(r"html \{ -webkit-text-size-adjust: 100%; text-size-adjust: 100%; \}", CSS)


def test_today_opens_on_its_tiles_and_scrolls_to_the_end_only_after_something_was_sent():
    assert "async function loadChat(toEnd)" in DASH
    fn = DASH[DASH.index("async function loadChat(toEnd)"):]
    fn = fn[:fn.index("\n}\n")]
    assert re.search(r"if \(toEnd\) \{\s*var scroller = [^\n]*\n\s*setTimeout\(function\(\)\{ scroller\.scrollTop = "
                     r"scroller\.scrollHeight; \}, 50\);\s*\}", fn), "the scroll to the end must sit behind toEnd"
    # The first load and the switch back to the Today tab pass nothing: they stay at the top.
    assert "if (id === 'chat') { loadChat(); loadTodayHome(); loadTodayNext(); }" in DASH
    assert re.search(r"// ── Init ──\nloadChat\(\);\n", DASH)
    # A sent message, a vision reply and a polled reply each scroll to the newest message.
    assert DASH.count("loadChat(true)") == 3
    assert "loadChat(); playResponse" not in DASH
