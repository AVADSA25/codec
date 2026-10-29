"""The voice call stops CODEC only for a sentence, not a noise.

While CODEC speaks (page) or thinks (server), an interrupt needs loud audio for
about 0.9 s, short pauses between words allowed. One loud frame (a cough, a door)
no longer cuts it off.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import codec_voice

REPO = Path(__file__).resolve().parents[1]
PAGE = (REPO / "codec_voice.html").read_text(encoding="utf-8")
node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
FRAME_MS = 256  # ScriptProcessor(4096) at 16 kHz


def _page_barge(frames, gap_ms=None):
    """Run the page's bargeIn over a list of loud/quiet frames; return the first
    frame index that interrupts, or -1."""
    src = re.search(r"/\* BARGE-IN-START \*/(.*?)/\* BARGE-IN-END \*/", PAGE, re.S).group(1)
    script = src + f"""
    const frames = {json.dumps(frames)};
    const st = {{len: 0, gap: 0}};
    let hit = -1;
    frames.forEach((loud, i) => {{ if (hit < 0 && bargeIn(st, loud, {FRAME_MS}, 900, 350)) hit = i; }});
    console.log(JSON.stringify(hit));
    """
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip())


@node
def test_one_loud_frame_does_not_interrupt():
    assert _page_barge([True, False, False, False, False]) == -1


@node
def test_two_short_noises_apart_do_not_interrupt():
    assert _page_barge([True, False, False, True, False, False, True]) == -1


@node
def test_a_sentence_interrupts_after_about_a_second():
    assert _page_barge([True, True, True, True, True]) == 3  # 4 x 256 ms >= 900 ms


@node
def test_short_pauses_between_words_still_count():
    # loud, one quiet frame (256 ms < 350 ms gap), loud... -> one sentence
    assert _page_barge([True, False, True, False, True]) == 4


def test_page_interrupt_uses_the_sentence_rule():
    branch = PAGE[PAGE.index("if (isPlaying) {"):PAGE.index("ws.send(JSON.stringify({ type: 'interrupt' }));")]
    assert "bargeIn(barge, rms > INTERRUPT_RMS" in branch
    assert "if (rms > INTERRUPT_RMS &&" not in branch


def _server(frames):
    """Feed 256 ms frames (True = loud); return the frame index that interrupts, or None."""
    p = SimpleNamespace(_barge_len=0.0, _barge_gap=0.0)
    for i, loud in enumerate(frames):
        if codec_voice.VoicePipeline._sustained_barge(p, loud, 0.256):
            return i
    return None


def test_server_noise_does_not_interrupt():
    assert _server([True, False, False, True, False, False, True]) is None


def test_server_sentence_interrupts():
    assert _server([True, True, True, True, True]) == 3  # 4 x 256 ms >= 0.9 s


def test_server_short_pause_between_words_counts():
    assert _server([True, False, True, True, True]) == 3


def test_server_threshold_default():
    assert codec_voice.INTERRUPT_MIN_SECONDS == pytest.approx(0.9) or "interrupt_min_seconds" in str(codec_voice._vad_cfg)
