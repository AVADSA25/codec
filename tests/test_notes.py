"""Notes (codec_notes.py, routes/notes.py, docs/NOTES-DESIGN.md): the owner's saved notes.

The store is tested on a temp folder (CODEC_NOTES_DIR); the routes through FastAPI's test client on an app
that holds only the notes router. The safety rules each have a test that fails when the rule is broken.
"""
from __future__ import annotations

import json
import os
import stat
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import codec_notes as notes
from routes.notes import router


@pytest.fixture
def folder(tmp_path, monkeypatch):
    d = tmp_path / "notes"
    monkeypatch.setenv("CODEC_NOTES_DIR", str(d))
    monkeypatch.delenv("NOTES_ENABLED", raising=False)
    return d


@pytest.fixture
def audits(monkeypatch):
    seen = []
    import codec_audit
    monkeypatch.setattr(codec_audit, "log_event", lambda event, source, message="", extra=None, **kw: seen.append(
        {"event": event, "source": source, "message": message, "extra": extra, **kw}))
    return seen


@pytest.fixture
def client(folder):
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


# ── the store ────────────────────────────────────────────────────────────────

def test_create_makes_a_private_file_and_a_title_from_the_first_line(folder):
    n = notes.create("## **Lunch menu**\n\n- sea bass\n- burrata", source="chat")
    assert notes.ID_RE.match(n["id"])
    assert n["title"] == "Lunch menu"
    assert n["source"] == "chat" and n["created"] == n["updated"]
    f = folder / f"{n['id']}.json"
    assert json.loads(f.read_text())["body"].startswith("## **Lunch menu**")
    assert mode(f) == 0o600
    assert mode(folder) == 0o700


def test_an_explicit_title_is_kept_and_tidied(folder):
    n = notes.create("body text", title="  Two\nlines   here ")
    assert n["title"] == "Two lines here"


def test_a_body_with_only_marks_gets_the_untitled_name(folder):
    assert notes.create("***\n---")["title"] == "Untitled"


@pytest.mark.parametrize("kwargs, text", [
    ({"body": "   \n "}, "needs some text"),
    ({"body": ""}, "needs some text"),
    ({"body": 5}, "must be text"),
    ({"body": "x" * (notes.MAX_BODY + 1)}, "100,000 characters"),
    ({"body": "ok", "title": "t" * (notes.MAX_TITLE + 1)}, "120 characters"),
    ({"body": "ok", "title": 7}, "must be text"),
    ({"body": "ok", "source": "flash"}, "not known"),
])
def test_bad_input_is_refused_with_a_plain_sentence(folder, kwargs, text):
    with pytest.raises(notes.NoteError) as e:
        notes.create(**kwargs)
    assert e.value.status == 400 and text in e.value.message
    assert not folder.exists() or not list(folder.glob("*.json"))


def test_the_text_may_be_exactly_at_the_limit(folder):
    assert notes.create("x" * notes.MAX_BODY)["id"]


def test_the_number_of_notes_is_capped(folder, monkeypatch):
    monkeypatch.setattr(notes, "MAX_NOTES", 3)
    for i in range(3):
        notes.create(f"note {i}")
    with pytest.raises(notes.NoteError) as e:
        notes.create("one too many")
    assert e.value.status == 409 and "full" in e.value.message


def test_line_endings_and_null_bytes_are_cleaned(folder):
    n = notes.create("a\r\nb\rc\x00d")
    assert n["body"] == "a\nb\ncd"


def test_text_in_other_scripts_and_emoji_survives_the_round_trip(folder):
    text = "Café déjà vu\n日本語のメモ\nرسالة\n🙂 ok"
    assert notes.get(notes.create(text)["id"])["body"] == text


def test_update_changes_the_text_and_moves_the_note_to_the_top(folder):
    a = notes.create("first")
    b = notes.create("second")
    assert [n["id"] for n in notes.list_notes()["notes"]] == [b["id"], a["id"]]
    changed = notes.update(a["id"], body="first, edited")
    assert changed["body"] == "first, edited" and changed["updated"] > a["updated"]
    assert changed["created"] == a["created"]
    assert [n["id"] for n in notes.list_notes()["notes"]] == [a["id"], b["id"]]


def test_a_save_with_nothing_changed_does_not_move_the_note(folder, audits):
    a = notes.create("same")
    notes.create("other")
    again = notes.update(a["id"], title=a["title"], body="same")
    assert again["updated"] == a["updated"]
    assert [e["event"] for e in audits] == ["note_saved", "note_saved"]


