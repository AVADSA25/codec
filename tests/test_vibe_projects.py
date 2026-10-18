"""Vibe projects: files, checkpoints, undo (docs/VIBE-PROJECTS-DESIGN.md).

Every test works in a temporary folder (CODEC_VIBE_ROOT / CODEC_VIBE_TRASH), so the
real ~/codec-workspace and ~/.codec are never touched.
"""
from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import codec_vibe_projects as vp


@pytest.fixture(autouse=True)
def _folders(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEC_VIBE_ROOT", str(tmp_path / "vibe"))
    monkeypatch.setenv("CODEC_VIBE_TRASH", str(tmp_path / "trash"))
    monkeypatch.delenv("VIBE_PROJECTS_ENABLED", raising=False)
    return tmp_path


def _pdir(pid):
    return vp.project_dir(pid)


def _files(pid):
    return {f["path"]: vp.read_file(pid, f["path"])["content"] for f in vp.list_files(pid)}


def _two_files(**over):
    base = {"index.html": "<h1>one</h1>\n", "style.css": "h1 { color: red; }\n"}
    base.update(over)
    return [{"path": p, "content": c} for p, c in base.items()]


# ── paths ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [
    "../outside.txt", "a/../../outside.txt", "/etc/passwd", "~/x.txt", "a\\b.txt", "", "   ", ".",
    ".vibe/project.json", ".git/config", "node_modules/x.js", "a/.git/hooks/pre-commit",
    ".env", ".env.local", "config/.env", "server.pem", "keys/id_rsa", "credentials.json",
    "a/b/c/d/e/f/g/h/i.txt", "x" * 201, "bad\x00name.txt", "tab\tname.txt",
])
def test_path_safety_refuses_bad_paths(bad):
    pid = vp.create_project("Safety")["id"]
    with pytest.raises(vp.VibeError) as e:
        vp.write_file(pid, bad, "x")
    assert e.value.status in (400, 413)
    assert [f["path"] for f in vp.list_files(pid)] == []
    with pytest.raises(vp.VibeError):
        vp.apply_blocks(pid, [{"path": bad, "content": "x"}], "bad")
    assert vp.list_files(pid) == []


def test_path_safety_refuses_links_out_of_the_project(tmp_path):
    pid = vp.create_project("Links")["id"]
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("top secret")
    os.symlink(outside, _pdir(pid) / "linked")
    os.symlink(outside / "secret.txt", _pdir(pid) / "peek.txt")
    for rel in ("linked/x.txt", "peek.txt"):
        with pytest.raises(vp.VibeError):
            vp.write_file(pid, rel, "overwritten")
    with pytest.raises(vp.VibeError):
        vp.read_file(pid, "peek.txt")
    assert (outside / "secret.txt").read_text() == "top secret"
    assert not (outside / "x.txt").exists()
    # links are not part of the project: not listed, not checkpointed
    assert vp.list_files(pid) == []


def test_path_safety_caps_size_and_count():
    pid = vp.create_project("Caps")["id"]
    with pytest.raises(vp.VibeError) as e:
        vp.write_file(pid, "big.txt", "x" * (vp.MAX_FILE_BYTES + 1))
    assert e.value.status == 413
    vp.write_file(pid, "edge.txt", "x" * vp.MAX_FILE_BYTES)        # exactly 1 MB is fine
    with pytest.raises(vp.VibeError):
        vp.apply_blocks(pid, [{"path": f"f{i}.txt", "content": "x"} for i in range(vp.MAX_BLOCKS + 1)], "many")
    for i in range(vp.MAX_FILES - 1):
        vp.write_file(pid, f"d/f{i}.txt", "x")
    with pytest.raises(vp.VibeError) as e:
        vp.write_file(pid, "one-too-many.txt", "x")
    assert e.value.status == 413
    with pytest.raises(vp.VibeError):
        vp.write_file(pid, "bad-content.txt", 123)


def test_path_safety_normalises_the_path_it_accepts():
    pid = vp.create_project("Norm")["id"]
    vp.write_file(pid, "./a//b/../c.txt", "x")
    assert [f["path"] for f in vp.list_files(pid)] == ["a/c.txt"]


# ── restore ──────────────────────────────────────────────────────────────

