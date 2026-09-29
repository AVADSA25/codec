"""Installable PWA with an offline shell (UI phase 2, P2.14; docs/P2.14-DESIGN.md).

The service worker caches only the static shell and never an API response;
page loads go to the network and fall back to a calm offline page when the Mac
is unreachable (network error or a tunnel gateway error). The manifest has
real icons and three shortcuts; the shell registers the worker and offers
Install; Chat accepts only the fixed "start my day" starter from a link.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import routes.health as health

REPO = Path(__file__).resolve().parent.parent
_SW_FILE = REPO / "static" / "sw.js"
SW = _SW_FILE.read_text(encoding="utf-8") if _SW_FILE.exists() else ""
SHELL = (REPO / "static" / "codec-shell.js").read_text(encoding="utf-8")
APP_PAGES = ["codec_dashboard.html", "codec_chat.html", "codec_voice.html", "codec_vibe.html",
             "codec_tasks.html", "codec_cortex.html", "codec_audit.html"]


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(health.router)
    return TestClient(app)


def test_worker_is_served_from_the_root_without_a_login(client):
    r = client.get("/sw.js")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/javascript")
    assert r.headers["cache-control"] == "no-cache" and r.headers["service-worker-allowed"] == "/"
    assert "self.addEventListener('fetch'" in r.text
    src = (REPO / "codec_dashboard.py").read_text(encoding="utf-8")
    public = re.search(r"PUBLIC_ROUTES = \{([^}]*)\}", src).group(1)
    assert '"/sw.js"' in public


def test_worker_precaches_only_static_files_that_exist():
    assert "var PRECACHE" in SW, "no worker"
    listed = re.findall(r"'(/static/[^']+)'", SW[SW.index("var PRECACHE"):SW.index("];")])
    assert listed and all(p.startswith("/static/") for p in listed)
    for p in listed:
        assert (REPO / p.lstrip("/")).is_file(), f"{p} does not exist"
    assert "/static/codec.css" in listed and any(p.endswith(".woff2") for p in listed)


def test_manifest_has_real_icons_and_three_shortcuts(client):
    m = client.get("/manifest.json").json()
    assert m["display"] == "standalone" and m["start_url"] == "/" and m["theme_color"] == "#121215"
    icons = {(i["sizes"], i["purpose"]): i["src"] for i in m["icons"]}
    assert set(icons) == {("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")}
    for (sizes, _), src in icons.items():
        path = REPO / src.lstrip("/")
        assert path.is_file(), src
        w, h = Image.open(path).size
        assert f"{w}x{h}" == sizes, f"{src} is {w}x{h}, declared {sizes}"
    assert Image.open(REPO / "static/icons/maskable-512.png").getpixel((4, 4))[3] == 255, "maskable needs an opaque background"
    shortcuts = {s["name"]: s["url"] for s in m["shortcuts"]}
    assert shortcuts == {"New chat": "/chat#new", "Voice": "/voice", "Start my day": "/chat#starter=day"}


@pytest.mark.parametrize("name", APP_PAGES)
def test_app_pages_link_the_manifest_and_touch_icon(name):
    src = (REPO / name).read_text(encoding="utf-8")
    assert '<link rel="manifest" href="/manifest.json">' in src
    assert '<link rel="apple-touch-icon" href="/static/icons/apple-touch-icon.png">' in src
    assert Image.open(REPO / "static/icons/apple-touch-icon.png").size == (180, 180)


def test_shell_registers_the_worker_and_offers_install():
    assert "navigator.serviceWorker.register('/sw.js')" in SHELL and "window.isSecureContext" in SHELL
    assert 'id="installBtn"' in SHELL and "Install CODEC" in SHELL and 'id="installHint"' in SHELL
    assert "window.addEventListener('beforeinstallprompt'" in SHELL and "e.preventDefault()" in SHELL
    assert "ev.prompt();" in SHELL and "display-mode: standalone" in SHELL
    dash = (REPO / "codec_dashboard.html").read_text(encoding="utf-8")
    assert "addEventListener('beforeinstallprompt'" not in dash, "one capture, in the shell"


def test_chat_accepts_only_the_fixed_starter():
    chat = (REPO / "codec_chat.html").read_text(encoding="utf-8")
    fn = chat[chat.index("function _chatFromHash()"):chat.index("(function bootSession(){")]
    assert "if(h==='#starter=day'){" in fn and "sendStarter('start my day')" in fn
    assert "decodeURIComponent(h.slice(" not in fn.split("if(h==='#starter=day'){")[1].split("return true;")[0]
    assert "#starter=' +" not in chat and "starter=([" not in chat, "no free-form starter text from a link"


# The worker, run under Node with a fake `self`, `caches` and `fetch`.
_HARNESS = r"""
const vm = require('vm'), fs = require('fs');
const code = fs.readFileSync(process.argv[1], 'utf8');
const handlers = {}, store = new Map();
let network = async () => new Response('page', { status: 200 });
const cache = {
  addAll: async (urls) => { for (const u of urls) store.set(u, new Response('pre:' + u)); },
  match: async (req) => store.get(typeof req === 'string' ? req : new URL(req.url).pathname + new URL(req.url).search),
  put: async (req, res) => { store.set(new URL(req.url).pathname + new URL(req.url).search, res); },
};
const self = { location: { origin: 'https://codec.test' }, addEventListener: (t, f) => { handlers[t] = f; },
               skipWaiting: async () => {}, clients: { claim: async () => {} } };
