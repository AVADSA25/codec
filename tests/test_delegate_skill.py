"""skills/delegate.py: the local n8n webhook by default, or the config
"delegate" target with its auth header read from the Keychain."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import codec_config  # noqa: E402
import codec_keychain  # noqa: E402


def _load_delegate():
    spec = importlib.util.spec_from_file_location("_skill_delegate", REPO / "skills" / "delegate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Resp:
    status_code = 200
    text = '{"output": "done"}'

    def json(self):
        return {"output": "done"}


def _capture(monkeypatch, mod):
    sent = {}

    def _post(url, json=None, headers=None, timeout=None):
        sent.update(url=url, json=json, headers=headers)
        return _Resp()
    monkeypatch.setattr(mod.requests, "post", _post)
    return sent


def test_delegate_posts_to_local_webhook_without_config(monkeypatch):
    monkeypatch.delitem(codec_config.cfg, "delegate", raising=False)
    mod = _load_delegate()
    sent = _capture(monkeypatch, mod)
    assert mod.run("delegate log expense 12 euro lunch") == "done"
    assert sent["url"] == mod.WORKFLOW_WEBHOOK
    assert sent["headers"] == {}
    assert sent["json"] == {"message": "log expense 12 euro lunch", "source": "codec", "app": ""}


def test_delegate_uses_configured_target_and_keychain_header(monkeypatch):
    monkeypatch.setitem(codec_config.cfg, "delegate", {
        "url": "https://n8n.example.com/webhook/x",
        "auth_header": "X-Webhook-Key",
        "key_slot": "test_delegate_slot",
    })
    monkeypatch.setattr(codec_keychain, "keychain_get",
                        lambda slot: "s3cret" if slot == "test_delegate_slot" else None)
    mod = _load_delegate()
    sent = _capture(monkeypatch, mod)
    assert mod.run("delegate log expense 12 euro lunch") == "done"
    assert sent["url"] == "https://n8n.example.com/webhook/x"
    assert sent["headers"] == {"X-Webhook-Key": "s3cret"}
