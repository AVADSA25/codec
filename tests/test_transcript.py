"""Test transcription post-processing (clean_transcript)"""
import sys
import os
sys.path.insert(0, os.path.expanduser("~/codec-repo"))


def test_import():
    try:
        from codec_keyboard import clean_transcript
    except ImportError:
        from codec_config import clean_transcript
    assert clean_transcript is not None


def test_hallucination_stripped():
    from codec_config import clean_transcript
    assert clean_transcript("thank you for watching") == ""
    assert clean_transcript("Thanks for listening") == ""
    assert clean_transcript("subscribe to my channel") == ""


def test_repeated_words():
    from codec_config import clean_transcript
    result = clean_transcript("the the quick brown fox")
    assert "the the" not in result


def test_codec_correction():
    from codec_config import clean_transcript
    result = clean_transcript("hey kodak what time is it")
    assert "CODEC" in result


def test_capitalization():
    from codec_config import clean_transcript
    result = clean_transcript("open safari please")
    assert result[0].isupper()


def test_punctuation():
    from codec_config import clean_transcript
    result = clean_transcript("open safari please")
    assert result[-1] in '.!?'


def test_empty_input():
    from codec_config import clean_transcript
    assert clean_transcript("") == ""
    assert clean_transcript(None) is None


def test_filler_word_stripped():
    from codec_config import clean_transcript
    result = clean_transcript("um open the browser")
    assert not result.lower().startswith("um")


def test_already_punctuated():
    from codec_config import clean_transcript
    result = clean_transcript("What time is it?")
    assert result.endswith("?")
    assert not result.endswith("?.")
