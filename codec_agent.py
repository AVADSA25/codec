"""CODEC Agent — session script builder and subprocess runner"""
import os
import sys
import json
import tempfile
import subprocess
import logging

from codec_config import (
    QWEN_BASE_URL, QWEN_MODEL, QWEN_VISION_URL, QWEN_VISION_MODEL,
    TTS_VOICE, LLM_API_KEY, LLM_KWARGS, LLM_PROVIDER,
    TTS_ENGINE, KOKORO_URL, KOKORO_MODEL,
    DB_PATH, TASK_QUEUE_FILE, SESSION_ALIVE,
    STREAMING, cfg,
)

log = logging.getLogger('codec')


def build_session_params(safe_sys, session_id):
    """Build parameter dict for codec_session.Session — new module-based approach."""
    return {
        "sys_msg": safe_sys,
        "session_id": session_id,
        "qwen_base_url": QWEN_BASE_URL,
        "qwen_model": QWEN_MODEL,
        "qwen_vision_url": QWEN_VISION_URL,
        "qwen_vision_model": QWEN_VISION_MODEL,
        "tts_voice": TTS_VOICE,
        "llm_api_key": LLM_API_KEY,
        "llm_kwargs": LLM_KWARGS,
        "llm_provider": LLM_PROVIDER,
        "tts_engine": TTS_ENGINE,
        "kokoro_url": KOKORO_URL,
        "kokoro_model": KOKORO_MODEL,
        "db_path": DB_PATH,
        "task_queue": TASK_QUEUE_FILE,
        "session_alive": SESSION_ALIVE,
        "streaming": STREAMING,
        "agent_name": cfg.get("agent_name", "C"),
        "key_voice": cfg.get("key_voice", "f18"),
        "key_text": cfg.get("key_text", "f16"),
    }


def run_session_module(safe_sys, session_id, task, timeout=120):
    """Run session using the new codec_session module in a subprocess."""
    params = build_session_params(safe_sys, session_id)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(params, f)
        params_path = f.name
    try:
        repo = os.path.dirname(os.path.abspath(__file__))
        result = subprocess.run(
            [sys.executable, "-c", f"""
import sys, json
sys.path.insert(0, {repr(repo)})
from codec_session import Session
with open({repr(params_path)}) as _pf: params = json.load(_pf)
s = Session(**params)
s.run()
"""],
            text=True,
            timeout=timeout,
            env={**os.environ, "CODEC_TASK": task},
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        log.warning(f"Session module timed out after {timeout}s for task: {task[:60]}")
        return False
    except Exception as e:
        log.error(f"Session module error: {e}")
        return False
    finally:
        try:
            os.unlink(params_path)
        except Exception:
            pass
