"""P-029: the app writes the eight Quick Actions on first launch
(docs/HOTKEYS-QUICK-ACTIONS-DESIGN.md).

Every test passes its own temporary Services folder: nothing here reads or writes
this Mac's ~/Library/Services, and the generated script runs against fake
pbcopy / afplay / osascript, so no clipboard, sound or notification is touched.
"""
from __future__ import annotations

import importlib.util
import json
import os
import plistlib
import shlex
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "packaging" / "macos"
TITLES = ["Elevate", "Explain", "Prompt", "Proofread", "Read Aloud", "Reply", "Save", "Translate"]
BASH = shutil.which("bash")


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def qa(tmp_path, monkeypatch):
    mod = _load(PKG / "quick_actions.py", "quick_actions_under_test")
    # Belt and braces: even a default argument lands in tmp, and no refresh runs.
    monkeypatch.setattr(mod, "SERVICES_DIR", str(tmp_path / "not-the-real-services"))
    monkeypatch.setattr(mod, "refresh_menu", lambda *a, **k: pytest.fail("refresh_menu must not run in tests"))
    return mod


def _exe(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _bundle(tmp_path: Path, name: str = "Sovereign AI Workstation.app") -> Path:
    contents = tmp_path / "Applications" / name / "Contents"
    res = contents / "Resources"
    _exe(res / "python" / "bin" / "python3", "#!/bin/sh\nexit 0\n")
    (res / "app").mkdir(parents=True)
    (res / "app" / "codec_textassist.py").write_text("# stand-in\n")
    with (contents / "Info.plist").open("wb") as fh:
        plistlib.dump({"CFBundleName": "Sovereign AI Workstation"}, fh)
    return contents


def _snapshot(root: Path) -> dict:
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None, p.stat().st_mtime_ns)
            for p in sorted(root.rglob("*"))}


def _command(folder: Path) -> str:
    with (folder / "Contents" / "document.wflow").open("rb") as fh:
        doc = plistlib.load(fh)
    return doc["actions"][0]["action"]["ActionParameters"]["COMMAND_STRING"]


# ── What gets written ───────────────────────────────────────────────────────

def test_writes_the_eight_services_pointing_inside_the_bundle(qa, tmp_path):
    contents = _bundle(tmp_path)
    interp, app_dir = qa.bundle_targets(str(contents))
    assert interp == str(contents / "Resources" / "python" / "bin" / "python3")
    services = tmp_path / "Services"
    results = qa.install(str(services), interp, app_dir)
    assert [a for _n, a in results] == ["written"] * 8
    assert sorted(p.name for p in services.iterdir()) == sorted(f"CODEC {t}.workflow" for t in TITLES)
    for title, mode in qa.SERVICES:
        folder = services / f"CODEC {title}.workflow"
        with (folder / "Contents" / "Info.plist").open("rb") as fh:
            info = plistlib.load(fh)
        svc = info["NSServices"][0]
        assert svc["NSMenuItem"]["default"] == f"CODEC {title}"
        assert svc["NSMessage"] == "runWorkflowAsService" and svc["NSSendTypes"] == ["public.utf8-plain-text"]
        assert info[qa.MARKER]["mode"] == mode
        with (folder / "Contents" / "document.wflow").open("rb") as fh:
            doc = plistlib.load(fh)
        meta, params = doc["workflowMetaData"], doc["actions"][0]["action"]["ActionParameters"]
        assert meta["workflowTypeIdentifier"] == "com.apple.Automator.servicesMenu"
        assert meta["serviceInputTypeIdentifier"] == "com.apple.Automator.text"
        assert params["inputMethod"] == 0 and params["shell"] == "/bin/bash"
        cmd = params["COMMAND_STRING"]
        assert f"PY={shlex.quote(interp)}" in cmd and f"APP={shlex.quote(app_dir)}" in cmd
        assert f'"$APP/codec_textassist.py" {mode} ' in cmd
        assert "/Users/" not in cmd.replace(str(tmp_path), ""), "no owner path baked in"


def test_a_second_run_is_unchanged_and_touches_nothing(qa, tmp_path):
    contents = _bundle(tmp_path)
    services = tmp_path / "Services"
    qa.install(str(services), *qa.bundle_targets(str(contents)))
    before = _snapshot(services)
    results = qa.install(str(services), *qa.bundle_targets(str(contents)))
    assert [a for _n, a in results] == ["unchanged"] * 8
    assert _snapshot(services) == before


