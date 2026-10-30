"""The buyer app's package set (audit P-011, D-35): hash lock, licence exclusions,
bundle install flags, service imports, pinned model revisions."""
import importlib.util
import json
import re
import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
LOCK = REPO / "packaging/macos/requirements-lock.txt"
REQS = REPO / "requirements.txt"


def _pins(path=LOCK):
    names = {}
    for line in path.read_text().splitlines():
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==(\S+)", line)
        if m:
            names[re.sub(r"[-_.]+", "-", m.group(1)).lower()] = m.group(2)
    return names


def _load_checker():
    spec = importlib.util.spec_from_file_location(
        "bundle_imports", REPO / "packaging/macos/verify/bundle_imports.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_lock_has_every_declared_runtime_package():
    pins = _pins()
    for name in ("mlx-whisper", "google-api-python-client", "google-auth",
                 "google-auth-oauthlib", "zeroconf", "pyobjc-framework-avfoundation",
                 "pyobjc-framework-quartz", "pyobjc-framework-cocoa", "pyperclip", "numba"):
        assert name in pins, f"{name} is missing from the buyer lock"
    # requirements.txt names them too, so the next lock refresh keeps them.
    reqs = REQS.read_text().lower()
    for name in ("mlx-whisper", "google-api-python-client", "zeroconf",
                 "pyobjc-framework-avfoundation", "pyperclip"):
        assert re.search(rf"^{re.escape(name)}\b", reqs, re.M), f"{name} not declared in requirements.txt"


def test_forbidden_packages_are_in_neither_file():
    # GPL/AGPL code (pyautogui -> mouseinfo, PyMuPDF) and PyAV (bundles GPL libx264/libx265,
    # needed by faster-whisper) never ship in the paid app.
    bad = re.compile(r"^(pymupdf|fitz|pyautogui|mouseinfo|faster-whisper|av|torch)\b", re.I | re.M)
    for path in (LOCK, REQS):
        hits = [m.group(0) for m in bad.finditer(path.read_text())]
        assert not hits, f"{path.name} lists {hits}"


def test_every_lock_entry_is_hash_pinned_and_header_count_is_true():
    text = LOCK.read_text()
    blocks = re.split(r"\n(?=[A-Za-z0-9][A-Za-z0-9._-]*==)", text)[1:]
    assert blocks and all("--hash=sha256:" in b for b in blocks), "an entry has no hash"
    m = re.search(r"(\d+) packages, every file hash-pinned", text)
    assert m and int(m.group(1)) == len(blocks), "the header's package count is stale"


def test_bundle_script_installs_from_the_lock_with_hashes_wheels_and_no_deps():
    sh = (REPO / "packaging/macos/bundle_python.sh").read_text()
    assert "--require-hashes --only-binary=:all: --no-deps" in sh
    assert 'requirements-lock.txt' in sh
    assert 'install -r "$REQS"' not in sh and "requirements.txt" not in sh.split("set -euo")[1]
    assert "has no --hash lines" in sh, "the script must refuse a lock without hashes"


def test_ci_installs_the_lock_the_same_way():
    ci = (REPO / ".github/workflows/ci.yml").read_text()
    assert 'pip install --require-hashes --only-binary=:all: --no-deps -r "$BUYER_LOCK"' in ci


@pytest.mark.skipif(shutil.which("node") is None, reason="reads ecosystem.config.js with node")
def test_services_import_only_packages_that_are_in_the_lock():
    mod = _load_checker()
    modules, problems = mod.required_modules()
    assert not problems, problems
    # the scan really reaches the google skills, the STT server and request_mic
    assert {"googleapiclient.discovery", "mlx_whisper", "AVFoundation", "fastapi"} <= set(modules)


@pytest.mark.skipif(shutil.which("node") is None, reason="reads ecosystem.config.js with node")
def test_the_checker_fails_when_the_lock_lacks_a_package(tmp_path, monkeypatch):
    mod = _load_checker()
    thin = tmp_path / "lock.txt"
    thin.write_text("numpy==2.0.0 \\\n    --hash=sha256:00\n")
    monkeypatch.setattr(mod, "LOCK", str(thin))
    _modules, problems = mod.required_modules()
    assert any("fastapi" in p for p in problems), "a thin lock must be reported"


def test_optional_imports_each_have_a_reason():
    data = json.loads((REPO / "packaging/macos/optional_imports.json").read_text())
    data.pop("_comment")
    assert data and all(isinstance(v, str) and len(v) > 20 for v in data.values())


def test_every_model_revision_is_a_40_hex_commit():
    models = json.loads((REPO / "packaging/macos/models.json").read_text())["models"]
    assert models
    for m in models:
        assert re.fullmatch(r"[0-9a-f]{40}", m["revision"]), f"{m['name']} is not pinned: {m['revision']}"


def test_setup_download_passes_the_pinned_revision(monkeypatch, tmp_path):
    import codec_setup
    models = json.loads((REPO / "packaging/macos/models.json").read_text())["models"]
    entry = next(m for m in models if m["repo"] == codec_setup.BUNDLED_LOCAL_MODEL)
    assert codec_setup.bundled_local_revision() == entry["revision"]
    # the download call hands that revision to huggingface_hub
    src = (REPO / "codec_setup.py").read_text()
    assert "snapshot_download(repo_id=BUNDLED_LOCAL_MODEL, revision=bundled_local_revision()" in src
    # another manifest can be named by CODEC_MODELS_JSON; an unpinned one is not used
    other = tmp_path / "models.json"
    other.write_text(json.dumps({"models": [{"repo": codec_setup.BUNDLED_LOCAL_MODEL, "revision": "d" * 40}]}))
    monkeypatch.setenv("CODEC_MODELS_JSON", str(other))
    assert codec_setup.bundled_local_revision() == "d" * 40
    other.write_text(json.dumps({"models": [{"repo": codec_setup.BUNDLED_LOCAL_MODEL, "revision": "main"}]}))
    assert codec_setup.bundled_local_revision() == entry["revision"]
