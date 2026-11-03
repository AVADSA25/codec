"""Memory recall on normal sentences (P-062, docs/MEMORY-RECALL-DESIGN.md).

CodecMemory.search quotes every word, leaves common words out of the content-word passes,
OR-joins content words, and searches with LIKE when the FTS index fails. Every database here is
a fixture of made-up rows in tmp_path; the owner's memory.db is never opened.
"""
import logging
import os
import sqlite3
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from codec_memory import CodecMemory, _fts_phrase, _query_words, _sanitize_fts_query  # noqa: E402

ROWS = [
    ("s-weather", "user", "Checked the weather for Saturday: sunny and 24 degrees"),
    ("s-meeting", "assistant", "What time is the team meeting tomorrow? It starts at ten."),
    ("s-hotel", "user", "The hotel invoice for the conference was paid on Monday"),
    ("s-lobby", "assistant", "Sent the lobby photos to the design agency"),
    ("s-file", "user", "Renamed file_name in the export script"),
    ("s-filex", "user", "The fileXname typo is still there"),
]


def _db(tmp_path, rows=ROWS, name="recall.db"):
    mem = CodecMemory(db_path=str(tmp_path / name))
    ids = [mem.save(*row) for row in rows]
    return SimpleNamespace(mem=mem, ids=ids, by_session={r[0]: i for r, i in zip(rows, ids)})


@pytest.fixture
def db(tmp_path):
    d = _db(tmp_path)
    yield d
    d.mem.close()


def _plain_fts(mem, query, limit=10):
    """What search() did before P-062: the sanitized text straight into MATCH, [] on an error."""
    try:
        return mem._fts_query(mem._get_conn(), _sanitize_fts_query(query), limit)
    except sqlite3.OperationalError:
        return []


def _drop_fts(mem, *tables):
    with sqlite3.connect(mem.db_path) as c:
        for table in tables or ("conversations_fts",):
            c.execute(f"DROP TABLE {table}")


def _ids(rows):
    return [r["id"] for r in rows]


@pytest.mark.parametrize("query,session", [
    ("what's the weather", "s-weather"),
    ("weather?", "s-weather"),
    ("invoice, hotel", "s-hotel"),
    ("did the hotel get paid yesterday", "s-hotel"),
])
def test_sentences_find_the_row_plain_fts_missed(db, query, session):
    assert _plain_fts(db.mem, query) == []
    hits = db.mem.search(query)
    assert hits and hits[0]["id"] == db.by_session[session]
    assert len(_ids(hits)) == len(set(_ids(hits)))


def test_demo_question_recalls_the_hotel(db):
    question = "What did we discuss about the hotel"
    assert _plain_fts(db.mem, question) == []
    assert _ids(db.mem.search(question)) == [db.by_session["s-hotel"]]
    ctx = db.mem.get_context(question, n=5)
    assert ctx.startswith("[Memory context]") and "hotel invoice" in ctx


def test_common_words_alone_do_not_pull_rows(db):
    # "What" and "the" are in the meeting row, "the" in the lobby row: neither may come back.
    assert _ids(db.mem.search("what's the weather")) == [db.by_session["s-weather"]]
    # Positive control: the meeting row is found by its own words.
    assert db.by_session["s-meeting"] in _ids(db.mem.search("team meeting"))
    # A query made only of common words still runs the old every-word pass, unchanged.
    only_common = "what is the"
    assert _ids(db.mem.search(only_common)) == _ids(_plain_fts(db.mem, only_common))
    assert _ids(db.mem.search(only_common)) == [db.by_session["s-meeting"]]


# The texts of tests/test_memory.py, plus look-alike rows that share some of their words.
_OLD_TEXTS = [
    "trigger test alpha beta gamma", "alpha release notes for beta testers",
    "gamma rays and alpha particles", "deleteme unique phrase qwerty",
    "a unique phrase about something else", "I like python, python is great, python forever",
    "java is also a language", "python and java in one sentence",
    "remember the capital of France is Paris", "Paris trip photos", "important meeting notes",
    "meeting room booked for the notes review", "context test message about rockets",
]
_PHRASES = [
    "alpha beta gamma", "deleteme unique phrase qwerty", "unique phrase", "python java", "python",
    "capital of France is Paris", "important meeting notes", "meeting notes", "rockets",
]


def test_phrase_queries_keep_their_old_order(tmp_path):
    d = _db(tmp_path, [("s", "user", t) for t in _OLD_TEXTS])
    for phrase in _PHRASES:
        old = _plain_fts(d.mem, phrase)
        new = d.mem.search(phrase)
        assert old, phrase
        assert [(r["id"], r["score"]) for r in new[:len(old)]] == [(r["id"], r["score"]) for r in old], phrase
    d.mem.close()


