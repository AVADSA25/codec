"""Model compare from a reply (UI phase 3, P3.10; docs/P3.10-DESIGN.md).

Token counts on every compare leg, the target list (local leg, Cookbook models,
registered cloud models; no AVA tiers), the run with the per-compare cloud
opt-in and the capped cloud path, and Chat's dialog with Keep this one. Nothing
here reaches a model or ~/.codec: tmp config, fakes.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent
CHAT = (REPO / "codec_chat.html").read_text(encoding="utf-8")
CLOUD = {"id": "mimo-test", "label": "MiMo test (cloud)", "base_url": "https://api.example-cloud.test/v1",
         "key_slot": "mimo_test_key", "price_in_per_m": 0.4, "price_out_per_m": 0.8, "monthly_cap_usd": 10}


class _Resp:
    def __init__(self, body, status=200):
        self.status_code, self._body, self.text = status, body, json.dumps(body)

    def json(self):
        return self._body


def test_call_hands_back_the_servers_token_counts(monkeypatch):
    import requests

    import codec_llm
    monkeypatch.setattr(requests, "post", lambda url, **kw: _Resp(
        {"choices": [{"message": {"content": "Hello"}}], "usage": {"prompt_tokens": 9, "completion_tokens": 4}}))
    usage = {}
    assert codec_llm.call([{"role": "user", "content": "hi"}], base_url="http://127.0.0.1:8083/v1", model="m",
                          usage_out=usage) == "Hello"
    assert usage == {"prompt_tokens": 9, "completion_tokens": 4}
    assert codec_llm.call([{"role": "user", "content": "hi"}], base_url="http://127.0.0.1:8083/v1", model="m") == "Hello"


def test_every_leg_reports_time_tokens_and_speed(monkeypatch):
    import codec_compare
    import codec_llm

    def fake_call(messages, base_url, model, usage_out=None, **kw):
        if model == "counted":
            usage_out.update({"completion_tokens": 120})
        if model == "broken":
            raise codec_llm.LLMError("server down")
        return "x" * 400

    monkeypatch.setattr(codec_llm, "call", fake_call)
    eps = [{"label": m, "kind": "openai", "model": m, "base_url": "http://127.0.0.1:1/v1", "tier": "local"}
           for m in ("counted", "guessed", "broken")]
    counted, guessed, broken = codec_compare.compare("q", endpoints=eps)["results"]
    assert counted["tokens"] == 120 and counted["tokens_estimated"] is False and counted["tok_s"] > 0
    assert guessed["tokens"] == 100 and guessed["tokens_estimated"] is True, "about 4 characters a token"
    assert broken["ok"] is False and broken["tokens"] is None and broken["tok_s"] is None
    assert codec_compare._speed("", {}, 1.0) == {"tokens": 0, "tokens_estimated": True, "tok_s": None}


@pytest.fixture
def cmp(tmp_path, monkeypatch):
    import codec_audit
    import codec_cloud_models as ccm
    import codec_compare
    import routes.compare as rc
    cfg = {"llm_base_url": CLOUD["base_url"], "llm_model": "mimo-test", "extra_models": [CLOUD],
           "llm_local_restore": {"llm_base_url": "http://localhost:8083/v1", "llm_model": "mlx-community/Qwen3.6-35B-A3B-4bit"}}
    (tmp_path / "config.json").write_text(json.dumps(cfg))
    monkeypatch.setattr(rc, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(ccm, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(ccm, "SPEND_PATH", str(tmp_path / "cloud_spend.json"))
    monkeypatch.setattr(ccm, "get_key", lambda entry: "test-key")
    monkeypatch.setattr(codec_compare, "_cookbook_endpoints", lambda: [
        {"label": "cookbook-gemma", "kind": "openai", "model": "google/gemma-3-12b-it-4bit",
         "base_url": "http://127.0.0.1:8111/v1", "tier": "cookbook"}])
    audits = []
    monkeypatch.setattr(codec_audit, "log_event", lambda *a, **k: audits.append((a, k)))
    app = FastAPI()
    app.include_router(rc.router)
    return SimpleNamespace(client=TestClient(app), ccm=ccm, audits=audits, tmp=tmp_path)


def test_the_targets_are_cookbook_and_registered_cloud_models_only(cmp):
    d = cmp.client.get("/api/compare/targets").json()
    assert d["local"] == {"id": "local", "label": "Qwen3.6 35B A3B (this Mac)", "model": "mlx-community/Qwen3.6-35B-A3B-4bit"}, \
        "Chat is on the cloud: the local leg is the model a switch back restores"
    assert [(t["id"], t["kind"]) for t in d["targets"]] == [("cookbook-gemma", "local"), ("cloud:mimo-test", "cloud")]
    cloud = d["targets"][1]
    assert cloud["label"] == "MiMo test (cloud)" and cloud["spent"] == 0 and cloud["cap"] == 10
    assert "base_url" not in json.dumps(d) and "endpoint" not in json.dumps(d)


def test_a_run_compares_two_legs_and_the_cloud_needs_the_tick(cmp, monkeypatch):
    import codec_llm
    called = []

    def fake_call(messages, base_url, model, usage_out=None, **kw):
        called.append((base_url, model))
        usage_out.update({"completion_tokens": 10})
        return "answer from " + model

    monkeypatch.setattr(codec_llm, "call", fake_call)
    c = cmp.client
    r = c.post("/api/compare", json={"prompt": "Why is the sky blue?", "target": "cloud:mimo-test"})
    assert r.status_code == 403 and "tick the cloud box" in r.json()["error"] and called == []
    assert c.post("/api/compare", json={"prompt": "  ", "target": "cookbook-gemma"}).status_code == 400
    assert c.post("/api/compare", json={"prompt": "x" * 8001, "target": "cookbook-gemma"}).status_code == 400
    assert c.post("/api/compare", json={"prompt": "q", "target": "ava:gemini-2.5-pro"}).status_code == 400
    res = c.post("/api/compare", json={"prompt": "Why is the sky blue?", "target": "cookbook-gemma"}).json()["results"]
    assert [x["label"] for x in res] == ["Qwen3.6 35B A3B (this Mac)", "gemma 3 12b it (Cookbook)"]
    assert called == [("http://localhost:8083/v1", "mlx-community/Qwen3.6-35B-A3B-4bit"),
                      ("http://127.0.0.1:8111/v1", "google/gemma-3-12b-it-4bit")]
    assert all(x["ok"] and x["tokens"] == 10 and x["tokens_estimated"] is False for x in res)
    res = c.post("/api/compare", json={"prompt": "Why is the sky blue?", "target": "cloud:mimo-test", "cloud_ok": True}).json()
    assert called[-1] == (CLOUD["base_url"], "mimo-test"), "the cloud leg goes through codec_llm.call"
    assert [a[0][0] for a in cmp.audits] == ["compare_run", "compare_run"]
    assert cmp.audits[-1][1]["extra"] == {"target": "cloud:mimo-test", "kind": "cloud", "ok": [True, True]}
    assert "sky" not in repr(cmp.audits), "no question or answer in the audit line"


def test_a_cloud_leg_past_its_cap_is_refused_before_anything_is_sent(cmp, monkeypatch):
    import requests
    Path(cmp.ccm.SPEND_PATH).write_text(json.dumps({cmp.ccm._month(): {"mimo-test": {"usd": 10.0}}}))

    def fake_post(url, **kw):
        if "example-cloud" in url:
            pytest.fail("the cloud was called past its cap")
        return _Resp({"choices": [{"message": {"content": "local answer"}}], "usage": {"completion_tokens": 2}})

    monkeypatch.setattr(requests, "post", fake_post)
    res = cmp.client.post("/api/compare", json={"prompt": "q", "target": "cloud:mimo-test", "cloud_ok": True}).json()["results"]
    assert res[0]["ok"] is True and res[0]["response"] == "local answer"
    assert res[1]["ok"] is False and "cap" in res[1]["error"]


def test_chat_offers_compare_on_every_reply_with_the_opt_in_and_keep():
    more = CHAT[CHAT.index("function replyMore(div,btn){"):CHAT.index("function retryOtherModel(div){")]
    assert "label:'Compare models',icon:'columns',run:function(){compareOpen(div)}" in more
    assert more.index("Compare models") > more.index("if(last){"), "outside the last-reply-only block"
    block = CHAT[CHAT.index("// ── Compare models (P3.10"):CHAT.index("// ── Composer card")]
    assert "fetch('/api/compare/targets')" in block and "fetch('/api/compare',{method:'POST'" in block
    assert "go.disabled=!t||CMP.busy||(cloud&&!document.getElementById('cmpCloudOk').checked);" in block
    assert "Use the cloud for this compare. It costs money and counts toward the monthly cap." in block
    assert "_verKeep(n,tail,'assistant');" in block and 'data-dialog="open"' in block
    assert "columns:" in (REPO / "static" / "codec-shell.js").read_text(encoding="utf-8")
    assert ".cmp-cols{display:grid;grid-template-columns:1fr 1fr" in CHAT and "@media(max-width:767px){.cmp-cols{grid-template-columns:1fr}" in CHAT


def _jsdom_available() -> bool:
    if shutil.which("node") is None:
        return False
    r = subprocess.run(["node", "-e", "require.resolve('jsdom')"], capture_output=True, cwd=REPO,
                       env=os.environ.copy(), timeout=20)
    return r.returncode == 0


_JS = r"""
const fs = require('fs');
const { JSDOM } = require('jsdom');
const src = fs.readFileSync(process.argv[1], 'utf8');
const cut = (a, b) => src.slice(src.indexOf(a), src.indexOf(b));
const code = cut('var _verPending=null;', '// ── Thumbs feedback (P2.7)') +
  cut('function _isLastReply(div)', 'function replyMore(div,btn)') +
  cut('// ── Compare models (P3.10', '// ── Composer card') +
  cut('function _questionBefore(msgEl)', '// Read an answer aloud (P2.8)');
