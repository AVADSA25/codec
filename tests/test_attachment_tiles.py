"""Thumbnails for attachments and a lightbox (UI phase 2, P2.12; docs/P2.12-DESIGN.md).

A sent message keeps its attachment tiles (name, kind, size, a small JPEG
thumbnail, what the vision model saw) in an additive qchat column; chat and
generated images open in the #screenModal lightbox with a Download button.
"""
from __future__ import annotations

import json
import sqlite3
import stat
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.qchat as qchat

REPO = Path(__file__).resolve().parent.parent
PAGE = (REPO / "codec_chat.html").read_text(encoding="utf-8")
THUMB = "data:image/jpeg;base64," + "QUJD" * 40


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "qchat.db"
    monkeypatch.setattr(qchat, "QCHAT_DB", str(path))
    monkeypatch.setattr(qchat, "_qchat_conn", None)
    yield path
    if qchat._qchat_conn is not None:
        qchat._qchat_conn.close()
    monkeypatch.setattr(qchat, "_qchat_conn", None)


@pytest.fixture
def client(db):
    app = FastAPI()
    app.include_router(qchat.router)
    return TestClient(app)


def _save(client, atts, content="look at this"):
    return client.post("/api/qchat/save", json={"session_id": "s1", "title": "Garden",
                                                "messages": [{"role": "user", "content": content, "attachments": atts},
                                                             {"role": "assistant", "content": "A tidy garden."}]})


def test_tiles_round_trip(client):
    atts = [{"name": "roses.jpg", "kind": "image", "size": 204800, "thumb": THUMB, "saw": "Red roses by a fence."},
            {"name": "plan.pdf", "kind": "pdf", "size": 52000}]
    assert _save(client, atts).status_code == 200
    msgs = client.get("/api/qchat/session/s1").json()
    assert msgs[0]["attachments"] == atts
    assert "attachments" not in msgs[1], "a message without tiles has none"


def test_tiles_are_sanitised(client):
    atts = [{"name": "a.png", "kind": "image", "thumb": "data:image/png;base64,QUJD"},
            {"name": "b.svg", "kind": "image", "thumb": "data:image/svg+xml;base64,PHN2Zz4="},
            {"name": "c.jpg", "kind": "image", "thumb": "data:image/jpeg;base64,QUJD\"><script>"},
            {"name": "d.jpg", "kind": "image", "thumb": "data:image/jpeg;base64," + "A" * 20000},
            {"name": "e.txt", "kind": "text", "content": "the whole file", "thumb": THUMB, "saw": "x"},
            {"name": "f.bin", "kind": "weird", "size": "lots"},
            {"name": "", "kind": "image"},
            {"name": "g.jpg", "kind": "image", "saw": "y" * 9000},
            "not a dict"] + [{"name": f"h{i}.txt", "kind": "text"} for i in range(10)]
    _save(client, atts)
    got = client.get("/api/qchat/session/s1").json()[0]["attachments"]
    assert len(got) == qchat.ATTACH_MAX
    by = {a["name"]: a for a in got}
    for n in ("a.png", "b.svg", "c.jpg", "d.jpg"):
        assert "thumb" not in by[n], n
    assert by["e.txt"] == {"name": "e.txt", "kind": "text"}, "no file text, no thumbnail on a non-image"
    assert by["f.bin"] == {"name": "f.bin", "kind": "file"}
    assert len(by["g.jpg"]["saw"]) == qchat.SAW_MAX_CHARS
    assert "" not in by


def test_backup_before_the_column_on_a_database_with_rows(tmp_path, monkeypatch):
    path = tmp_path / "qchat.db"
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE qchat_sessions (id TEXT PRIMARY KEY, title TEXT, created_at TEXT, updated_at TEXT, "
              "user_id TEXT DEFAULT 'default', pinned INTEGER DEFAULT 0, archived INTEGER DEFAULT 0)")
    c.execute("CREATE TABLE qchat_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, "
              "content TEXT, timestamp TEXT, user_id TEXT DEFAULT 'default', superseded_at TEXT)")
    c.execute("CREATE TABLE qchat_feedback (id INTEGER PRIMARY KEY)")
    c.execute("INSERT INTO qchat_messages (session_id, role, content) VALUES ('s9', 'user', 'Water the roses')")
    c.commit()
    c.close()
    monkeypatch.setattr(qchat, "QCHAT_DB", str(path))
    monkeypatch.setattr(qchat, "_qchat_conn", None)
    try:
        conn = qchat.qchat_db()
        bak = Path(str(path) + ".bak-p2.12")
        assert bak.exists() and stat.S_IMODE(bak.stat().st_mode) == 0o600
        b = sqlite3.connect(bak)
        assert "attachments" not in {r[1] for r in b.execute("PRAGMA table_info(qchat_messages)")}
        b.close()
        assert "attachments" in {r[1] for r in conn.execute("PRAGMA table_info(qchat_messages)")}
        assert conn.execute("SELECT content FROM qchat_messages").fetchone()[0] == "Water the roses"
    finally:
        qchat._qchat_conn.close()
        monkeypatch.setattr(qchat, "_qchat_conn", None)


