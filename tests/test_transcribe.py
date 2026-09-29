"""Dictation through CODEC's local Whisper (UI phase 2, P2.5; docs/P2.5-DESIGN.md).

POST /api/transcribe forwards one recording to the configured stt_url with the
model and no language, caps the size before Whisper is called, maps Whisper
failures, and drops Whisper's silence artefacts with discard_reason. The mic
buttons on Chat, Home and Vibe use the shell's recorder; the browser's speech
service is only a per-device opt-in.
"""
from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent
SHELL = (REPO / "static" / "codec-shell.js").read_text(encoding="utf-8")
WEBM = b"\x1aE\xdf\xa3" + b"\x00" * 4000  # the EBML magic, then padding: Whisper is faked


@pytest.fixture
def api(monkeypatch):
    import codec_config
    import routes.transcribe as rt
    seen = []
    reply = {"status": 200, "json": {"text": "Please summarize my unread emails."}}

    def handler(request: httpx.Request):
        seen.append(request)
        if reply.get("raise"):
            raise reply["raise"]
        return httpx.Response(reply["status"], json=reply["json"])

    monkeypatch.setattr(rt, "_TRANSPORT", httpx.MockTransport(handler))
    monkeypatch.setattr(codec_config, "WHISPER_URL", "http://127.0.0.1:8084/v1/audio/transcriptions")
    app = FastAPI()
    app.include_router(rt.router)
    return TestClient(app), seen, reply


def _post(client, data=WEBM, ctype="audio/webm;codecs=opus", name="dictation.webm"):
    return client.post("/api/transcribe", files={"file": (name, data, ctype)})


def test_forwards_the_audio_with_the_model_and_no_language(api):
    client, seen, _ = api
    r = _post(client)
    assert r.status_code == 200 and r.json() == {"text": "Please summarize my unread emails.", "dropped": None}
    (req,) = seen
    assert str(req.url) == "http://127.0.0.1:8084/v1/audio/transcriptions"
    body = req.content
    assert b'name="model"' in body and b'filename="dictation.webm"' in body and WEBM in body
    assert b'name="language"' not in body, "no language: the server can detect it"


def test_safari_recordings_keep_their_container(api):
    client, seen, _ = api
    assert _post(client, ctype="audio/mp4", name="dictation.m4a").status_code == 200
    assert b'filename="dictation.m4a"' in seen[0].content


def test_oversized_and_non_audio_uploads_never_reach_whisper(api):
    client, seen, _ = api
    import routes.transcribe as rt
    assert _post(client, data=b"\x00" * (rt.MAX_BYTES + 1)).status_code == 413
    assert _post(client, data=b"hello", ctype="text/plain", name="a.txt").status_code == 415
    assert client.post("/api/transcribe", data={"file": "not a file"}).status_code == 400
    assert _post(client, data=b"").status_code == 400
    assert seen == []


def test_whisper_failures_become_clear_errors(api):
    client, _, reply = api
    reply.update(status=500, json={"detail": "boom"})
    assert _post(client).status_code == 502
    reply.update(status=200, json={"text": "Failed: mlx_whisper: error: argument"})
    assert _post(client).status_code == 502
    reply["raise"] = httpx.ConnectError("refused")
    r = _post(client)
    assert r.status_code == 503 and "whisper-stt" in r.json()["error"]


@pytest.mark.parametrize("text,reason", [
    ("Thank you.", "noise"),
    ("Thank you for watching.", "hallucination"),
    ("Thank you. Thank you. Thank you.", "repetitive"),
    ("   ", "empty"),
    ("Please summarize my unread emails from today.", None),
    ("Bonjour, peux-tu résumer mes emails d'aujourd'hui ?", None),
    ("Weather in Marbella", None),
])
def test_silence_artefacts_are_dropped_and_speech_kept(api, text, reason):
    client, _, reply = api
    reply.update(status=200, json={"text": text})
    assert _post(client).json() == {"text": "" if reason else text.strip(), "dropped": reason}


def test_filters_use_the_voice_pipelines_word_sets():
    from codec_voice_filters import NOISE_WORDS, WHISPER_HALLUCINATIONS, discard_reason
    assert all(discard_reason(w) == "noise" for w in NOISE_WORDS if w)
    assert all(discard_reason(h + ".") in ("noise", "hallucination") for h in WHISPER_HALLUCINATIONS)
    voice = (REPO / "codec_voice.py").read_text(encoding="utf-8")
    assert "if clean in NOISE_WORDS:" in voice and "if text_lower in WHISPER_HALLUCINATIONS:" in voice


def test_the_route_is_mounted_and_csrf_checked():
    dash = (REPO / "codec_dashboard.py").read_text(encoding="utf-8")
    assert "app.include_router(transcribe_router)" in dash
    assert re.search(r'request\.method in \("POST", "PUT", "DELETE", "PATCH"\)', dash)


def test_the_shell_records_locally_and_offers_browser_speech_only_as_opt_in():
    assert "dictation: { toggle: dictToggle" in SHELL
    assert "fetch('/api/transcribe', { method: 'POST', headers: csrf({}), body: fd })" in SHELL
    assert "new MediaRecorder(" in SHELL and "getFloatTimeDomainData" in SHELL
    assert "DICT_SILENCE_MS = 2000, DICT_MAX_MS = 120000" in SHELL
    assert "lsGet('codec-dictation') === 'browser' && speechClass()" in SHELL, "browser speech only by opt-in"
    assert 'id="dictBtn" hidden' in SHELL and "Uses your browser\\'s cloud service" in SHELL
    assert "r.lang = navigator.language" in SHELL


@pytest.mark.parametrize("page,call", [
    ("codec_chat.html",
     "CodecShell.dictation.toggle(document.getElementById('micBtn'),document.getElementById('chatInput'));"),
    ("codec_dashboard.html",
     "var btn = document.getElementById('micBtn');\n  CodecShell.dictation.toggle(btn, document.getElementById('cmdInput'), {"),
    ("codec_vibe.html",
     "CodecShell.dictation.toggle(document.getElementById('vibeMic'),document.getElementById('vi'));"),
])
def test_the_mic_buttons_use_the_shell_and_no_fixed_english(page, call):
    src = (REPO / page).read_text(encoding="utf-8")
    assert call in src
    assert "SpeechRecognition" not in src and "'en-US'" not in src
