"""Record the microphone: SoX when it is installed, sounddevice when it is not.

A buyer's Mac has no SoX (Homebrew, GPL, never bundled), so wake word, push-to-talk and
dictation need a recorder that ships with the app (audit P-033, D-35). The rule is the
Studio's path stays first: when `sox` is found, every call runs exactly the command line
it ran before; sounddevice (already in the buyer lock) is used only when it is not.

Three shapes, matching the three ways CODEC records:
  start_recording(path)                      open-ended; a handle with terminate()/wait()/poll()
  record_chunk(path, seconds)                a fixed length (wake-word polling, live dictation)
  record_until_silence(path, max_seconds)    stops 1.5 s after speech ends (wake follow-up)
All write 16-bit mono WAV.
"""
import os
import shutil
import subprocess
import threading

SOX_CANDIDATES = ("/opt/homebrew/bin/sox", "/usr/local/bin/sox")
BLOCK_SECONDS = 0.1          # sounddevice read size
SILENCE_THRESHOLD = 0.02     # 2% of full scale, the same figure as the sox silence effect
SILENCE_STOP_SECONDS = 1.5


def sox_command():
    """Path of the sox program, or None. PATH first, then the Homebrew folders (a launchd
    or PM2 service often has no Homebrew on its PATH)."""
    found = shutil.which("sox")
    if found:
        return found
    for cand in SOX_CANDIDATES:
        if os.path.exists(cand):
            return cand
    return None


def _sox_input(rate, device):
    return ["-t", "coreaudio", device, "-r", str(rate), "-c", "1", "-b", "16", "-e", "signed-integer"]


# ── sounddevice side ─────────────────────────────────────────────────────────
def _input_device(sd, name):
    """sounddevice index for a sox coreaudio device name; None means the default input."""
    if not name or name == "default":
        return None
    try:
        for i, dev in enumerate(sd.query_devices()):
            if dev.get("max_input_channels", 0) > 0 and name.lower() in str(dev.get("name", "")).lower():
                return i
    except Exception:
        pass
    return None


def _open_stream(rate, device):
    import sounddevice as sd
    stream = sd.InputStream(samplerate=rate, channels=1, dtype="int16",
                            device=_input_device(sd, device))
    stream.start()
    return stream


def _read_block(stream, rate):
    data, _overflowed = stream.read(int(rate * BLOCK_SECONDS))
    return data.reshape(-1)


def _rms(block):
    import numpy as np
    if block.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(block.astype("float64") ** 2))) / 32768.0


def _wav(path, rate):
    import soundfile as sf
    return sf.SoundFile(path, mode="w", samplerate=rate, channels=1, subtype="PCM_16", format="WAV")


class _StreamRecording:
    """Open-ended recording with the part of the Popen interface the callers use."""

    def __init__(self, path, rate, device):
        self._stop = threading.Event()
        self._error = None
        self._stream = _open_stream(rate, device)
        self._thread = threading.Thread(target=self._run, args=(path, rate), daemon=True,
                                        name="codec-record")
        self._thread.start()

    def _run(self, path, rate):
        try:
            with _wav(path, rate) as out:
                while not self._stop.is_set():
                    out.write(_read_block(self._stream, rate))
        except Exception as e:                       # the file stays as far as it got
            self._error = e
        finally:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass

    def poll(self):
        return None if self._thread.is_alive() else (1 if self._error else 0)

    def terminate(self):
        self._stop.set()

    kill = terminate

    def wait(self, timeout=None):
        self._thread.join(timeout)
        if self._thread.is_alive():
            raise subprocess.TimeoutExpired("codec_record", timeout)
        return self.poll()


# ── public API ───────────────────────────────────────────────────────────────
def start_recording(path, rate=16000, device="default"):
    """Start recording to `path` until terminate(). Returns a Popen (sox) or a stand-in."""
    sox = sox_command()
    if sox:
        return subprocess.Popen([sox, *_sox_input(rate, device), path],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return _StreamRecording(path, rate, device)


def record_chunk(path, seconds, rate=16000, device="default"):
    """Record `seconds` of audio to `path`, then return."""
    sox = sox_command()
    if sox:
        subprocess.run([sox, *_sox_input(rate, device), path, "trim", "0", str(seconds)],
                       timeout=int(seconds) + 3, capture_output=True)
        return
    stream = _open_stream(rate, device)
    try:
        with _wav(path, rate) as out:
            for _ in range(max(1, round(seconds / BLOCK_SECONDS))):
                out.write(_read_block(stream, rate))
    finally:
        stream.stop()
        stream.close()


def record_until_silence(path, max_seconds, rate=16000, device="default"):
    """Record until the speaker stops: waits for speech, then ends SILENCE_STOP_SECONDS after
    the last loud block, or at `max_seconds`. Leading silence is dropped, like sox's silence
    effect does, so an ongoing sentence is captured from its first word."""
    sox = sox_command()
    if sox:
        subprocess.run([sox, *_sox_input(rate, device), path, "trim", "0", str(max_seconds),
                        "silence", "1", "0.1", "2%", "1", str(SILENCE_STOP_SECONDS), "2%"],
                       timeout=int(max_seconds) + 4, capture_output=True)
        return
    stream = _open_stream(rate, device)
    quiet_blocks_to_stop = round(SILENCE_STOP_SECONDS / BLOCK_SECONDS)
    spoke, quiet = False, 0
    try:
        with _wav(path, rate) as out:
            for _ in range(max(1, round(max_seconds / BLOCK_SECONDS))):
                block = _read_block(stream, rate)
                if _rms(block) >= SILENCE_THRESHOLD:
                    spoke, quiet = True, 0
                elif spoke:
                    quiet += 1
                if spoke:
                    out.write(block)
                    if quiet >= quiet_blocks_to_stop:
                        break
    finally:
        stream.stop()
        stream.close()
