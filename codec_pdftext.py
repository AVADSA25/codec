"""Text out of a PDF without poppler or PyMuPDF (audit P-033, D-35).

`pdftotext` is poppler (GPL) and not on a buyer's Mac; PyMuPDF is AGPL. Apple's PDFKit is
part of macOS and reachable through the Quartz bindings already in the buyer lock
(pyobjc-framework-quartz), so it adds no package. Callers keep `pdftotext` as the first
choice where it exists (`extract_with_poppler`), so the Studio's output does not change.
"""
import shutil
import subprocess


class PdfTextError(Exception):
    """The PDF could not be read; the message is safe to show the user."""


def poppler_available():
    return shutil.which("pdftotext") is not None


def extract_with_poppler(path, timeout=90):
    r = subprocess.run(["pdftotext", "-layout", path, "-"],
                       capture_output=True, text=True, timeout=timeout)
    return r.stdout


def extract_with_pdfkit(path, max_pages=None):
    """Text of the PDF at `path` (the first `max_pages` pages, or all) through PDFKit."""
    try:
        from Foundation import NSURL
        from Quartz import PDFDocument
    except ImportError as e:
        raise PdfTextError("Reading PDFs needs macOS (PDFKit) or the pdftotext program.") from e
    doc = PDFDocument.alloc().initWithURL_(NSURL.fileURLWithPath_(path))
    if doc is None:
        raise PdfTextError("This file is not a readable PDF.")
    if doc.isLocked():
        raise PdfTextError("This PDF is password-protected.")
    if max_pages is None:
        return str(doc.string() or "")
    pages = []
    for i in range(min(int(max_pages), int(doc.pageCount()))):
        pages.append(str(doc.pageAtIndex_(i).string() or ""))
    return "\n".join(pages)


def extract_text(path, max_pages=None):
    """pdftotext when installed (whole file), PDFKit otherwise. Raises PdfTextError."""
    if max_pages is None and poppler_available():
        try:
            return extract_with_poppler(path)
        except OSError:
            pass                      # pdftotext vanished or cannot run: use PDFKit
        # A timeout (subprocess.TimeoutExpired) is not caught: the caller reports it.
    return extract_with_pdfkit(path, max_pages)
