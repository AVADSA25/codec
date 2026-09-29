"""Calm voice call with standard controls (UI phase 2, P2.9; docs/P2.9-DESIGN.md).

The server side: a typed turn arrives as the `text` control, is queued like an
utterance and runs through the same pipeline without speech to text or an echo.
The page side: one level-driven orb instead of the old motion, bubbles labelled
You and CODEC, Plex type, and the new controls wired to the socket and routes.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
PAGE = (REPO / "codec_voice.html").read_text(encoding="utf-8")


def _pipeline():
    ws = AsyncMock()
    ws.send_json = AsyncMock()
    ws.send_bytes = AsyncMock()
    with patch("codec_voice.VoicePipeline._load_skills"), \
            patch("codec_voice._build_system_prompt", return_value="sys"):
        from codec_voice import VoicePipeline
        p = VoicePipeline(ws)
    p.skills = {}
    p.mode = "default"
    p._awaiting_ask_user = None
    p._disconnect_reason = "user"
    return p, ws


def _fn(name):
    start = PAGE.index("function " + name + "(")
    return PAGE[start:PAGE.index("\n    }\n", start)]


def test_the_text_control_queues_a_typed_turn():
    p, ws = _pipeline()
    frames = [{"type": "websocket.receive", "text": json.dumps({"type": "text", "text": "  what's on today?  "})},
              {"type": "websocket.receive", "text": json.dumps({"type": "text", "text": "   "})},
              {"type": "websocket.receive", "text": json.dumps({"type": "text"})},
              {"type": "websocket.receive", "text": json.dumps({"type": "text", "text": "x" * 2500})},
              {"type": "websocket.disconnect"}]
    ws.receive = AsyncMock(side_effect=frames)
    asyncio.run(p._audio_receiver())
    items = [p.utterance_queue.get_nowait() for _ in range(p.utterance_queue.qsize())]
    assert items == ["what's on today?", "x" * 2000, None], "stripped, empty ones ignored, capped at 2,000"


def test_your_turn_with_nothing_said_goes_back_to_listening():
    """An empty hold (or Your Turn) must not leave the page on "processing"."""
    p, ws = _pipeline()
    ws.receive = AsyncMock(side_effect=[{"type": "websocket.receive", "text": json.dumps({"type": "your_turn"})},
                                        {"type": "websocket.disconnect"}])
    asyncio.run(p._audio_receiver())
    assert {"type": "status", "status": "listening"} in [c.args[0] for c in ws.send_json.await_args_list]


def test_a_typed_turn_skips_speech_to_text_and_is_not_echoed():
    p, ws = _pipeline()
    p.transcribe = AsyncMock(return_value="spoken words")
    p._poll_pending_question_for_voice = AsyncMock(return_value=None)
    p.dispatch_crew_from_voice = AsyncMock(return_value=None)
    p._match_skill = MagicMock(return_value=None)
    p._speak = AsyncMock(return_value=True)
    heard = []

    async def reply(text):
        heard.append(text)
        for token in ("Sure. ", "Here it is."):
            yield token

    p.generate_response = reply
    pcm = b"\x01\x00" * 16000

    async def go():
        for item in ("hello from the keyboard", pcm, None):
            p.utterance_queue.put_nowait(item)
        await p._pipeline()

    asyncio.run(go())
    assert p.transcribe.await_count == 1, "only the spoken turn goes to speech to text"
    assert heard == ["hello from the keyboard", "spoken words"]
    sent = [c.args[0] for c in ws.send_json.await_args_list]
    users = [m["text"] for m in sent if m.get("type") == "transcript" and m.get("role") == "user"]
    assert users == ["spoken words"], "the typed line is on the page already; the spoken one is echoed"
    replies = [m["text"] for m in sent if m.get("type") == "transcript" and m.get("role") == "assistant"]
    assert replies == ["Sure. Here it is.", "Sure. Here it is."]


def test_one_orb_follows_the_level_and_the_old_motion_is_gone():
    for gone in ("spin-slow", "ring-outer", "ring-energy", "waveform", "body::before", "linear-gradient",
                 "radial-gradient", "pulse-green", "hold-indicator"):
        assert gone not in PAGE, gone
    assert 'class="orb"' in PAGE and "transform: scale(calc(1 + var(--lvl) * 0.3))" in PAGE
    assert "setLevel(rms / 2800)" in PAGE, "the mic RMS the page already computed drives the orb"
    assert "src.connect(outAnalyser || audioCtx.destination)" in PAGE and "getFloatTimeDomainData" in PAGE
    assert "@media (prefers-reduced-motion: reduce)" in PAGE
    assert 'id="statusLabel"' in PAGE


def test_bubbles_say_you_and_the_type_is_plex():
    assert "var TX_LABEL = { user: 'You', assistant: 'CODEC' };" in PAGE
    assert "'M'" not in PAGE and ">M<" not in PAGE
    assert "SF Pro" not in PAGE and "SF Mono" not in PAGE
    assert "font-family: var(--font-sans);" in PAGE and "font-family: var(--font-mono);" in PAGE
    assert ".tx-line.user {" in PAGE and "align-self: flex-end" in PAGE
    assert 'id="holdHint">Hold the circle to talk, let go to send<' in PAGE


def test_the_screenshot_uses_get_and_a_toast():
    shot = _fn("takeScreenshot")
    # The image loads from the GET-only route itself: the page CSP allows 'self' images, not blob: URLs.
    assert "img.src='/api/screenshot?_t='+Date.now();" in shot and "createObjectURL" not in shot and "method" not in shot
    assert "showToast('Screenshot taken')" in shot and "showToast('Could not take a screenshot')" in shot
    import codec_dashboard
    img_src = re.search(r"img-src ([^;]+);", codec_dashboard.CSPMiddleware.CSP).group(1)
    assert "'self'" in img_src and "blob:" not in img_src
    assert "alert(" not in PAGE
    shell = (REPO / "static" / "codec-shell.js").read_text(encoding="utf-8")
    assert "install: install, toast: toast," in shell, "the shell exports its toast"


def test_the_call_controls_are_wired():
    assert 'id="muteBtn" aria-label="Mute" aria-pressed="false" onclick="toggleMute()"' in PAGE
    mute = _fn("applyMute")
    assert "t.enabled = live" in mute and "var live = !micMuted || isHolding;" in mute
    stop = _fn("stopSpeaking")
    assert "JSON.stringify({ type: 'interrupt' })" in stop and "stopAudio();" in stop
    typed = _fn("sendTyped")
    assert "ws.send(JSON.stringify({ type: 'text', text: text }))" in typed and "txAppend('user', text)" in typed
    assert 'maxlength="2000"' in PAGE
    cont = _fn("continueInChat")
    assert "fetch('/api/qchat/save'" in cont and "session_id: id, title: title, messages: msgs" in cont
    assert "location.href = '/chat#session=' + encodeURIComponent(id);" in cont
    assert "#transcript .tx-line.user, #transcript .tx-line.assistant" in _fn("callMessages")
    assert 'id="micSelect"' in PAGE and "enumerateDevices()" in _fn("refreshMics")
    assert "audio.deviceId = { exact: want };" in _fn("openMicStream")


def test_think_narration_becomes_a_tool_chip():
    m = re.search(r"var TOOL_NARRATION = /(.+?)/;", PAGE)
    assert m, "the page reads Think mode's tool narration"
    pattern = re.compile(m.group(1).replace("\\u2026", "…"))
    voice = (REPO / "codec_voice.py").read_text(encoding="utf-8")
    assert "msg = f\"Using {str(update['tool']).replace('_', ' ')}…\"" in voice
    assert pattern.match("Using web search…").group(1) == "web search"
    assert not pattern.match("Still processing — hang on…")
    assert "div.className = 'tx-chip';" in PAGE
