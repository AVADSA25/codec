"""Completion verifier for "save to Google Drive / Google Doc" deliverables.

Buyer-journey / demo audit (#17): a research Project's checkpoint claimed it had
"saved the report to Google Drive" but only wrote a local .md file — a false
completion. This module lets codec_agent_runner refuse to mark such a checkpoint
done unless a REAL Google Doc was actually produced (its docs.google.com URL
appears in the checkpoint history, and — best-effort — the doc resolves in Drive).

Pure/stdlib except the optional live existence check, so the gating logic is
fully unit-testable without Google OAuth.
"""
from __future__ import annotations

import re
from typing import Any, Optional

# The expected_output asks for a Google-Drive/Doc deliverable (not a local file).
_WANTS_GDOC_RE = re.compile(
    r"google\s*docs?|\bgdoc\b|google\s*drive|\bto\s+drive\b|"
    r"google\s*document|docs\.google",
    re.IGNORECASE,
)

# A real Google Doc URL (what create_google_doc returns).
_DOC_URL_RE = re.compile(r"https://docs\.google\.com/document/d/[a-zA-Z0-9_-]+")
_DOC_ID_RE = re.compile(r"/document/d/([a-zA-Z0-9_-]+)")


def wants_gdoc(expected_output: str) -> bool:
    """True when the checkpoint's expected_output calls for a Google Doc / Drive
    deliverable (so a local .md doesn't satisfy it)."""
    return bool(_WANTS_GDOC_RE.search(expected_output or ""))


def find_doc_url(history: Any) -> Optional[str]:
    """Return the first Google Doc URL found in the checkpoint history (or a raw
    string). None if no real doc URL was produced."""
    if isinstance(history, str):
        m = _DOC_URL_RE.search(history)
        return m.group(0) if m else None
    for h in (history or []):
        text = str((h or {}).get("result", "")) if isinstance(h, dict) else str(h)
        m = _DOC_URL_RE.search(text)
        if m:
            return m.group(0)
    return None


def doc_id_from_url(url: str) -> Optional[str]:
    m = _DOC_ID_RE.search(url or "")
    return m.group(1) if m else None


def doc_exists(url_or_id: str) -> Optional[bool]:
    """Best-effort live check that the doc really exists in Drive. Returns
    True/False if we could check, or None if we couldn't (no creds / offline) —
    callers treat None as "can't disprove", so a missing OAuth setup never
    hard-fails a checkpoint that did produce a URL."""
    doc_id = doc_id_from_url(url_or_id) or url_or_id
    if not doc_id:
        return False
    try:
        import codec_google_auth
        svc = codec_google_auth.build_service("docs", "v1")
        svc.documents().get(documentId=doc_id, fields="documentId").execute()
        return True
    except Exception:
        # 404 → gone; auth/other error → unknown. We can't reliably tell them
        # apart across client libraries, so be conservative: unknown, not False.
        return None


def verify_deliverable(checkpoint: dict, history: Any, *, live: bool = False) -> tuple[bool, str]:
    """(ok, reason). ok=False means the checkpoint asked for a Google Doc but no
    real doc was produced — the agent must actually create one before finishing.

    live=True additionally confirms the doc resolves in Drive (needs OAuth)."""
    expected = str((checkpoint or {}).get("expected_output", ""))
    if not wants_gdoc(expected):
        return True, "deliverable is not a Google Doc"

    url = find_doc_url(history)
    if not url:
        return False, (
            "This checkpoint's deliverable is a Google Doc, but no Google Doc was "
            "actually created — there is no docs.google.com/document/... link in "
            "your results, only local text. Do NOT mark this done. Create the real "
            "doc now with the google_docs skill (e.g. 'create a google doc titled "
            "<title>' with the report as the body), then return its URL."
        )

    if live:
        exists = doc_exists(url)
        if exists is False:
            return False, (
                f"A Google Doc URL was produced ({url}) but it does not resolve in "
                f"Drive — the doc was not really created. Create it for real with "
                f"the google_docs skill and return the working URL."
            )
    return True, url
