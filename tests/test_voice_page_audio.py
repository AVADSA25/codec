"""Reply audio on the voice page (static/voice-audio.js, docs/CODEC-VOICE-02-LIVE-DESIGN.md).

Node runs the player against a fake audio context (a clock, buffers, sources that record when they were started). The rules: whole
files and streamed voice 02 chunks play in arrival order, each starting exactly where the one before ends; a late chunk starts a
moment after "now", never in the past; an interrupt stops everything and ignores frames until the server's audio_reset (or a
time-out); one broken frame never blocks the ones after it; and the page is wired to the player, not to decodeAudioData.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import codec_voice_speech

REPO = Path(__file__).resolve().parent.parent
JS = REPO / "static" / "voice-audio.js"
SRC = JS.read_text(encoding="utf-8")
PAGE = (REPO / "codec_voice.html").read_text(encoding="utf-8")

node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

PRELUDE = r"""
function fakeCtx() {
  const ctx = {
    currentTime: 0, state: 'running', resumed: 0, sources: [], destination: { name: 'destination' },
    resume() { this.state = 'running'; this.resumed++; },
    createBuffer(ch, len, rate) { const data = new Float32Array(len); return { duration: len / rate, length: len, sampleRate: rate, getChannelData() { return data; } }; },
    createBufferSource() {
      const s = { buffer: null, started: null, stopped: false, connected: null, onended: null,
                  connect(n) { this.connected = n; }, start(t) { this.started = t; }, stop() { this.stopped = true; },
                  end() { if (this.onended) this.onended(); } };
      ctx.sources.push(s); return s;
    },
    /* a "file": byte 0 = length in tenths of a second, byte 1 = decode time in ms, 255 in byte 0 = cannot be decoded */
    decodeAudioData(ab) {
      const u8 = new Uint8Array(ab);
      return new Promise((res, rej) => setTimeout(() => u8[0] === 255 ? rej(new Error('cannot decode')) : res({ duration: u8[0] / 10 }), u8[1]));
    },
  };
  return ctx;
}
function pcm(seconds, rate = 24000, fill = 1000) {
  const n = Math.round(seconds * rate), buf = new ArrayBuffer(8 + n * 2), v = new DataView(buf);
  [0x43, 0x50, 0x43, 0x4d].forEach((b, i) => v.setUint8(i, b));
  v.setUint32(4, rate, true);
  for (let i = 0; i < n; i++) v.setInt16(8 + i * 2, fill, true);
  return buf;
}
function file(tenths, delayMs = 0, tag = 1) { const b = new Uint8Array(256); b[0] = tenths; b[1] = delayMs; b[128] = tag; return b.buffer; }
const flush = (ms = 60) => new Promise(r => setTimeout(r, ms));
function player(extra = {}) {
  const ctx = fakeCtx(), states = [], warns = [];
  const p = V.create(Object.assign({ ctx, onState: (on) => states.push(on), warn: (m) => warns.push(m) }, extra));
  return { ctx, p, states, warns };
}
const close = (a, b) => Math.abs(a - b) < 1e-9;
"""


def run(body: str):
    script = (f"const V = require({json.dumps(str(JS))}); {PRELUDE} "
              f"(async () => {{ {body} }})().then(r => console.log(JSON.stringify(r))).catch(e => {{ console.error(e); process.exit(1); }});")
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip())


# ── the frame ───────────────────────────────────────────────────────────────

@node
def test_a_pcm_frame_is_read_into_floats_at_its_own_rate():
    out = run("const a = V.parsePcm(pcm(0.001, 24000, 16384)), b = V.parsePcm(pcm(0.001, 16000, -32768)); "
              "return { rate: a.rate, n: a.samples.length, first: a.samples[0], rate2: b.rate, low: b.samples[0] };")
    assert out == {"rate": 24000, "n": 24, "first": 0.5, "rate2": 16000, "low": -1}


@node
def test_broken_frames_are_not_pcm_audio():
    out = run("""
      const hdr = (rate, extra) => { const b = new ArrayBuffer(8 + extra), v = new DataView(b); [0x43,0x50,0x43,0x4d].forEach((c,i)=>v.setUint8(i,c)); v.setUint32(4, rate, true); return b; };
      return [V.parsePcm(hdr(24000, 0)), V.parsePcm(hdr(24000, 1)), V.parsePcm(hdr(0, 8)), V.parsePcm(hdr(4000000000, 8)),
              V.parsePcm(file(5)), V.parsePcm(new ArrayBuffer(3)), V.parsePcm(null)];""")
    assert out == [None] * 7


@node
def test_only_the_cpcm_marker_makes_a_frame_pcm():
    out = run("""
      const mp3 = new Uint8Array([0x49, 0x44, 0x33, 4, 0, 0, 0, 0]).buffer, fff = new Uint8Array([0xff, 0xfb, 0x90, 0]).buffer,
            wav = new Uint8Array([0x52, 0x49, 0x46, 0x46, 0, 0, 0, 0]).buffer;
      const near = new Uint8Array([0x43, 0x50, 0x43, 0x58, 0, 0, 0, 0, 0, 0]).buffer;          // "CPCX": close, but not the marker
      return [V.isPcm(pcm(0.01)), V.isPcm(mp3), V.isPcm(fff), V.isPcm(wav), V.isPcm(new ArrayBuffer(2)), V.isPcm(null), V.isPcm(near)];""")
    assert out == [True, False, False, False, False, False, False]


def test_the_server_and_the_page_agree_on_the_frame_layout():
    frame = codec_voice_speech.pcm_frame(b"\x00\x40\x00\xc0", 24000)           # samples 16384 and -16384
    out = run(f"const b = new Uint8Array({list(frame)}).buffer; const p = V.parsePcm(b); return {{ pcm: V.isPcm(b), rate: p.rate, s: Array.from(p.samples) }};")
    assert out == {"pcm": True, "rate": 24000, "s": [0.5, -0.5]}


# ── playing in order, back to back ──────────────────────────────────────────

@node
def test_streamed_chunks_start_exactly_where_the_one_before_ends():
    out = run("""
      const { ctx, p } = player(); ctx.currentTime = 10;
      p.push(pcm(0.5)); p.push(pcm(0.5)); p.push(pcm(0.25));
      await flush();
      const s = ctx.sources.map(x => x.started);
      return { n: s.length, first: close(s[0], 10 + V.LEAD), second: close(s[1], s[0] + 0.5), third: close(s[2], s[1] + 0.5), out: ctx.sources[0].connected === ctx.destination };""")
    assert out == {"n": 3, "first": True, "second": True, "third": True, "out": True}


@node
def test_a_chunk_that_arrives_late_starts_a_moment_after_now_never_in_the_past():
    out = run("""
      const { ctx, p } = player(); ctx.currentTime = 10;
      p.push(pcm(0.5)); await flush();
      ctx.currentTime = 20;                              // the stream stalled for ten seconds
      p.push(pcm(0.5)); await flush();
      return close(ctx.sources[1].started, 20 + V.LEAD);""")
    assert out is True


@node
def test_whole_files_play_in_arrival_order_even_when_a_later_one_decodes_first():
    out = run("""
      const { ctx, p } = player(); ctx.currentTime = 1;
      p.push(file(5, 80, 1)); p.push(file(3, 0, 2));    // the first takes 80 ms to decode, the second none
      await flush(200);
      const d = ctx.sources.map(x => x.buffer.duration), s = ctx.sources.map(x => x.started);
      return { d, back_to_back: close(s[1], s[0] + 0.5) };""")
    assert out == {"d": [0.5, 0.3], "back_to_back": True}


@node
def test_files_and_streamed_chunks_mixed_in_one_reply_keep_their_order_and_join_up():
    out = run("""
      const { ctx, p } = player(); ctx.currentTime = 1;
      p.push(pcm(0.4)); p.push(file(5, 30, 1)); p.push(pcm(0.2));
      await flush(200);
      const d = ctx.sources.map(x => +x.buffer.duration.toFixed(3)), s = ctx.sources.map(x => x.started);
      return { d, joined: close(s[1], s[0] + 0.4) && close(s[2], s[1] + 0.5) };""")
    assert out == {"d": [0.4, 0.5, 0.2], "joined": True}


# ── playing or not ──────────────────────────────────────────────────────────

@node
def test_it_is_playing_from_the_first_frame_until_the_last_source_has_ended():
    out = run("""
      const { ctx, p, states } = player();
      const before = p.isPlaying();
      p.push(pcm(0.3)); p.push(pcm(0.3)); await flush();
      const during = p.isPlaying();
      ctx.sources[0].end(); const afterFirst = p.isPlaying();
      ctx.sources[1].end();
      return { before, during, afterFirst, after: p.isPlaying(), states };""")
    assert out == {"before": False, "during": True, "afterFirst": True, "after": False, "states": [True, False]}


@node
def test_a_source_ending_while_a_file_is_still_decoding_does_not_mean_silence():
    out = run("""
      const { ctx, p, states } = player(); ctx.currentTime = 1;
      p.push(pcm(0.2)); p.push(file(4, 60)); await flush(10);
      ctx.sources[0].end();                              // the first chunk ended, the file is not decoded yet
      const mid = p.isPlaying();
      await flush(200);
      ctx.sources[1].end();
      return { mid, states, last: p.isPlaying() };""")
    assert out == {"mid": True, "states": [True, False], "last": False}


@node
def test_a_new_reply_after_silence_starts_at_now_and_not_at_the_old_end():
    out = run("""
      const { ctx, p } = player(); ctx.currentTime = 5;
      p.push(pcm(1)); await flush(); ctx.sources[0].end();
      ctx.currentTime = 50;
      p.push(pcm(1)); await flush();
      return close(ctx.sources[1].started, 50 + V.LEAD);""")
    assert out is True


# ── interrupting ────────────────────────────────────────────────────────────

@node
def test_stop_silences_everything_and_work_in_flight_never_plays():
    out = run("""
      const { ctx, p, states } = player(); ctx.currentTime = 1;
      p.push(pcm(0.5)); p.push(file(5, 80)); await flush(10);
      p.stop();
      const stopped = ctx.sources.map(x => x.stopped);
      await flush(200);
      const count = ctx.sources.length;                  // the file that was decoding must not be scheduled afterwards
      ctx.sources[0].end();                              // a late "ended" from a stopped source changes nothing
      p.push(pcm(0.5)); await flush();
      return { stopped, count, playing: p.isPlaying(), restart: close(ctx.sources[1].started, 1 + V.LEAD), states };""")
    assert out == {"stopped": [True], "count": 1, "playing": True, "restart": True, "states": [True, False, True]}


@node
def test_after_an_interrupt_frames_are_ignored_until_the_server_resets():
    out = run("""
      const { ctx, p } = player();
      p.push(pcm(0.5)); await flush();
      p.beginInterrupt();
      const dropped = [p.push(pcm(0.5)), p.push(file(5))];
      await flush();
      const none = ctx.sources.filter(x => !x.stopped).length;
      p.reset();
      const accepted = p.push(pcm(0.5)); await flush();
      return { dropped, none, accepted, dropping: p.isDropping(), live: ctx.sources.filter(x => !x.stopped).length };""")
    assert out == {"dropped": [False, False], "none": 0, "accepted": True, "dropping": False, "live": 1}


@node
def test_a_server_that_never_answers_does_not_keep_the_page_deaf():
    out = run("""
      const timers = [];
      const { ctx, p } = player({ setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; }, clearTimeout: () => {} });
      p.beginInterrupt();
      const during = p.push(pcm(0.5));
      timers[0].fn();                                    // the time-out fires
      const after = p.push(pcm(0.5));
      return { ms: timers[0].ms, during, after, dropping: p.isDropping() };""")
    assert out == {"ms": 1500, "during": False, "after": True, "dropping": False}


@node
def test_a_second_interrupt_restarts_the_time_out_instead_of_stacking_them():
    out = run("""
      const timers = [], cleared = [];
      const { p } = player({ setTimeout: (fn, ms) => { timers.push(fn); return timers.length; }, clearTimeout: (id) => cleared.push(id) });
      p.beginInterrupt(); p.beginInterrupt();
      p.reset();
      return { timers: timers.length, cleared };""")
    assert out == {"timers": 2, "cleared": [1, 2]}


# ── trouble ─────────────────────────────────────────────────────────────────

@node
def test_a_broken_chunk_is_skipped_and_the_next_one_plays():
    out = run("""
      const { ctx, p, warns } = player();
      const broken = pcm(0.3, 0);                        // a rate of 0
      p.push(broken); p.push(pcm(0.3)); await flush();
      return { sources: ctx.sources.length, warns: warns.length };""")
    assert out == {"sources": 1, "warns": 1}


@node
def test_a_file_that_cannot_be_decoded_does_not_block_the_frames_after_it():
    out = run("""
      const { ctx, p, warns, states } = player();
      p.push(file(255, 0)); p.push(pcm(0.3)); await flush(100);
      const playing = p.isPlaying();
      ctx.sources[0].end();
      return { sources: ctx.sources.length, warned: warns.length === 1 && /decode/.test(warns[0]), playing, states };""")
    assert out == {"sources": 1, "warned": True, "playing": True, "states": [True, False]}


@node
def test_when_only_a_bad_file_arrived_the_player_goes_quiet_again():
    out = run("const { p, states } = player(); p.push(file(255, 0)); await flush(100); return { playing: p.isPlaying(), states };")
    assert out == {"playing": False, "states": [True, False]}


@node
def test_a_repeated_file_frame_is_skipped_while_it_plays_but_streamed_chunks_never_are():
    out = run("""
      const { ctx, p } = player();
      const a = p.push(file(5, 0, 7)), b = p.push(file(5, 0, 7)), c = p.push(file(5, 0, 8));
      const x = p.push(pcm(0.1)), y = p.push(pcm(0.1));
      await flush(100);
      return { a, b, c, x, y, sources: ctx.sources.length };""")
    assert out == {"a": True, "b": False, "c": True, "x": True, "y": True, "sources": 4}


@node
def test_a_suspended_context_is_resumed_and_empty_frames_are_ignored():
    out = run("""
      const { ctx, p } = player(); ctx.state = 'suspended';
      const empty = [p.push(new ArrayBuffer(0)), p.push(null), p.push(undefined)];
      p.push(pcm(0.1)); await flush();
      return { empty, resumed: ctx.resumed };""")
    assert out == {"empty": [False, False, False], "resumed": 1}


@node
def test_the_playback_context_is_its_own_at_24_khz_and_falls_back_to_the_microphones():
    out = run("""
      const made = [];
      function Ok(o) { made.push(o.sampleRate); this.kind = 'own'; }
      function Refuses() { throw new Error('rate not supported'); }
      const fallback = { kind: 'microphone' };
      return [V.makeContext(Ok, 24000, fallback).kind, made, V.makeContext(Refuses, 24000, fallback).kind, V.makeContext(undefined, 24000, fallback).kind];""")
    assert out == ["own", [24000], "microphone", "microphone"]


# ── the page is wired to the player ─────────────────────────────────────────

def test_the_script_is_stamped_and_loaded_before_the_page_script_uses_it():
    tag = re.search(r'<script src="/static/voice-audio\.js\?v=[0-9a-f]{8}"></script>', PAGE)
    assert tag and tag.start() < PAGE.index("function queueAudio(")
    from tools import stamp_static
    assert "voice-audio.js" in stamp_static.STAMPED
    assert stamp_static.main(True) == 0


def test_the_page_no_longer_decodes_or_queues_audio_itself():
    for gone in ("decodeAudioData", "audioQueue", "curSource", "playNext"):
        assert gone not in PAGE, gone
    assert "player.push(data)" in PAGE


def test_the_page_says_it_can_play_the_stream_and_keeps_the_resume_parameter():
    assert "'//' + location.host + '/ws/voice?pcm=1'" in PAGE
    assert "url += '&resume=' + encodeURIComponent(wsSessionId);" in PAGE


def test_the_reply_plays_in_its_own_24_khz_context_and_the_microphone_stays_at_16():
    assert "var PLAY_RATE   = 24000;" in PAGE
    assert "audioCtx = new Ctx({ sampleRate: 16000 });" in PAGE
    assert "playCtx = VoiceAudio.makeContext(Ctx, PLAY_RATE, audioCtx);" in PAGE
    assert "outAnalyser = playCtx.createAnalyser();" in PAGE and "outAnalyser.connect(playCtx.destination);" in PAGE


def test_an_interrupt_starts_the_drop_and_the_servers_audio_reset_ends_it():
    stop = PAGE[PAGE.index("function stopAudio()"):PAGE.index("function onPlayState(")]
    assert "player.beginInterrupt()" in stop
    assert "msg.type === 'audio_reset'" in PAGE and "player.reset()" in PAGE
    mic = PAGE[PAGE.index("Interrupt detection: if Q is speaking"):PAGE.index("var i16 = new Int16Array")]
    assert "ws.send(JSON.stringify({ type: 'interrupt' }));" in mic and "stopAudio();" in mic


def test_leaving_the_call_stops_the_player_and_closes_its_own_context():
    stop = PAGE[PAGE.index("function stopMic()"):PAGE.index("The picker lists the audio inputs")]
    assert "player.stop()" in stop and "playCtx.close()" in stop and "playCtx !== audioCtx" in stop


def test_the_state_follows_the_player():
    fn = PAGE[PAGE.index("function onPlayState("):PAGE.index("/* ── WebSocket with auto-reconnect")]
    assert "isPlaying = on;" in fn and "setState('speaking')" in fn and "setState('listening')" in fn and "levelLoop" in fn


def test_the_player_script_never_builds_html_or_evaluates_text():
    for banned in ("innerHTML", "eval(", "new Function", "document.write"):
        assert banned not in SRC, banned
