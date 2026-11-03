"""The git update path: install.sh --update, update.sh and scripts/smoke.py (docs/SAFE-UPDATE-DESIGN.md, P-027).

The scripts run only in a throwaway clone in tmp_path whose origin is a local bare fixture repo, with pm2,
deploy_skills.sh and sync_to_pm2.sh replaced by stubs that write to a record file, and the state folder
(CODEC_STATE) in tmp_path. Git reads no user or system config. Nothing runs against ~/codec-repo, the live PM2
or ~/.codec.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = {"install": ["install.sh", "--update"], "update": ["update.sh"]}
GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t",
           "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "GIT_TERMINAL_PROMPT": "0"}

FIXTURE_SMOKE = """import pathlib, sys
root = pathlib.Path(__file__).resolve().parent.parent
v = (root / "version.txt").read_text().strip()
print("fixture smoke:", v)
if "dirty" in v:
    (root / "version.txt").write_text("written during the update\\n")
sys.exit(0 if v.endswith("ok") else 1)
"""


def _git(*args, cwd) -> str:
    r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=60,
                       env={**os.environ, **GIT_ENV})
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _exe(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


def _fixture(tmp_path: Path, next_version: str):
    """origin.git with commit A (smoke passes) and commit B (version.txt = next_version); clone at A."""
    origin, seed, clone = tmp_path / "origin.git", tmp_path / "seed", tmp_path / "clone"
    _git("init", "-q", "--bare", "-b", "main", str(origin), cwd=tmp_path)
    _git("clone", "-q", str(origin), str(seed), cwd=tmp_path)
    for name in ("install.sh", "update.sh"):
        shutil.copy2(REPO / name, seed / name)
    for name in ("codec_preupdate.py", "codec_backup.py"):
        shutil.copy2(REPO / name, seed / name)
    _exe(seed / "deploy_skills.sh", '#!/bin/bash\necho deploy >> "$RECORD"\n')
    _exe(seed / "sync_to_pm2.sh", '#!/bin/bash\necho sync >> "$RECORD"\npm2 restart open-codec\n')
    (seed / "scripts").mkdir()
    (seed / "scripts" / "smoke.py").write_text(FIXTURE_SMOKE)
    (seed / "version.txt").write_text("A ok\n")
    (seed / "local.txt").write_text("tracked, never changed by the update\n")
    _git("checkout", "-q", "-B", "main", cwd=seed)
    _git("add", "-A", cwd=seed)
    _git("commit", "-q", "-m", "A", cwd=seed)
    _git("push", "-q", "-u", "origin", "main", cwd=seed)
    a = _git("rev-parse", "HEAD", cwd=seed)
    _git("clone", "-q", str(origin), str(clone), cwd=tmp_path)
    (seed / "version.txt").write_text(next_version + "\n")
    _git("commit", "-q", "-am", "B", cwd=seed)
    _git("push", "-q", "origin", "main", cwd=seed)
    b = _git("rev-parse", "HEAD", cwd=seed)
    # The owner's own work in the install: an untracked file and an edit to a tracked file B does not touch.
    (clone / "mywork.txt").write_text("my notes\n")
    (clone / "local.txt").write_text("my local edit\n")
    return clone, a, b


def _state(tmp_path: Path, damaged: bool = False) -> Path:
    state = tmp_path / "state"
    state.mkdir()
    con = sqlite3.connect(str(state / "memory.db"))
    con.execute("CREATE TABLE t (body TEXT)")
    con.executemany("INSERT INTO t VALUES (?)", [("z" * 500,) for _ in range(200)])
    con.commit()
    con.close()
    (state / "config.json").write_text("{}")
    if damaged:
        db = state / "memory.db"
        data = bytearray(db.read_bytes())
        for i in range(4096 * 2, 4096 * 3):
            data[i] = (i * 7) % 256
        db.write_bytes(bytes(data))
    return state


def _run(which: str, clone: Path, state: Path, tmp_path: Path):
    stubs = tmp_path / "bin"
    stubs.mkdir(exist_ok=True)
    _exe(stubs / "pm2", '#!/bin/bash\necho "pm2 $*" >> "$RECORD"\n')
    (stubs / "python3").unlink(missing_ok=True)
    (stubs / "python3").symlink_to(sys.executable)
    record = tmp_path / "record.txt"
    record.write_text("")
    env = {**os.environ, **GIT_ENV, "PATH": f"{stubs}{os.pathsep}{os.environ.get('PATH', '')}",
           "CODEC_STATE": str(state), "CODEC_PY": sys.executable, "RECORD": str(record)}
    env.pop("PYTHONPATH", None)
    r = subprocess.run(["bash", str(clone / SCRIPTS[which][0]), *SCRIPTS[which][1:]], cwd=str(tmp_path),
                       capture_output=True, text=True, timeout=180, env=env)
    return r, record.read_text().splitlines()


def _snapshots(state: Path) -> list:
    root = state / "pre_update"
    return sorted(p for p in root.iterdir() if not p.name.startswith(".")) if root.exists() else []


def test_scripts_call_the_real_smoke_and_never_reset_hard():
    for name in ("install.sh", "update.sh"):
        text = (REPO / name).read_text()
        assert "scripts/smoke.py" in text and "codec_smoke_test" not in text, name
        assert 'git reset --keep "$ROLLBACK_COMMIT"' in text and "reset --hard" not in text, name
        assert "codec_preupdate.py --src" in text, name
    assert "codec_smoke_test" not in (REPO / "sync_to_pm2.sh").read_text()
    assert not (REPO / "codec_smoke_test.py").exists()


@pytest.mark.parametrize("which", sorted(SCRIPTS))
def test_passing_smoke_completes_at_new_commit(which, tmp_path):
    clone, a, b = _fixture(tmp_path, "B ok")
    state = _state(tmp_path)
    r, record = _run(which, clone, state, tmp_path)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "Update complete" in out and _git("rev-parse", "HEAD", cwd=clone) == b
    assert record == ["deploy", "sync", "pm2 restart open-codec"]  # the stubs, never the real pm2
    assert out.index("fixture smoke: B ok") < out.index("Deploying skills")  # checked before deploying
    (snap,) = _snapshots(state)
    assert (snap / "memory.db").is_file() and f'"label": "git {a}"' in (snap / "manifest.json").read_text()
    assert (clone / "mywork.txt").read_text() == "my notes\n"


@pytest.mark.parametrize("which", sorted(SCRIPTS))
def test_failing_smoke_rolls_back_and_keeps_work(which, tmp_path):
    clone, a, b = _fixture(tmp_path, "B broken")
    state = _state(tmp_path)
    r, record = _run(which, clone, state, tmp_path)
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "fixture smoke: B broken" in out and f"Rolled back to {a}" in out
    assert "Update complete" not in out
    assert _git("rev-parse", "HEAD", cwd=clone) == a
    assert (clone / "version.txt").read_text() == "A ok\n"
    assert (clone / "mywork.txt").read_text() == "my notes\n"         # untracked work kept
    assert (clone / "local.txt").read_text() == "my local edit\n"     # a local edit kept (reset --hard drops it)
    assert record == []                                                # nothing deployed, PM2 untouched
    assert len(_snapshots(state)) == 1                                 # the copy was taken before the check


@pytest.mark.parametrize("which", sorted(SCRIPTS))
def test_snapshot_failure_stops_the_update(which, tmp_path):
    clone, a, b = _fixture(tmp_path, "B ok")
    state = _state(tmp_path, damaged=True)
    r, record = _run(which, clone, state, tmp_path)
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "Could not save a copy of CODEC's data" in out and f"Rolled back to {a}" in out
    assert "fixture smoke" not in out and "Update complete" not in out and record == []
    assert _git("rev-parse", "HEAD", cwd=clone) == a and _snapshots(state) == []


@pytest.mark.parametrize("which", sorted(SCRIPTS))
def test_rollback_that_reset_keep_refuses_exits_2_at_the_new_commit(which, tmp_path):
    # The failing check writes into a file the update changed: reset --keep refuses rather than overwrite it.
    clone, a, b = _fixture(tmp_path, "B dirty")
    state = _state(tmp_path)
    r, record = _run(which, clone, state, tmp_path)
    out = r.stdout + r.stderr
    assert r.returncode == 2, out
    assert "Rollback refused" in out and f"git reset --keep {a}" in out and "Update complete" not in out
    assert _git("rev-parse", "HEAD", cwd=clone) == b and record == []
    assert (clone / "version.txt").read_text() == "written during the update\n"  # nothing overwritten


# ── scripts/smoke.py ─────────────────────────────────────────────────────────

_STUBS = {
    "codec_config.py": 'DRAFT_TASK_FILE = "/nonexistent/p027/draft_task.json"\n',
    "codec_watcher.py": (
        'TASK_FILE = "/nonexistent/p027/draft_task.json"\n'
        "def handle_draft(*a): pass\n"
        "def extract_content(*a): pass\n"
        "def clean_draft(text):\n"
        '    lines = text.split("\\n", 1)\n'
        '    if len(lines) > 1 and lines[0].lower().startswith("here is"):\n'
        "        text = lines[1].strip()\n"
        "    return text.strip('\"')\n"),
    "codec_core.py": ('DRAFT_KEYWORDS = ["draft", "reply"]\n'
                      "def is_draft(t):\n    return any(k in t.lower() for k in DRAFT_KEYWORDS)\n"),
}


def _smoke_tree(tmp_path: Path) -> Path:
    """smoke.py with the real codec_overlays, codec_identity, codec.py and codec_dashboard.py (the last two are
    only read) and small stand-ins for the modules that read the owner's settings when imported."""
    tree = tmp_path / "tree"
    (tree / "scripts").mkdir(parents=True)
    shutil.copy2(REPO / "scripts" / "smoke.py", tree / "scripts" / "smoke.py")
    for name in ("codec_overlays.py", "codec_identity.py", "codec.py", "codec_dashboard.py"):
        shutil.copy2(REPO / name, tree / name)
    for name, text in _STUBS.items():
        (tree / name).write_text(text)
    return tree


