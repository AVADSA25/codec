"""Best local model for this Mac (P-039, docs/MODEL-FIT-OFFER-DESIGN.md).

RAM, disk, the image-job lock and the model in use are fixtures. Nothing here
downloads a model, reaches the Hub, or calls the real codec_models.set_active.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import codec_image  # noqa: E402
import codec_models  # noqa: E402
from codec_cookbook import catalog, download, fit, offer  # noqa: E402

GiB = 1024 ** 3 / 1e9          # hw.memsize / 1e9 of a Mac with 1 GiB
STUDIO = 64 * GiB
SIXTEEN = 16 * GiB
SEVEN_B = "mlx-community/Qwen2.5-7B-Instruct-4bit"
DASH = (REPO / "codec_dashboard.html").read_text(encoding="utf-8")

# What the Hub serves for each repo (config.json geometry and weight-file bytes),
# typed from the model pages on 2026-11-04: the numbers the Cookbook fetched.
HUB_CONFIG = {
    "mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit": {"num_hidden_layers": 48, "num_attention_heads": 32, "num_key_value_heads": 4, "head_dim": 128, "hidden_size": 2048},
    "mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit": {"num_hidden_layers": 48, "num_attention_heads": 32, "num_key_value_heads": 4, "head_dim": 128, "hidden_size": 2048},
    "mlx-community/Qwen3-Next-80B-A3B-Instruct-4bit": {"num_hidden_layers": 48, "num_attention_heads": 16, "num_key_value_heads": 2, "head_dim": 256, "hidden_size": 2048},
    "mlx-community/Qwen3-4B-Instruct-2507-4bit": {"num_hidden_layers": 36, "num_attention_heads": 32, "num_key_value_heads": 8, "head_dim": 128, "hidden_size": 2560},
    "mlx-community/Llama-3.2-3B-Instruct-4bit": {"num_hidden_layers": 28, "num_attention_heads": 24, "num_key_value_heads": 8, "head_dim": 128, "hidden_size": 3072},
    SEVEN_B: {"num_hidden_layers": 28, "num_attention_heads": 28, "num_key_value_heads": 4, "hidden_size": 3584},
}
HUB_WEIGHT_GB = {"mlx-community/Qwen3-4B-Instruct-2507-4bit": 2263022417 / 1e9,
                 "mlx-community/Llama-3.2-3B-Instruct-4bit": 1807496278 / 1e9}
# The five entries the Cookbook had before P-039, ranked on a 64 GB Mac.
STUDIO_PINNED = [("qwen3-30b-a3b", True, 21.3), ("qwen3-coder-30b", True, 21.3),
                 ("qwen25-7b", True, 6.7), ("qwen3-4b", True, 5.3), ("llama32-3b", True, 4.5),
                 ("qwen3-next-80b", False, 48.6)]


@pytest.fixture(autouse=True)
def mac(tmp_path, monkeypatch):
    """A Mac with nothing downloaded, the 7B in use, room on disk, no image job."""
    state = {"active": SEVEN_B, "local": {}, "disk": 500.0, "min_free": None, "switched": []}

    def no_hub(*_a, **_k):
        raise AssertionError("the catalog must rank without the Hub")

    monkeypatch.setattr(fit, "load_config", no_hub)
    monkeypatch.setattr(fit, "weight_gb_from_hub", no_hub)
    monkeypatch.setattr(offer, "_active_model", lambda: state["active"])
    monkeypatch.setattr(offer, "_local_sizes", lambda: dict(state["local"]))
    monkeypatch.setattr(offer, "_disk_free_gb", lambda _p: state["disk"])
    monkeypatch.setattr(offer, "_image_min_free_gb", lambda: state["min_free"])
    monkeypatch.setattr(codec_image, "LOCK_PATH", str(tmp_path / "image_job.lock"))
    monkeypatch.setattr(download, "DL_DIR", str(tmp_path / "downloads"))
    monkeypatch.setattr(download, "HF_CACHE", str(tmp_path / "hub"))

    def fake_set_active(model_id, verify=True):
        state["switched"].append(model_id)
        return {"ok": True, "active": model_id, "changed": True, "detail": "loaded in 1s"}

    monkeypatch.setattr(codec_models, "set_active", fake_set_active)
    return state


def _ids(items):
    return [m["id"] for m in items]


def _offered(d):
    return ([d["best"]] if d["best"] else []) + d["others"]


# ── G1: the 64 GB ranking stays as it was ────────────────────────────────────

@pytest.mark.parametrize("gib", [64, 96, 128, 192])
def test_studio_ranking_reserve_and_margin_from_64_gb_up(gib):
    assert fit.os_reserve_for(gib * GiB) == 24 and fit.margin_for(gib * GiB) == 8


def test_studio_ranking_unchanged_with_the_old_figures(monkeypatch):
    new = fit.recommend(catalog.all_entries(),
                        fit.available_gb(STUDIO, [3.0], fit.os_reserve_for(STUDIO)), 8192,
                        fit.margin_for(STUDIO))
    # Before P-039: reserve 24, margin 8, KV and the 4B/3B weights fetched from the Hub.
    old_entries = []
    for e in catalog.all_entries():
        e.pop("kv")
        if e["hf_repo"] in HUB_WEIGHT_GB:
            e.pop("anchor_gb")
        old_entries.append(e)
    monkeypatch.setattr(fit, "load_config", lambda repo: HUB_CONFIG[repo])
    monkeypatch.setattr(fit, "weight_gb_from_hub", lambda repo: HUB_WEIGHT_GB[repo])
    old = fit.recommend(old_entries, fit.available_gb(STUDIO, [3.0], os_reserve_gb=24), 8192,
                        margin_gb=8)
    key = [(r["entry"]["id"], r["fits"], r["need_gb"], r["headroom_gb"]) for r in new]
    assert key == [(r["entry"]["id"], r["fits"], r["need_gb"], r["headroom_gb"]) for r in old]


def test_studio_ranking_pinned():
    ranked = fit.recommend(catalog.all_entries(), fit.available_gb(STUDIO, [3.0], fit.os_reserve_for(STUDIO)),
                           8192, fit.margin_for(STUDIO))
    assert [(r["entry"]["id"], r["fits"], r["need_gb"]) for r in ranked] == STUDIO_PINNED
    before = [i for i, _f, _n in STUDIO_PINNED if i != "qwen25-7b"]
    assert [r["entry"]["id"] for r in ranked if r["entry"]["id"] != "qwen25-7b"] == before


# ── G2: a 16 GB Mac ──────────────────────────────────────────────────────────

def test_sixteen_gb_scales_reserve_and_margin():
    assert fit.os_reserve_for(SIXTEEN) == 6.0 and fit.margin_for(SIXTEEN) == 2.0
    assert fit.os_reserve_for(8 * GiB) == 4.0 and fit.margin_for(8 * GiB) == 1.0, "floors"


def test_sixteen_gb_fit_ranks_the_7b_top_and_nothing_over_free_memory():
    avail = fit.available_gb(SIXTEEN, [0.0], fit.os_reserve_for(SIXTEEN))
    ranked = fit.recommend(catalog.all_entries(), avail, 8192, fit.margin_for(SIXTEEN))
    fitting = [r for r in ranked if r["fits"]]
    assert fitting[0]["entry"]["id"] == "qwen25-7b"
    assert all(r["need_gb"] <= avail for r in fitting)
    assert {r["entry"]["id"] for r in ranked if not r["fits"]} >= {"qwen3-30b-a3b", "qwen3-next-80b"}
    # Before P-039 the same Mac had less than nothing free, so nothing fit.
    assert fit.available_gb(SIXTEEN, [0.0], os_reserve_gb=24) < 0


def test_sixteen_gb_offer_best_is_the_7b(mac):
    d = offer.build(total_gb=SIXTEEN, resident_gb=0.0)
    assert d["best"]["id"] == "qwen25-7b" and d["best"]["size_gb"] == 4.3
    assert d["ram_gb"] == 17.2 and d["reserve_gb"] == 6.0 and d["margin_gb"] == 2.0
    assert all(m["need_gb"] <= d["available_gb"] for m in _offered(d))
    assert "qwen3-30b-a3b" not in _ids(_offered(d))
    assert d["best"]["active"] is True, "the 7B in use counts as on this Mac"


def test_eight_gb_offers_nothing():
    assert offer.build(total_gb=8 * GiB, resident_gb=0.0)["best"] is None


# ── G3: the 7B entry is the model the app ships ──────────────────────────────

def test_catalog_7b_matches_models_json():
    shipped = next(m for m in json.loads((REPO / "packaging/macos/models.json").read_text())["models"]
                   if m["name"] == "qwen-7b-4bit")
    licences = json.loads((REPO / "packaging/macos/model_licences.json").read_text())["models"]
    e = catalog.get("qwen25-7b")
    assert e["hf_repo"] == shipped["repo"] == codec_models.DEFAULT_MODEL
    assert e["revision"] == shipped["revision"]
    assert e["size_gb"] == shipped["approx_gb"]
    assert e["licence"] == licences["qwen-7b-4bit"]["licence"].lower()


# ── G4: licences ─────────────────────────────────────────────────────────────

def test_licence_every_catalog_entry_carries_an_allowlisted_one():
    for e in catalog.all_entries():
        assert e["licence"] in catalog.COMMERCIAL_LICENCES, e["id"]
        assert catalog.licence_ok(e)


def _with_unlicensed(monkeypatch):
    extra = [{"id": "nc-model", "label": "NC", "hf_repo": "mlx-community/NC-9B-4bit", "roles": ["chat"],
              "anchor_gb": 9.0, "size_gb": 9.0, "licence": "cc-by-nc-4.0", "kv": HUB_CONFIG[SEVEN_B]},
             {"id": "no-licence", "label": "None", "hf_repo": "mlx-community/None-8B-4bit", "roles": ["chat"],
              "anchor_gb": 8.0, "size_gb": 8.0, "kv": HUB_CONFIG[SEVEN_B]}]
    rows = catalog.all_entries() + extra
    monkeypatch.setattr(catalog, "_load", lambda: rows)
    return extra


def test_licence_outside_the_allowlist_is_never_offered(monkeypatch):
    extra = _with_unlicensed(monkeypatch)
    mem = offer._memory(STUDIO, 3.0)
    control = fit.recommend(extra, mem["avail"], 8192, mem["margin"])
    assert all(r["fits"] for r in control), "control: both would fit this Mac"
    offered = _ids(_offered(offer.build(total_gb=STUDIO, resident_gb=3.0)))
    assert "qwen25-7b" in offered and not {"nc-model", "no-licence"} & set(offered)
    for mid in ("nc-model", "no-licence"):
        assert "licence" in offer.start_download(mid, total_gb=STUDIO, resident_gb=3.0)["error"]
        assert "licence" in offer.use(mid, total_gb=STUDIO, resident_gb=3.0)["error"]


# ── G5: the Llama notice ─────────────────────────────────────────────────────

def test_llama_notice_comes_with_the_llama_offer():
    items = {m["id"]: m for m in _offered(offer.build(total_gb=SIXTEEN, resident_gb=0.0))}
    lic = items["llama32-3b"]["licence"]
    assert lic["name"] == "Llama 3.2 Community License"
    assert ("Llama 3.2 is licensed under the Llama 3.2 Community License, Copyright © Meta "
            "Platforms, Inc. All Rights Reserved.") in lic["notice"]
    assert "Built with Llama" in lic["notice"] and lic["policy_url"].startswith("https://www.llama.com/")
    assert items["qwen25-7b"]["licence"]["notice"] == ""


# ── page rendering in Node with a small DOM stand-in ─────────────────────────

node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
FIT_JS = DASH[DASH.index("// ── Best model for this Mac (P-039"):DASH.index("// ── Advanced: edit config.json")]
DOM = r"""
function El(tag) { this.tagName = tag; this.children = []; this.className = ''; this._t = '';
  this.attrs = {}; this.hidden = false; this.disabled = false; this.value = 0; this.max = 0; this.isConnected = true; }
