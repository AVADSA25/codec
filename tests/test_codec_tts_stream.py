"""Voice 02 phase 2 (codec_tts.py, docs/CODEC-VOICE-02-LIVE-DESIGN.md): the clone streamed as PCM for the live conversation.

No real server is called here: an httpx mock transport answers. A gate script tries the real mlx-audio server.
The rules: settings only for purpose "live" and only when the phase 1 protections hold, whole samples only, a clear split between
"nothing was heard" (CloneUnavailable) and "it stopped after audio began" (CloneBroke), the connection closed whenever the reader
leaves, and text pieces that never exceed the limit and never lose a word.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

import codec_tts

CLONE_MODEL = "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16"
WAV = b"RIFF" + b"\x24\x00\x00\x00" + b"WAVE" + b"\x00" * 200
URL = "http://localhost:8085/v1/audio/speech"


@pytest.fixture(autouse=True)
def model_cache(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    (hub / ("models--" + CLONE_MODEL.replace("/", "--")) / "snapshots" / "abc").mkdir(parents=True)
    monkeypatch.setenv("HF_HUB_CACHE", str(hub))
    return hub


@pytest.fixture
def cfg(tmp_path):
    ref = tmp_path / "reference.wav"
    ref.write_bytes(WAV)
    words = tmp_path / "reference.txt"
    words.write_text("Good evening. This is the reference.\n")
    return {"tts_clone": {"enabled_for": ["speak", "live"], "model": CLONE_MODEL,
                          "ref_audio": str(ref), "ref_text_file": str(words)}}


@pytest.fixture
def live(cfg):
    return codec_tts.live_settings(cfg)


# ── settings ────────────────────────────────────────────────────────────────

def test_live_settings_are_off_unless_live_is_enabled(cfg):
    cfg["tts_clone"]["enabled_for"] = ["speak"]
    assert codec_tts.live_settings(cfg) is None
    assert codec_tts.live_settings({}) is None
    assert codec_tts.live_settings(None) is None


def test_live_settings_defaults(live):
    assert live["interval"] == 0.5 and live["first_byte_timeout"] == 8.0 and live["idle_timeout"] == 6.0
    assert live["chunk_chars"] == 280 and live["breaker_failures"] == 2 and live["rate"] == 24000
    assert live["model"] == CLONE_MODEL and live["ref_text"].startswith("Good evening") and live["lang_code"] == "auto"


def test_live_settings_overrides_stay_inside_safe_bounds(cfg):
    cfg["tts_clone"]["live"] = {"interval": 99, "first_byte_timeout": 0, "idle_timeout": "soon", "chunk_chars": 9999,
                                "breaker_failures": -3}
    s = codec_tts.live_settings(cfg)
    assert s["interval"] == 2.0 and s["first_byte_timeout"] == 1.0 and s["idle_timeout"] == 6.0
    assert s["chunk_chars"] == s["max_chars"] == 400 and s["breaker_failures"] == 1


def test_live_settings_accept_a_valid_override(cfg):
    cfg["tts_clone"]["live"] = {"interval": 1.0, "chunk_chars": 200, "breaker_failures": 3}
    s = codec_tts.live_settings(cfg)
    assert s["interval"] == 1.0 and s["chunk_chars"] == 200 and s["breaker_failures"] == 3


def test_a_live_block_that_is_not_a_dict_is_ignored(cfg):
    cfg["tts_clone"]["live"] = "fast"
    assert codec_tts.live_settings(cfg)["interval"] == 0.5


def test_the_phase_1_protections_still_hold_for_live(cfg, model_cache):
    bad_ref = dict(cfg["tts_clone"], ref_audio=cfg["tts_clone"]["ref_audio"] + ".missing")
    assert codec_tts.live_settings({"tts_clone": bad_ref}) is None
    uncached = dict(cfg["tts_clone"], model="some/model-not-in-the-cache")
    assert codec_tts.live_settings({"tts_clone": uncached}) is None


def test_models_url_and_model_loaded():
    assert codec_tts.models_url(URL) == "http://localhost:8085/v1/models"

    class R:
        def __init__(self, data):
            self._d = data

        def json(self):
            return self._d
    assert codec_tts.model_loaded(URL, "a/b", get=lambda u, timeout: R({"data": [{"id": "x"}, {"id": "a/b"}]})) is True
    assert codec_tts.model_loaded(URL, "a/b", get=lambda u, timeout: R({"data": [{"id": "x"}]})) is False
    assert codec_tts.model_loaded(URL, "a/b", get=lambda u, timeout: R({})) is False

    def boom(u, timeout):
        raise OSError("down")
    assert codec_tts.model_loaded(URL, "a/b", get=boom) is False


def test_the_fallback_note_has_a_public_name(monkeypatch):
    seen = []
    monkeypatch.setattr(codec_tts, "_note_fallback", lambda purpose, reason: seen.append((purpose, reason)))
    codec_tts.note_fallback("live", "timeout")
    assert seen == [("live", "timeout")]


# ── the splitter ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [None, "", "   \n\t "])
def test_nothing_to_say_gives_no_pieces(text):
    assert codec_tts.split_for_speech(text, 50) == []


def test_a_short_text_stays_whole():
    assert codec_tts.split_for_speech("Your note is saved.", 280) == ["Your note is saved."]


def test_sentences_are_separate_pieces():
    assert codec_tts.split_for_speech("One. Two! Three? Four… five.", 280) == ["One.", "Two!", "Three?", "Four…", "five."]


def test_spacing_and_new_lines_are_normalised():
    assert codec_tts.split_for_speech("Line one.\n\nLine   two.", 280) == ["Line one.", "Line two."]


def test_a_long_sentence_is_cut_at_a_comma_when_there_is_one():
    text = "This sentence goes on for quite a while, and then it keeps going with more words after the comma until the end."
    pieces = codec_tts.split_for_speech(text, 70)
    assert len(pieces) == 2 and pieces[0].endswith("while,") and all(len(p) <= 70 for p in pieces)


def test_a_long_sentence_without_a_comma_is_cut_at_a_space():
    pieces = codec_tts.split_for_speech(" ".join(["word"] * 40), 50)
    assert all(len(p) <= 50 and not p.startswith(" ") and not p.endswith(" ") for p in pieces)
    assert " ".join(pieces).split() == ["word"] * 40


def test_a_word_longer_than_the_limit_is_cut_in_the_middle():
    pieces = codec_tts.split_for_speech("a" * 130, 50)
    assert [len(p) for p in pieces] == [50, 50, 30]


@pytest.mark.parametrize("limit", [40, 80, 280, 400])
def test_no_piece_is_longer_than_the_limit_and_no_word_is_lost(limit):
    text = ("The weekly report is ready. Sales are up four percent on last week, and the breakfast service ran two minutes "
            "faster than the week before, which nobody expected after the long holiday weekend and the rota changes! "
            "Two suppliers sent late invoices; one delivery is still missing: it was due on Tuesday. Which would you like?")
    pieces = codec_tts.split_for_speech(text, limit)
    assert pieces and all(0 < len(p) <= limit for p in pieces)
    assert " ".join(pieces).split() == text.split()


# ── the stream ──────────────────────────────────────────────────────────────

def serve(chunks, *, status=200, ctype="audio/pcm", before=0.0, gap=0.0, fail_after=False, fail_before=False, closed=None, calls=None):
    """A mock transport handler that streams `chunks`."""
    async def body():
        try:
            if fail_before:
                raise httpx.ReadError("cut before any audio")
            if before:
                await asyncio.sleep(before)
            for i, c in enumerate(chunks):
                if i and gap:
                    await asyncio.sleep(gap)
                yield c
            if fail_after:
                raise httpx.ReadError("cut after audio")
        finally:
            if closed is not None:
                closed.append(True)

    def handler(request):
        if calls is not None:
            calls.append(request)
        return httpx.Response(status, headers={"content-type": ctype}, content=body())
    return handler


def run(coro):
    return asyncio.run(coro)


async def take_all(handler, live, text="Hello there."):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        return [c async for c in codec_tts.astream_clone(http, URL, text, live)]


def test_the_stream_yields_whole_samples_only(live):
    got = run(take_all(serve([b"\x01\x00\x02", b"\x00\x03\x00\x04\x00", b"\x05"]), live))
    assert all(len(c) % 2 == 0 for c in got)
    assert b"".join(got) == b"\x01\x00\x02\x00\x03\x00\x04\x00"       # the last lone byte is dropped, never played


def test_the_request_asks_for_a_pcm_stream_of_the_clone(live):
    calls = []
    run(take_all(serve([b"\x00\x00" * 10], calls=calls), live, text="Say this."))
    assert len(calls) == 1 and calls[0].method == "POST" and str(calls[0].url) == URL
    import json
    body = json.loads(calls[0].content)
    assert body == {"model": CLONE_MODEL, "input": "Say this.", "ref_audio": live["ref_audio"], "ref_text": live["ref_text"],
                    "lang_code": "auto", "response_format": "pcm", "stream": True, "streaming_interval": 0.5}


def test_a_bad_status_before_audio_is_unavailable(live):
    with pytest.raises(codec_tts.CloneUnavailable, match="http_500"):
        run(take_all(serve([b'{"detail":"x"}'], status=500, ctype="application/json"), live))


def test_an_answer_that_is_not_audio_is_unavailable(live):
    with pytest.raises(codec_tts.CloneUnavailable, match="not_audio"):
        run(take_all(serve([b'{"detail":"x"}'], ctype="application/json"), live))


def test_an_empty_body_is_unavailable(live):
    with pytest.raises(codec_tts.CloneUnavailable, match="empty"):
        run(take_all(serve([]), live))


def test_a_body_of_one_lone_byte_is_unavailable(live):
    with pytest.raises(codec_tts.CloneUnavailable, match="empty"):
        run(take_all(serve([b"\x01"]), live))


def test_no_first_byte_in_time_is_unavailable(live):
    live["first_byte_timeout"] = 0.2
    with pytest.raises(codec_tts.CloneUnavailable, match="timeout"):
        run(take_all(serve([b"\x00\x00"], before=2.0), live))


def test_silence_after_audio_has_started_is_broke(live):
    live["idle_timeout"] = 0.2
    with pytest.raises(codec_tts.CloneBroke, match="timeout"):
        run(take_all(serve([b"\x00\x00" * 4, b"\x00\x00" * 4], gap=2.0), live))


def test_the_first_byte_may_take_longer_than_the_idle_time(live):
    live["first_byte_timeout"] = 2.0
    live["idle_timeout"] = 0.2
    got = run(take_all(serve([b"\x00\x00" * 4], before=0.5), live))
    assert b"".join(got) == b"\x00\x00" * 4


def test_a_refused_connection_is_unavailable(live):
    def refuse(request):
        raise httpx.ConnectError("refused")
    with pytest.raises(codec_tts.CloneUnavailable, match="ConnectError"):
        run(take_all(refuse, live))


def test_a_cut_before_any_audio_is_unavailable(live):
    with pytest.raises(codec_tts.CloneUnavailable, match="ReadError"):
        run(take_all(serve([b"\x00\x00"], fail_before=True), live))


def test_a_cut_after_audio_is_broke(live):
    async def go():
        got = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(serve([b"\x00\x00" * 4], fail_after=True))) as http:
            with pytest.raises(codec_tts.CloneBroke, match="ReadError"):
                async for c in codec_tts.astream_clone(http, URL, "x", live):
                    got.append(c)
        return got
    assert run(go()) == [b"\x00\x00" * 4]       # what was heard before the cut was delivered


def test_leaving_the_stream_early_closes_the_connection(live):
    closed = []

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(serve([b"\x00\x00" * 4] * 50, gap=0.01, closed=closed))) as http:
            agen = codec_tts.astream_clone(http, URL, "x", live)
            first = await agen.__anext__()
            await agen.aclose()
            return first
    assert run(go()) == b"\x00\x00" * 4
    assert closed == [True], "the server side of the stream was closed"


def test_cancelling_the_reader_closes_the_connection(live):
    closed = []

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(serve([b"\x00\x00" * 4] * 50, gap=5.0, closed=closed))) as http:
            async def reader():
                async for _ in codec_tts.astream_clone(http, URL, "x", live):
                    pass
            task = asyncio.ensure_future(reader())
            await asyncio.sleep(0.2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    run(go())
    assert closed == [True]

# ── the warm-up ─────────────────────────────────────────────────────────────

class Reply:
    status_code = 200
    content = b"ID3" + b"\xff\xf3" * 100
    headers = {"content-type": "audio/mpeg"}


@pytest.fixture
def posts(monkeypatch):
    calls = []
    monkeypatch.setattr(codec_tts.requests, "post", lambda url, json=None, timeout=None: calls.append(json) or Reply())
    return calls


def test_the_warm_up_uses_the_clone_when_only_live_is_enabled(cfg, posts):
    cfg["tts_clone"]["enabled_for"] = ["live"]
    assert codec_tts.warm_up(URL, cfg) is True
    assert len(posts) == 1 and posts[0]["ref_audio"] == cfg["tts_clone"]["ref_audio"] and posts[0]["model"] == CLONE_MODEL


def test_the_warm_up_for_one_purpose_checks_only_that_purpose(cfg, posts):
    cfg["tts_clone"]["enabled_for"] = ["speak"]
    assert codec_tts.warm_up(URL, cfg, "live") is False and posts == []
    assert codec_tts.warm_up(URL, cfg, "speak") is True and len(posts) == 1


def test_the_warm_up_asks_nothing_when_no_purpose_is_enabled(cfg, posts):
    cfg["tts_clone"]["enabled_for"] = []
    assert codec_tts.warm_up(URL, cfg) is False and posts == []
    assert codec_tts.warm_up(URL, {}) is False and posts == []