def test_no_backup_for_an_empty_database(db):
    qchat.qchat_db()
    assert not Path(str(db) + ".bak-p2.12").exists()


def test_exports_list_the_names_without_thumbnails(client):
    _save(client, [{"name": "roses.jpg", "kind": "image", "thumb": THUMB}])
    md = client.get("/api/qchat/session/s1/export?format=md").text
    assert "_Attached: roses.jpg_" in md and "base64" not in md
    js = json.loads(client.get("/api/qchat/session/s1/export?format=json").text)
    assert js["messages"][0]["attachments"] == [{"name": "roses.jpg", "kind": "image"}]


# ── The page ───────────────────────────────────────────────────────────────

def _block(start, end):
    i = PAGE.index(start)
    return PAGE[i:PAGE.index(end, i)]


def test_tiles_in_the_composer():
    tiles = _block("// ── Attachment tiles (P2.12", "function removeChip(")
    assert "112/Math.max(img.width,img.height,1)" in tiles and "toDataURL('image/jpeg',0.7)" in tiles
    assert 'class="fc-ring" role="progressbar"' in tiles and "_fmtSize(" in tiles
    assert ".fc-thumb{width:56px;height:56px" in PAGE
    assert "@media (prefers-reduced-motion: reduce){.fc-ring{animation:none" in PAGE
    up = _block("async function handleUpload(e){", "e.target.value=''")
    assert up.count("_loadingChip(") == 2 and "_thumbOf(theFile)" in up
    assert "(analyzing...)" not in up and "(extracting...)" not in up


def test_sent_and_loaded_messages_keep_their_tiles():
    assert "var atts=_attTiles(pendingFiles);" in PAGE
    assert "saveMessages([{role:'user',content:displayText,attachments:atts}]);" in PAGE
    assert "addMessage('user',displayText,undefined,null,false,atts);" in PAGE
    assert "if(role==='user')_renderAtts(div,atts);" in PAGE
    assert "m.timestamp,false,m.attachments);" in PAGE and "_savedRows.push(_rowCopy(m))" in PAGE
    rc = _block("function _rowCopy(m){", "\n}\n")
    assert "k!=='full'" in rc, "the in-page 1280px image is never saved"
    tiles = _block("function _attTiles(files){", "function _renderAtts(")
    assert "f.type!=='imgref'" in tiles and "content" not in tiles.replace("f.content||''", ""), "no file text in tiles"


def test_what_codec_saw_is_folded_away():
    r = _block("function _renderAtts(div,atts){", "\n}\n")
    assert "sm.textContent='What CODEC saw'" in r and "createElement('details')" in r
    assert "p.textContent=" in r and "innerHTML" not in r.replace("insertAdjacentHTML('beforeend',a.kind==='image'?_ICO_IMG:_ICO_DOC)", "")
    assert "[IMAGE ANALYSIS of '+f.name+']" in PAGE, "the model still gets the description"


def test_the_lightbox():
    lb = _block("var _lightboxFrom = null;", "</script>")
    assert "dl.hidden = false;" in lb and "_lightboxFrom = from || document.activeElement;" in lb
    assert 'id="screenDl"' in PAGE and 'role="dialog" aria-modal="true"' in PAGE
    assert "openLightbox(a.full||a.thumb,a.name,t)" in PAGE
    assert "openLightbox(u,'codec-image-'+id+'.png',a)" in PAGE and "a.target='_blank'" not in PAGE
    assert "openLightbox('data:image/jpeg;base64,' + d.image" in PAGE
    pat = lb[lb.index("if (!/"):lb.index(".test(src)) return;")]
    assert "api\\/image\\/file" in pat and "data:image\\/(jpeg|png)" in pat