Object.defineProperty(El.prototype, 'textContent', {
  get: function () { return this._t + this.children.map(function (c) { return c.textContent; }).join(''); },
  set: function (v) { this._t = String(v); this.children = []; } });
El.prototype.appendChild = function (c) { this.children.push(c); return c; };
El.prototype.setAttribute = function (k, v) { this.attrs[k] = v; };
El.prototype.addEventListener = function () {};
var document = { createElement: function (t) { return new El(t); },
  createTextNode: function (t) { var e = new El('#text'); e._t = String(t); return e; },
  getElementById: function () { return null; } };
var setInterval = function () { return 1; }, clearInterval = function () {};
function all(e, out) { out.push(e); e.children.forEach(function (c) { all(c, out); }); return out; }
"""


def _render(item, best=False):
    script = (DOM + FIT_JS + "\nvar row = _fitItem(" + json.dumps(item) + ", " + json.dumps(best) + ");\n"
              "var els = all(row, []);\n"
              "console.log(JSON.stringify({text: row.textContent, cls: row.className,"
              " bars: els.filter(function (e) { return e.tagName === 'progress'; })"
              ".map(function (e) { return {value: e.value, max: e.max, hidden: e.hidden}; }),"
              " notices: els.filter(function (e) { return e.className === 'set-fit-notice'; }).length,"
              " buttons: els.filter(function (e) { return e.tagName === 'button'; })"
              ".map(function (e) { return {text: e.textContent, disabled: e.disabled}; })}));")
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _items16():
    return {m["id"]: m for m in _offered(offer.build(total_gb=SIXTEEN, resident_gb=0.0))}


@node
def test_llama_notice_page_shows_it_with_the_entry():
    r = _render(_items16()["llama32-3b"])
    assert r["notices"] == 1 and "Llama 3.2 Community License, Copyright" in r["text"]
    assert "Built with Llama" in r["text"] and "Acceptable Use Policy" in r["text"]
    assert _render(_items16()["qwen3-4b"])["notices"] == 0
    assert "more.open = true;" in FIT_JS, "the other offers, the Llama one with them, are shown open"


@node
def test_page_renders_size_and_a_progress_bar_while_downloading(mac):
    mac["active"] = "mlx-community/Some-Cloud"
    item = offer.build(total_gb=SIXTEEN, resident_gb=0.0)["best"]
    item["download"] = {"state": "running", "percent": 40, "bytes": 1, "total_bytes": 4300000000}
    r = _render(item, True)
    assert "Qwen2.5 7B" in r["text"] and "4.3 GB download" in r["text"]
    assert r["bars"] == [{"value": 40, "max": 100, "hidden": False}]
    assert r["buttons"] == [{"text": "Downloading...", "disabled": True}]
    item["download"] = {"state": "not_started", "percent": 0, "bytes": 0}
    r = _render(item, True)
    assert r["buttons"] == [{"text": "Download 4.3 GB", "disabled": False}] and r["bars"][0]["hidden"] is True
    item["installed"] = True
    assert [b["text"] for b in _render(item, True)["buttons"]] == ["Use it"]


def test_page_settings_row_reads_the_fit_routes():
    llm = DASH[DASH.index("{ title: 'Language model'"):DASH.index("{ title: 'Vision'")]
    assert "modelFit: true" in llm
    render = DASH[DASH.index("function renderSettings(cfg)"):DASH.index("async function loadHotkeyPresets()")]
    assert 'id="modelFitBody"' in render and "loadModelFit();" in render
    for url in ("fetch('/api/models/fit')", "fetch('/api/models/fit/download'",
                "fetch('/api/models/fit/download?id=' + encodeURIComponent(m.id))", "fetch('/api/models/fit/use'"):
        assert url in FIT_JS, url
    assert "innerHTML" not in FIT_JS and "confirm(" not in FIT_JS
    assert "_fitEl('progress', 'set-fit-bar')" in FIT_JS


# ── G6: memory guard, image-job lock, disk ──────────────────────────────────

def test_guard_use_refuses_a_model_that_does_not_fit(mac):
    mac["local"] = {"mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit": 17.2}
    r = offer.use("qwen3-30b-a3b", total_gb=SIXTEEN, resident_gb=0.0)
    assert r["ok"] is False and "does not load it here" in r["error"]
    assert mac["switched"] == []
    # control: the same model on a 64 GB Mac goes to set_active
    assert offer.use("qwen3-30b-a3b", total_gb=STUDIO, resident_gb=3.0)["ok"] is True
    assert mac["switched"] == ["mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit"]


def test_guard_download_refuses_a_model_that_does_not_fit(mac, monkeypatch):
    started = []
    monkeypatch.setattr(download, "start", lambda *a: started.append(a) or {"state": "starting"})
    r = offer.start_download("qwen3-next-80b", total_gb=STUDIO, resident_gb=3.0)
    assert r["ok"] is False and "48.6 GB" in r["error"] and started == []


def test_guard_use_refuses_a_model_not_downloaded_and_skips_the_one_in_use(mac):
    mac["active"] = "mlx-community/Other-4bit"
    r = offer.use("qwen3-4b", total_gb=SIXTEEN, resident_gb=0.0)
    assert r["ok"] is False and "not downloaded" in r["error"] and mac["switched"] == []
    mac["active"] = SEVEN_B
    assert offer.use("qwen25-7b", total_gb=SIXTEEN, resident_gb=0.0)["changed"] is False
    assert mac["switched"] == []


def test_image_lock_blocks_the_switch(mac):
    mac["active"] = "mlx-community/Other-4bit"
    mac["local"] = {SEVEN_B: 4.3}
    Path(codec_image.LOCK_PATH).write_text(json.dumps({"pid": os.getpid(), "job": "abc"}))
    r = offer.use("qwen25-7b", total_gb=SIXTEEN, resident_gb=0.0)
    assert r["ok"] is False and r["error"] == codec_image.busy_message()
    assert offer.build(total_gb=SIXTEEN, resident_gb=0.0)["image_busy"] == r["error"]
    assert mac["switched"] == []
    # control: a lock left by a runner that has exited no longer blocks
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    Path(codec_image.LOCK_PATH).write_text(json.dumps({"pid": p.pid, "job": "abc"}))
    assert offer.use("qwen25-7b", total_gb=SIXTEEN, resident_gb=0.0)["ok"] is True
    assert mac["switched"] == [SEVEN_B]


def test_image_note_when_create_image_needs_36_gb(mac):
    mac["min_free"] = 36.0
    items = {m["id"]: m for m in _offered(offer.build(total_gb=STUDIO, resident_gb=3.0))}
    assert "36 GB free" in items["qwen3-30b-a3b"]["image_note"]
    assert items["qwen25-7b"]["image_note"] == ""
    mac["min_free"] = None
    assert offer.build(total_gb=STUDIO, resident_gb=3.0)["best"]["image_note"] == ""


def test_guard_download_disk_space_and_the_pinned_revision(mac, monkeypatch):
    mac["active"] = "mlx-community/Other-4bit"
    started, audited = [], []
    monkeypatch.setattr(download, "start", lambda *a: started.append(a) or {"state": "starting"})
    monkeypatch.setattr(offer, "_audit_download", lambda e: audited.append(e["id"]))
    mac["disk"] = 6.0
    r = offer.start_download("qwen25-7b", total_gb=SIXTEEN, resident_gb=0.0)
    assert r["ok"] is False and "6.3 GB free" in r["error"] and started == []
    mac["disk"] = None
    assert "disk space" in offer.start_download("qwen25-7b", total_gb=SIXTEEN, resident_gb=0.0)["error"]
    mac["disk"] = 6.4
    r = offer.start_download("qwen25-7b", total_gb=SIXTEEN, resident_gb=0.0)
    assert r["ok"] is True and r["state"] == "not_started"
    assert started == [(SEVEN_B, catalog.get("qwen25-7b")["revision"])] and audited == ["qwen25-7b"]
    mac["local"] = {SEVEN_B: 4.3}
    assert "already on this Mac" in offer.start_download("qwen25-7b", total_gb=SIXTEEN, resident_gb=0.0)["error"]


def test_guard_a_running_download_is_not_on_this_mac_yet(mac, monkeypatch):
    # A finished shard can already pass discover_local's size check mid-download.
    mac["active"] = "mlx-community/Other-4bit"
    mac["local"] = {SEVEN_B: 2.1}
    started = []
    monkeypatch.setattr(download, "start", lambda *a: started.append(a) or {"state": "starting"})
    os.makedirs(download.DL_DIR)
    Path(download._status_file(SEVEN_B)).write_text(
        json.dumps({"repo": SEVEN_B, "state": "running", "pid": os.getpid()}))
    best = offer.build(total_gb=SIXTEEN, resident_gb=0.0)["best"]
    assert best["installed"] is False and best["download"]["state"] == "running"
    r = offer.start_download("qwen25-7b", total_gb=SIXTEEN, resident_gb=0.0)
    assert r["ok"] is True and r["state"] == "running" and started == []
    r = offer.use("qwen25-7b", total_gb=SIXTEEN, resident_gb=0.0)
    assert r["ok"] is False and "not downloaded" in r["error"] and mac["switched"] == []
    # control: once the job is done, the same files are the model on this Mac
    Path(download._status_file(SEVEN_B)).write_text(
        json.dumps({"repo": SEVEN_B, "state": "done", "pid": os.getpid()}))
    assert offer.build(total_gb=SIXTEEN, resident_gb=0.0)["best"]["installed"] is True
    assert offer.use("qwen25-7b", total_gb=SIXTEEN, resident_gb=0.0)["ok"] is True
    assert mac["switched"] == [SEVEN_B]


def test_guard_download_writes_one_audit_line(mac, monkeypatch, tmp_path):
    import codec_audit
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", tmp_path / "audit.log")
    offer._audit_download(catalog.get("qwen25-7b"))
    line = json.loads((tmp_path / "audit.log").read_text().splitlines()[-1])
    assert line["event"] == "model_download_started" and line["source"] == "codec-cookbook"
    assert line["extra"] == {"model": "qwen25-7b", "repo": SEVEN_B, "size_gb": 4.3}


# ── G7: progress and routes ──────────────────────────────────────────────────

def test_progress_counts_blobs_and_reports_a_percent(tmp_path):
    blobs = Path(download.HF_CACHE) / "models--org--tiny" / "blobs"
    blobs.mkdir(parents=True)
    (blobs / "aa").write_bytes(b"x" * 1000)
    (blobs / "bb.incomplete").write_bytes(b"x" * 500)
    (tmp_path / "outside").write_bytes(b"x" * 9000)
    os.symlink(tmp_path / "outside", blobs / "cc")
    assert download.bytes_on_disk("org/tiny") == 1500 and download.bytes_on_disk("org/none") == 0
    entry = {"id": "tiny", "hf_repo": "org/tiny", "size_gb": 3e-6}
    os.makedirs(download.DL_DIR)
    status = Path(download.DL_DIR) / "org__tiny.json"
    status.write_text(json.dumps({"repo": "org/tiny", "state": "running", "pid": os.getpid()}))
    p = offer.progress(entry)
    assert (p["state"], p["bytes"], p["total_bytes"], p["percent"]) == ("running", 1500, 3000, 50)
    status.write_text(json.dumps({"repo": "org/tiny", "state": "done", "pid": os.getpid()}))
    assert offer.progress(entry)["percent"] == 100


def test_progress_download_child_gets_the_revision(monkeypatch):
    seen = []

    class P:
        pid = 4242

    monkeypatch.setattr(download.subprocess, "Popen", lambda argv, **kw: seen.append(argv) or P())
    download.start("org/a", "c26a38f6a37d0a51b4e9a1eb3026530fa35d9fed")
    download.start("org/b")
    assert seen[0][3:] == ["org/a", download._status_file("org/a"), "c26a38f6a37d0a51b4e9a1eb3026530fa35d9fed"]
    assert seen[1][3:] == ["org/b", download._status_file("org/b")]
    assert "snapshot_download(repo, revision=rev)" in download._CHILD


@pytest.fixture
def client(monkeypatch):
    from codec_cookbook import probe
    from routes import models as rm
    monkeypatch.setattr(probe, "unified_total_gb", lambda: SIXTEEN)
    monkeypatch.setattr(probe, "resident_gb_total", lambda: 0.0)
    app = FastAPI()
    app.include_router(rm.router)
    return TestClient(app)


def test_route_fit_shows_the_best_model_with_its_size(client, mac):
    mac["active"] = "mlx-community/Other-4bit"
    d = client.get("/api/models/fit").json()
    assert d["best"]["id"] == "qwen25-7b" and d["best"]["size_gb"] == 4.3
    assert d["best"]["download"]["percent"] == 0 and d["best"]["download"]["total_bytes"] == 4300000000
    p = client.get("/api/models/fit/download", params={"id": "qwen25-7b"})
    assert p.status_code == 200 and p.json()["state"] == "not_started"
    assert client.get("/api/models/fit/download", params={"id": "nope"}).status_code == 404


def test_route_refusals_are_409_and_a_switch_uses_set_active(client, mac):
    assert client.post("/api/models/fit/use", json={}).status_code == 400
    assert client.post("/api/models/fit/download", json={}).status_code == 400
    r = client.post("/api/models/fit/use", json={"id": "qwen3-next-80b"})
    assert r.status_code == 409 and r.json()["ok"] is False
    assert client.post("/api/models/fit/download", json={"id": "qwen3-30b-a3b"}).status_code == 409
    mac["active"] = "mlx-community/Other-4bit"
    mac["local"] = {SEVEN_B: 4.3}
    r = client.post("/api/models/fit/use", json={"id": "qwen25-7b"})
    assert r.status_code == 200 and r.json()["active"] == SEVEN_B and mac["switched"] == [SEVEN_B]


def test_route_model_switch_runs_off_the_event_loop(client, monkeypatch):
    """POST /api/model waits for set_active in a worker thread (P-016), so a
    model load of up to minutes never freezes the dashboard's event loop."""
    import asyncio

    import codec_models
    seen = {}

    def fake_set_active(model_id):
        try:
            asyncio.get_running_loop()
            seen["on_loop"] = True
        except RuntimeError:
            seen["on_loop"] = False
        seen["model"] = model_id
        return {"ok": True, "active": model_id}

    monkeypatch.setattr(codec_models, "set_active", fake_set_active)
    assert client.post("/api/model", json={}).status_code == 400
    r = client.post("/api/model", json={"model": "mlx-community/Other-4bit"})
    assert r.status_code == 200 and r.json()["active"] == "mlx-community/Other-4bit"
    assert seen == {"on_loop": False, "model": "mlx-community/Other-4bit"}
