"""Replies render as sanitized Markdown (UI phase 1, PR-C).

Chat replies, Home (Flash) replies and Tasks report bodies go through one
renderer, static/codec-md.js: marked, then DOMPurify, then code and table boxes.
Model output must never reach innerHTML without DOMPurify, and the user's own
chat messages stay plain text.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import codec_config
import codec_dashboard
import routes._shared as shared
from codec_dashboard import app

REPO = Path(__file__).resolve().parent.parent
RENDERER = REPO / "static" / "codec-md.js"
PAGES = ["codec_chat.html", "codec_dashboard.html", "codec_tasks.html"]
SCRIPTS = ["/static/vendor/marked.umd.js", "/static/vendor/purify.min.js",
           "/static/vendor/highlight.min.js", "/static/codec-md.js"]


@pytest.mark.parametrize("name", PAGES)
def test_page_loads_the_vendored_renderer_before_using_it(name):
    src = (REPO / name).read_text(encoding="utf-8")
    # a ?v=<hash> suffix (tools/stamp_static.py) is allowed after the path
    at = [(m.start() if (m := re.search(r'<script src="' + re.escape(s) + r'(\?v=[0-9a-f]+)?"></script>', src)) else -1)
          for s in SCRIPTS]
    assert all(i >= 0 for i in at), f"{name} does not load {[s for s, i in zip(SCRIPTS, at) if i < 0]}"
    assert at == sorted(at), f"{name}: the renderer must load after marked, DOMPurify and highlight.js"
    assert at[-1] < src.find("codecMarkdown."), f"{name} uses codecMarkdown before loading it"


def test_renderer_sanitizes_every_reply():
    src = RENDERER.read_text(encoding="utf-8")
    assert "purify.sanitize(p.parse(src))" in src, "reply HTML must come out of DOMPurify.sanitize"
    forbid = re.search(r"FORBID_TAGS:\s*\[([^\]]*)\]", src).group(1)
    for tag in ("style", "form", "input", "button", "iframe", "object", "embed"):
        assert f"'{tag}'" in forbid, f"<{tag}> must be forbidden"
    assert "'style'" in re.search(r"FORBID_ATTR:\s*\[([^\]]*)\]", src).group(1)
    assert "setAttribute('target', '_blank')" in src and "'noopener noreferrer'" in src
    # Without marked or DOMPurify the text is escaped, never passed through as HTML.
    assert "if (!p || !purify) return plain(src);" in src


def test_chat_user_messages_stay_plain_text():
    src = (REPO / "codec_chat.html").read_text(encoding="utf-8")
    assert "if(role==='user')bub.textContent=" in src
    assert "innerHTML=formatMsg(" not in src.replace(" ", "")


def test_renderer_copy_fallback_is_ios_capable():
    """Same contract as the pages' _copyFallback (tests/test_copy_button_binding.py)."""
    src = RENDERER.read_text(encoding="utf-8")
    body = src[src.index("function _copyFallback"):][:1400]
    assert "contentEditable" in body and "createRange" in body
    assert not re.search(r"pointerEvents\s*=\s*['\"]none['\"]", body)


def test_renderer_is_served_as_javascript(monkeypatch):
    # /static is auth-public: the renderer must load on the login-guarded pages.
    monkeypatch.setattr(codec_config, "get_dashboard_token", lambda: "")
    monkeypatch.setattr(codec_dashboard, "AUTH_ENABLED", True)
    monkeypatch.setattr(shared, "AUTH_ENABLED", True)
    monkeypatch.setattr(codec_dashboard, "_auth_available", lambda: True)
    r = TestClient(app, client=("127.0.0.1", 5000)).get("/static/codec-md.js")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/javascript")
    assert "window.codecMarkdown" in r.text


_JS_HARNESS = r"""
const fs = require('fs'), path = require('path');
const { JSDOM } = require('jsdom');
const w = new JSDOM('<!doctype html><body></body>', {runScripts: 'outside-only'}).window;
for (const f of JSON.parse(process.argv[1])) w.eval(fs.readFileSync(f, 'utf8'));
const d = w.document.createElement('div');
d.innerHTML = w.codecMarkdown.html(process.argv[2]);
const bad = [];
if (d.querySelector('script')) bad.push('script');
for (const el of d.querySelectorAll('*')) for (const a of el.attributes) {
  if (/^on/i.test(a.name)) bad.push(a.name);
  if (/^\s*javascript:/i.test(a.value)) bad.push('javascript: ' + a.name);
  if (el.tagName === 'IMG' && a.name === 'src') bad.push('img src ' + a.value);
}
for (const a of d.querySelectorAll('a')) if (a.rel !== 'noopener noreferrer' || a.target !== '_blank') bad.push('link');
console.log(bad.length ? 'UNSAFE ' + bad.join(', ') : 'SAFE ' + d.querySelectorAll('a').length);
"""


def _jsdom_available() -> bool:
    if shutil.which("node") is None:
        return False
    r = subprocess.run(["node", "-e", "require.resolve('jsdom')"], capture_output=True,
                       cwd=REPO, env=os.environ.copy(), timeout=20)
    return r.returncode == 0


@pytest.mark.skipif(not _jsdom_available(), reason="node with jsdom not resolvable (set NODE_PATH)")
def test_malicious_reply_renders_inert_under_jsdom():
    libs = [str(REPO / "static" / "vendor" / f) for f in ("marked.umd.js", "purify.min.js", "highlight.min.js")]
    libs.append(str(RENDERER))
    reply = ("<img src=x onerror=alert(1)> [click](javascript:alert(1)) "
             "<script>alert(1)</script> [ok](https://example.test/)")
    out = subprocess.run(["node", "-e", _JS_HARNESS, json.dumps(libs), reply], capture_output=True,
                         text=True, cwd=REPO, env=os.environ.copy(), timeout=60)
    assert out.stdout.strip() == "SAFE 2", out.stdout + out.stderr
