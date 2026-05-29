"""Pilot PP-6 — text typed into a password/secret field must NOT be persisted verbatim to
the trace (and thus to compiled skills); it is redacted at record time. Closes audit P-13.

Reference: docs/PP6-SECRET-REDACTION-DESIGN.md.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))  # ~/codec on sys.path

from pilot.pilot_agent import redact_typed_secret  # noqa: E402
from pilot.snapshot import IndexedElement  # noqa: E402


def _el(name="", attrs=None):
    return IndexedElement(index=1, role="textbox", name=name, xpath="//x",
                          css_sel="x", bbox={}, attrs=attrs or {})


def test_password_input_text_redacted():
    el = _el(name="Password", attrs={"type": "password"})
    out = redact_typed_secret({"action": "type", "index": 1, "text": "hunter2"}, el)
    assert "hunter2" not in out["text"] and "redact" in out["text"].lower()


def test_secret_named_field_redacted():
    el = _el(name="API Token", attrs={"type": "text"})
    out = redact_typed_secret({"action": "type", "index": 1, "text": "sk-secret-abc"}, el)
    assert "sk-secret-abc" not in out["text"]


def test_normal_field_not_redacted():
    el = _el(name="Search", attrs={"type": "text", "placeholder": "Search stories"})
    out = redact_typed_secret({"action": "type", "index": 1, "text": "weather in Paris"}, el)
    assert out["text"] == "weather in Paris"


def test_non_type_action_untouched():
    el = _el(name="Password", attrs={"type": "password"})
    action = {"action": "click", "index": 1}
    assert redact_typed_secret(action, el) == action