def test_an_empty_title_asks_for_the_automatic_one(folder):
    a = notes.create("old words", title="Custom")
    b = notes.update(a["id"], title="", body="New first line\nmore")
    assert b["title"] == "New first line"


def test_update_with_nothing_to_change_is_refused(folder):
    a = notes.create("x")
    with pytest.raises(notes.NoteError) as e:
        notes.update(a["id"])
    assert e.value.status == 400


def test_search_needs_every_word_in_any_case_in_title_or_text(folder):
    notes.create("Grilled sea bass with fennel", title="Lunch")
    notes.create("Staffing plan for the weekend")
    notes.create("Fennel salad recipe", title="Salad")
    ids = lambda q: [n["title"] for n in notes.list_notes(q)["notes"]]  # noqa: E731
    assert sorted(ids("FENNEL")) == ["Lunch", "Salad"]
    assert ids("fennel bass") == ["Lunch"]
    assert ids("salad") == ["Salad"]
    assert ids("lunch") == ["Lunch"]          # a word from the title only
    assert ids("fennel weekend") == []
    r = notes.list_notes("fennel")
    assert r["total"] == 3 and r["matched"] == 2


def test_the_list_gives_a_short_preview_and_the_limit_is_clamped(folder):
    for i in range(5):
        notes.create(f"line one\n\n   spaced    out {i} " + "w" * 300)
    r = notes.list_notes(limit=2)
    assert len(r["notes"]) == 2 and r["matched"] == 5
    assert len(r["notes"][0]["preview"]) == notes.PREVIEW and "\n" not in r["notes"][0]["preview"]
    assert "body" not in r["notes"][0]
    assert len(notes.list_notes(limit=0)["notes"]) == 1 and len(notes.list_notes(limit=10_000)["notes"]) == 5


@pytest.mark.parametrize("bad", ["../x", "..", "A1B2C3D4E5F6", "abc", "a1b2c3d4e5f6/../..", "a1b2c3d4e5f6.json", "", None, 12, "g1b2c3d4e5f6"])
def test_an_id_that_is_not_twelve_hex_digits_never_touches_the_disk(folder, bad):
    outside = folder.parent / "x.json"
    outside.write_text("{}")
    for call in (lambda: notes.get(bad), lambda: notes.update(bad, body="x"), lambda: notes.trash(bad)):
        with pytest.raises(notes.NoteError) as e:
            call()
        assert e.value.status == 404
    assert outside.read_text() == "{}"


def test_trash_moves_the_file_and_nothing_is_erased(folder):
    a = notes.create("keep me in the trash")
    notes.trash(a["id"])
    assert not (folder / f"{a['id']}.json").exists()
    kept = list((folder / ".trash").glob(f"{a['id']}-*.json"))
    assert len(kept) == 1 and mode(kept[0]) == 0o600
    assert json.loads(kept[0].read_text())["body"] == "keep me in the trash"
    assert notes.list_notes()["total"] == 0
    for call in (lambda: notes.get(a["id"]), lambda: notes.trash(a["id"])):
        with pytest.raises(notes.NoteError) as e:
            call()
        assert e.value.status == 404


def test_a_damaged_file_is_skipped_not_fatal(folder):
    good = notes.create("fine")
    (folder / "aaaaaaaaaaaa.json").write_text("{not json")
    (folder / "bbbbbbbbbbbb.json").write_text(json.dumps({"id": "cccccccccccc", "title": "t", "body": "b",
                                                          "created": "x", "updated": "y"}))
    (folder / "readme.json").write_text("{}")
    assert [n["id"] for n in notes.list_notes()["notes"]] == [good["id"]]


