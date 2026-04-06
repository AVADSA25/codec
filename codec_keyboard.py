"""CODEC Keyboard — listener, wake word, recording, double-tap shortcuts"""
import os
import time
import tempfile
import subprocess
import threading
import logging

from pynput import keyboard as kb

from codec_config import (
    KEY_TOGGLE, KEY_VOICE, KEY_TEXT,
    WAKE_WORD, WAKE_PHRASES, WAKE_ENERGY, WAKE_CHUNK_SEC, WHISPER_URL,
    cfg, clean_transcript,
)

log = logging.getLogger('codec')

# Lock for thread-safe mutation of shared state fields
_state_lock = threading.Lock()



def start_keyboard_listener(state, ctx):
    """
    Start all keyboard listeners and the wake word thread.

    state: shared mutable dict (active, recording, rec_proc, audio_path,
           last_f13, last_minus, last_star, last_plus, screen_ctx, doc_ctx,
           rec_overlay)
    ctx:   dict of callbacks:
             push, dispatch, audit, transcribe, close_session,
             show_overlay, show_toggle_overlay, show_recording_overlay,
             show_processing_overlay,
             do_text, do_screenshot_question, do_document_input
    """
    push                  = ctx['push']
    dispatch              = ctx['dispatch']
    audit                 = ctx['audit']
    transcribe            = ctx['transcribe']
    close_session         = ctx['close_session']
    show_overlay          = ctx['show_overlay']
    show_toggle_overlay   = ctx['show_toggle_overlay']
    show_recording_overlay = ctx['show_recording_overlay']
    show_processing_overlay = ctx['show_processing_overlay']
    do_text               = ctx['do_text']
    do_screenshot_question = ctx['do_screenshot_question']
    do_document_input     = ctx['do_document_input']

    # ── Recording start/stop ──────────────────────────────────────────────────

    def do_start_recording():
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        state["audio_path"] = tmp.name
        tmp.close()
        rec = subprocess.Popen(
            ["sox", "-t", "coreaudio", "default", "-r", "16000", "-c", "1",
             "-b", "16", "-e", "signed-integer", state["audio_path"]],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        state["rec_proc"] = rec
        log.info("Recording...")

    def do_stop_voice():
        audio = state.get("audio_path")
        rec = state.get("rec_proc")
        if rec:
            try:
                rec.terminate()
                rec.wait(timeout=3)
            except Exception as e:
                log.warning(f"Non-critical error: {e}")
        state["rec_proc"] = None
        with _state_lock:
            state["recording"] = False
        if not audio or not os.path.exists(audio):
            return
        if os.path.getsize(audio) < 1000:
            try:
                os.unlink(audio)
            except Exception as e:
                log.warning(f"Non-critical error: {e}")
            return
        log.info("Transcribing...")
        if state.get('rec_overlay'):
            try:
                state['rec_overlay'].terminate()
            except Exception as e:
                log.warning(f"Non-critical error: {e}")
            state['rec_overlay'] = None
        push(lambda: show_processing_overlay('Transcribing...', 4000))
        task = transcribe(audio)
        if task:
            task = clean_transcript(task)
        if not task:
            log.info("No speech detected")
            return
        log.info(f"Heard: {task}")
        if state.get("screen_ctx"):
            task = task + " [SCREEN CONTEXT: " + state["screen_ctx"][:800] + "]"
            state["screen_ctx"] = ""
        dispatch(task)

    # ── Wake word listener ────────────────────────────────────────────────────

    def wake_word_listener():
        import sounddevice as sd
        import numpy as np
        import soundfile as sf
        import requests as req_wake
        sample_rate = 16000  # Whisper target rate

        # ── Detect native device sample rate to avoid CoreAudio errors ───────
        try:
            dev_info = sd.query_devices(sd.default.device[0], 'input')
            device_rate = int(dev_info['default_samplerate'])
        except Exception:
            device_rate = sample_rate
        need_resample = device_rate != sample_rate
        capture_rate = device_rate if need_resample else sample_rate
        chunk_samples = int(WAKE_CHUNK_SEC * capture_rate)
        if need_resample:
            log.info(f"Wake: device rate {device_rate}Hz, will resample to {sample_rate}Hz")

        def _resample(audio_data, from_rate, to_rate):
            """Simple linear resample from from_rate to to_rate."""
            if from_rate == to_rate:
                return audio_data
            ratio = to_rate / from_rate
            n_out = int(len(audio_data) * ratio)
            indices = np.linspace(0, len(audio_data) - 1, n_out).astype(int)
            return audio_data[indices]

        # ── Smoothed energy state (Fazm-inspired decay) ──────────────────────
        DECAY_RATE       = 0.85    # smoothing decay per chunk
        NOISE_FLOOR      = 30.0    # absolute floor — ignore mic noise below this
        MIN_SPEECH_FRAC  = 0.12    # at least 12% of samples must be above threshold
        CONFIDENCE_FLOOR = -1.0    # reject Whisper segments with avg_logprob below this
        smoothed_energy  = 0.0
        log.info(f"Wake word listener started (capture={capture_rate}Hz, whisper={sample_rate}Hz)")
        while True:
            if not WAKE_WORD or state["recording"] or not state["active"]:
                time.sleep(0.3)
                continue
            try:
                audio = sd.rec(chunk_samples, samplerate=capture_rate, channels=1, dtype='int16')
                sd.wait()
                # Resample to 16kHz for Whisper if device rate differs
                if need_resample:
                    audio = _resample(audio, capture_rate, sample_rate)

                # ── 1. Smoothed energy gate ───────────────────────────────────
                raw_energy = float(np.abs(audio).mean())
                smoothed_energy = max(raw_energy, smoothed_energy * DECAY_RATE)
                if smoothed_energy < max(WAKE_ENERGY, NOISE_FLOOR):
                    continue

                # ── 2. Minimum speech duration (≥12% of chunk above threshold)
                speech_fraction = float(np.mean(np.abs(audio) > WAKE_ENERGY * 0.4))
                if speech_fraction < MIN_SPEECH_FRAC:
                    log.debug(f"Wake: speech too short ({speech_fraction:.0%}), skipping")
                    continue

                tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
                tmp.close()
                sf.write(tmp.name, audio, sample_rate)
                try:
                    with open(tmp.name, "rb") as f:
                        r = req_wake.post(
                            WHISPER_URL,
                            files={"file": ("wake.wav", f, "audio/wav")},
                            data={"model": "mlx-community/whisper-large-v3-turbo", "language": "en"},
                            timeout=10)
                    if r.status_code != 200:
                        log.warning(f"Wake: Whisper returned HTTP {r.status_code}")
                    if r.status_code == 200:
                        resp_data = r.json()
                        text = resp_data.get("text", "").lower().strip()

                        # ── 3. Confidence filter from Whisper segments ────────
                        segments = resp_data.get("segments", [])
                        if segments:
                            avg_logprob = sum(s.get("avg_logprob", -0.5) for s in segments) / len(segments)
                            if avg_logprob < CONFIDENCE_FLOOR:
                                log.info(f"Wake: low confidence rejected (logprob={avg_logprob:.2f}): '{text}'")
                                continue

                        wake_phrases_lower = [p.lower() for p in WAKE_PHRASES]
                        if any(phrase in text for phrase in wake_phrases_lower):
                            command = text
                            for phrase in wake_phrases_lower:
                                command = command.replace(phrase, "").strip()
                            noise_words = ['music', 'yeah', 'baby', 'oh', 'la', 'da', 'na', 'hmm', 'ooh', 'ah', 'uh']

                            def _is_noise(txt):
                                words = txt.lower().split()
                                if len(words) < 2:
                                    return True
                                real = [w for w in words if len(w) > 2 and w not in noise_words]
                                return len(real) < 2  # tightened: require ≥2 real words

                            command = clean_transcript(command) or command
                            if len(command) > 3 and not _is_noise(command):
                                log.info(f"Wake + command: {command}")
                                audit("WAKE_CMD", command[:200])
                                push(lambda: show_overlay('Heard you!', '#E8711A', 1500))
                                push(lambda cmd=command: dispatch(cmd))
                            elif len(command) > 3:
                                log.info(f"Wake noise rejected: {command}")
                                audit("WAKE_NOISE", command[:200])
                            else:
                                log.info("Wake word detected! Listening...")
                                push(lambda: show_overlay('Listening...', '#E8711A', 5000))
                                full_audio = sd.rec(int(8 * capture_rate), samplerate=capture_rate, channels=1, dtype='int16')
                                sd.wait()
                                if need_resample:
                                    full_audio = _resample(full_audio, capture_rate, sample_rate)
                                tmp2 = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
                                tmp2.close()
                                sf.write(tmp2.name, full_audio, sample_rate)
                                task = transcribe(tmp2.name)
                                if task:
                                    task = clean_transcript(task)
                                if task and not _is_noise(task):
                                    log.info(f"Heard: {task}")
                                    audit("WAKE_TASK", task[:200])
                                    push(lambda t=task: dispatch(t))
                                elif task:
                                    log.info(f"Post-wake noise rejected: {task}")
                                    audit("WAKE_NOISE", task[:200])
                except Exception as e:
                    log.warning(f"Non-critical error: {e}")
                finally:
                    try:
                        os.unlink(tmp.name)
                    except Exception as e:
                        log.warning(f"Non-critical error: {e}")
            except Exception as e:
                log.warning(f"Wake word listener error: {e}")
                time.sleep(0.5)
            time.sleep(0.1)

    # ── Keyboard handlers ─────────────────────────────────────────────────────

    def on_press(key):
        now = time.time()
        if key == KEY_TOGGLE:
            # Mac F13 fires on_press for both keyDown and keyUp (pynput quirk).
            # Use a 1.5-second cooldown to prevent the second event from re-toggling.
            if now - state["last_f13"] < 1.5:
                return
            state["last_f13"] = now
            if state["active"]:
                with _state_lock:
                    state["active"] = False
                push(lambda: show_toggle_overlay(False, ''))
                push(close_session)
                log.info("OFF")
                try:
                    with open(os.path.expanduser("~/.codec/overlay_events.jsonl"), "a") as _f:
                        _f.write('{"type":"toggle_off"}\n')
                except Exception as e:
                    log.warning(f"Toggle off overlay event write failed: {e}")
            else:
                with _state_lock:
                    state["active"] = True
                push(lambda: show_toggle_overlay(
                    True,
                    cfg.get('key_voice', 'f18').upper() + '=voice  ' +
                    cfg.get('key_text', 'f16').upper() + '=text  **=screen  ++=doc  --=chat'
                ))
                log.info("ON -- " + cfg.get("key_voice", "f18").upper() +
                         "=voice | " + cfg.get("key_text", "f16").upper() +
                         "=text | *=screen | +=doc")
                try:
                    with open(os.path.expanduser("~/.codec/overlay_events.jsonl"), "a") as _f:
                        _f.write('{"type":"toggle_on"}\n')
                except Exception as e:
                    log.warning(f"Toggle on overlay event write failed: {e}")
            return
        if not state["active"]:
            return
        if key == KEY_TEXT:
            if not state["recording"]:
                push(do_text)
            return
        if key == KEY_VOICE:
            now_v = time.time()
            _kv_label = cfg.get('key_voice', 'f18').upper()
            if not state["recording"]:
                # First tap — start normal hold-to-record
                with _state_lock:
                    state["recording"] = True
                    state["ptt_locked"] = False
                state["last_f18_press"] = now_v
                try:
                    subprocess.run(["pkill", "-f", "C O D E C"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception as e:
                    log.warning(f"Non-critical error: {e}")
                push(do_start_recording)
                state['rec_overlay'] = show_recording_overlay(_kv_label)
            elif not state.get("ptt_locked"):
                # Second tap while recording (not yet locked)
                if now_v - state.get("last_f18_press", 0.0) < 0.5:
                    # Double-tap within 0.5s → lock mode
                    with _state_lock:
                        state["ptt_locked"] = True
                    state["last_f18_press"] = 0.0
                    if state.get('rec_overlay'):
                        try:
                            state['rec_overlay'].terminate()
                        except Exception as e:
                            log.warning(f"Non-critical error: {e}")
                    state['rec_overlay'] = show_overlay(
                        '\U0001f534 REC LOCKED \u2014 tap ' + _kv_label + ' to stop', '#ff3b3b', 0)
                    log.info("PTT locked")
            else:
                # Tap while locked → stop recording
                with _state_lock:
                    state["ptt_locked"] = False
                if state.get('rec_overlay'):
                    try:
                        state['rec_overlay'].terminate()
                    except Exception as e:
                        log.warning(f"Non-critical error: {e}")
                    state['rec_overlay'] = None
                push(do_stop_voice)
            return
        if hasattr(key, 'char') and key.char == '*':
            if now - state["last_star"] < 0.5:
                log.info("Star x2 -- screenshot mode")
                push(do_screenshot_question)
                state["last_star"] = 0.0
                return
            state["last_star"] = now
            return
        if hasattr(key, 'char') and key.char == '+':
            if now - state.get("last_plus", 0.0) < 0.5:
                log.info("Plus x2 -- document mode")
                push(do_document_input)
                state["last_plus"] = 0.0
                return
            state["last_plus"] = now
            return
        if hasattr(key, 'char') and key.char == '-':
            if now - state.get("last_minus", 0.0) < 0.5:
                log.info("Minus x2 -- live chat mode")
                voice_url = cfg.get("voice_url", "http://localhost:8090/voice?auto=1")
                push(lambda: show_overlay('Live Chat connecting...', '#E8711A', 3000))
                audit("LIVECHAT", voice_url)
                subprocess.Popen(["open", "-a", "Google Chrome", voice_url])
                state["last_minus"] = 0.0
                return
            state["last_minus"] = now
            return

    def on_release(key):
        if key == KEY_VOICE and state["recording"] and not state.get("ptt_locked"):
            if state.get('rec_overlay'):
                try:
                    state['rec_overlay'].terminate()
                except Exception as e:
                    log.warning(f"Non-critical error: {e}")
                state['rec_overlay'] = None
            push(do_stop_voice)

    # ── Start threads and listener loop ──────────────────────────────────────

    if WAKE_WORD:
        threading.Thread(target=wake_word_listener, daemon=True).start()

    while True:
        try:
            with kb.Listener(on_press=on_press, on_release=on_release) as listener:
                listener.join()
        except Exception as e:
            log.warning(f"Listener restarting: {e}")
            time.sleep(0.5)
