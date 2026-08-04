"""Copy / edit / speak buttons must never carry message text in an inline onclick.

Regression guard for a bug that survived one "fix" and kept being reported.

`encodeURIComponent()` does NOT escape an apostrophe — "'" is an unreserved mark
(A-Z a-z 0-9 - _ . ! ~ * ' ( )), so it passes through untouched. Embedding the
result in a single-quoted JS string inside an HTML attribute therefore still
breaks on the first apostrophe:

    onclick="copyMsgText(decodeURIComponent('I don't know'),this)"

That is a SyntaxError, so the handler never runs and the click silently does
nothing. Most sentences contain an apostrophe, which is why it presented as
"the copy button never works" on desktop AND phone — the PWA's Chat link serves
codec_chat.html too.

The contract: buttons are emitted bare and bound with addEventListener over a
closure. No escaping, and message content can never become executable.
"""
from __future__ import annotations

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent

# Files that render user/assistant message text with action buttons.
HTML_FILES = ["codec_chat.html", "codec_dashboard.html", "codec_vibe.html", "codec_voice.html"]

# The broken shape: an inline handler that decodes data back into a JS string.
_INLINE_DATA_HANDLER = re.compile(r"""on\w+\s*=\s*["'][^"']*decodeURIComponent\s*\(""")


@pytest.mark.parametrize("name", HTML_FILES)
def test_no_message_text_embedded_in_inline_handler(name):
    """No inline on*= handler may reconstruct message text via decodeURIComponent."""
    path = REPO / name
    if not path.exists():
        pytest.skip(f"{name} not present")
    hits = _INLINE_DATA_HANDLER.findall(path.read_text(encoding="utf-8"))
    assert not hits, (
        f"{name}: message text embedded in an inline handler ({len(hits)} site(s)). "
        "encodeURIComponent does not escape apostrophes, so this breaks on \"don't\". "
        "Emit a bare button and bind it with addEventListener over a closure."
    )


def test_chat_binds_actions_by_closure():
    """codec_chat.html binds copy/edit/speak via data-act + addEventListener."""
    src = (REPO / "codec_chat.html").read_text(encoding="utf-8")
    for act in ("copy", "edit", "speak"):
        assert f'data-act="{act}"' in src, f"missing bare button for data-act={act}"
        assert re.search(
            rf"""querySelector\(\s*['"]\[data-act="{act}"\]['"]\s*\)""", src
        ), f"data-act={act} button is never bound by closure"
    assert "addEventListener('click'" in src


@pytest.mark.parametrize("name", ["codec_chat.html", "codec_dashboard.html"])
def test_copy_fallback_is_ios_capable(name):
    """The execCommand fallback must be usable on iOS Safari.

    navigator.clipboard is undefined on any non-secure origin, so plain-http LAN
    access falls back here. iOS needs contentEditable + a Range selection, and
    pointer-events:none silently blocks the selection.
    """
    src = (REPO / name).read_text(encoding="utf-8")
    start = src.index("function _copyFallback")
    body = src[start : start + 1400]
    assert "contentEditable" in body, f"{name}: iOS needs contentEditable on the copy node"
    assert "createRange" in body, f"{name}: iOS ignores .select(); a Range selection is required"
    assert not re.search(r"pointerEvents\s*=\s*['\"]none['\"]", body), (
        f"{name}: pointer-events:none blocks the iOS selection and breaks the fallback"
    )
