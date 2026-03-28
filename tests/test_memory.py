"""Test FTS5 memory system"""
import pytest
import sys
import os

sys.path.insert(0, os.path.expanduser("~/codec-repo"))


def test_memory_import():
    from codec_memory import CodecMemory
    assert CodecMemory is not None


def test_memory_connect():
    from codec_memory import CodecMemory
    mem = CodecMemory()
    assert mem.db_path is not None


def test_memory_search():
    from codec_memory import CodecMemory
    mem = CodecMemory()
    # Should not crash even with no results
    results = mem.search("test query that probably has no match xyz123")
    assert isinstance(results, list)


def test_memory_get_sessions():
    from codec_memory import CodecMemory
    mem = CodecMemory()
    sessions = mem.get_sessions(5)
    assert isinstance(sessions, list)


def test_memory_get_context():
    from codec_memory import CodecMemory
    mem = CodecMemory()
    ctx = mem.get_context("test", n=5)
    assert isinstance(ctx, str)
