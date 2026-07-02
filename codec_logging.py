"""CODEC structured logging — JSON format for log aggregation."""
import logging
import json
import sys
import time
import os

class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "module": record.module,
            "line": record.lineno,
        }
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, default=str)


def setup_logging(level=logging.INFO, json_output=True):
    """Configure root logger with JSON or plain formatting.

    Call once at process startup. Set CODEC_LOG_JSON=0 for plain format.

    Level-split handlers (2026-07 log review): DEBUG/INFO → stdout,
    WARNING+ → stderr. PM2 maps stderr to `<name>-error.log`, so with a
    single stderr StreamHandler the error logs were ~95% INFO chatter —
    real errors were invisible. After the split, `*-error.log` only
    contains WARNING and above.
    """
    root = logging.getLogger()
    if root.handlers:
        return  # already configured

    if json_output and os.environ.get("CODEC_LOG_JSON", "1") != "0":
        formatter = JSONFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            datefmt="%H:%M:%S"
        )

    out_handler = logging.StreamHandler(sys.stdout)
    out_handler.setFormatter(formatter)
    out_handler.addFilter(lambda record: record.levelno < logging.WARNING)

    err_handler = logging.StreamHandler(sys.stderr)
    err_handler.setFormatter(formatter)
    err_handler.setLevel(logging.WARNING)

    root.addHandler(out_handler)
    root.addHandler(err_handler)
    root.setLevel(level)
