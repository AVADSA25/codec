"""Math and diagrams in replies (UI phase 2, MATH; docs/MATH-DESIGN.md).

KaTeX and Mermaid are vendored under static/vendor (a SHA-256 row each in
SOURCES.md), loaded by static/codec-md.js only when a reply needs them, and
both outputs pass through DOMPurify before they reach the page.
"""
from __future__ import annotations

import hashlib
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
STATIC = REPO / "static"
RENDERER = (STATIC / "codec-md.js").read_text(encoding="utf-8")
SOURCES = (STATIC / "vendor" / "SOURCES.md").read_text(encoding="utf-8")
FONTS = sorted((STATIC / "vendor" / "katex" / "fonts").glob("*.woff2"))


def test_every_vendored_file_matches_its_sources_row():
    rows = dict(re.findall(r"^\| `(vendor/(?:katex/[^`]+|mermaid\.min\.js))` \|[^|]+\|[^|]+\| `([0-9a-f]{64})` \|$",
                           SOURCES, re.M))
    assert len(FONTS) == 20 and len(rows) == 23, (len(FONTS), sorted(rows))
    for rel, digest in rows.items():
        assert hashlib.sha256((STATIC / rel).read_bytes()).hexdigest() == digest, rel
    assert "katex 0.18.9" in SOURCES and "mermaid 12.0.0" in SOURCES
    assert (STATIC / "vendor" / "katex" / "LICENSE.txt").is_file() and (STATIC / "vendor" / "LICENSE-mermaid.txt").is_file()


def test_static_serves_them_with_the_right_types(monkeypatch):
    monkeypatch.setattr(codec_config, "get_dashboard_token", lambda: "")
    monkeypatch.setattr(codec_dashboard, "AUTH_ENABLED", True)
    monkeypatch.setattr(shared, "AUTH_ENABLED", True)
    monkeypatch.setattr(codec_dashboard, "_auth_available", lambda: True)
    client = TestClient(app, client=("127.0.0.1", 5000))
    for path, kind in (("/static/vendor/katex/katex.min.js?v=0.18.9", "text/javascript"),
                       ("/static/vendor/katex/katex.min.css?v=0.18.9", "text/css"),
                       ("/static/vendor/katex/fonts/" + FONTS[0].name, "font/woff2"),
                       ("/static/vendor/mermaid.min.js?v=12.0.0", "text/javascript")):
        r = client.get(path)
        assert r.status_code == 200 and r.headers["content-type"].startswith(kind), path
    assert client.get("/static/vendor/katex/LICENSE.txt").status_code == 404, "licence texts are not served"


def test_no_page_loads_them_up_front_and_the_renderer_loads_them_by_version():
    for page in REPO.glob("*.html"):
        src = page.read_text(encoding="utf-8").lower()
        assert "katex" not in src and "mermaid" not in src, page.name
    assert "js: '/static/vendor/katex/katex.min.js?v=0.18.9'" in RENDERER
    assert "css: '/static/vendor/katex/katex.min.css?v=0.18.9'" in RENDERER
    assert "js: '/static/vendor/mermaid.min.js?v=12.0.0'" in RENDERER
    assert "need('katex')" in RENDERER and "need('mermaid')" in RENDERER
    assert "if (!streaming) {" in RENDERER, "diagrams are drawn for a finished reply only"


def test_both_libraries_run_locked_down_and_through_dompurify():
    assert "securityLevel: 'strict'" in RENDERER and "htmlLabels: false" in RENDERER
    assert "suppressErrorRendering: true" in RENDERER and "startOnLoad: false" in RENDERER
    assert "trust: false" in RENDERER and "maxExpand: 500" in RENDERER
    assert "out = p.sanitize(k.renderToString(tex," in RENDERER
    assert "var xml = safeSvg(res && res.svg, id);" in RENDERER
    assert "img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(xml);" in RENDERER
    assert "FORBID_TAGS: ['script', 'foreignObject', 'image', 'a', 'iframe']" in RENDERER


