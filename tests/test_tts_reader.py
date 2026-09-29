"""Read aloud that reads the whole answer, with voice and speed (UI phase 2, P2.8;
docs/P2.8-DESIGN.md).

/api/tts returns each request's own audio (POST for long text, voice and speed
checked before Kokoro is called); /api/tts/voices lists the local Kokoro voices
and the owner's custom ones; the shell's reader turns Markdown into speech text,
splits it into sentences and plays them in order with a small player; Chat and
Home read the whole answer; Settings picks the voice and tts_speed.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent
SHELL = (REPO / "static" / "codec-shell.js").read_text(encoding="utf-8")
MP3 = b"ID3\x04\x00" + b"\xff\xf3" * 200


class _Reply:
    def __init__(self, status=200, content=MP3, ctype="audio/mp3"):
        self.status_code, self.content, self.headers = status, content, {"content-type": ctype}


@pytest.fixture
def tts(tmp_path, monkeypatch):
    import routes.tts as rt
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"tts_voice": "/voices/owner.safetensors", "tts_speed": 0.85,
                               "tts_url": "http://127.0.0.1:8085/v1/audio/speech"}))
    hub = tmp_path / "hub" / "models--mlx-community--Kokoro-82M-bf16" / "snapshots" / "abc" / "voices"
    hub.mkdir(parents=True)
    for n in ("am_adam", "bf_emma", "ff_siwis"):
        (hub / f"{n}.safetensors").write_bytes(b"x")
    custom = tmp_path / "custom"
    custom.mkdir()
    (custom / "k9-test.safetensors").write_bytes(b"x")
    monkeypatch.setattr(rt, "CONFIG_PATH", str(cfg))
    monkeypatch.setattr(rt, "CUSTOM_VOICE_DIR", str(custom))
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    calls, reply = [], {"r": _Reply()}

    def fake_post(url, json=None, timeout=None):
        calls.append((url, json))
        if isinstance(reply["r"], Exception):
            raise reply["r"]
        return reply["r"]

    monkeypatch.setattr(rt.rq, "post", fake_post)
    app = FastAPI()
    app.include_router(rt.router)
    return TestClient(app), calls, reply, custom


def test_post_returns_its_own_audio_with_the_chosen_voice_and_speed(tts):
    client, calls, _, _ = tts
    r = client.post("/api/tts", json={"text": "Hello there. " * 50, "voice": "bf_emma", "speed": 1.3})
    assert r.status_code == 200 and r.content == MP3 and r.headers["content-type"] == "audio/mpeg"
    assert r.headers["cache-control"] == "no-store"
    (url, body), = calls
    assert body["voice"] == "bf_emma" and body["speed"] == 1.3 and body["input"] == "Hello there. " * 50


def test_defaults_come_from_the_config_and_get_still_works(tts):
    client, calls, _, _ = tts
    assert client.post("/api/tts", json={"text": "Hi."}).status_code == 200
    assert calls[-1][1]["voice"] == "/voices/owner.safetensors" and calls[-1][1]["speed"] == 0.85
    r = client.get("/api/tts", params={"text": "x" * 800})
    assert r.status_code == 200 and r.content == MP3 and len(calls[-1][1]["input"]) == 500
    src = (REPO / "routes" / "tts.py").read_text(encoding="utf-8")
    assert 'expanduser("~/.codec/pwa_audio.mp3")' not in src and "FileResponse" not in src, "no shared audio file"
    assert "def tts(" in src and "async def tts(" not in src, "the Kokoro call runs in the thread pool"


@pytest.mark.parametrize("body,status", [
    ({"text": ""}, 400),
    ({"text": "Hi", "voice": "../../etc/passwd"}, 400),
    ({"text": "Hi", "voice": "zz_nobody"}, 400),
    ({"text": "Hi", "speed": 3}, 400),
    ({"text": "Hi", "speed": "fast"}, 400),
    ({"text": "x" * 1001}, 413),
])
def test_bad_requests_never_reach_kokoro(tts, body, status):
    client, calls, _, _ = tts
    assert client.post("/api/tts", json=body).status_code == status
    assert calls == []


def test_kokoro_down_or_failing_gives_a_clear_error(tts):
    import requests
    client, _, reply, _ = tts
    reply["r"] = requests.ConnectionError("refused")
    r = client.post("/api/tts", json={"text": "Hi."})
    assert r.status_code == 503 and "kokoro-82m" in r.json()["error"]
    reply["r"] = _Reply(status=500, content=b"")
    assert client.post("/api/tts", json={"text": "Hi."}).status_code == 502


def test_voice_list_labels_built_in_and_custom_voices(tts):
    client, calls, _, custom = tts
    d = client.get("/api/tts/voices").json()
    assert d["current"] == "/voices/owner.safetensors" and d["speed"] == 0.85
    by_id = {v["id"]: v for v in d["voices"]}
    assert by_id["am_adam"]["label"] == "Adam, American English, male" and by_id["am_adam"]["group"] == "English"
    assert by_id["bf_emma"]["label"] == "Emma, British English, female"
    assert by_id["ff_siwis"]["label"] == "Siwis, French, female" and by_id["ff_siwis"]["group"] == "Other languages"
    mine = str(custom / "k9-test.safetensors")
    assert by_id[mine] == {"id": mine, "label": "Your voice: k9-test", "group": "Your voices", "custom": True}
    assert client.post("/api/tts", json={"text": "Hi.", "voice": mine}).status_code == 200
    assert calls[-1][1]["voice"] == mine


def test_tts_speed_is_validated_and_returned_by_the_config_routes(tmp_path, monkeypatch):
    import routes.config as rc
    assert rc._validate_config_updates({"tts_speed": 0.85}) == []
    assert rc._validate_config_updates({"tts_speed": 3})
    assert rc._validate_config_updates({"tts_speed": "fast"})
    src = (REPO / "routes" / "config.py").read_text(encoding="utf-8")
    assert '"tts_speed": config.get("tts_speed", 1.1),' in src


_NODE = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[1], 'utf8');
const block = src.slice(src.indexOf('// speech text: begin'), src.indexOf('// speech text: end'));
const f = new Function(block + '; return { speechText, speechChunks };')();
const md = [
  '## Plan for today', '', 'Here is **what** I found in [the docs](https://x.y/z) and ![a chart](c.png):', '',
  '- First, run `npm install` and wait', '- Second, it costs 3.5 euros.', '',
  '```python', 'print("secret code")', '```', '',
  '| Name | Time |', '|---|---|', '| Gym | 7am |', '', 'See https://example.com for more. _Done_!'
].join('\n');
const text = f.speechText(md);
const long = 'word '.repeat(200) + 'end.';
console.log(JSON.stringify({ text, chunks: f.speechChunks(text), long: f.speechChunks(long).map(p => p.length),
                              empty: f.speechChunks(f.speechText('```\nonly code\n```')) }));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_speech_text_drops_code_and_marks_and_splits_into_sentences():
    out = subprocess.run(["node", "-e", _NODE, str(REPO / "static" / "codec-shell.js")], capture_output=True,
                         text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    r = json.loads(out.stdout.strip().splitlines()[-1])
    assert r["text"].split("\n") == [
        "Plan for today.", "Here is what I found in the docs and a chart:", "First, run npm install and wait.",
        "Second, it costs 3.5 euros.", "Name, Time.", "Gym, 7am.", "See the link for more. Done!"]
    assert "secret" not in r["text"] and "**" not in r["text"] and "http" not in r["text"]
    assert r["chunks"][0].startswith("Plan for today. Here is what I found")
    assert any("it costs 3.5 euros." in c for c in r["chunks"]), "a decimal point does not end a sentence"
    assert all(n <= 280 for n in r["long"]) and len(r["long"]) >= 4
    assert r["empty"] == []


def test_the_shell_reader_prefetches_and_has_a_player():
    assert "speech: { speak: speak, stop: sayStop, toggle: sayToggle, rate: sayRate" in SHELL
    assert "for (var k = i + 1; k <= i + 2 && k < SAY.parts.length; k++) sayFetch(k, gen)" in SHELL
    assert "fetch('/api/tts', { method: 'POST'" in SHELL and "URL.createObjectURL(b)" in SHELL
    assert "var RATES = [0.9, 1, 1.1, 1.2, 1.3, 1.4];" in SHELL and "a.playbackRate = SAY.rate;" in SHELL
    assert "holder.innerHTML = sidebarHTML() + topHTML() + playerHTML()" in SHELL
    for label in ('aria-label="Pause"', 'aria-label="Reading speed"', 'aria-label="Stop reading"'):
        assert label in SHELL
    assert "e.name === 'NotAllowedError'" in SHELL, "autoplay without a tap waits for Resume"


def test_chat_and_home_read_the_whole_answer():
    chat = (REPO / "codec_chat.html").read_text(encoding="utf-8")
    home = (REPO / "codec_dashboard.html").read_text(encoding="utf-8")
    assert "CodecShell.speech.speak(String(text), { btn: btn });" in chat
    assert "voiceReplyEnabled && !quiet) speakMessage(String(content));" in chat
    assert "CodecShell.speech.speak(text);" in home
    for page in (chat, home):
        assert "/api/tts?text=" not in page, "no GET with a cut-down text"
    assert "String(text).slice(0, 500)" not in chat and "text.substring(0, 300)" not in home


def test_settings_has_the_voice_picker_preview_and_speed():
    home = (REPO / "codec_dashboard.html").read_text(encoding="utf-8")
    assert 'id="voiceSection"' in home and 'id="voiceSelect"' in home and 'id="voicePreviewBtn"' in home
    assert 'id="voiceSpeed" min="0.7" max="1.3"' in home and "loadVoice();" in home
    assert "if (key === 'tts_voice' || key === 'tts_speed') continue;" in home
    assert "body: JSON.stringify({ tts: fields })" in home
