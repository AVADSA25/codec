"""CODEC version — single source of truth (F-5).

Reads the repo-root ``VERSION`` file at import and exposes ``__version__``. Falls back to
a module constant if the file is missing (e.g. a partial install / zipapp). Never raises.

Import this instead of hardcoding a version string anywhere:

    from codec_version import __version__
"""
from __future__ import annotations

from pathlib import Path

# Keep in sync with the VERSION file + the CHANGELOG's latest entry (pinned by
# tests/test_versioning.py). This constant is only a fallback when VERSION can't be read.
_FALLBACK = "3.1.0"


def _read_version() -> str:
    try:
        v = (Path(__file__).resolve().parent / "VERSION").read_text(encoding="utf-8").strip()
        return v or _FALLBACK
    except Exception:
        return _FALLBACK


__version__ = _read_version()
