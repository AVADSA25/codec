"""Readable Cortex and Audit (UI phase 2, P2.15; docs/P2.15-DESIGN.md).

Settings > Audit pages past 200 events, opens each record, has a time range and checks the log's
signatures through a read-only route; the old /audit page is gone. Cortex has no text under 12px,
zoom buttons, live counts and model names, an Open link on the page's own host, escaped API text
and bottom sheets on the phone. Nothing here touches ~/.codec: tmp paths and fakes.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent
CORTEX = (REPO / "codec_cortex.html").read_text(encoding="utf-8")
HOME = (REPO / "codec_dashboard.html").read_text(encoding="utf-8")
SHELL = (REPO / "static" / "codec-shell.js").read_text(encoding="utf-8")


@pytest.fixture
def audit(tmp_path, monkeypatch):
    import codec_audit
    import routes.audit as ra
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", tmp_path / "audit.log")
    monkeypatch.setattr(codec_audit, "_AUDIT_DIR", tmp_path)
    app = FastAPI()
    app.include_router(ra.router)
    return TestClient(app), tmp_path / "audit.log"


def _write(log: Path, n: int) -> None:
    with open(log, "a", encoding="utf-8") as f:
        for i in range(n):
            f.write(json.dumps({"ts": f"2026-10-06T10:{i // 60:02d}:{i % 60:02d}.000+00:00", "schema": 1,
                                "event": "tool_result", "source": "codec-test", "tool": f"t{i}", "outcome": "ok"}) + "\n")


def test_the_stream_pages_past_200_events(audit):
    client, log = audit
    _write(log, 450)
    pages = [client.get("/api/audit/stream", params={"limit": 200, "offset": o}).json() for o in (0, 200, 400)]
    assert [len(p["events"]) for p in pages] == [200, 200, 50]
    assert [p["has_more"] for p in pages] == [True, True, False]
    assert pages[0]["events"][0]["tool"] == "t449" and pages[1]["events"][0]["tool"] == "t249"
    assert pages[2]["events"][-1]["tool"] == "t0", "newest first, nothing skipped or repeated"
    tools = [e["tool"] for p in pages for e in p["events"]]
    assert len(set(tools)) == 450
    assert client.get("/api/audit/stream", params={"offset": -5, "limit": 10}).json()["events"][0]["tool"] == "t449"


def test_verify_wraps_the_signature_check_and_gives_counts_only(audit, monkeypatch):
    import codec_audit
    client, _ = audit
    calls = []

    def fake(path=None):
        calls.append(path)
        return {"path": os.path.expanduser("~/.codec/audit.log"), "total_lines": 95, "signed_lines": 90,
                "unsigned_lines": 5, "broken_lines": 0, "first_broken_line_no": None, "integrity_ok": True, "error": None}

    monkeypatch.setattr(codec_audit, "verify_audit_log", fake)
    d = client.get("/api/audit/verify").json()
    assert calls == [None], "today's log, through verify_audit_log"
    assert d == {"total_lines": 95, "signed_lines": 90, "unsigned_lines": 5, "broken_lines": 0,
                 "first_broken_line_no": None, "integrity_ok": True, "error": None}, "no file path"
    monkeypatch.setattr(codec_audit, "verify_audit_log",
                        lambda path=None: {"integrity_ok": False, "error": "Audit log not found: " + os.path.expanduser("~/.codec/audit.log")})
    assert client.get("/api/audit/verify").json()["error"] == "Audit log not found: ~/.codec/audit.log"
    assert client.post("/api/audit/verify").status_code == 405, "read-only"


def test_cortex_skills_reports_the_crew_count():
    import routes.cortex as rc
    from codec_agents import CREW_REGISTRY
    app = FastAPI()
    app.include_router(rc.router)
    d = TestClient(app).get("/api/cortex/skills").json()
    assert d["crew_count"] == len(CREW_REGISTRY) and d["count"] == len(d["skills"])


def test_the_old_audit_page_is_retired():
    import codec_dashboard
    assert not (REPO / "codec_audit.html").exists()
    r = asyncio.run(codec_dashboard.audit_page())
    assert r.status_code in (302, 307) and r.headers["location"] == "/#audit"
    assert "{ label: 'Audit', href: '/#audit', icon: 'doc' }" in SHELL
    assert "'/audit'" not in SHELL


def test_settings_audit_has_buttons_range_paging_details_and_verify():
    assert ('<button type="button" class="audit-pill active" data-cat="\' + c + \'" aria-pressed="true">' in HOME
            and "pill.setAttribute('aria-pressed', String(on));" in HOME)
    assert '<select id="auditRange" aria-label="Time range"' in HOME and '<option value="7d">Last 7 days</option>' in HOME
    assert "var since = _auditSinceFromRange(_auditCurrentRange);" in HOME and "CodecShell.menu(rangeEl)" in HOME
    assert 'id="auditMoreBtn" hidden>Show older events</button>' in HOME and "_auditFetchEvents(true)" in HOME
    assert "'&limit=' + limit + '&offset=' + offset" in HOME and "_auditHasMore = !!data.has_more;" in HOME
    assert 'role="button" tabindex="0" aria-expanded="\' + open + \'"' in HOME and "pre.textContent = open ? _auditRecord(ev) : '';" in HOME
    assert "fetch('/api/audit/verify')" in HOME and 'id="auditVerifyResult" role="status" aria-live="polite"' in HOME
    assert ".audit-pill:focus-visible" in HOME and ".audit-event:focus-visible" in HOME


def test_cortex_text_sizes_zoom_live_text_link_and_phone_sheets():
    css = CORTEX[:CORTEX.index("</style>")]
    small = re.findall(r"calc\((\d+(?:\.\d+)?)px \* var\(--fs-scale\)\)", css)
    assert all(float(n) >= 12 for n in small), small
    assert all(float(n) >= 12 for n in re.findall(r"font-size:\s*(\d+(?:\.\d+)?)px", css))
    for label in ("Zoom in", "Zoom out", "Fit to screen"):
        assert f'aria-label="{label}"' in CORTEX
    assert "function zoomBy(f)" in CORTEX
    assert not re.search(r"\b89 [Ss]kills|89 built-in|\b12 Agent Crews|12 autonomous|Qwen2\.5-VL-7B|>7 Products<", CORTEX)
    assert "http://localhost:" not in CORTEX and "serviceUrl(n.port)" in CORTEX
    assert "esc(s.name)" in CORTEX and "esc(triggers)" in CORTEX and "esc(msg.substring(0,60))" in CORTEX
    phone = CORTEX[CORTEX.index("@media (max-width:767px){"):]
    for needle in ("#skills-panel{top:auto;left:0;right:0;bottom:0", "#cxPanels{display:block;position:fixed",
                   "#cxPanelsBtn{display:flex", "#cxPanels.open{transform:translateY(0)}"):
        assert needle in phone[:2500], needle


_JS_HARNESS = r"""
const fs = require('fs');
const { JSDOM } = require('jsdom');
const html = fs.readFileSync(process.argv[1], 'utf8');
const API = {
  '/api/cortex/skills': { skills: [{ name: '<img src=x onerror="window.__pwn=1">', triggers: ['<b>bold</b>'] }], count: 91, crew_count: 12 },
  '/api/config': { llm: { llm_model: 'org/Model<i>X' }, tts: { tts_voice: '<script>v</script>' }, stt: {}, wake: {},
                   vision: { vision_model: 'org/Vision-<u>Y' } },
  '/api/cortex/logs/dispatch': { logs: '12:00:01 [CODEC] said <img src=y onerror="window.__pwn2=1">' },
  '/api/cortex/health': { dashboard: 'ok' },
};
function page(url) {
  return new JSDOM(html, { runScripts: 'dangerously', pretendToBeVisual: true, url: url, beforeParse(w) {
    w.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {} });
    w.fetch = (u) => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(API[String(u).split('?')[0]] || {}) });
  } }).window;
}
(async () => {
  const w = page('http://192.168.1.20:8090/cortex'), d = w.document, out = {};
  await new Promise(r => setTimeout(r, 200));
  out.skillImgs = d.querySelectorAll('#skillList img, #skillList b').length;
  out.skillText = d.getElementById('skillList').textContent;
  out.configTags = d.querySelectorAll('#settingsContent script, #settingsContent i').length;
  out.configText = d.getElementById('settingsContent').textContent;
  out.feedImgs = d.querySelectorAll('#activityList img').length;
  out.feedText = d.getElementById('activityList').textContent;
  out.pwn = !!(w.__pwn || w.__pwn2);
  out.productsBtn = d.getElementById('vsProducts').textContent;
  w.switchView('products');
  const pv = d.getElementById('productsView');
  out.products = pv.textContent.includes('91 skills fire instantly') && pv.textContent.includes('250K Context + 12 Agent Crews') &&
    pv.textContent.includes('7 Products') && !pv.innerHTML.includes('{skills}') && !pv.innerHTML.includes('{crews}');
  w.switchView('neural');
  w.showDetail('vision');
  const info = d.getElementById('detailInfo');
  out.vision = info.textContent.includes('Vision-<u>Y for screen reading') && !info.querySelector('u');
  w.showDetail('qwen');
  const a = d.querySelector('#detailInfo a.detail-btn');
  out.link = a ? [a.getAttribute('href'), a.textContent] : null;
  out.llm = d.getElementById('detailInfo').textContent.includes('Model<i>X. Main language model') && !d.querySelector('#detailInfo i');
  const s0 = w.pan.scale; w.zoomBy(1.25); const s1 = w.pan.scale; w.zoomBy(0.8);
  out.zoom = [Math.round(s1 / s0 * 100), Math.round(w.pan.scale / s0 * 100)];
  w.togglePanels();
  out.sheet = [d.getElementById('cxPanels').classList.contains('open'), d.getElementById('cxPanelsBtn').getAttribute('aria-expanded')];
  const t = page('https://codec.example.com/cortex');
  await new Promise(r => setTimeout(r, 100));
  t.showDetail('qwen');
  out.tunnelLink = !!t.document.querySelector('#detailInfo a.detail-btn');
  console.log(JSON.stringify(out)); process.exit(0);
})().catch(e => { console.log('ERR ' + e.message); process.exit(1); });
setTimeout(() => { console.log('TIMEOUT'); process.exit(2); }, 20000);
"""


def _jsdom_available() -> bool:
    if shutil.which("node") is None:
        return False
    r = subprocess.run(["node", "-e", "require.resolve('jsdom')"], capture_output=True, cwd=REPO, env=os.environ.copy(), timeout=20)
    return r.returncode == 0


@pytest.mark.skipif(not _jsdom_available(), reason="node with jsdom not resolvable (set NODE_PATH)")
def test_cortex_escapes_api_text_and_fills_live_counts_under_jsdom():
    out = subprocess.run(["node", "-e", _JS_HARNESS, str(REPO / "codec_cortex.html")], capture_output=True,
                         text=True, cwd=REPO, env=os.environ.copy(), timeout=60)
    line = [ln for ln in out.stdout.splitlines() if ln.startswith("{") or ln.startswith("ERR") or ln == "TIMEOUT"][-1]
    d = json.loads(line)
    assert d["pwn"] is False and d["skillImgs"] == 0 and '<img src=x onerror="window.__pwn=1">' in d["skillText"]
    assert "<b>bold</b>" in d["skillText"]
    assert d["configTags"] == 0 and "<script>v</script>" in d["configText"]
    assert d["feedImgs"] == 0 and "said <img src=y" in d["feedText"]
    assert d["productsBtn"] == "7 Products" and d["products"] is True
    assert d["vision"] is True and d["llm"] is True
    assert d["link"] == ["http://192.168.1.20:8083/", "Open :8083"], "the page's own host, not localhost"
    assert d["tunnelLink"] is False, "no link to a port a tunnel host does not serve"
    assert d["zoom"] == [125, 100] and d["sheet"] == [True, "true"]
