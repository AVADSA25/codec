"""Shared app shell (UI phase 2, P2.1; docs/P2.1-DESIGN.md).

Seven pages load static/codec-shell.js first in <body> and lose their own
header, nav and side panel. The shell's top bar and phone tab bar sit in the
flow, text size scales --fs-scale on every page (no root zoom), Voice, Vibe,
Cortex, Audit and Auth are on the type scale, Home's tab row is short, and
Chat opens /chat#new and /chat#session=<id>.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SHELL = REPO / "static" / "codec-shell.js"
CSS = (REPO / "static" / "codec.css").read_text(encoding="utf-8")
PAGES = {"codec_dashboard.html": "home", "codec_chat.html": "chat", "codec_voice.html": "voice",
         "codec_vibe.html": "vibe", "codec_tasks.html": "tasks", "codec_cortex.html": "cortex",
         "codec_audit.html": "audit"}
SCALED = ["codec_voice.html", "codec_vibe.html", "codec_cortex.html", "codec_audit.html", "codec_auth.html"]


def _src(name: str) -> str:
    return (REPO / name).read_text(encoding="utf-8")


def _css_rule(css: str, selector: str) -> str:
    m = re.search(r"(?:^|\}|\*/)\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert m, f"no rule for {selector}"
    return m.group(1)


@pytest.mark.parametrize("name,page", sorted(PAGES.items()))
def test_page_loads_the_shell_first_and_has_no_copy_of_its_own(name, page):
    src = _src(name)
    body = src[src.index("<body"):]
    first = re.search(r"<body[^>]*>\s*(<[^>]+>)", body).group(1)
    assert re.fullmatch(r'<script src="/static/codec-shell\.js(\?v=[0-9a-f]+)?" data-page="' + page + r'"[^>]*>', first), \
        f"{name}: the shell is not the first element of <body>: {first}"
    for gone in ('class="page-nav"', 'class="side-panel"', 'id="sidePanel"', 'class="header"', "<header",
                 ".page-nav", ".side-panel", ".nav-link", "function openSidePanel", "function lockSession",
                 "function applyTheme", "function setTextSize", "api/notifications/count"):
        assert gone not in src, f"{name} still carries its own {gone}"
    assert 'class="cs-main' in src or "cs-main\"" in src or " cs-main" in src, f"{name} has no .cs-main root"


def test_auth_has_no_shell_but_scales_text_without_zoom():
    src = _src("codec_auth.html")
    assert '<script src="/static/codec-shell.js' not in src
    assert "--fs-scale" in src and "small:0.93,medium:1,large:1.12" in src


@pytest.mark.parametrize("name", sorted(PAGES) + ["codec_auth.html"])
def test_no_page_zooms_the_root(name):
    src = _src(name)
    assert "style.zoom" not in src and "--tz" not in src, f"{name} still zooms the root"


@pytest.mark.parametrize("name", SCALED)
def test_page_is_on_the_type_scale(name):
    raw = re.findall(r"font-size:\s*\d+(?:\.\d+)?px", _src(name))
    assert not raw, f"{name} has raw px font sizes: {raw[:5]}"


def test_shell_css_bars_are_in_the_flow():
    assert "--cs-side-w: 272px" in CSS and "--cs-side-w: 64px" in CSS
    assert "@media (max-width: 767px)" in CSS
    for sel in (".cs-top", ".cs-tabs"):
        for m in re.finditer(r"(?:^|\})\s*" + re.escape(sel) + r"\s*\{([^}]*)\}", CSS):
            assert "position: fixed" not in m.group(1), f"{sel} must stay in the flow"
    phone = CSS[CSS.index("@media (max-width: 767px)"):]
    tabs = re.search(r"\.cs-tabs\s*\{([^}]*)\}", phone).group(1)
    assert "order: 99" in tabs and "display: flex" in tabs and "safe-area-inset-bottom" in tabs
    assert "position: fixed" in _css_rule(CSS, ".cs-side"), "the sidebar is the one fixed column"


def test_shell_owns_the_shared_behaviour():
    js = SHELL.read_text(encoding="utf-8")
    for name in ("openSidePanel", "closeSidePanel", "applyTheme", "setThemePref", "toggleTheme",
                 "applyTextSize", "setTextSize", "lockSession"):
        assert f"window.{name} = " in js, f"the shell does not export {name}"
    assert "small: 0.93, medium: 1, large: 1.12" in js and "--fs-scale" in js
    assert "zoom" not in js.replace("zoomed", "")
    for el in ('id="voiceState"', 'id="muteX1"', 'id="muteX2"', 'id="textSizeGroup"', 'id="wakeState"',
               'id="menuBtn"', 'id="statusDot"', 'id="hdrModelSlot"'):
        assert el in js, f"the shell does not build {el}"
    # The quick-settings button is a sliders icon titled for what it opens, not "Home".
    assert 'title="Quick settings"' in js and 'title="Home"' not in js
    # Chat ids from the server go through escaping, never into inline handlers.
    assert "data-open=\"' + esc(id)" in js and "encodeURIComponent(id)" in js
    assert "onclick=\"loadSession" not in js


def test_home_tab_row_is_short_and_reads_the_hash():
    src = _src("codec_dashboard.html")
    tabs = re.findall(r'<button class="tab[^"]*" onclick="showTab\(\'(\w+)\'\)" id="tab-\w+"', src)
    assert tabs == ["chat", "history", "skills", "settings"]
    sub = src[src.index('id="settingsSubnav"'):]
    sub = sub[:sub.index("</div>")]
    assert "showTab('connector')" in sub and "showTab('audit')" in sub and 'href="/cortex"' in sub
    assert "cortexFrame" not in src
    assert "window.addEventListener('hashchange', _tabFromHash)" in src and "_tabFromHash();" in src


def test_chat_opens_new_and_saved_chats_from_the_hash():
    src = _src("codec_chat.html")
    fn = src[src.index("function _chatFromHash()"):src.index("(function bootSession(){")]
    assert "h!=='#new'&&h.indexOf('#session=')!==0" in fn
    assert "loadSession(want)" in fn and "startNewSession()" in fn and "history.replaceState" in fn
    assert "window.addEventListener('hashchange',_chatFromHash)" in src
    assert "if(_chatFromHash())return;" in src
    assert 'id="sidebar"' not in src and "function openSidebar" not in src
    assert "CodecShell.setActiveChat" in src and "CodecShell.refreshHistorySoon()" in src


def test_shell_script_is_stamped():
    tool = (REPO / "tools" / "stamp_static.py").read_text(encoding="utf-8")
    assert '"codec-shell.js"' in tool


def _jsdom_available() -> bool:
    if shutil.which("node") is None:
        return False
    r = subprocess.run(["node", "-e", "require.resolve('jsdom')"], capture_output=True,
                       cwd=REPO, env=os.environ.copy(), timeout=20)
    return r.returncode == 0


_HARNESS = r"""
const { JSDOM } = require('jsdom');
const fs = require('fs');
const [shellPath, width] = process.argv.slice(1);
const html = '<!doctype html><html><head><meta name="theme-color" content="#121215"></head><body>' +
  '<script>window.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve([]) });</script>' +
  '<script data-page="tasks" data-title="Tasks" data-status>' +
  fs.readFileSync(shellPath, 'utf8').replace(/<\/script/gi, '<\\/script') + '</script>' +
  '<div class="cs-main">page</div></body></html>';