def test_restore_brings_back_every_file_exactly_and_keeps_history():
    pid = vp.create_project("Round trip", _two_files())["id"]
    start = vp.list_checkpoints(pid)[0]
    assert start["kind"] == "start" and start["label"] == "Start"
    vp.apply_blocks(pid, [{"path": "index.html", "content": "<h1>two</h1>\n"},
                          {"path": "deep/dir/app.js", "content": "console.log(1)\n"}], "second")
    (_pdir(pid) / "style.css").unlink()
    assert set(_files(pid)) == {"index.html", "deep/dir/app.js"}
    before = [c["id"] for c in vp.list_checkpoints(pid)]

    out = vp.restore(pid, start["id"])
    assert _files(pid) == {"index.html": "<h1>one</h1>\n", "style.css": "h1 { color: red; }\n"}
    assert not (_pdir(pid) / "deep").exists(), "the folder of a removed file goes too"
    after = [c["id"] for c in vp.list_checkpoints(pid)]
    assert set(before) <= set(after) and len(after) > len(before), "history only grows"
    assert out["restored_to"] == start["id"] and out["checkpoint"]["kind"] == "restore"
    # the manual deletion that was never checkpointed is saved before the restore, not lost
    assert out["saved_before"] and any(c["kind"] == "before_restore" for c in vp.list_checkpoints(pid))


def test_restore_can_itself_be_undone_by_restoring_the_newer_checkpoint():
    pid = vp.create_project("Redo", _two_files())["id"]
    ai = vp.apply_blocks(pid, [{"path": "index.html", "content": "<h1>new</h1>\n"}], "change")["checkpoint"]
    start = vp.list_checkpoints(pid)[-1]
    vp.restore(pid, start["id"])
    assert _files(pid)["index.html"] == "<h1>one</h1>\n"
    vp.restore(pid, ai["id"])
    assert _files(pid)["index.html"] == "<h1>new</h1>\n"


def test_restore_keeps_exact_bytes_including_crlf_and_non_utf8():
    pid = vp.create_project("Bytes")["id"]
    raw = b"line1\r\nline2\r\n\xff\xfe\x00bin"
    (_pdir(pid) / "data.bin").write_bytes(raw)
    vp.checkpoint_now(pid, "with data")
    cid = vp.list_checkpoints(pid)[0]["id"]
    (_pdir(pid) / "data.bin").write_bytes(b"changed")
    vp.restore(pid, cid)
    assert (_pdir(pid) / "data.bin").read_bytes() == raw
    assert vp.read_file(pid, "data.bin").get("binary") is True


def test_restore_to_the_state_it_is_already_in_makes_no_new_checkpoint():
    pid = vp.create_project("Same", _two_files())["id"]
    latest = vp.list_checkpoints(pid)[0]
    n = len(vp.list_checkpoints(pid))
    out = vp.restore(pid, latest["id"])
    assert len(vp.list_checkpoints(pid)) == n and out["checkpoint"]["id"] == latest["id"]


def test_restore_refuses_a_damaged_history_that_points_outside(tmp_path):
    pid = vp.create_project("Damaged", _two_files())["id"]
    import json
    cp = _pdir(pid) / ".vibe" / "checkpoints.jsonl"
    rec = json.loads(cp.read_text().splitlines()[-1])
    rec["id"], rec["seq"] = "c0099", 99
    rec["files"] = {"../../escaped.txt": "0" * 64}
    cp.write_text(cp.read_text() + json.dumps(rec) + "\n")
    with pytest.raises(vp.VibeError):
        vp.restore(pid, "c0099")
    assert not (tmp_path / "escaped.txt").exists()
    with pytest.raises(vp.VibeError) as e:
        vp.restore(pid, "nope")
    assert e.value.status == 400
    with pytest.raises(vp.VibeError) as e:
        vp.restore(pid, "c0777")
    assert e.value.status == 404


# ── apply ────────────────────────────────────────────────────────────────

def test_apply_writes_everything_behind_a_checkpoint_before_and_after():
    pid = vp.create_project("Apply", _two_files())["id"]
    # a hand edit that was never checkpointed must land in the "before" checkpoint
    (_pdir(pid) / "index.html").write_text("<h1>hand edit</h1>\n")
    out = vp.apply_blocks(pid, [{"path": "index.html", "content": "<h1>ai</h1>\n<p>x</p>\n"},
                                {"path": "app.js", "content": "a()\nb()\n"}], "make it nicer")
    kinds = [c["kind"] for c in vp.list_checkpoints(pid)]
    assert kinds[:2] == ["ai", "before"] and kinds[-1] == "start"
    by_path = {c["path"]: c for c in out["changed"]}
    assert by_path["app.js"] == {"path": "app.js", "status": "added", "added": 2, "removed": 0}
    assert by_path["index.html"]["status"] == "changed" and by_path["index.html"]["added"] == 2
    assert out["checkpoint"]["stat"]["added"] == 1 and out["checkpoint"]["stat"]["changed"] == 1
    assert out["before"] == vp.list_checkpoints(pid)[1]["id"]
    assert _files(pid)["index.html"] == "<h1>ai</h1>\n<p>x</p>\n"
    # the hand edit is recoverable from the before checkpoint
    vp.restore(pid, out["before"])
    assert _files(pid)["index.html"] == "<h1>hand edit</h1>\n" and "app.js" not in _files(pid)