const dom = new JSDOM('<!doctype html><body><div id="messages"></div></body>', { runScripts: 'outside-only', pretendToBeVisual: true });
const w = dom.window, posts = [];
w.fetch = (u, o) => {
  if (u === '/api/compare/targets') return Promise.resolve({ ok: true, json: () => Promise.resolve({
    local: { id: 'local', label: 'Qwen3.6 (this Mac)', model: 'q' },
    targets: [{ id: 'cookbook-gemma', label: 'Gemma (Cookbook)', kind: 'local' },
              { id: 'cloud:mimo', label: 'MiMo (cloud)', kind: 'cloud', spent: 1.2, cap: 10 }] }) });
  posts.push(JSON.parse(o.body));
  return Promise.resolve({ ok: true, json: () => Promise.resolve({ results: [
    { label: 'Qwen3.6 (this Mac)', ok: true, response: 'Local answer', elapsed_ms: 2000, tokens: 100, tokens_estimated: false, tok_s: 50 },
    { label: 'MiMo (cloud)', ok: true, response: 'Cloud answer', elapsed_ms: 1000, tokens: 40, tokens_estimated: true, tok_s: 40 }] }) });
};
w.CodecShell = { watchDialogs() {} };
w.eval(`var chatHist=[],_savedRows=[],_discardFrom=null,isProcessing=false,saved=[];
  function _markDiscard(n){_discardFrom=n}
  function saveMessages(m){saved.push({from:_discardFrom,rows:m});_discardFrom=null;_savedRows=_savedRows.concat(m)}
  function showToast(){} function scrollBottom(){}
  function escHtml(s){var d=document.createElement('div');d.textContent=s||'';return d.innerHTML.replace(/"/g,'&quot;')}
  function mdRender(el,t){el.textContent=t}
  function addMessage(role,t){var d=document.createElement('div');d.className='msg '+role;d.innerHTML='<div class="msg-bubble"></div><div class="msg-meta"><span class="msg-time"></span></div>';d.firstChild.textContent=t;document.getElementById('messages').appendChild(d);_verAttach(d,role);return d}
  ` + code);