def test_never_overwrites_a_workflow_without_the_marker(qa, tmp_path):
    services = tmp_path / "Services"
    hand = services / "CODEC Proofread.workflow" / "Contents"
    hand.mkdir(parents=True)
    with (hand / "Info.plist").open("wb") as fh:  # the Studio's shape: no CODECQuickAction key
        plistlib.dump({"NSServices": [{"NSMenuItem": {"default": "CODEC Proofread"}}]}, fh)
    (hand / "document.wflow").write_bytes(qa.document("proofread", "python3.13 ~/codec-repo/codec_textassist.py proofread"))
    (hand / "QuickLook").mkdir()
    (hand / "QuickLook" / "Thumbnail.png").write_bytes(b"png")
    before = _snapshot(services / "CODEC Proofread.workflow")
    for app in ("A", "B"):
        contents = _bundle(tmp_path / app)
        for create in (True, False):
            results = dict(qa.install(str(services), *qa.bundle_targets(str(contents)), create=create))
            assert results["CODEC Proofread.workflow"] == "kept"
    assert _snapshot(services / "CODEC Proofread.workflow") == before, "a hand-made workflow was changed"
    assert not qa.is_ours(str(services / "CODEC Proofread.workflow"))


def test_rewrites_its_own_when_the_app_moved(qa, tmp_path):
    services = tmp_path / "Services"
    old = _bundle(tmp_path / "old")
    new = _bundle(tmp_path / "new")
    qa.install(str(services), *qa.bundle_targets(str(old)))
    results = qa.install(str(services), *qa.bundle_targets(str(new)))
    assert [a for _n, a in results] == ["updated"] * 8
    assert str(new) in _command(services / "CODEC Translate.workflow")
    assert str(old) not in _command(services / "CODEC Translate.workflow")
    assert sorted(p.name for p in services.iterdir()) == sorted(f"CODEC {t}.workflow" for t in TITLES), \
        "no hidden temp or old folder left behind"


def test_repair_mode_never_recreates_one_the_user_deleted(qa, tmp_path):
    services = tmp_path / "Services"
    qa.install(str(services), *qa.bundle_targets(str(_bundle(tmp_path / "old"))))
    shutil.rmtree(services / "CODEC Save.workflow")
    results = dict(qa.install(str(services), *qa.bundle_targets(str(_bundle(tmp_path / "new"))), create=False))
    assert results.pop("CODEC Save.workflow") == "skipped"
    assert set(results.values()) == {"updated"}
    assert not (services / "CODEC Save.workflow").exists()


def test_dry_run_writes_nothing(qa, tmp_path):
    services = tmp_path / "Services"
    results = qa.install(str(services), *qa.bundle_targets(str(_bundle(tmp_path))), dry_run=True)
    assert [a for _n, a in results] == ["would write"] * 8
    assert not services.exists()


# ── The script inside, run against fake tools ───────────────────────────────

def _fake_tools(tmp_path: Path) -> tuple[dict, Path]:
    calls = tmp_path / "calls.txt"
    clip = tmp_path / "clipboard.txt"
    bin_dir = tmp_path / "fakebin"
    tools = {
        "pbcopy": str(_exe(bin_dir / "pbcopy", f'#!/bin/sh\ncat > "{clip}"\necho pbcopy >> "{calls}"\n')),
        "afplay": str(_exe(bin_dir / "afplay", f'#!/bin/sh\necho "afplay $*" >> "{calls}"\n')),
        "osascript": str(_exe(bin_dir / "osascript", f'#!/bin/sh\nprintf "osascript %s\\n" "$2" >> "{calls}"\n')),
    }
    return tools, calls


def _run_script(text: str) -> subprocess.CompletedProcess:
    return subprocess.run([BASH, "-c", text], input="the selected text", capture_output=True, text=True, timeout=30)