_JS_HARNESS = r"""
const fs = require('fs');
const { JSDOM } = require('jsdom');
const w = new JSDOM('<!doctype html><head></head><body></body>', {runScripts: 'outside-only', pretendToBeVisual: true}).window;
for (const f of JSON.parse(process.argv[1])) w.eval(fs.readFileSync(f, 'utf8'));
const md = w.codecMarkdown, out = {};
const probe = (t) => { const d = w.document.createElement('div'); d.innerHTML = md.html(t); return d; };
const marks = (d) => [...d.querySelectorAll('code[class^="language-math"]')].map(c => c.className.replace('language-', '') + ':' + c.textContent.trim());
out.marks = marks(probe('Area $x^2$ and \\(\\alpha\\), inline $$a+b$$ too.\n\n$$\n\\frac{1}{2}\n$$\n\n\\[\\sqrt{2}\\]\n\n```math\ne^{i\\pi}\n```'));
out.money = marks(probe('It costs $5 and $10. Set $PATH:$HOME. Code `$x$` stays. A $ 5 $ gap.'));
w.eval(fs.readFileSync(process.argv[2], 'utf8'));
const k = md.texHtml('x^2', false);
out.katex = !!k && k.includes('class="katex"') && k.includes('<math');
const bad = md.texHtml('\\href{javascript:alert(1)}{x} \\url{https://example.test}', false) || '';
out.hrefInert = bad.includes('\\href') && !/<a[\s>]|\shref=|xlink:href/i.test(bad);  // red source text, never a link
const el = w.document.createElement('div');
w.document.body.appendChild(el);
md.render(el, 'Inline $y^3$ here.\n\n$$\n\\sum_{i=1}^n i\n$$');
for (const l of w.document.querySelectorAll('link')) if (l.onload) l.onload();
setTimeout(() => {
  out.rendered = el.querySelectorAll('.md-math .katex').length;
  out.leftMarks = el.querySelectorAll('code[class^="language-math"]').length;
  const svg = '<svg xmlns="http://www.w3.org/2000/svg" id="codec-mmd-1" viewBox="0 0 120.4 40" width="100%" onload="alert(1)">' +
    '<style>@import url(https://evil.test/a.css); .n{fill:url(https://evil.test/t)} .e{marker-end:url(#codec-mmd-1_arrow)}</style>' +
    '<script>alert(2)</script><foreignObject><div>hi</div></foreignObject>' +
    '<a href="https://evil.test/"><text>link</text></a><image href="https://evil.test/p.png"/>' +
    '<use href="https://evil.test/#x"/><rect id="csToast" style="fill:url(https://evil.test/r)" onclick="alert(3)"/>' +
    '<g id="codec-mmd-1-node"><path marker-end="url(#codec-mmd-1_arrow)"/></g></svg>';
  const xml = md.safeSvg(svg, 'codec-mmd-1');
  out.svg = {
    outside: /evil\.test/.test(xml), script: /<script|alert/i.test(xml), foreign: /foreignObject/i.test(xml),
    handlers: /\son\w+=/i.test(xml), foreignId: /id="csToast"/.test(xml), ownId: /id="codec-mmd-1-node"/.test(xml),
    ownUrl: xml.includes('url(#codec-mmd-1_arrow)'), size: /width="121"/.test(xml) && /height="40"/.test(xml),
    style: /<style/.test(xml), text: xml.includes('link')
  };
  console.log(JSON.stringify(out));
}, 50);
"""


def _jsdom_available() -> bool:
    if shutil.which("node") is None:
        return False
    r = subprocess.run(["node", "-e", "require.resolve('jsdom')"], capture_output=True,
                       cwd=REPO, env=os.environ.copy(), timeout=20)
    return r.returncode == 0


@pytest.fixture(scope="module")
def jsdom_run():
    if not _jsdom_available():
        pytest.skip("node with jsdom not resolvable (set NODE_PATH)")
    libs = [str(STATIC / "vendor" / f) for f in ("marked.umd.js", "purify.min.js", "highlight.min.js")]
    libs.append(str(STATIC / "codec-md.js"))
    out = subprocess.run(["node", "-e", _JS_HARNESS, json.dumps(libs), str(STATIC / "vendor" / "katex" / "katex.min.js")],
                         capture_output=True, text=True, cwd=REPO, env=os.environ.copy(), timeout=90)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_math_marks_follow_the_delimiters_and_money_stays_text(jsdom_run):
    assert jsdom_run["marks"] == ["math-inline:x^2", "math-inline:\\alpha", "math-display:a+b",
                                  "math:\\frac{1}{2}", "math:\\sqrt{2}", "math:e^{i\\pi}"]
    assert jsdom_run["money"] == [], "money, shell variables, code spans and spaced dollars are not math"


def test_katex_renders_and_links_stay_inert(jsdom_run):
    assert jsdom_run["katex"] is True, "HTML and MathML survive the math purifier"
    assert jsdom_run["hrefInert"] is True
    assert jsdom_run["rendered"] == 2 and jsdom_run["leftMarks"] == 0


def test_a_diagram_svg_is_made_safe_for_an_image(jsdom_run):
    s = jsdom_run["svg"]
    assert not s["outside"] and not s["script"] and not s["foreign"] and not s["handlers"], s
    assert not s["foreignId"] and s["ownId"] and s["ownUrl"], s
    assert s["size"] and s["style"] and s["text"], s