const sandbox = { self, caches: { open: async () => cache, keys: async () => [], delete: async () => true },
                  fetch: (req) => network(req), Response, URL, Promise, console };
vm.runInNewContext(code, sandbox);
async function dispatch(url, init) {
  const req = Object.assign(new Request('https://codec.test' + url, init && init.method ? { method: init.method } : {}),
                            {}); Object.defineProperty(req, 'mode', { value: (init && init.mode) || 'cors' });
  let responded = null; const waits = [];
  handlers.fetch({ request: req, respondWith: (p) => { responded = p; }, waitUntil: (p) => waits.push(p) });
  if (!responded) return { handled: false };
  const res = await responded; const text = await res.text();
  return { handled: true, status: res.status, text: text.slice(0, 200) };
}
(async () => {
  const out = {};
  await new Promise(r => handlers.install({ waitUntil: (p) => p.then(r) }));
  out.precached = store.size;
  out.api = await dispatch('/api/qchat/sessions');
  out.post = await dispatch('/static/codec.css', { method: 'POST' });
  out.page = await dispatch('/chat', { mode: 'navigate' });
  network = async () => new Response('Cloudflare', { status: 530 });
  out.gateway = await dispatch('/chat', { mode: 'navigate' });
  network = async () => { throw new TypeError('offline'); };
  out.offline = await dispatch('/tasks', { mode: 'navigate' });
  out.cachedCss = await dispatch('/static/codec.css');
  network = async () => new Response('fresh', { status: 200 });
  out.newAsset = await dispatch('/static/codec-shell.js?v=abc');
  out.stored = [...store.keys()].filter(k => !k.startsWith('/static/'));
  console.log(JSON.stringify(out));
})();
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_worker_behaviour_under_node():
    out = subprocess.run(["node", "-e", _HARNESS, str(REPO / "static" / "sw.js")], capture_output=True,
                         text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    r = json.loads(out.stdout.strip().splitlines()[-1])
    assert r["precached"] >= 8
    assert r["api"] == {"handled": False}, "API requests go straight to the network"
    assert r["post"] == {"handled": False}, "writes are never touched"
    assert r["page"]["status"] == 200 and r["page"]["text"] == "page", "pages come from the network"
    assert r["gateway"]["status"] == 503 and "not reachable" in r["gateway"]["text"]
    assert r["offline"]["status"] == 503 and "not reachable" in r["offline"]["text"]
    assert r["cachedCss"]["text"].startswith("pre:/static/codec.css"), "static files come from the cache"
    assert r["newAsset"]["text"] == "fresh"
    assert r["stored"] == [], "nothing outside /static is ever stored"
