"""Completion verifier for Google-Doc deliverables (#17).

A research Project once wrote a local .md and claimed "saved to Google Drive".
These tests pin the gate that refuses that false completion unless a real
docs.google.com/document/... URL was actually produced.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import codec_gdoc_verify as gv  # noqa: E402


def test_wants_gdoc_detects_drive_deliverables():
    for ok in ["save it to Google Drive", "produce a Google Doc", "export as a gdoc",
               "save the report as a Google Document", "put it in docs.google.com"]:
        assert gv.wants_gdoc(ok), ok
    for no in ["save to a local file", "write report.md", "print the summary",
               "save to ~/Downloads/notes.txt"]:
        assert not gv.wants_gdoc(no), no


def test_find_doc_url_in_history():
    hist = [
        {"result": "searched the web..."},
        {"result": "Saved. https://docs.google.com/document/d/1AbC_def-123/edit"},
    ]
    url = gv.find_doc_url(hist)
    # The extractor stops at the doc id (the trailing /edit isn't needed to
    # identify the doc); that's enough for verification.
    assert url == "https://docs.google.com/document/d/1AbC_def-123"
    assert gv.doc_id_from_url(url) == "1AbC_def-123"


def test_find_doc_url_none_when_only_local_file():
    hist = [{"result": "Saved /Users/x/Downloads/report.md (4,200 bytes)."}]
    assert gv.find_doc_url(hist) is None


def test_verify_passes_when_not_a_gdoc_deliverable():
    ok, _ = gv.verify_deliverable({"expected_output": "a local markdown file"}, [])
    assert ok is True


def test_verify_fails_when_gdoc_wanted_but_only_local_md():
    """The exact false-completion bug: expected a Drive doc, produced only a .md."""
    cp = {"expected_output": "the competitor report saved to Google Drive"}
    hist = [{"result": "Saved /Users/x/Downloads/competitors.md (5 KB)."}]
    ok, reason = gv.verify_deliverable(cp, hist)
    assert ok is False
    assert "google doc" in reason.lower() and "google_docs skill" in reason.lower()


def test_verify_passes_when_real_doc_url_present():
    cp = {"expected_output": "save the report to a Google Doc"}
    hist = [{"result": "Created https://docs.google.com/document/d/XYZ123/edit"}]
    ok, url = gv.verify_deliverable(cp, hist)
    assert ok is True and "docs.google.com" in url


def test_doc_exists_returns_none_without_creds(monkeypatch):
    """No OAuth → can't verify → None (never a hard False that wedges a real URL)."""
    import builtins
    real_import = builtins.__import__

    def boom(name, *a, **k):
        if name == "codec_google_auth":
            raise ImportError("no creds")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", boom)
    assert gv.doc_exists("https://docs.google.com/document/d/AAA/edit") is None
