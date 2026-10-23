/* Reply audio of the voice call (docs/CODEC-VOICE-02-LIVE-DESIGN.md).

   The server sends the reply as WebSocket binary frames of two kinds, and this player plays both in the order they arrive,
   each one starting exactly where the one before it ends on the audio clock, so there are no gaps between sentences or chunks:
     - a whole audio file (the Kokoro MP3): decoded with decodeAudioData;
     - a streamed chunk of voice 02: "CPCM" + the sample rate (4 bytes, little endian) + signed 16-bit little-endian mono samples.
   An interrupt stops everything and then ignores frames until the server's audio_reset (or a short time-out), so chunks that were
   already on the way cannot play after the owner has spoken.

   Plain functions and one factory, so Node can test them with a fake audio context (tests/test_voice_page_audio.py). */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.VoiceAudio = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var HEADER = 8;               /* "CPCM" + the sample rate */
  var LEAD = 0.03;              /* seconds between "now" and the start of audio that is late */
  var DROP_MS = 1500;           /* how long frames are ignored after an interrupt if the server never answers */
  var MIN_RATE = 8000, MAX_RATE = 96000;

  function isPcm(data) {
    if (!data || data.byteLength < 4) return false;
    var b = new Uint8Array(data, 0, 4);
    return b[0] === 0x43 && b[1] === 0x50 && b[2] === 0x43 && b[3] === 0x4d;
  }

  /* -> { rate, samples: Float32Array } or null when the frame is broken. */
  function parsePcm(data) {
    if (!isPcm(data) || data.byteLength < HEADER + 2) return null;
    var view = new DataView(data);
    var rate = view.getUint32(4, true);
    if (rate < MIN_RATE || rate > MAX_RATE) return null;
    var n = Math.floor((data.byteLength - HEADER) / 2);
    var samples = new Float32Array(n);
    for (var i = 0; i < n; i++) samples[i] = view.getInt16(HEADER + i * 2, true) / 32768;
    return { rate: rate, samples: samples };
  }

  /* The context the reply plays through: its own, at the rate the voice was made at, or the fallback (the microphone's). */
  function makeContext(Ctor, rate, fallback) {
    try {
      if (Ctor) return new Ctor({ sampleRate: rate });
    } catch (e) { /* the browser refused this rate */ }
    return fallback;
  }

  function create(opts) {
    var ctx = opts.ctx;
    var out = opts.out || ctx.destination;
    var onState = opts.onState || function () {};
    var setTimer = opts.setTimeout || setTimeout;
    var clearTimer = opts.clearTimeout || clearTimeout;
    var dropMs = opts.dropMs || DROP_MS;
    var warn = opts.warn || function () {};

    var playHead = 0;           /* audio-clock time at which what is scheduled so far ends */
    var epoch = 0;              /* bumped by stop(): work that started before it is discarded */
    var pending = 0;            /* frames received and not yet scheduled (this epoch) */
    var live = [];              /* sources scheduled or playing */
    var playing = false;
    var dropping = false;
    var dropTimer = null;
    var lastFile = null;
    var chain = Promise.resolve();

    function setPlaying(v) {
      if (v !== playing) { playing = v; onState(v); }
    }
    function settle() {
      if (!live.length && !pending) { playHead = 0; lastFile = null; setPlaying(false); }
    }

    function schedule(buf) {
      var when = Math.max(playHead, ctx.currentTime + LEAD);
      var src = ctx.createBufferSource();
      src.buffer = buf;
      src.connect(out);
      src.onended = function () {
        var i = live.indexOf(src);
        if (i >= 0) live.splice(i, 1);
        settle();
      };
      live.push(src);
      src.start(when);
      playHead = when + buf.duration;
    }

    function handle(data, mine) {
      if (mine !== epoch) return null;
      if (isPcm(data)) {
        var pcm = parsePcm(data);
        if (!pcm) { warn('broken PCM frame skipped'); return null; }
        var buf = ctx.createBuffer(1, pcm.samples.length, pcm.rate);
        buf.getChannelData(0).set(pcm.samples);
        schedule(buf);
        return null;
      }
      return ctx.decodeAudioData(data.slice(0)).then(function (decoded) {
        if (mine === epoch) schedule(decoded);
      });
    }

    function duplicate(data) {
      if (!lastFile || !playing || lastFile.byteLength !== data.byteLength) return false;
      var a = new Uint8Array(lastFile), b = new Uint8Array(data);
      for (var i = 0; i < a.length; i += 64) { if (a[i] !== b[i]) return false; }
      return true;
    }

    /* A frame from the server. False when it was ignored. */
    function push(data) {
      if (dropping || !data || !data.byteLength) return false;
      var pcm = isPcm(data);
      if (!pcm && duplicate(data)) return false;
      if (!pcm) lastFile = data;
      if (ctx.state === 'suspended' && ctx.resume) { try { ctx.resume(); } catch (e) { /* needs a gesture */ } }
      var mine = epoch;
      pending++;
      setPlaying(true);
      chain = chain
        .then(function () { return handle(data, mine); })
        .catch(function (e) { warn('audio frame skipped: ' + (e && e.message ? e.message : e)); })
        .then(function () {
          if (mine === epoch) { pending--; settle(); }
        });
      return true;
    }

    function stop() {
      epoch++;
      var gone = live;
      live = [];
      gone.forEach(function (s) {
        s.onended = null;
        try { s.stop(); } catch (e) { /* already ended */ }
      });
      pending = 0;
      playHead = 0;
      lastFile = null;
      setPlaying(false);
    }

    /* The owner interrupted: stop at once and ignore frames until reset() (or the time-out, for a server that never answers). */
    function beginInterrupt() {
      stop();
      dropping = true;
      if (dropTimer) clearTimer(dropTimer);
      dropTimer = setTimer(function () { dropping = false; dropTimer = null; }, dropMs);
    }

    /* The server's audio_reset: stop, and accept frames again. */
    function reset() {
      stop();
      dropping = false;
      if (dropTimer) { clearTimer(dropTimer); dropTimer = null; }
    }

    return {
      push: push, stop: stop, beginInterrupt: beginInterrupt, reset: reset,
      isPlaying: function () { return playing; },
      isDropping: function () { return dropping; }
    };
  }

  return { create: create, parsePcm: parsePcm, isPcm: isPcm, makeContext: makeContext, LEAD: LEAD, DROP_MS: DROP_MS };
});
