"""CODEC structured logging — JSON format for log aggregation."""
import logging
import json
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
    """
    root = logging.getLogger()
    if root.handlers:
        return  # already configured

    handler = logging.StreamHandler()
    if json_output and os.environ.get("CODEC_LOG_JSON", "1") != "0":
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            datefmt="%H:%M:%S"
        ))
    root.addHandler(handler)
    root.setLevel(level)