const dom = new JSDOM(html, { url: 'http://localhost/tasks', runScripts: 'dangerously', pretendToBeVisual: true,
  beforeParse(w) { Object.defineProperty(w, 'innerWidth', { value: +width, configurable: true }); } });
const w = dom.window;
w.document.addEventListener('DOMContentLoaded', () => setTimeout(() => {
  const d = w.document, b = d.body, out = {};
  out.order = [...b.children].map(e => e.id || e.className).slice(0, 7);
  out.navOn = d.querySelector('.cs-nav .cs-on').textContent;
  out.tabOn = d.querySelector('.cs-tab.cs-on').textContent;
  out.rail0 = b.classList.contains('cs-rail');
  w.setTextSize('large');
  out.scale = d.documentElement.style.getPropertyValue('--fs-scale');
  out.zoom = d.documentElement.style.zoom || '';
  w.setThemePref('light');
  out.theme = d.documentElement.getAttribute('data-theme');
  out.pressed = d.querySelector('[data-theme-opt="light"]').getAttribute('aria-pressed');
  w.CodecShell.toggleRail();
  out.rail1 = b.classList.contains('cs-rail');
  w.CodecShell.openDrawer();
  out.drawer = b.classList.contains('cs-drawer');
  w.openSidePanel();
  out.panel = d.getElementById('sidePanel').classList.contains('open');
  out.screenHidden = d.getElementById('screenBtn').hidden;
  console.log(JSON.stringify(out));
  process.exit(0);  // the shell's 30-second Inbox poll would keep node alive
}, 50));
"""


def _run_shell(width: int) -> dict:
    out = subprocess.run(["node", "-e", _HARNESS, str(SHELL), str(width)], capture_output=True, text=True,
                         cwd=REPO, env=os.environ.copy(), timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(not _jsdom_available(), reason="node with jsdom not resolvable (set NODE_PATH)")
def test_shell_mounts_and_switches_on_desktop():
    r = _run_shell(1470)
    # After the harness's fetch stub (no id), the shell comes before the page content.
    # P2.8's read-aloud strip (csPlayer) mounts between the top bar and the tab bar.
    assert [x for x in r["order"] if x][:5] == ["csSide", "csScrim", "csTop", "csPlayer", "csTabs"], r["order"]
    assert r["navOn"] == "Tasks" and r["tabOn"] == "Tasks"
    assert r["scale"] == "1.12" and r["zoom"] == ""
    assert r["theme"] == "light" and r["pressed"] == "true"
    assert r["rail0"] is False and r["rail1"] is True, "Cmd+Shift+S / collapse makes the 64px rail"
    assert r["drawer"] is False, "no drawer on the desktop"
    assert r["panel"] is True and r["screenHidden"] is True, "items the page cannot run are hidden"


@pytest.mark.skipif(not _jsdom_available(), reason="node with jsdom not resolvable (set NODE_PATH)")
def test_shell_opens_the_drawer_on_the_phone():
    r = _run_shell(375)
    assert r["rail0"] is False and r["drawer"] is True
