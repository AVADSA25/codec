"""R-04: Home's Flash text skips the shell-danger check; command paths keep it.

Flash (`codec_dashboard.html` sendCmd -> POST /api/command, source "flash")
sends chat text to the model and the chat skill matcher, never to a shell, so a
backtick or `$(` must reach the model. Every other /api/command source (the
heartbeat's drafted tasks, a caller with no source) and the terminal skill still
refuse it. docs/FLASH-SHELL-CHECK-DESIGN.md.
"""
import asyncio
import importlib.util
import sqlite3
import subprocess
from pathlib import Path

import pytest

import codec_config
import codec_dashboard
import codec_llm

REPO = Path(__file__).resolve().parent.parent


class _Req:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


@pytest.fixture
def flash(monkeypatch, tmp_path):
    """Run /api/command against a temp DB and a fake model; return (send, prompts)."""
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.execute("CREATE TABLE conversations (id INTEGER PRIMARY KEY AUTOINCREMENT,"
               " session_id TEXT, timestamp TEXT, role TEXT, content TEXT)")
    prompts = []
    monkeypatch.setattr(codec_dashboard, "get_db", lambda: db)
    monkeypatch.setattr(codec_dashboard, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(codec_dashboard, "_try_skill", lambda task: (None, None))
    monkeypatch.setattr(codec_config, "get_llm_api_key", lambda: "")
    monkeypatch.setattr(codec_llm, "call", lambda messages, **kw: prompts.append(messages) or "ok")

    def send(body):
        async def go():
            resp = await codec_dashboard.send_command(_Req(body))
            pending = asyncio.all_tasks() - {asyncio.current_task()}
            if pending:
                await asyncio.wait_for(asyncio.gather(*pending), timeout=10)
            return resp
        return asyncio.run(go())

    return send, prompts


@pytest.mark.parametrize("text", ["Reply with one inline `code` word", "What does $(date) print in bash?"])
def test_flash_text_with_shell_characters_reaches_the_model(flash, text):
    send, prompts = flash
    resp = send({"task": text, "source": "flash", "session_id": "flash-test"})
    assert isinstance(resp, dict) and resp["status"] == "processing"
    assert len(prompts) == 1 and prompts[0][-1] == {"role": "user", "content": text}


@pytest.mark.parametrize("body", [
    {"command": "echo `whoami`", "source": "heartbeat"},  # a drafted task the heartbeat queues
    {"command": "echo $(whoami)"},                        # no source: still checked
])
def test_other_sources_are_still_refused(flash, body):
    send, prompts = flash
    resp = send(body)
    assert resp.status_code == 403 and prompts == []


def test_the_terminal_skill_still_refuses_and_runs_nothing(monkeypatch):
    spec = importlib.util.spec_from_file_location("_r04_terminal", REPO / "skills" / "terminal.py")
    term = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(term)
    ran = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: ran.append(a))
    assert term.run("run echo $(whoami)").startswith("Blocked for safety")
    assert term.run("run echo `whoami`").startswith("Blocked for safety")
    assert ran == []


def test_the_flash_page_sends_source_flash():
    home = (REPO / "codec_dashboard.html").read_text(encoding="utf-8")
    send = home[home.index("async function sendCmd()"):]
    send = send[send.index("fetch('/api/command'"):][:300]
    assert "source: 'flash'" in send
