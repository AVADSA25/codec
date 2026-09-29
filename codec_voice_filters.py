"""CODEC Voice — pure-function filters + signal helpers.

B6-P4 / SR-35: extracted from codec_voice.py. These are stateless data
+ helpers used by the voice pipeline:

  - `NOISE_WORDS`           common conversational fillers Whisper produces
                            on near-silence (very high false-positive risk
                            otherwise).
  - `WHISPER_HALLUCINATIONS` YouTube outros, annotations, artifacts that
                            Whisper hallucinates from silence / background
                            noise (~30 phrases).
  - `is_noise(text)`        True if `text.strip().lower()` is in NOISE_WORDS.
  - `is_hallucination(text)`True if `text.lower()` contains any
                            WHISPER_HALLUCINATIONS phrase.
  - `rms_int16(chunk)`      Root-mean-square of an int16 PCM byte buffer.
                            Drives VAD silence detection.
  - `discard_reason(text)`  Why a whole Whisper transcript should be dropped
                            (noise word, hallucination, repetition) or None.
                            Used by dictation (routes/transcribe.py, P2.5).

codec_voice re-exports each name so any existing import keeps working.
Splitting these out lets the filter sets be tested + tuned without
standing up the full WebSocket pipeline.
"""
from __future__ import annotations

import re
from typing import Optional

import numpy as np


# Whisper noise filter — short utterances Whisper produces on near-
# silence. Comparing case-insensitively against the full stripped string.
NOISE_WORDS = {
    "you", "thank you", "thanks", "thanks for watching", "bye", "goodbye",
    "see you", "see you next time", "please subscribe", "like and subscribe",
    "", "hmm", "uh", "oh", "hm", "um", "yeah", "yep", "mm", "mhm",
    "okay", "ok", "right", "sure", "yes", "no", "hey", "hi", "hello",
    "so", "well", "um hmm", "uh huh", "ah", "er",
}

# Common Whisper hallucination phrases (YouTube outros, annotations,
# artifacts produced when the model receives silence or background
# noise). Substring match against the lowercased transcript.
WHISPER_HALLUCINATIONS = {
    "thank you for watching",
    "thanks for watching",
    "please subscribe",
    "like and subscribe",
    "see you next time",
    "see you in the next video",
    "thanks for listening",
    "thank you for listening",
    "please like and subscribe",
    "don't forget to subscribe",
    "hit the bell icon",
    "thank you very much",
    "thanks for your support",
    "subtitles by",
    "transcribed by",
    "translated by",
    "copyright",
    "all rights reserved",
    "music playing",
    "applause",
    "laughter",
    "silence",
    "inaudible",
    "foreign language",
    "speaking foreign language",
    "you",
    "bye",
    "okay bye",
    "so",
}


def is_noise(text: str) -> bool:
    """True if `text` is a Whisper noise-word artifact (full match)."""
    if text is None:
        return False
    return text.strip().lower() in NOISE_WORDS


def is_hallucination(text: str) -> bool:
    """True if `text` contains any known Whisper hallucination phrase
    (substring match against the lowercased text)."""
    if not text:
        return False
    low = text.lower()
    return any(p in low for p in WHISPER_HALLUCINATIONS)


# One fragment of 4+ characters three times in a row, with or without spaces
# or punctuation between the copies.
_REPEATED = re.compile(r"(.{4,}?)(?:[\s.,!?;:]*\1){2,}")


def discard_reason(text: str) -> Optional[str]:
    """Why a Whisper transcript should be dropped whole, or None to keep it.

    The voice pipeline's checks (codec_voice.VoicePipeline.transcribe), over
    the same word sets: the transcript is only a noise word, only a known
    hallucination, or one fragment of 4+ characters three times in a row.
    Two differences, both stricter on Whisper's silence artefacts: trailing
    punctuation is ignored for the set checks ("Thank you for watching."),
    and the copies may be separated by spaces or punctuation ("Thank you.
    Thank you. Thank you."). The voice pipeline's one-word rule is not here:
    a dictated single word is kept unless it is a noise word.
    """
    if text is None or not text.strip():
        return "empty"
    clean = text.strip().lower().rstrip(".!?, ")
    if clean in NOISE_WORDS:
        return "noise"
    if clean in WHISPER_HALLUCINATIONS:
        return "hallucination"
    if _REPEATED.search(text.strip().lower()):
        return "repetitive"
    return None


def rms_int16(chunk: bytes) -> float:
    """Root-mean-square of an int16 PCM byte buffer. Returns 0.0 for an
    empty / undersized buffer (avoids numpy noise on a half-sample edge).
    """
    if len(chunk) < 2:
        return 0.0
    samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(samples ** 2)))