def _smoke(tree: Path):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    return subprocess.run([sys.executable, str(tree / "scripts" / "smoke.py")], cwd=str(tree), env=env,
                          capture_output=True, text=True, timeout=120)


def test_smoke_passes_on_fixture_modules_and_fails_on_a_broken_import(tmp_path):
    tree = _smoke_tree(tmp_path)
    good = _smoke(tree)
    assert good.returncode == 0 and "checks passed." in good.stdout, good.stdout + good.stderr
    assert "Wake phrase matching is case-insensitive" in good.stdout

    (tree / "codec_watcher.py").write_text("import p027_no_such_module\n")
    bad = _smoke(tree)
    out = bad.stdout + bad.stderr
    assert bad.returncode == 1, out
    assert "codec_watcher imports — ModuleNotFoundError" in out and "checks FAILED." in out
    assert "Traceback" not in out                               # a failed check, not a crash
    assert "\033[32m✓\033[0m Wake keyword matches in any case" in out  # the other sections still ran


@pytest.mark.skipif(getattr(sys.modules.get("live_state_guard"), "OWNER_HOME_EXISTED",
                            os.path.isdir(os.path.expanduser("~/.codec"))),
                    reason="imports the real modules, which read ~/.codec: only where none exists (CI)")
def test_smoke_passes_on_this_checkout():
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "smoke.py")], cwd=str(REPO),
                       capture_output=True, text=True, timeout=180)
    assert r.returncode == 0 and "checks passed." in r.stdout, r.stdout + r.stderr