@pytest.mark.skipif(BASH is None, reason="needs bash")
def test_a_moved_app_gives_one_notice_no_shell_text_and_exit_0(qa, tmp_path):
    tools, calls = _fake_tools(tmp_path)
    gone = tmp_path / "Gone.app" / "Contents" / "Resources"
    text = qa.script("proofread", str(gone / "python" / "bin" / "python3"), str(gone / "app"),
                     tools=tools, log_dir=str(tmp_path / "logs"))
    r = _run_script(text)
    assert r.returncode == 0
    assert r.stdout == "" and r.stderr == "", "Automator would show this as an error"
    lines = calls.read_text().splitlines()
    assert len(lines) == 1 and lines[0].startswith("osascript display notification"), lines
    assert qa.MOVED_NOTICE in lines[0] and "\n" not in qa.MOVED_NOTICE
    assert not (tmp_path / "clipboard.txt").exists(), "the clipboard is left alone"


@pytest.mark.skipif(BASH is None, reason="needs bash")
def test_the_script_copies_the_selection_and_runs_textassist_from_the_app_folder(qa, tmp_path):
    tools, calls = _fake_tools(tmp_path)
    app_dir = tmp_path / "My App" / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "codec_textassist.py").write_text("#\n")
    ran = tmp_path / "ran.txt"
    interp = _exe(tmp_path / "My App" / "python3",
                  f'#!/bin/sh\necho "$PWD|$*" > "{ran}"\necho "some error text" >&2\nexit 3\n')
    logs = tmp_path / "logs"
    r = _run_script(qa.script("translate", str(interp), str(app_dir), tools=tools, log_dir=str(logs)))
    assert r.returncode == 0 and r.stdout == "" and r.stderr == ""
    assert (tmp_path / "clipboard.txt").read_text() == "the selected text"
    assert calls.read_text().splitlines() == ["pbcopy", "afplay /System/Library/Sounds/Blow.aiff"]
    cwd, args = ran.read_text().strip().split("|")
    assert os.path.realpath(cwd) == os.path.realpath(app_dir)
    assert args == f"{app_dir}/codec_textassist.py translate"
    assert "some error text" in (logs / "quick-actions.log").read_text(), "errors go to the log, not to Automator"


# ── Wiring: build, first launch, later launches ─────────────────────────────

def test_build_app_copies_quick_actions_next_to_first_run():
    build = (PKG / "build_app.sh").read_text()
    assert 'cp "$PKG_DIR/quick_actions.py" "$CONTENTS/Resources/quick_actions.py"' in build


@pytest.fixture
def first_run(qa, monkeypatch):
    fr = _load(PKG / "first_run.py", "first_run_under_test")
    monkeypatch.setattr(fr, "_quick_actions", lambda: qa)
    return fr


def test_first_run_writes_them_from_a_bundle(first_run, tmp_path):
    contents = _bundle(tmp_path)
    services = tmp_path / "Services"
    home = tmp_path / "codec_home"
    home.mkdir()
    assert first_run.install_quick_actions(str(contents), str(home), services_dir=str(services)) == 0
    assert len(list(services.iterdir())) == 8
    assert str(contents / "Resources" / "python" / "bin" / "python3") in _command(services / "CODEC Reply.workflow")
    assert "Quick Actions" in " ".join(first_run.plan())


def test_first_run_skips_outside_the_app_when_switched_off_and_from_a_dmg(first_run, tmp_path):
    services = tmp_path / "Services"
    home = tmp_path / "codec_home"
    home.mkdir()
    assert first_run.install_quick_actions(None, str(home), services_dir=str(services)) == 0
    dmg = "/Volumes/CODEC Installer/Sovereign AI Workstation.app/Contents"
    assert first_run.install_quick_actions(dmg, str(home), services_dir=str(services)) == 0
    (home / "config.json").write_text(json.dumps({"assist_enabled": False}))
    assert first_run.install_quick_actions(str(_bundle(tmp_path)), str(home), services_dir=str(services)) == 0
    assert not services.exists()