def test_twenty_saves_at_once_make_twenty_whole_notes(folder):
    ids, errors = [], []

    def save(i):
        try:
            ids.append(notes.create(f"note number {i}")["id"])
        except Exception as e:      # pragma: no cover - the assert below reports it
            errors.append(e)

    threads = [threading.Thread(target=save, args=(i,)) for i in range(20)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors and len(set(ids)) == 20
    assert notes.list_notes()["total"] == 20


def test_the_audit_log_gets_ids_sizes_and_the_source_never_text(folder, audits):
    secret_title, secret_body = "Bank PIN idea", "the code is 4711 and the title is secret"
    n = notes.create(secret_body, title=secret_title, source="chat")
    notes.update(n["id"], body=secret_body + " more")
    notes.trash(n["id"])
    assert [e["event"] for e in audits] == ["note_saved", "note_updated", "note_deleted"]
    for e in audits:
        assert e["source"] == "codec-notes"
        assert set(e["extra"]) == {"id", "source", "chars"} and e["extra"]["id"] == n["id"]
        flat = json.dumps(e)
        assert "4711" not in flat and "secret" not in flat and "Bank" not in flat


def test_enabled_follows_the_kill_switch(monkeypatch):
    monkeypatch.delenv("NOTES_ENABLED", raising=False)
    assert notes.enabled()
    for off in ("false", "0", "no", "OFF"):
        monkeypatch.setenv("NOTES_ENABLED", off)
        assert not notes.enabled()
    monkeypatch.setenv("NOTES_ENABLED", "true")
    assert notes.enabled()


def test_the_default_folder_is_under_the_codec_home(monkeypatch):
    monkeypatch.delenv("CODEC_NOTES_DIR", raising=False)
    assert str(notes.root()).endswith(os.path.join(".codec", "notes"))


# ── the routes ───────────────────────────────────────────────────────────────

def test_the_whole_cycle_over_http(client, folder):
    r = client.post("/api/notes", json={"body": "# Plan\n\nBuy fennel", "source": "chat"})
    assert r.status_code == 200
    note = r.json()
    assert note["title"] == "Plan" and note["source"] == "chat"

    assert client.get("/api/notes", params={"q": "fennel"}).json()["matched"] == 1
    assert client.get("/api/notes", params={"q": "nothing like this"}).json()["notes"] == []
    assert client.get(f"/api/notes/{note['id']}").json()["body"] == "# Plan\n\nBuy fennel"

    r = client.put(f"/api/notes/{note['id']}", json={"title": "Shopping", "body": "Buy fennel and lemons"})
    assert r.status_code == 200 and r.json()["title"] == "Shopping"
    assert client.get("/api/notes").json()["notes"][0]["title"] == "Shopping"

    assert client.delete(f"/api/notes/{note['id']}").json() == {"ok": True}
    assert client.get(f"/api/notes/{note['id']}").status_code == 404
    assert client.get("/api/notes").json()["total"] == 0


def test_the_kill_switch_answers_404_everywhere(client, monkeypatch):
    note = client.post("/api/notes", json={"body": "before"}).json()
    monkeypatch.setenv("NOTES_ENABLED", "false")
    calls = [client.get("/api/notes"), client.post("/api/notes", json={"body": "x"}),
             client.get(f"/api/notes/{note['id']}"), client.put(f"/api/notes/{note['id']}", json={"body": "y"}),
             client.delete(f"/api/notes/{note['id']}")]
    assert [c.status_code for c in calls] == [404] * 5
    monkeypatch.delenv("NOTES_ENABLED")
    assert client.get(f"/api/notes/{note['id']}").json()["body"] == "before"      # nothing was changed


def test_bad_requests_get_a_plain_error(client):
    assert client.post("/api/notes", content=b"not json", headers={"content-type": "application/json"}).status_code == 400
    assert client.post("/api/notes", json=["a list"]).status_code == 400
    r = client.post("/api/notes", json={"body": "   "})
    assert r.status_code == 400 and "needs some text" in r.json()["error"]
    assert client.put("/api/notes/aaaaaaaaaaaa", json={}).status_code == 400
    assert client.put("/api/notes/aaaaaaaaaaaa", json={"body": "x"}).status_code == 404


def test_a_huge_request_is_stopped_before_it_is_parsed(client, folder):
    big = json.dumps({"body": "x" * 2_000_000})
    r = client.post("/api/notes", content=big, headers={"content-type": "application/json"})
    assert r.status_code == 413 and "too much" in r.json()["error"]
    assert not list(folder.glob("*.json")) if folder.exists() else True


def test_a_path_like_id_in_the_url_finds_nothing(client, folder):
    secret = folder.parent / "secret.json"
    folder.mkdir(parents=True, exist_ok=True)
    secret.write_text('{"id":"secret","title":"t","body":"b","created":"c","updated":"u"}')
    for path in ("/api/notes/..%2fsecret", "/api/notes/%2e%2e%2fsecret", "/api/notes/secret", "/api/notes/..%5csecret"):
        assert client.get(path).status_code == 404
        assert client.delete(path).status_code == 404
    assert secret.exists()


def test_the_routes_are_not_open_to_other_methods(client):
    assert client.patch("/api/notes/aaaaaaaaaaaa", json={"body": "x"}).status_code == 405
    assert client.delete("/api/notes").status_code == 405