def test_rows_with_every_word_come_first(tmp_path):
    texts = ["hotel hotel hotel hotel", "hotel lobby", "the lobby was busy at noon",
             "new lobby chairs arrived", "lobby lights need fixing", "met in the lobby",
             "groceries for the weekend", "call the plumber", "book the dentist",
             "pay the electricity bill", "renew the passport", "water the plants"]
    d = _db(tmp_path, [("s", "user", t) for t in texts])
    repeated, both = d.ids[0], d.ids[1]
    # Control: plain BM25 over "hotel OR lobby" puts the row repeating one word first.
    pure_or = d.mem._fts_query(d.mem._get_conn(), 'content:"hotel" OR content:"lobby"', 10)
    assert _ids(pure_or)[:2] == [repeated, both]
    hits = d.mem.search("hotel lobby?")
    assert _ids(hits)[:2] == [both, repeated]
    assert len(hits) == 6 and len(set(_ids(hits))) == 6
    assert _ids(d.mem.search("hotel lobby?", limit=1)) == [both]
    assert d.mem.search("hotel lobby?", limit=0) == []
    d.mem.close()


def test_punctuation_and_operator_words_are_plain_text(db, monkeypatch):
    def no_fallback(*a, **k):
        raise AssertionError("the LIKE fallback ran: FTS5 read part of the query as syntax")
    monkeypatch.setattr(CodecMemory, "_like_query", no_fallback)
    for q in ['hotel" OR "1"="1', "AN*D hotel", "role:user hotel", "{content}: hotel", "hotel -lobby",
              "NEAR(hotel invoice)", "invoice^2 hotel", "follow-up 3.7.0 hotel", "it's 50% paid; ok? hotel",
              "back\\slash hotel", "hotel + invoice", "日本語 hotel"]:
        assert db.by_session["s-hotel"] in _ids(db.mem.search(q)), q
    assert db.mem.search("?! ... --") == []
    assert _fts_phrase('say "hi"') == '"say ""hi"""'
    assert _fts_phrase("hotel", "content") == 'content:"hotel"'


def test_broken_fts_index_falls_back_to_like(db, caplog):
    _drop_fts(db.mem)
    with caplog.at_level(logging.WARNING, logger="codec_memory"):
        hits = db.mem.search("did the hotel get paid yesterday")
    assert hits[0]["id"] == db.by_session["s-hotel"] and hits[0]["score"] == -2.0
    assert "full-text index failed" in caplog.text and "hotel" not in caplog.text
    assert _ids(db.mem.search("what's the weather")) == [db.by_session["s-weather"]]
    # LIKE wildcards in a word are escaped: file_name does not match fileXname.
    assert _ids(db.mem.search("file_name")) == [db.by_session["s-file"]]
    for r in hits:
        assert set(r) == {"id", "session_id", "timestamp", "role", "content", "score"}


def test_fallback_failure_returns_empty(db):
    _drop_fts(db.mem, "conversations_fts", "conversations")
    assert db.mem.search("did the hotel get paid yesterday") == []


def test_user_id_respected_in_every_pass(tmp_path):
    mem = CodecMemory(db_path=str(tmp_path / "users.db"))
    alice = mem.save("s-a", "user", "The hotel invoice was paid on Monday", user_id="alice")
    alice_2 = mem.save("s-a", "user", "Booked the hotel shuttle", user_id="alice")
    mem.save("s-b", "user", "The hotel invoice was paid on Friday", user_id="bob")
    mem.save("s-b", "user", "Hotel shuttle for bob", user_id="bob")
    question = "did the hotel get paid yesterday"
    assert _ids(mem.search(question, user_id="alice")) == [alice, alice_2]
    assert len(mem.search(question)) == 4
    _drop_fts(mem)
    assert _ids(mem.search(question, user_id="alice")) == [alice, alice_2]
    mem.close()


def test_sanitizer_output_is_pinned():
    assert _sanitize_fts_query("test AND drop") == "test drop"
    assert _sanitize_fts_query('test" OR "1"="1') == "test 1=1"
    assert _sanitize_fts_query("what's the weather?") == "what's the weather?"
    assert _sanitize_fts_query('NEAR(a b) *c^ "d"') == "a b c d"
    assert len(_sanitize_fts_query("x" * 300)) == 200


def test_query_words_split():
    assert _query_words("What's the weather, today?") == (["What's", "the", "weather", "today"], ["weather"])
    assert _query_words("follow-up on 3.7.0 for the hotel's invoice") == (
        ["follow-up", "on", "3.7.0", "for", "the", "hotel's", "invoice"],
        ["follow-up", "3.7.0", "hotel", "invoice"])
    assert _query_words("Hotel hotel HOTEL") == (["Hotel"], ["Hotel"])
    assert _query_words("what\u2019s up __init__ ___") == (["what\u2019s", "up", "init"], ["init"])
    assert _query_words("?! ...") == ([], [])
