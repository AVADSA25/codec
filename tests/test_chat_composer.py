"""Chat composer, queue, starters and keys (UI phase 1, PR-D).

The chat page has one composer card (+ menu, mode pill, model pill), queues a
message sent while a reply streams, shows starter cards on a new chat, and has
IME-safe Enter. The Home Flash composer binds Enter once.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CHAT = (REPO / "codec_chat.html").read_text(encoding="utf-8")
DASH = (REPO / "codec_dashboard.html").read_text(encoding="utf-8")


def _between(src: str, start: str, end: str) -> str:
    i = src.index(start)
    return src[i:src.index(end, i)]


def _function(src: str, name: str) -> str:
    """Source of a top-level `function name(...)` up to the next top-level function."""
    i = src.index(f"function {name}(")
    j = src.find("\nfunction ", i + 1)
    return src[i:j if j > 0 else None]


def test_one_composer_card_holds_the_controls():
    assert 'class="sub-tabs"' not in CHAT, "the old mode-bar row is back"
    card = _between(CHAT, '<div class="composer" id="composer">', '<input type="file" id="fileUp"')
    for el in ('id="chatInput"', 'id="plusBtn"', 'id="modePill"', 'id="cbModelSlot"',
               'id="modelSelect"', 'id="micBtn"', 'id="sendBtn"', 'id="stopBtn"', 'id="queueChips"'):
        assert el in card, f"{el} is not inside the composer card"
    plus = _between(card, 'id="plusMenu"', 'id="modeMenu"')
    for label in ("Attach files", "Screenshot of Mac", "Webcam snapshot", "Working folder", "Search the web"):
        assert label in plus, f"+ menu has no '{label}'"
    modes = re.findall(r'data-mode="(\w+)"', _between(card, 'id="modeMenu"', 'id="queueChips"'))
    assert modes == ["chat", "think", "research", "project", "image"]
    # The Agents crew picker and builder live in the sheet, not above the messages.
    sheet = _between(CHAT, 'id="agentSheet"', 'id="scheduleBtn"')
    assert 'id="crewSelect"' in sheet and 'id="agentBuilder"' in sheet


def test_web_search_toggle_drives_force_search():
    toggle = _function(CHAT, "toggleWebSearch")
    assert "webSearchEnabled=!webSearchEnabled" in toggle
    assert CHAT.count("force_search:webSearchEnabled") == 2, "both /api/chat calls must send the toggle"


def test_enter_is_ime_safe_and_touch_enter_is_a_newline():
    key = _function(CHAT, "handleKey")
    assert "!e.isComposing" in key and "e.keyCode!==229" in key
    assert "!_isTouch()" in key


def test_flash_enter_is_bound_once_and_ime_safe():
    tag = re.search(r'<textarea[^>]*id="cmdInput"[^>]*>', DASH).group(0)
    assert "onkeydown" not in tag, "Flash Enter is bound inline as well as by addEventListener"
    binds = re.findall(r"getElementById\('cmdInput'\)\.addEventListener\('keydown'[^\n]*", DASH)
    assert len(binds) == 1 and "!e.isComposing" in binds[0] and "e.keyCode !== 229" in binds[0]


def test_text_sent_during_a_reply_is_queued_and_sent_after():
    send = _function(CHAT, "sendMessage")
    assert send.index("if(isProcessing){") < send.index("_queue.push(")
    assert "_drainQueue()" in _function(CHAT, "_showSend")
    # Stop keeps the queue until the owner presses Send.
    assert "_queuePaused=true" in _function(CHAT, "stopGeneration")


def test_stream_follows_only_at_the_bottom():
    follow = _function(CHAT, "scrollBottom")
    assert "if(_follow)" in follow and "_showJump(true)" in follow
    assert "scrollHeight-m.scrollTop-m.clientHeight<80" in _function(CHAT, "_nearBottom")


def test_starter_phrases_fire_allowlisted_skills():
    """"Start my day" must fire daily_kickoff, not reach the LLM as chat."""
    from codec_skill_registry import SkillRegistry
    from routes.chat import CHAT_SKILL_ALLOWLIST

    block = _between(CHAT, "var STARTERS=[", "];")
    phrases = re.findall(r"""send:(['"])(.+?)\1\}""", block)
    assert [p for _, p in phrases][:1] == ["start my day"]
    reg = SkillRegistry(str(REPO / "skills"))
    reg.scan()
    for _, phrase in phrases:
        hits = reg.match_all_triggers(phrase)
        assert hits and hits[0] in CHAT_SKILL_ALLOWLIST, f"starter '{phrase}' fires {hits}"
    assert "daily_kickoff" == reg.match_all_triggers("start my day")[0]


def test_stopped_reply_becomes_a_normal_message():
    keep = _function(CHAT, "_keepPartial")
    assert "addMessage('assistant',txt,false,null,true)" in keep, "a stopped reply needs its action row"
    assert "if(!ans){div.remove();return}" in keep, "a stop before any text left a stuck 'thinking' bubble"


def test_message_buttons_bind_their_own_button():
    """A shared variable made Edit receive null (and Copy the speak button)."""
    assert "editMessage(txt,eb)" in CHAT and "copyMsgText(txt,cb)" in CHAT and "speakMessage(txt,sb)" in CHAT
