"""Vibe checkpoints keep each line small in big folders (docs/VIBE-CHECKPOINT-SIZE-DESIGN.md, owner card S-06).

An attached folder's checkpoint line points at its file map ("files_ref", stored once in objects/) instead of holding
it; a created project's line holds the map as before. Every folder is under tmp_path: CODEC_VIBE_ROOT,
CODEC_VIBE_TRASH and CODEC_VIBE_ATTACH_ROOTS point there, so the real ~/codec-workspace, ~/.codec and the owner's
folders are never touched. The home folder is never faked.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path

import pytest

import codec_vibe_projects as vp

SITE = {"index.html": "<h1>hi</h1>\n", "src/app.js": "let a = 1;\n", "README.md": "# app\n"}
MISSING = "A stored file version is missing"


@pytest.fixture(autouse=True)
def _roots(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEC_VIBE_ROOT", str(tmp_path / "vibe"))
    monkeypatch.setenv("CODEC_VIBE_TRASH", str(tmp_path / "trash"))
    (tmp_path / "Projects").mkdir()
    monkeypatch.setenv("CODEC_VIBE_ATTACH_ROOTS", str(tmp_path / "Projects"))
    for name in ("VIBE_PROJECTS_ENABLED", "VIBE_ATTACH_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(vp, "_audit", lambda event, message, extra: None)
    vp._AGENT_RUNS.clear()


def folder(tmp_path, name, files) -> Path:
    d = tmp_path / "Projects" / name
    for rel, text in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return d


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def lines(rdir: Path) -> list[bytes]:
    return (rdir / ".vibe" / "checkpoints.jsonl").read_bytes().splitlines()


def disk(d: Path) -> dict:
    return {os.path.relpath(os.path.join(dp, n), d): Path(dp, n).read_text() for dp, _dn, fn in os.walk(d) for n in fn}


def norm(x):
    """Without the times, which differ between two runs of the same steps."""
    if isinstance(x, dict):
        return {k: norm(v) for k, v in x.items() if k != "ts"}
    if isinstance(x, list):
        return [norm(v) for v in x]
    return x


def to_old_lines(rdir: Path, upto: int | None = None) -> set[str]:
    """Rewrite the first `upto` lines (all when None) the way the code before this change wrote them: the map itself
    in place of files_ref, no files_count. Written here from the line, not with the module's helper."""
    path = rdir / ".vibe" / "checkpoints.jsonl"
    out, refs = [], set()
    for i, raw in enumerate(path.read_text().splitlines()):
        rec = json.loads(raw)
        if "files_ref" in rec and (upto is None or i < upto):
            refs.add(rec["files_ref"])
            files = json.loads((rdir / ".vibe" / "objects" / rec["files_ref"]).read_bytes())
            rec = {("files" if k == "files_ref" else k): (files if k == "files_ref" else v)
                   for k, v in rec.items() if k != "files_count"}
        out.append(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
    path.write_text("".join(out))
    return refs


# ── a 2,000-file attached folder ─────────────────────────────────────────

def test_two_thousand_file_folder_keeps_a_line_under_1_kb_and_listing_reads_no_map(tmp_path, monkeypatch):
    files = {f"src/feature_{i // 50:02d}/components/Widget{i:04d}Component.tsx": f"export const w{i} = {i};\n"
             for i in range(2000)}
    d = folder(tmp_path, "big", files)
    pid = vp.attach_folder(str(d))["id"]
    rdir = vp.record_dir(pid)
    first = "src/feature_00/components/Widget0000Component.tsx"
    vp.write_file(pid, first, "by hand\n")                                   # c0001: the folder before it
    vp.apply_blocks(pid, [{"path": first, "content": "by the AI\n"}], "AI")  # c0002 before, c0003 ai
    raw = lines(rdir)
    assert len(raw) == 3
    for line in raw:
        rec = json.loads(line)
        assert "files" not in rec and rec["files_count"] == 2000 and len(rec["files_ref"]) == 64
        listed = len(json.dumps(rec["changed"], ensure_ascii=False, separators=(",", ":")))
        assert len(line) - listed < 1024, "the line without its list of changed paths"
    assert all(len(line) < 1024 for line in raw[1:]), [len(x) for x in raw]       # one file changed in each
    assert len(json.loads(raw[0])["changed"]) == 40, "the first version names its first 40 files, as before"
    want = {rel: sha((d / rel).read_bytes()) for rel in files}               # measured from the folder itself
    newest = json.loads(raw[-1])
    stored = (rdir / ".vibe" / "objects" / newest["files_ref"]).read_bytes()
    assert sha(stored) == newest["files_ref"] and json.loads(stored) == want
    assert vp._files(rdir, newest) == want

    reads = []
    real_files, real_blob = vp._files, vp._blob
    monkeypatch.setattr(vp, "_files", lambda p, rec: reads.append("map") or real_files(p, rec))
    monkeypatch.setattr(vp, "_blob", lambda p, s: reads.append("object") or real_blob(p, s))
    vp._MAP_CACHE.clear()
    listed = vp.list_checkpoints(pid)
    info = vp.project_info(pid)
    vp.rewound_checkpoints(pid)
    vp.rewind_state(pid, "sess0001")
    assert reads == [], "listing the history reads no map object"
    assert [c["files_count"] for c in listed] == [2000, 2000, 2000] and not any("files_ref" in c for c in listed)
    assert info["checkpoint_count"] == 3 and info["last_checkpoint"]["files_count"] == 2000
    assert all(len(json.dumps(c)) < 1024 for c in listed[:2])         # newest first; the first names 40 paths


# ── a history that mixes lines from before and after this change ────────

def _build(tmp_path, name) -> tuple[str, Path, Path]:
    d = folder(tmp_path, name, SITE)
    pid = vp.attach_folder(str(d))["id"]
    vp.write_file(pid, "index.html", "<h1>by hand</h1>\n")                                       # c0001 start
    vp.apply_blocks(pid, [{"path": "src/app.js", "content": "let a = 2;\n"},
                          {"path": "src/b.js", "content": "let b;\n"}], "AI one")                  # c0002, c0003
    vp.write_file(pid, "notes.txt", "mine\n")
    vp.agent_checkpoint(pid, "Before step 1", "before", "r1", 1)                                  # c0004
    vp.agent_write(pid, "src/c.js", "let c;\n")
    vp.agent_checkpoint(pid, "Step 1", "ai", "r1", 1)                                             # c0005
    vp.apply_blocks(pid, [{"path": "index.html", "content": "<h1>AI two</h1>\n"}], "AI two")       # c0006
    return pid, d, vp.record_dir(pid)


def _exercise(pid: str, d: Path, rdir: Path) -> dict:
    out = {"list": norm(vp.list_checkpoints(pid)),
           "diff_1_6": vp.diff(pid, "c0001", "c0006"),
           "diff_3_working": vp.diff(pid, "c0003", "working"),
           "diff_none_2": vp.diff(pid, "", "c0002"),
           "changes": [norm(vp.checkpoint_changes(pid, f"c000{i}")) for i in range(1, 7)],
           "preview": vp.restore_preview(pid, "c0002")}
    fork = vp.fork_project(pid, "c0005")
    fdir = vp.project_dir(fork["id"])
    fork_lines = [json.loads(x) for x in lines(fdir)]
    assert all(isinstance(c.get("files"), dict) and "files_ref" not in c for c in fork_lines), "a copy is a created project"
    out["fork"] = {"files": {k: v for k, v in disk(fdir).items() if not k.startswith(".vibe/")},
                   "lines": norm(fork_lines), "count": fork["checkpoint_count"]}
    out["undo"] = norm(vp.undo_last_ai(pid))
    out["disk_undo"] = disk(d)
    out["ai_restore"] = norm(vp.restore(pid, "c0003", scope="ai"))
    out["disk_ai_restore"] = disk(d)
    rw = vp.rewind(pid, "c0002", "sess0001", [1, 2])
    out["rewind"] = norm(rw)
    out["disk_rewind"] = disk(d)
    out["unrewind"] = norm(vp.undo_rewind(pid, rw["checkpoint"]["id"], "sess0001"))
    out["disk_unrewind"] = disk(d)
    out["restore_all"] = norm(vp.restore(pid, "c0001"))
    out["disk_restore_all"] = disk(d)
    out["final"] = norm(vp.list_checkpoints(pid))
    out["final_changes"] = [norm(vp.checkpoint_changes(pid, c["id"])) for c in vp.list_checkpoints(pid)]
    return out


def test_mixed_history_gives_the_same_restore_undo_rewind_diff_and_copy(tmp_path):
    new_only = _build(tmp_path, "new")
    mixed = _build(tmp_path, "mixed")
    old_only = _build(tmp_path, "old")
    to_old_lines(mixed[2], upto=3)
    refs = to_old_lines(old_only[2])
    for ref in refs:            # an old line needs no map object: take them away
        os.unlink(old_only[2] / ".vibe" / "objects" / ref)
    vp._MAP_CACHE.clear()
    shapes = {k: ["files" if b'"files":{' in x else "ref" for x in lines(r)]
              for k, (_p, _d, r) in {"new": new_only, "mixed": mixed, "old": old_only}.items()}
    assert shapes == {"new": ["ref"] * 6, "mixed": ["files"] * 3 + ["ref"] * 3, "old": ["files"] * 6}

    results = {k: _exercise(*v) for k, v in {"new": new_only, "mixed": mixed, "old": old_only}.items()}
    assert results["new"] == results["mixed"] == results["old"]
    r = results["new"]
    assert r["undo"]["undid"] == "c0006" and r["disk_undo"]["index.html"] == "<h1>by hand</h1>\n"
    assert "src/c.js" not in r["disk_ai_restore"] and r["disk_rewind"]["src/app.js"] == "let a = 1;\n"
    assert r["disk_unrewind"] == r["disk_ai_restore"] and r["disk_restore_all"] == SITE
    assert [f["path"] for f in r["diff_1_6"]["files"]] == ["index.html", "notes.txt", "src/app.js", "src/b.js", "src/c.js"]
    assert r["preview"] == {"checkpoint": "c0002", "files": 5, "other": 1, "attached": True}
    assert r["fork"]["count"] == 5 and r["fork"]["files"] == {
        "index.html": "<h1>by hand</h1>\n", "notes.txt": "mine\n", "README.md": "# app\n", "src/app.js": "let a = 2;\n",
        "src/b.js": "let b;\n", "src/c.js": "let c;\n"}
    for _p, _d, rdir in (mixed, old_only):      # what came after the change is written the new way, in every history
        assert b'"files_ref"' in lines(rdir)[-1]


# ── a missing or damaged map object ─────────────────────────────────────

def test_missing_or_damaged_map_object_is_the_same_500_as_a_missing_file_version(tmp_path):
    d = folder(tmp_path, "app", SITE)
    pid = vp.attach_folder(str(d))["id"]
    rdir = vp.record_dir(pid)
    vp.write_file(pid, "index.html", "<h1>by hand</h1>\n")
    vp.apply_blocks(pid, [{"path": "src/app.js", "content": "let a = 2;\n"}], "AI")
    start = json.loads(lines(rdir)[0])
    obj = rdir / ".vibe" / "objects" / start["files_ref"]
    good = obj.read_bytes()
    assert vp.diff(pid, "c0001", "c0003")["files"]          # loaded once: in the cache now
    with pytest.raises(vp.VibeError) as e:
        vp._blob(rdir, "0" * 64)
    assert e.value.status == 500 and e.value.message == MISSING
    before = disk(d)
    count = len(vp.list_checkpoints(pid))

    def refused(*calls):
        for call in calls:
            with pytest.raises(vp.VibeError) as e:
                call()
            assert (e.value.status, e.value.message) == (500, MISSING)
        assert disk(d) == before and len(vp.list_checkpoints(pid)) == count, "nothing was changed or saved"

    damaged = [
        None,                                                     # missing (also after it was cached)
        good[:-2] + b"X}",                                        # one byte changed
        json.dumps({"index.html": "0" * 64}).encode(),            # another map under this name
    ]
    for content in damaged:
        if content is None:
            obj.unlink()
        else:
            obj.write_bytes(content)
        refused(lambda: vp.restore(pid, "c0001"), lambda: vp.diff(pid, "c0001", "working"),
                lambda: vp.checkpoint_changes(pid, "c0002"), lambda: vp.fork_project(pid, "c0001"),
                lambda: vp.restore_preview(pid, "c0001"))
        assert vp.list_checkpoints(pid)[-1]["files_count"] == 3, "listing needs no map"
    obj.write_bytes(good)
    assert vp.diff(pid, "c0001", "c0003")["files"], "the right bytes back: it works again"

    for bad in ([], {"index.html": "../../../etc/passwd"}, {"x": 1}):   # the right name, but not a file map
        data = json.dumps(bad).encode()
        (rdir / ".vibe" / "objects" / sha(data)).write_bytes(data)
        rec = dict(json.loads(lines(rdir)[-1]), id="c0099", seq=99, files_ref=sha(data))
        with open(rdir / ".vibe" / "checkpoints.jsonl", "a") as f:
            f.write(json.dumps(rec) + "\n")
        with pytest.raises(vp.VibeError) as e:
            vp.restore(pid, "c0099")
        assert (e.value.status, e.value.message) == (500, MISSING)
        assert disk(d) == before
        hist = (rdir / ".vibe" / "checkpoints.jsonl").read_text().splitlines()
        (rdir / ".vibe" / "checkpoints.jsonl").write_text("".join(x + "\n" for x in hist[:-1]))


# ── created projects: the map stays in the line ─────────────────────────

def test_created_project_lines_still_hold_the_map_and_no_map_object_is_stored():
    pid = vp.create_project("Made", [{"path": "index.html", "content": "<h1>one</h1>\n"}])["id"]
    vp.apply_blocks(pid, [{"path": "index.html", "content": "<h1>two</h1>\n"}, {"path": "src/a.js", "content": "a\n"}], "AI")
    vp.write_file(pid, "notes.txt", "mine\n")
    vp.undo_last_ai(pid)
    vp.restore(pid, "c0001")
    vp.checkpoint_now(pid, "Saved version")
    pdir = vp.project_dir(pid)
    shas = set()
    for line in lines(pdir):
        rec = json.loads(line)
        assert line.decode() == json.dumps(rec, ensure_ascii=False, separators=(",", ":"))
        assert list(rec)[:9] == ["id", "seq", "ts", "kind", "label", "parent", "files", "stat", "changed"]
        assert "files_ref" not in rec and "files_count" not in rec
        shas |= set(rec["files"].values())
    assert {p.name for p in (pdir / ".vibe" / "objects").iterdir()} == shas, "only file versions, no map object"


# ── the newest line is kept in memory, and follows a change made elsewhere ──

def test_newest_line_cache_skips_the_history_and_follows_an_outside_change(tmp_path, monkeypatch):
    d = folder(tmp_path, "app", SITE)
    pid = vp.attach_folder(str(d))["id"]
    rdir = vp.record_dir(pid)
    vp.write_file(pid, "index.html", "<h1>x</h1>\n")
    vp.checkpoint_now(pid, "Saved")
    parsed = []
    real = vp._load_checkpoints
    monkeypatch.setattr(vp, "_load_checkpoints", lambda p: parsed.append(p) or real(p))
    vp.agent_write(pid, "src/app.js", "let a = 3;\n")
    rec = vp.agent_checkpoint(pid, "Step", "ai", "r1", 1)
    assert parsed == [] and rec["parent"] == "c0002", "taking a checkpoint parsed no history"
    last = json.loads(lines(rdir)[-1])
    with open(rdir / ".vibe" / "checkpoints.jsonl", "a") as f:      # a line written elsewhere
        f.write(json.dumps(dict(last, id="c0050", seq=50)) + "\n")
    vp.write_file(pid, "index.html", "<h1>y</h1>\n")
    rec = vp.checkpoint_now(pid, "After")
    assert (rec["id"], rec["parent"]) == ("c0051", "c0050") and len(parsed) == 1

    made = vp.create_project("Made", [{"path": "a.txt", "content": "1\n"}])["id"]
    pdir = vp.project_dir(made)
    last = json.loads(lines(pdir)[-1])
    with open(pdir / ".vibe" / "checkpoints.jsonl", "a") as f:
        f.write(json.dumps(dict(last, id="c0007", seq=7), ensure_ascii=False, separators=(",", ":")) + "\n")
    vp.write_file(made, "a.txt", "2\n")
    rec = vp.checkpoint_now(made)
    assert (rec["id"], rec["parent"]) == ("c0008", "c0007")


# ── no checkpoint map is read except through _files() ───────────────────

ALLOWED_READERS = {"_files", "_load_checkpoints", "_public"}


def _files_reads(source: str) -> list[tuple[str, int]]:
    """(function, line) of every read of a "files" key: x["files"], x.get/pop/setdefault("files"), "files" in x."""
    tree = ast.parse(source)
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    def owner(node) -> str:
        while node in parents:
            node = parents[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return node.name
        return "<module>"

    def is_files(node) -> bool:
        return isinstance(node, ast.Constant) and node.value == "files"

    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load) and is_files(node.slice):
            hits.append((owner(node), node.lineno))
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and node.func.attr in ("get", "pop", "setdefault") and node.args and is_files(node.args[0])):
            hits.append((owner(node), node.lineno))
        elif isinstance(node, ast.Compare) and is_files(node.left) and isinstance(node.ops[0], (ast.In, ast.NotIn)):
            hits.append((owner(node), node.lineno))
    return sorted(hits, key=lambda h: h[1])


def test_no_direct_files_read_of_a_checkpoint_outside_the_readers():
    planted = ('def restore(c):\n    return c["files"]\n\ndef count(c):\n    return len(c.get("files", {}))\n\n'
               'def has(c):\n    return "files" in c\n\ndef write(c, m):\n    c["files"] = m\n    return {"files": m}\n')
    assert [name for name, _ in _files_reads(planted)] == ["restore", "count", "has"], "the detector finds reads"
    source = Path(vp.__file__).read_text(encoding="utf-8")
    hits = _files_reads(source)
    outside = [(name, line) for name, line in hits if name not in ALLOWED_READERS]
    assert outside == [], f"read a checkpoint's map through _files(): {outside}"
    assert {name for name, _ in hits} == ALLOWED_READERS
