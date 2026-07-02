"""codec_logging level-split (2026-07 log review): DEBUG/INFO → stdout,
WARNING+ → stderr, so PM2 `<name>-error.log` only carries real problems."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import codec_logging


def _fresh_root():
    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    root.handlers = []
    return root, saved


def _restore_root(root, saved):
    root.handlers, level = saved[0], saved[1]
    root.setLevel(level)


def test_setup_logging_splits_streams():
    root, saved = _fresh_root()
    try:
        codec_logging.setup_logging()
        assert len(root.handlers) == 2
        out = [h for h in root.handlers if getattr(h, "stream", None) is sys.stdout]
        err = [h for h in root.handlers if getattr(h, "stream", None) is sys.stderr]
        assert len(out) == 1 and len(err) == 1
        # stderr handler ignores INFO
        assert err[0].level == logging.WARNING
        # stdout handler filters out WARNING+ (no double emission)
        info_rec = logging.LogRecord("t", logging.INFO, __file__, 1, "m", (), None)
        warn_rec = logging.LogRecord("t", logging.WARNING, __file__, 1, "m", (), None)
        assert out[0].filter(info_rec)
        assert not out[0].filter(warn_rec)
    finally:
        _restore_root(root, saved)


def test_setup_logging_idempotent():
    root, saved = _fresh_root()
    try:
        codec_logging.setup_logging()
        codec_logging.setup_logging()
        assert len(root.handlers) == 2, "second call must not add handlers"
    finally:
        _restore_root(root, saved)