def test_apply_is_all_or_nothing():
    pid = vp.create_project("Atomic", _two_files())["id"]
    before = _files(pid)
    n = len(vp.list_checkpoints(pid))
    with pytest.raises(vp.VibeError):
        vp.apply_blocks(pid, [{"path": "index.html", "content": "<h1>changed</h1>\n"},
                              {"path": "../escape.txt", "content": "x"}], "bad batch")
    assert _files(pid) == before and len(vp.list_checkpoints(pid)) == n
    with pytest.raises(vp.VibeError):
        vp.apply_blocks(pid, [], "empty")
    with pytest.raises(vp.VibeError):
        vp.apply_blocks(pid, [{"path": "a.txt"}], "no content")
    with pytest.raises(vp.VibeError):
        vp.apply_blocks(pid, "not a list", "wrong type")


def test_apply_puts_back_what_it_wrote_when_a_write_fails(monkeypatch):
    pid = vp.create_project("Rollback", _two_files())["id"]
    before = _files(pid)
    real = vp._write_file_locked
    calls = {"n": 0}

    def flaky(pdir, rel, content):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk full")
        return real(pdir, rel, content)

    monkeypatch.setattr(vp, "_write_file_locked", flaky)
    with pytest.raises(OSError):
        vp.apply_blocks(pid, [{"path": "index.html", "content": "<h1>x</h1>\n"},
                              {"path": "new.txt", "content": "y"}], "fails")
    monkeypatch.setattr(vp, "_write_file_locked", real)
    assert _files(pid) == before


def test_apply_with_identical_content_makes_no_ai_checkpoint():
    pid = vp.create_project("Noop", _two_files())["id"]
    out = vp.apply_blocks(pid, [{"path": "index.html", "content": "<h1>one</h1>\n"}], "same")
    assert out["checkpoint"] is None and out["changed"] == []
    assert [c["kind"] for c in vp.list_checkpoints(pid)] == ["start"]


def test_apply_later_block_for_the_same_path_wins():
    pid = vp.create_project("Dupes")["id"]
    vp.apply_blocks(pid, [{"path": "a.txt", "content": "first"}, {"path": "a.txt", "content": "second"}], "dupes")
    assert _files(pid) == {"a.txt": "second"}


# ── undo ─────────────────────────────────────────────────────────────────

def test_undo_returns_to_before_the_newest_ai_change_and_goes_one_further_each_time():
    pid = vp.create_project("Undo", _two_files())["id"]
    vp.apply_blocks(pid, [{"path": "index.html", "content": "<h1>A</h1>\n"}], "A")
    vp.apply_blocks(pid, [{"path": "index.html", "content": "<h1>B</h1>\n"}, {"path": "b.txt", "content": "b"}], "B")
    assert vp.project_info(pid)["can_undo"] is True
    out = vp.undo_last_ai(pid)
    assert _files(pid)["index.html"] == "<h1>A</h1>\n" and "b.txt" not in _files(pid)
    assert out["checkpoint"]["kind"] == "restore" and out["undid"]
    vp.undo_last_ai(pid)
    assert _files(pid) == {"index.html": "<h1>one</h1>\n", "style.css": "h1 { color: red; }\n"}
    assert vp.project_info(pid)["can_undo"] is False
    with pytest.raises(vp.VibeError) as e:
        vp.undo_last_ai(pid)
    assert e.value.status == 409


def test_undo_of_the_first_change_empties_a_new_project_and_a_new_change_can_be_undone_again():
    pid = vp.create_project("Empty first")["id"]
    vp.apply_blocks(pid, [{"path": "main.py", "content": "print(1)\n"}], "first")
    vp.undo_last_ai(pid)
    assert vp.list_files(pid) == []
    vp.apply_blocks(pid, [{"path": "main.py", "content": "print(2)\n"}], "again")
    assert _files(pid) == {"main.py": "print(2)\n"}
    vp.undo_last_ai(pid)
    assert vp.list_files(pid) == []


