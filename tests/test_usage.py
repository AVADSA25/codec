"""Settings > Usage (UI phase 3, P3.11; docs/P3.11-DESIGN.md).

The per-reply metadata log, its summary, the 7-day skill counts from the audit
log, the usage route (spend, cap, where CODEC runs now), the automatic fallback
picker, the chat route's reply line, and the page. Nothing here touches
~/.codec or a model: tmp paths and fakes.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent
HOME = (REPO / "codec_dashboard.html").read_text(encoding="utf-8")
CLOUD = {"id": "mimo-test", "label": "MiMo test (cloud)", "base_url": "https://api.example-cloud.test/v1",
         "key_slot": "mimo_test_key", "price_in_per_m": 0.4, "price_out_per_m": 0.8, "monthly_cap_usd": 10}
CLOUD2 = dict(CLOUD, id="other-test", label="Other test (cloud)", base_url="https://api.other-cloud.test/v1",
              monthly_cap_usd=5)


@pytest.fixture
def log(tmp_path, monkeypatch):
    import codec_usage
    monkeypatch.setattr(codec_usage, "REPLY_LOG", str(tmp_path / "reply_stats.jsonl"))
    return SimpleNamespace(u=codec_usage, path=tmp_path / "reply_stats.jsonl")


def _lines(path):
    return [json.loads(x) for x in path.read_text().splitlines()]


def test_a_reply_line_holds_metadata_only(log):
    log.u.record_reply("mlx-community/Qwen3.6-35B-A3B-4bit", "http://localhost:8083/v1",
                       {"completion_tokens": 50, "tok_per_s": 25.5, "elapsed_s": 2.1, "prompt_tokens": 900, "model": "x"},
                       now=1000.0)
    log.u.record_reply("mimo-test", CLOUD["base_url"], None, source="voice", now=1001.0)
    a, b = _lines(log.path)
    assert a == {"ts": 1000.0, "source": "chat", "model": "mlx-community/Qwen3.6-35B-A3B-4bit", "cloud": False,
                 "tokens": 50, "tok_s": 25.5, "elapsed_s": 2.1}
    assert b == {"ts": 1001.0, "source": "voice", "model": "mimo-test", "cloud": True}
    assert stat.S_IMODE(log.path.stat().st_mode) == 0o600


def test_writing_never_breaks_a_reply_and_the_log_is_trimmed(log, monkeypatch, tmp_path):
    monkeypatch.setattr(log.u, "REPLY_LOG", str(tmp_path))  # a directory: the write fails
    log.u.record_reply("m", "http://localhost:8083/v1", {"tok_per_s": 1})
    monkeypatch.setattr(log.u, "REPLY_LOG", str(log.path))
    now = time.time()
    for i in range(20):
        log.u.record_reply("old", "http://localhost:8083/v1", None, now=now - 40 * 86400 + i)
    monkeypatch.setattr(log.u, "MAX_BYTES", 600)
    log.u.record_reply("new", "http://localhost:8083/v1", None, now=now)
    assert [x["model"] for x in _lines(log.path)] == ["new"], "lines older than 30 days go once it is too big"
    assert stat.S_IMODE(log.path.stat().st_mode) == 0o600


def test_the_summary_counts_replies_and_averages_speed_per_day(log):
    now = datetime(2026, 10, 6, 15, 0).timestamp()
    day = 86400
    for ts, base, s in ((now - 3600, "http://localhost:8083/v1", {"tok_per_s": 30}),
                        (now - 1800, "http://localhost:8083/v1", {"tok_per_s": 40}),
                        (now - 2 * day, "http://localhost:8083/v1", None),
                        (now - 600, CLOUD["base_url"], {"tok_per_s": 90}),
                        (now - 10 * day, "http://localhost:8083/v1", {"tok_per_s": 20}),
                        (now - 20 * day, "http://localhost:8083/v1", {"tok_per_s": 10})):
        log.u.record_reply("cloud" if "example" in base else "local", base, s, now=ts)
    d = log.u.reply_summary(days=7, speed_days=14, now=now)
    assert d["replies"] == {"local": 3, "cloud": 1} and d["by_source"] == {"chat": 4}
    rows = {(r["day"], r["model"]): (r["tok_s"], r["replies"]) for r in d["speed"]}
    assert rows == {("2026-10-06", "local"): (35.0, 2), ("2026-10-06", "cloud"): (90.0, 1), ("2026-09-26", "local"): (20.0, 1)}
    assert d["since"].startswith("2026-09-16"), "the oldest line in the file"


def test_get_stats_counts_skill_runs_over_seven_days(tmp_path, monkeypatch):
    import codec_audit
    import codec_usage
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", tmp_path / "audit.log")
    monkeypatch.setattr(codec_audit, "_AUDIT_DIR", tmp_path)
    now = datetime.now(timezone.utc)

    def rec(event, tool, days_ago=0.0):
        return {"ts": (now - timedelta(days=days_ago)).isoformat(timespec="milliseconds"), "event": event,
                "source": "x", "tool": tool, "outcome": "ok"}

    rows = [rec("tool_result", "weather"), rec("tool_result", "weather", 3), rec("wake_dispatch", "timer", 1.5),
            rec("hook_fired", "weather"), rec("tool_result", "", 1.5), rec("wake_dispatch", "timer", 9)]
    (tmp_path / "audit.log").write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert codec_audit.get_stats(hours=168)["by_tool"] == {"weather": 2, "timer": 1}
    assert codec_audit.get_stats(hours=24)["by_tool"] == {"weather": 1}
    assert codec_usage.busiest_skills(days=7) == [{"name": "weather", "count": 2}, {"name": "timer", "count": 1}]


@pytest.fixture
def use(tmp_path, monkeypatch, log):
    import codec_audit
    import codec_cloud_models as ccm
    import routes.usage as ru
    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr(ru, "CONFIG_PATH", str(cfg_path))
    monkeypatch.setattr(ccm, "CONFIG_PATH", str(cfg_path))
    monkeypatch.setattr(ccm, "SPEND_PATH", str(tmp_path / "cloud_spend.json"))
    month = ccm._month()
    prev = (datetime.strptime(month + "-01", "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m")
    Path(ccm.SPEND_PATH).write_text(json.dumps({month: {"mimo-test": {"usd": 2.5}}, prev: {"mimo-test": {"usd": 4.0},
                                                                                           "other-test": {"usd": 1.0}}}))
    audits = []
    monkeypatch.setattr(codec_audit, "log_event", lambda *a, **k: audits.append((a, k)))
    app = FastAPI()
    app.include_router(ru.router)

    def config(**kw):
        cfg = {"llm_base_url": "http://localhost:8083/v1", "llm_model": "mlx-community/Qwen3.6-35B-A3B-4bit",
               "extra_models": [CLOUD, CLOUD2], "skills_off": ["timer"]}
        cfg.update(kw)
        cfg_path.write_text(json.dumps(cfg))
    config()
    return SimpleNamespace(client=TestClient(app), config=config, cfg=cfg_path, audits=audits, prev=prev)


def test_the_usage_route_reports_spend_and_where_codec_runs(use):
    d = use.client.get("/api/usage").json()
    mimo, other = d["cloud"]
    assert (mimo["id"], mimo["spent"], mimo["cap"]) == ("mimo-test", 2.5, 10) and (other["spent"], other["cap"]) == (0, 5)
    assert [m["usd"] for m in mimo["months"]] == [2.5, 4.0] and other["months"][0]["month"] == use.prev
    assert d["now"] == {"cloud": False, "label": None, "auto": False, "since": None}
    assert d["fallback"]["value"] is None and [c["id"] for c in d["fallback"]["choices"]] == ["mimo-test", "other-test"]
    assert "answers twice in a row" in d["fallback"]["note"] and d["replies"]["days"] == 7 and isinstance(d["skills"], list)
    use.config(llm_base_url=CLOUD["base_url"], llm_model="mimo-test")
    assert use.client.get("/api/usage").json()["now"] == {"cloud": True, "label": "MiMo test (cloud)", "auto": False, "since": None}
    use.config(llm_base_url=CLOUD["base_url"], llm_model="mimo-test", llm_auto_fallback="mimo-test",
               llm_auto_fallback_active={"from": "mlx-community/Qwen3.6-35B-A3B-4bit", "at": 1791290000.0, "local_oks": 0})
    d = use.client.get("/api/usage").json()
    assert d["now"]["auto"] is True and d["now"]["since"] == datetime.fromtimestamp(1791290000.0).isoformat(timespec="seconds")
    assert d["fallback"]["value"] == "mimo-test"


def test_the_fallback_picker_takes_only_registered_cloud_models(use):
    c = use.client
    for bad in ("nope", 5, "mlx-community/Qwen3.6-35B-A3B-4bit"):
        assert c.put("/api/usage/auto_fallback", json={"value": bad}).status_code == 400, bad
    assert c.put("/api/usage/auto_fallback", json={"value": "other-test"}).json() == {"value": "other-test"}
    cfg = json.loads(use.cfg.read_text())
    assert cfg["llm_auto_fallback"] == "other-test" and cfg["skills_off"] == ["timer"], "other keys kept"
    assert c.put("/api/usage/auto_fallback", json={"value": None}).json() == {"value": None}
    assert "llm_auto_fallback" not in json.loads(use.cfg.read_text())
    assert [a[0][0] for a in use.audits] == ["auto_fallback_set", "auto_fallback_set"]
    assert use.audits[0][1]["extra"] == {"value": "other-test"}


def test_a_streamed_chat_reply_writes_one_line(log, monkeypatch):
    import codec_llm
    import routes.chat as chat

    def fake_stream(messages, **kw):
        yield "Plorb is not a word."
        yield codec_llm.StreamUsage({"completion_tokens": 6, "prompt_tokens": 20, "timings": {"predicted_per_second": 33.3}})

    monkeypatch.setattr(codec_llm, "stream", fake_stream)
    app = FastAPI()
    app.include_router(chat.router)
    r = TestClient(app).post("/api/chat", json={"messages": [{"role": "user", "content": "zzqx plorb blorft"}], "stream": True})
    assert r.status_code == 200 and '"tok_per_s": 33.3' in r.text
    (line,) = _lines(log.path)
    assert line["source"] == "chat" and line["tokens"] == 6 and line["tok_s"] == 33.3
    assert "plorb" not in log.path.read_text().lower(), "never the question or the answer"
    src = (REPO / "routes" / "chat.py").read_text(encoding="utf-8")
    assert "codec_usage.record_reply(model, base_url)  # UI P3.11" in src, "the non-stream path counts too"
    voice = (REPO / "codec_voice.py").read_text(encoding="utf-8")
    assert 'codec_usage.record_reply(_model, getattr(self, "_llm_base", QWEN_BASE_URL), source="voice")' in voice


def test_settings_has_the_usage_section():
    assert 'id="usageSection"' in HOME and 'id="usageCard"' in HOME
    assert "loadCheckin(); loadUsage(); }" in HOME
    block = HOME[HOME.index("// ── Usage (P3.11"):HOME.index("async function loadPush() {")]
    assert "fetch('/api/usage')" in block and "fetch('/api/usage/auto_fallback', { method: 'PUT'" in block
    assert 'role="radiogroup" aria-label="Automatic cloud fallback"' in block and 'role="progressbar"' in block
    conftest = (REPO / "tests" / "conftest.py").read_text(encoding="utf-8")
    assert "codec_usage.REPLY_LOG = str(tmp / \"reply_stats.jsonl\")" in conftest, "the suite never writes the owner's log"


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
const block = src.slice(src.indexOf('// ── Usage (P3.11'), src.indexOf('async function loadPush() {'));
const fmt = src.slice(src.indexOf('function fmtTime(ts) {'), src.indexOf('\n}\n', src.indexOf('function fmtTime(ts) {')) + 3);
const dom = new JSDOM('<!doctype html><body><div id="usageCard"></div></body>', { runScripts: 'outside-only', pretendToBeVisual: true });
const w = dom.window, puts = [];
let fallback = null;
w.fetch = (u, o) => {
  if (o && o.method === 'PUT') { puts.push(JSON.parse(o.body)); fallback = JSON.parse(o.body).value; return Promise.resolve({ ok: true, json: () => Promise.resolve({ value: fallback }) }); }
  return Promise.resolve({ ok: true, json: () => Promise.resolve({
    now: { cloud: false, label: null, auto: false, since: null },
    cloud: [{ id: 'mimo', label: 'MiMo', spent: 10, cap: 10, months: [{ label: 'October 2026', usd: 10 }, { label: 'September 2026', usd: 3.5 }] }],
    fallback: { value: fallback, choices: [{ id: 'mimo', label: 'MiMo' }], note: 'When the local model stops.' },
    replies: { days: 7, replies: { local: 12, cloud: 3 }, since: '2026-10-01T09:00:00', speed: [
      { day: '2026-10-05', model: 'mlx-community/Qwen3.6-35B-A3B-4bit', tok_s: 30, replies: 4 },
      { day: '2026-10-06', model: 'mlx-community/Qwen3.6-35B-A3B-4bit', tok_s: 60, replies: 2 }] },
    skills: [{ name: 'weather', count: 5 }, { name: 'timer', count: 1 }] }) });
};
w.eval('function escHtml(s){var d=document.createElement("div");d.textContent=s||"";return d.innerHTML.replace(/"/g,"&quot;")}\nfunction showToast(){}\n' + fmt + '\n' + block);
(async () => {
  const d = w.document, out = {}, tick = () => new Promise(r => setTimeout(r, 40));
  w.loadUsage();
  await tick();
  const card = d.getElementById('usageCard');
  out.text = card.textContent;
  out.full = !!card.querySelector('.use-bar.full');
  out.radios = [...card.querySelectorAll('input[name="useFallback"]')].map(i => [i.value, i.checked]);
  out.bars = [...card.querySelectorAll('.use-speed span')].map(s => s.style.height);
  const r = card.querySelector('input[name="useFallback"][value="mimo"]');
  r.checked = true; r.dispatchEvent(new w.Event('change'));
  await tick();
  out.puts = puts;
  out.after = [...d.querySelectorAll('input[name="useFallback"]')].map(i => [i.value, i.checked]);
  console.log(JSON.stringify(out)); process.exit(0);
})().catch(e => { console.log('ERR ' + e.message); process.exit(1); });
setTimeout(() => { console.log('TIMEOUT'); process.exit(2); }, 20000);
"""


@pytest.mark.skipif(not _jsdom_available(), reason="node with jsdom not resolvable (set NODE_PATH)")
def test_the_usage_section_renders_and_saves_under_jsdom():
    out = subprocess.run(["node", "-e", _JS, str(REPO / "codec_dashboard.html")], capture_output=True, text=True,
                         cwd=REPO, env=os.environ.copy(), timeout=60)
    line = [ln for ln in out.stdout.splitlines() if ln.startswith("{") or ln.startswith("ERR") or ln == "TIMEOUT"][-1]
    d = json.loads(line)
    t = d["text"]
    assert "CODEC runs on the local model on this Mac." in t and "MiMo: $10.00 of $10" in t and "At the cap" in t
    assert "Earlier: September 2026: $3.50" in t and "12 on this Mac · 3 in the cloud" in t
    assert "weather5 runs" in t and "timer1 run" in t and "Qwen3.6-35B-A3B: 60 tokens a second on 2026-10-06" in t
    assert d["full"] is True and d["radios"] == [["", True], ["mimo", False]] and d["bars"] == ["50%", "100%"]
    assert d["puts"] == [{"value": "mimo"}] and d["after"] == [["", False], ["mimo", True]]
