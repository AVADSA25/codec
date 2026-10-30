"""PDF text without poppler or PyMuPDF (audit P-033)."""
import base64
import importlib.util
import subprocess
import sys

import pytest

import codec_pdftext

# A one-page PDF with the text "Hello PDFKit". Hand-written; PDFKit rebuilds the xref.
PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
       b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
       b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 200]/Contents 4 0 R"
       b"/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
       b"4 0 obj<</Length 44>>stream\nBT /F1 18 Tf 20 100 Td (Hello PDFKit) Tj ET\nendstream\nendobj\n"
       b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
       b"trailer<</Root 1 0 R/Size 6>>\n%%EOF\n")

mac_only = pytest.mark.skipif(
    sys.platform != "darwin" or importlib.util.find_spec("Quartz") is None,
    reason="PDFKit is part of macOS and needs pyobjc-framework-quartz")


def _no_poppler(monkeypatch):
    monkeypatch.setattr(codec_pdftext.shutil, "which", lambda name: None)


def test_poppler_stays_first_when_installed(monkeypatch):
    monkeypatch.setattr(codec_pdftext.shutil, "which", lambda name: "/opt/homebrew/bin/pdftotext")
    seen = {}

    def run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="from poppler", stderr="")
    monkeypatch.setattr(subprocess, "run", run)
    assert codec_pdftext.extract_text("/t/a.pdf") == "from poppler"
    assert seen["cmd"] == ["pdftotext", "-layout", "/t/a.pdf", "-"]


@mac_only
def test_pdfkit_reads_the_text_without_poppler(monkeypatch, tmp_path):
    _no_poppler(monkeypatch)
    p = tmp_path / "a.pdf"
    p.write_bytes(PDF)
    assert "Hello PDFKit" in codec_pdftext.extract_text(str(p))
    assert "Hello PDFKit" in codec_pdftext.extract_text(str(p), max_pages=1)


@mac_only
def test_a_file_that_is_not_a_pdf_is_a_clear_error(monkeypatch, tmp_path):
    _no_poppler(monkeypatch)
    p = tmp_path / "bad.pdf"
    p.write_bytes(b"this is not a pdf")
    with pytest.raises(codec_pdftext.PdfTextError, match="not a readable PDF"):
        codec_pdftext.extract_text(str(p))


def test_without_pdfkit_or_poppler_the_error_names_both(monkeypatch, tmp_path):
    _no_poppler(monkeypatch)
    monkeypatch.setitem(sys.modules, "Quartz", None)
    with pytest.raises(codec_pdftext.PdfTextError, match="pdftotext"):
        codec_pdftext.extract_text(str(tmp_path / "x.pdf"))


# ── the upload route ────────────────────────────────────────────────────────
@pytest.fixture
def client(monkeypatch, tmp_path):
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from routes import upload
    monkeypatch.setattr(upload, "_pdf_upload_path", lambda: str(tmp_path / "pwa_upload.pdf"))
    app = FastAPI()
    app.include_router(upload.router)
    return TestClient(app)


def _post(client, data):
    return client.post("/api/upload", json={"filename": "doc.pdf", "data": base64.b64encode(data).decode()})


def test_route_without_any_extractor_answers_422_not_500(client, monkeypatch):
    _no_poppler(monkeypatch)
    monkeypatch.setitem(sys.modules, "Quartz", None)
    r = _post(client, PDF)
    assert r.status_code == 422 and "pdftotext" in r.json()["error"]


@mac_only
def test_route_returns_the_text_through_pdfkit(client, monkeypatch):
    _no_poppler(monkeypatch)
    r = _post(client, PDF)
    assert r.status_code == 200 and "Hello PDFKit" in r.json()["text"]


@mac_only
def test_route_rejects_a_broken_pdf_with_422(client, monkeypatch):
    _no_poppler(monkeypatch)
    r = _post(client, b"not a pdf at all")
    assert r.status_code == 422