def test_undo_saves_hand_edits_made_after_the_ai_change_first():
    pid = vp.create_project("Keep edits", _two_files())["id"]
    vp.apply_blocks(pid, [{"path": "index.html", "content": "<h1>ai</h1>\n"}], "ai")
    (_pdir(pid) / "index.html").write_text("<h1>ai then my edit</h1>\n")
    out = vp.undo_last_ai(pid)
    assert out["saved_before"]
    assert _files(pid)["index.html"] == "<h1>one</h1>\n"
    vp.restore(pid, out["saved_before"])
    assert _files(pid)["index.html"] == "<h1>ai then my edit</h1>\n"


def test_undo_after_restoring_an_old_version_does_not_undo_changes_that_are_no_longer_applied():
    pid = vp.create_project("Floor", _two_files())["id"]
    vp.apply_blocks(pid, [{"path": "index.html", "content": "<h1>A</h1>\n"}], "A")
    mid = vp.list_checkpoints(pid)[0]["id"]
    vp.apply_blocks(pid, [{"path": "index.html", "content": "<h1>B</h1>\n"}], "B")
    vp.restore(pid, mid)                    # back to A; B is no longer applied
    vp.undo_last_ai(pid)                    # undoes A, not B
    assert _files(pid)["index.html"] == "<h1>one</h1>\n"


# ── diff ─────────────────────────────────────────────────────────────────

def test_diff_between_checkpoints_and_against_the_working_files():
    pid = vp.create_project("Diff", _two_files())["id"]
    vp.apply_blocks(pid, [{"path": "index.html", "content": "<h1>two</h1>\n"}, {"path": "n.txt", "content": "n\n"}], "x")
    cps = vp.list_checkpoints(pid)
    d = vp.diff(pid, cps[-1]["id"], cps[0]["id"])
    by = {f["path"]: f for f in d["files"]}
    assert by["n.txt"]["status"] == "added" and "+n" in by["n.txt"]["diff"]
    assert "-<h1>one</h1>" in by["index.html"]["diff"] and "+<h1>two</h1>" in by["index.html"]["diff"]
    (_pdir(pid) / "n.txt").write_text("changed by hand\n")
    w = vp.diff(pid, cps[0]["id"], "working")
    assert [f["path"] for f in w["files"]] == ["n.txt"]
    assert vp.diff(pid, cps[-1]["id"], cps[0]["id"], "style.css")["files"] == []


def test_diff_text_is_cut_when_it_is_huge():
    pid = vp.create_project("Huge")["id"]
    vp.apply_blocks(pid, [{"path": "big.txt", "content": "\n".join(f"line {i}" for i in range(20000))}], "big")
    cps = vp.list_checkpoints(pid)
    text = vp.diff(pid, cps[-1]["id"], cps[0]["id"])["files"][0]["diff"]
    assert len(text) <= vp.MAX_DIFF_CHARS + 40 and "diff cut" in text


# ── trash and switch ─────────────────────────────────────────────────────

def test_trash_moves_the_folder_and_removes_the_project_from_the_list(tmp_path):
    pid = vp.create_project("Bin", _two_files())["id"]
    assert [p["id"] for p in vp.list_projects()] == [pid]
    out = vp.trash_project(pid)
    assert [p["id"] for p in vp.list_projects()] == []
    moved = tmp_path / "trash"
    assert os.path.isdir(out["trash"]) and out["trash"].startswith(str(moved))
    assert (moved / os.path.basename(out["trash"]) / "index.html").read_text() == "<h1>one</h1>\n"
    with pytest.raises(vp.VibeError) as e:
        vp.project_info(pid)
    assert e.value.status == 404