def test_first_run_repair_flag_runs_and_touches_nothing_outside_the_app(tmp_path):
    home = tmp_path / "codec_home"
    r = subprocess.run([sys.executable, str(PKG / "first_run.py"), "--home", str(home), "--quick-actions-repair"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert "not running from the app" in r.stdout


def test_later_launches_repair_and_the_first_launch_does_not(tmp_path, monkeypatch):
    entry = _load(PKG / "launcher" / "codec_app_main.py", "codec_app_main_under_test")
    contents = _bundle(tmp_path)
    (contents / "Resources" / "first_run.py").write_text("#\n")
    home = tmp_path / "codec_home"
    home.mkdir()
    runs = []

    class _Done:
        returncode, stdout, stderr = 0, "  updated       CODEC Reply.workflow\n", ""

    monkeypatch.setattr(entry, "_bundle_contents", lambda: contents)
    monkeypatch.setattr(entry, "_codec_dir", lambda: home)
    monkeypatch.setattr(entry, "_log", lambda *_a: None)
    monkeypatch.setattr(entry, "_fleet_loaded", lambda: 15)
    monkeypatch.setattr(entry.subprocess, "run", lambda cmd, **_k: runs.append(cmd) or _Done())
    monkeypatch.setattr(entry, "_run_first_run", lambda res: 0)
    assert entry.main([]) == 0
    assert runs == [], "the first launch writes them through first_run.py itself"
    (home / entry.SENTINEL).write_text("ok\n")
    assert entry.main([]) == 0
    assert len(runs) == 1 and runs[0][-1] == "--quick-actions-repair"
    assert runs[0][1] == str(contents / "Resources" / "first_run.py")


# ── codec_textassist.py: which modes replace the selection ──────────────────

def _run_textassist(tmp_path: Path, mode: str) -> tuple[list, str]:
    """Run the real codec_textassist.py with stand-ins for the model, the paste,
    the overlays, pbpaste/pbcopy and the result window, and record what it did."""
    work = tmp_path / "ta"
    work.mkdir()
    shutil.copy(REPO / "codec_textassist.py", work / "codec_textassist.py")
    rec, clip = work / "calls.jsonl", work / "clipboard.txt"
    stubs = {
        "codec_overlays.py": "def show_overlay(*a, **k):\n    return None\ndef hide_overlay(*a, **k):\n    return None\n",
        "codec_paste.py": f"import json\ndef paste_clipboard(**k):\n    open({str(rec)!r}, 'a').write(json.dumps('paste') + '\\n')\n",
        "codec_llm.py": "def call(messages, **k):\n    return 'RESULT'\n",
        "codec_cloud_models.py": "def local_only(base, model, cfg):\n    return base, model\n",
    }
    for name, body in stubs.items():
        (work / name).write_text(body)
    site = work / "site"
    site.mkdir()
    # Only the two child Pythons the script starts (`python -c <window or speech>`)
    # are recorded and replaced by `pass`; pbpaste and pbcopy run as usual.
    (site / "sitecustomize.py").write_text(
        "import json, subprocess, sys\n"
        "_Real = subprocess.Popen\n"
        "class _Popen(_Real):\n"
        "    def __init__(self, args, *a, **k):\n"
        "        if isinstance(args, list) and len(args) > 2 and args[1] == '-c':\n"
        "            kind = 'window' if 'tkinter' in args[2] else 'speech'\n"
        f"            open({str(rec)!r}, 'a').write(json.dumps(kind) + '\\n')\n"
        "            args = [sys.executable, '-c', 'pass']\n"
        "        super().__init__(args, *a, **k)\n"
        "subprocess.Popen = _Popen\n")
    bin_dir = work / "bin"
    _exe(bin_dir / "pbpaste", "#!/bin/sh\nprintf 'some selected text'\n")
    _exe(bin_dir / "pbcopy", f'#!/bin/sh\ncat > "{clip}"\necho \'"pbcopy"\' >> "{rec}"\n')
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}", "PYTHONPATH": str(site)}
    r = subprocess.run([sys.executable, str(work / "codec_textassist.py"), mode],
                       env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    calls = [json.loads(ln) for ln in rec.read_text().splitlines()] if rec.exists() else []
    return calls, (clip.read_text() if clip.exists() else "")


@pytest.mark.parametrize("mode", ["proofread", "elevate", "reply", "prompt"])
def test_textassist_replaces_the_selection_in(tmp_path, mode):
    calls, clipboard = _run_textassist(tmp_path, mode)
    assert clipboard == "RESULT"
    assert calls == ["pbcopy", "paste"], calls


@pytest.mark.parametrize("mode", ["explain", "translate"])
def test_textassist_shows_a_window_and_copies_without_pasting_in(tmp_path, mode):
    calls, clipboard = _run_textassist(tmp_path, mode)
    assert clipboard == "RESULT"
    assert "paste" not in calls, "Explain and Translate never replace the selection"
    assert calls[0] == "pbcopy" and "window" in calls, calls
