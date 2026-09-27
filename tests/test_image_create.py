"""Create image (codec_image + routes/image + the model-switch lock).

A fake runner stands in for Qwen-Image 2.1, so CI needs no model. The
properties that matter: jobs run one at a time, the memory guard refuses before
the model starts, inputs are bounded, served files stay inside their job
folder, cancel stops the process, and no local model loads during a job.
docs/CHAT-CREATE-IMAGE-DESIGN.md
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import codec_image  # noqa: E402

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 60

FAKE_RUNNER = r'''
import base64, json, os, sys, time
job = json.loads(sys.stdin.read())
out = job["out_dir"]
json.dump(job, open(os.path.join(out, "spec.json"), "w"))
print(json.dumps({"stage": "loading"}), flush=True)
mode = os.environ.get("FAKE_MODE", "ok")
if mode == "hang":
    time.sleep(60)
if mode == "crash":
    sys.stderr.write("Traceback (most recent call last):\n  File \"x\", line 1\nRuntimeError: MPS backend out of memory\n")
    sys.exit(1)
flag = os.path.join(os.environ["FAKE_PROBE_DIR"], "active")
try:
    fd = os.open(flag, os.O_CREAT | os.O_EXCL)
except FileExistsError:
    sys.stderr.write("OVERLAP: two runners at once\n"); sys.exit(2)
print(json.dumps({"stage": "loaded", "seconds": 0.1}), flush=True)
png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
for k in range(job["count"]):
    for step in range(job["steps"]):
        print(json.dumps({"stage": "generating", "image": k + 1, "of": job["count"], "step": step + 1, "steps": job["steps"]}), flush=True)
    time.sleep(0.2)
    name = "%02d.png" % (k + 1)
    open(os.path.join(out, name), "wb").write(png)
    print(json.dumps({"stage": "image", "file": name, "seconds": 0.2, "width": 1, "height": 1}), flush=True)
os.close(fd); os.remove(flag)
print(json.dumps({"stage": "done"}), flush=True)
'''


@pytest.fixture
def img(tmp_path, monkeypatch):
    model = tmp_path / "model"
    model.mkdir()
    (model / "model_index.json").write_text("{}")
    runner = tmp_path / "fake_runner.py"
    runner.write_text(FAKE_RUNNER)
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"image": {"python": sys.executable, "model_path": str(model),
                                         "min_free_gb": 36, "steps": 3}}))
    monkeypatch.setattr(codec_image, "CONFIG_PATH", str(cfg))
    monkeypatch.setattr(codec_image, "IMAGES_DIR", str(tmp_path / "images"))
    monkeypatch.setattr(codec_image, "LOCK_PATH", str(tmp_path / "image_job.lock"))
    monkeypatch.setattr(codec_image, "RUNNER", str(runner))
    monkeypatch.setattr(codec_image, "free_memory_gb", lambda: 64.0)
    monkeypatch.setenv("FAKE_PROBE_DIR", str(tmp_path))
    monkeypatch.delenv("FAKE_MODE", raising=False)
    monkeypatch.setattr(codec_image, "_Q", codec_image.ImageQueue())
    audits = []
    monkeypatch.setattr(codec_image, "_audit", lambda job: audits.append(job["state"]))
    return SimpleNamespace(tmp=tmp_path, cfg=cfg, model=model, audits=audits)


def _wait(job_id, states=("done", "failed", "refused", "cancelled"), timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        job = codec_image.get(job_id)
        if job and job["state"] in states:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} stuck in {codec_image.get(job_id)}")


def _dir(job_id):
    return codec_image._find_dir(job_id)


def test_status_and_submit_without_the_model(img):
    assert codec_image.status()["available"] is True
    (img.model / "model_index.json").unlink()
    st = codec_image.status()
    assert st["available"] is False and "not downloaded" in st["reason"]
    with pytest.raises(codec_image.ImageError, match="not downloaded"):
        codec_image.submit("a red boat")


def test_a_job_runs_to_done_and_leaves_nothing_running(img):
    job = codec_image.submit("a red boat on a calm sea", size=512)
    done = _wait(job["id"])
    assert done["state"] == "done", done
    assert done["results"] == [f"/api/image/file/{job['id']}/01.png"]
    path = codec_image.file_path(job["id"], "01.png")
    assert open(path, "rb").read().startswith(b"\x89PNG")
    spec = json.load(open(os.path.join(_dir(job["id"]), "spec.json")))
    assert spec["prompt"] == "a red boat on a calm sea" and spec["size"] == 512 and spec["refs"] == []
    assert json.load(open(os.path.join(_dir(job["id"]), "job.json")))["state"] == "done"
    assert not os.path.exists(codec_image.LOCK_PATH) and img.audits == ["done"]


def test_jobs_run_one_at_a_time(img):
    a = codec_image.submit("first")
    b = codec_image.submit("second")
    assert b["position"] == 2
    assert _wait(a["id"])["state"] == "done"
    assert _wait(b["id"])["state"] == "done", "the fake runner fails if two run at once"


def test_memory_guard_refuses_before_the_model_starts(img, monkeypatch):
    monkeypatch.setattr(codec_image, "free_memory_gb", lambda: 20.0)
    job = _wait(codec_image.submit("a castle")["id"])
    assert job["state"] == "refused" and "Not enough free memory" in job["error"]
    assert not os.path.exists(os.path.join(_dir(job["id"]), "spec.json")), "runner must not start"
    assert img.audits == ["refused"]


@pytest.mark.parametrize("kwargs, expect", [
    ({"prompt": "  "}, "Describe the image"),
    ({"prompt": "x", "size": 300}, "Size must be one of"),
    ({"prompt": "x", "count": 9}, "between 1 and"),
    ({"prompt": "x", "refs": [base64.b64encode(PNG).decode()] * 4}, "at most 3"),
    ({"prompt": "x", "refs": ["%%%not base64%%%"]}, "could not be read"),
    ({"prompt": "x", "refs": [base64.b64encode(b"GIF89a" + b"\x00" * 30).decode()]}, "PNG, JPEG or WebP"),
])
def test_input_limits(img, kwargs, expect):
    with pytest.raises(codec_image.ImageError, match=expect):
        codec_image.submit(**kwargs)


def test_oversized_reference_is_refused(img):
    cfg = json.loads(img.cfg.read_text())
    cfg["image"]["max_ref_mb"] = 0.0001
    img.cfg.write_text(json.dumps(cfg))
    with pytest.raises(codec_image.ImageError, match="larger than"):
        codec_image.submit("x", refs=[base64.b64encode(PNG + b"\x00" * 500).decode()])


def test_references_are_saved_and_given_to_the_model(img):
    refs = ["data:image/png;base64," + base64.b64encode(PNG).decode(), base64.b64encode(JPG).decode()]
    job = _wait(codec_image.submit("the same scene at night", refs=refs)["id"])
    assert job["state"] == "done" and job["refs"] == 2
    spec = json.load(open(os.path.join(_dir(job["id"]), "spec.json")))
    assert [os.path.basename(p) for p in spec["refs"]] == ["ref1.png", "ref2.jpg"]
    assert all(os.path.isfile(p) for p in spec["refs"])


def test_served_files_stay_inside_their_job_folder(img, tmp_path):
    job = _wait(codec_image.submit("a tree")["id"])
    assert codec_image.file_path(job["id"], "01.png")
    assert codec_image.file_path(job["id"], "../job.json") is None
    assert codec_image.file_path(job["id"], "job.json") is None
    assert codec_image.file_path("../../etc", "01.png") is None
    outside = tmp_path / "secret.png"
    outside.write_bytes(PNG)
    os.symlink(outside, os.path.join(_dir(job["id"]), "02.png"))
    assert codec_image.file_path(job["id"], "02.png") is None, "a symlink out of the folder is refused"


def test_cancel_stops_the_running_model(img, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "hang")
    job = codec_image.submit("a slow one")
    _wait(job["id"], states=("running",))
    for _ in range(100):                      # the lock appears once the runner starts
        if os.path.exists(codec_image.LOCK_PATH):
            break
        time.sleep(0.05)
    pid = json.load(open(codec_image.LOCK_PATH))["pid"]
    t0 = time.time()
    codec_image.cancel(job["id"])
    assert _wait(job["id"])["state"] == "cancelled" and time.time() - t0 < 12
    assert not codec_image._alive(pid) and not os.path.exists(codec_image.LOCK_PATH)


def test_a_crash_reports_the_runner_error(img, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "crash")
    job = _wait(codec_image.submit("x")["id"])
    assert job["state"] == "failed" and "MPS backend out of memory" in job["error"]


def test_unfinished_job_from_before_a_restart_reads_as_failed(img):
    job = _wait(codec_image.submit("x")["id"])
    path = os.path.join(_dir(job["id"]), "job.json")
    data = json.load(open(path))
    data["state"] = "running"
    json.dump(data, open(path, "w"))
    codec_image._Q = codec_image.ImageQueue()      # a fresh process knows nothing
    again = codec_image.get(job["id"])
    assert again["state"] == "failed" and "restarted" in again["error"]


def test_no_local_model_loads_while_a_job_runs(img, tmp_path, monkeypatch):
    import codec_cloud_models
    import codec_models
    cfg = tmp_path / "models.json"
    cfg.write_text(json.dumps({
        "llm_model": "mlx-community/A", "llm_base_url": "http://localhost:8083/v1",
        "extra_models": [{"id": "cloud-pro", "base_url": "https://cloud.example/v1",
                          "price_in_per_m": 0.1, "price_out_per_m": 0.1}]}))
    for mod in (codec_models, codec_cloud_models):
        monkeypatch.setattr(mod, "CONFIG_PATH", str(cfg))
    monkeypatch.setattr(codec_models, "discover_local",
                        lambda: [{"id": "mlx-community/B", "label": "B", "size_gb": 2.0}])
    monkeypatch.setattr(codec_models, "restart_server", lambda *a, **k: (True, "ok"))
    monkeypatch.setattr(codec_models, "probe", lambda m, **k: (True, "ok"))

    codec_image._write_lock(os.getpid(), "abcdefabcdef")
    r = codec_models.set_active("mlx-community/B")
    assert r["ok"] is False and "image is being generated" in r["error"]
    assert json.loads(cfg.read_text())["llm_model"] == "mlx-community/A"
    assert codec_models.set_active("cloud-pro")["ok"] is True, "a cloud switch is still allowed"

    codec_image._write_lock(2 ** 22 + 12345, "abcdefabcdef")   # a runner that is gone
    assert codec_image.busy_message() is None


def test_routes(img):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routes.image

    app = FastAPI()
    app.include_router(routes.image.router)
    c = TestClient(app)
    assert c.get("/api/image/status").json()["available"] is True
    r = c.post("/api/image/jobs", json={"prompt": ""})
    assert r.status_code == 400 and "Describe" in r.json()["error"]
    r = c.post("/api/image/jobs", json={"prompt": "a lighthouse", "size": 512})
    job_id = r.json()["job"]["id"]
    _wait(job_id)
    got = c.get(f"/api/image/file/{job_id}/01.png")
    assert got.status_code == 200 and got.content.startswith(b"\x89PNG")
    assert c.get(f"/api/image/file/{job_id}/job.json").status_code == 404
    assert c.get("/api/image/jobs/zzzz").status_code == 404