def test_switched_off_routes_answer_404_and_nothing_is_created(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_PROJECTS_ENABLED", "false")
    client = _client()
    assert client.post("/api/vibe/projects", json={"title": "x"}).status_code == 404
    assert client.get("/api/vibe/projects").status_code == 404
    assert not (tmp_path / "vibe").exists()


# ── audit ────────────────────────────────────────────────────────────────

def test_audit_events_carry_ids_and_counts_only(monkeypatch):
    seen = []
    import codec_audit
    monkeypatch.setattr(codec_audit, "log_event", lambda *a, **k: seen.append((a, k)))
    pid = vp.create_project("Secret title", [{"path": "private-notes.txt", "content": "TOP SECRET BODY"}])["id"]
    vp.apply_blocks(pid, [{"path": "app-key-notes.txt", "content": "ANOTHER SECRET"}], "label with SECRET words")
    vp.undo_last_ai(pid)
    vp.trash_project(pid)
    names = [a[0] for a, _k in seen]
    assert {"vibe_checkpoint", "vibe_apply", "vibe_restore", "vibe_project_trash"} <= set(names)
    blob = repr(seen)
    for forbidden in ("SECRET", "private-notes", "app-key-notes", "Secret title", "label with"):
        assert forbidden not in blob, f"audit leaked {forbidden!r}"


# ── http api ─────────────────────────────────────────────────────────────

def _client():
    from routes.vibe_projects import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_api_round_trip():
    c = _client()
    made = c.post("/api/vibe/projects", json={"title": "Round trip", "files": _two_files()})
    assert made.status_code == 200, made.text
    pid = made.json()["id"]
    assert [f["path"] for f in made.json()["files"]] == ["index.html", "style.css"]
    assert c.get("/api/vibe/projects").json()[0]["id"] == pid

    assert c.get(f"/api/vibe/projects/{pid}/file", params={"path": "index.html"}).json()["content"] == "<h1>one</h1>\n"
    assert c.put(f"/api/vibe/projects/{pid}/file", json={"path": "index.html", "content": "<h1>hand</h1>\n"}).json()["size"] == 14

    applied = c.post(f"/api/vibe/projects/{pid}/apply",
                     json={"blocks": [{"path": "app.js", "content": "x()\n"}], "label": "add js"}).json()
    assert applied["checkpoint"]["kind"] == "ai" and applied["changed"][0]["path"] == "app.js"
    cps = c.get(f"/api/vibe/projects/{pid}/checkpoints").json()
    assert [x["kind"] for x in cps][:3] == ["ai", "before", "start"]
    d = c.get(f"/api/vibe/projects/{pid}/diff", params={"from": cps[1]["id"], "to": cps[0]["id"]}).json()
    assert d["files"][0]["path"] == "app.js"
    assert c.get(f"/api/vibe/projects/{pid}").json()["can_undo"] is True

    undone = c.post(f"/api/vibe/projects/{pid}/undo").json()
    assert undone["checkpoint"]["kind"] == "restore"
    assert c.get(f"/api/vibe/projects/{pid}/file", params={"path": "index.html"}).json()["content"] == "<h1>hand</h1>\n"
    assert c.get(f"/api/vibe/projects/{pid}/file", params={"path": "app.js"}).status_code == 404

    back = c.post(f"/api/vibe/projects/{pid}/restore", json={"checkpoint": cps[-1]["id"]})
    assert back.status_code == 200 and back.json()["restored_to"] == cps[-1]["id"]
    saved = c.post(f"/api/vibe/projects/{pid}/checkpoint", json={"label": "mine"}).json()
    assert saved == {"checkpoint": None}      # nothing changed since the restore
    assert c.delete(f"/api/vibe/projects/{pid}").json()["ok"] is True
    assert c.get(f"/api/vibe/projects/{pid}").status_code == 404


def test_api_rejects_bad_ids_bodies_and_paths():
    c = _client()
    pid = c.post("/api/vibe/projects", json={"title": "x"}).json()["id"]
    assert c.get("/api/vibe/projects/NOT_VALID!/file").status_code in (400, 404)
    assert c.get("/api/vibe/projects/..%2Fetc/file").status_code in (400, 404)
    assert c.get("/api/vibe/projects/abc-000000").status_code == 404
    assert c.post("/api/vibe/projects", content=b"not json", headers={"content-type": "application/json"}).status_code == 400
    assert c.post("/api/vibe/projects", json=[1, 2]).status_code == 400
    assert c.put(f"/api/vibe/projects/{pid}/file", json={"path": "../x", "content": "y"}).status_code == 400
    assert c.put(f"/api/vibe/projects/{pid}/file", json={"path": "ok.txt", "content": 5}).status_code == 400
    assert c.get(f"/api/vibe/projects/{pid}/file", params={"path": "missing.txt"}).status_code == 404
    assert c.post(f"/api/vibe/projects/{pid}/apply", json={"blocks": [{"path": ".git/config", "content": "x"}]}).status_code == 400
    assert c.post(f"/api/vibe/projects/{pid}/restore", json={"checkpoint": "zzz"}).status_code == 400
    assert c.post(f"/api/vibe/projects/{pid}/undo").status_code == 409
    assert c.get(f"/api/vibe/projects/{pid}/diff", params={"from": "c9999", "to": "working"}).status_code == 404
    big = c.put(f"/api/vibe/projects/{pid}/file", json={"path": "big.txt", "content": "x" * (vp.MAX_FILE_BYTES + 1)})
    assert big.status_code == 413