(async () => {
  const d = w.document, out = {}, tick = () => new Promise(r => setTimeout(r, 30));
  w.eval(`addMessage('user','Why is the sky blue?');chatHist.push({role:'user',content:'Why is the sky blue?'});_savedRows.push({role:'user',content:'Why is the sky blue?'});
    var a1=addMessage('assistant','First answer');chatHist.push({role:'assistant',content:'First answer'});_savedRows.push({role:'assistant',content:'First answer'});`);
  w.eval('compareOpen(a1)');
  await tick();
  out.open = d.getElementById('cmpBox').classList.contains('open');
  out.q = d.getElementById('cmpQ').textContent;
  out.labels = [...d.querySelectorAll('#cmpBody .cs-check')].map(l => l.textContent.trim());
  const go = d.getElementById('cmpGo');
  out.goLocal = go.disabled;
  const radios = d.querySelectorAll('#cmpBody input[name="cmpT"]');
  radios[1].checked = true; radios[1].dispatchEvent(new w.Event('change', { bubbles: true }));
  out.goCloud = [go.disabled, d.getElementById('cmpCloud').hidden];
  const box = d.getElementById('cmpCloudOk'); box.checked = true; box.dispatchEvent(new w.Event('change', { bubbles: true }));
  out.goTicked = go.disabled;
  go.click();
  await tick();
  out.posts = posts;
  out.cols = [...d.querySelectorAll('.cmp-col')].map(c => [c.querySelector('.cmp-h').textContent, c.querySelector('.cmp-meta').textContent]);
  d.querySelector('[data-keep="1"]').click();
  await tick();
  out.closed = d.getElementById('cmpWrap').hidden;
  out.bubbles = [...d.querySelectorAll('#messages .msg-bubble')].map(b => b.textContent);
  out.hist = w.eval('chatHist.map(function(m){return m.content})');
  out.pager = (d.querySelector('#messages .msg.assistant .msg-ver-n') || {}).textContent;
  out.saved = w.eval('saved');
  console.log(JSON.stringify(out)); process.exit(0);
})().catch(e => { console.log('ERR ' + e.message); process.exit(1); });
setTimeout(() => { console.log('TIMEOUT'); process.exit(2); }, 20000);
"""


@pytest.mark.skipif(not _jsdom_available(), reason="node with jsdom not resolvable (set NODE_PATH)")
def test_the_compare_dialog_and_keep_this_one_under_jsdom():
    out = subprocess.run(["node", "-e", _JS, str(REPO / "codec_chat.html")], capture_output=True, text=True,
                         cwd=REPO, env=os.environ.copy(), timeout=60)
    line = [ln for ln in out.stdout.splitlines() if ln.startswith("{") or ln.startswith("ERR") or ln == "TIMEOUT"][-1]
    d = json.loads(line)
    assert d["open"] is True and d["q"].endswith("Why is the sky blue?")
    assert d["labels"][:2] == ["Gemma (Cookbook)", "MiMo (cloud) cloud, $1.20 of $10 this month"]
    assert d["goLocal"] is False and d["goCloud"] == [True, False] and d["goTicked"] is False
    assert d["posts"] == [{"prompt": "Why is the sky blue?", "target": "cloud:mimo", "cloud_ok": True}]
    assert d["cols"] == [["Qwen3.6 (this Mac)", "2.0 s · 100 tokens · 50 tok/s"],
                         ["MiMo (cloud)", "1.0 s · about 40 tokens · 40 tok/s"]]
    assert d["closed"] is True and d["bubbles"] == ["Why is the sky blue?", "Cloud answer"]
    assert d["hist"] == ["Why is the sky blue?", "Cloud answer"] and d["pager"] == "2 / 2"
    assert d["saved"] == [{"from": 1, "rows": [{"role": "assistant", "content": "Cloud answer"}]}]
