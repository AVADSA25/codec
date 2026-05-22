"""CODEC Agent — session launcher and subprocess runner"""
import os
import json
import tempfile
import subprocess
import logging

from codec_config import (
    QWEN_BASE_URL, QWEN_MODEL, QWEN_VISION_URL, QWEN_VISION_MODEL,
    TTS_VOICE, LLM_API_KEY, LLM_KWARGS, LLM_PROVIDER,
    TTS_ENGINE, KOKORO_URL, KOKORO_MODEL,
    DB_PATH, TASK_QUEUE_FILE, SESSION_ALIVE, Q_TERMINAL_TITLE,
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




def run_session_in_terminal(safe_sys, session_id, task):
    """Launch a codec_session.Session in a new Terminal window (module-based).

    Replaces the old build_session_script() approach:
    - Writes session params to a temp JSON file (no API keys in script text)
    - Writes a minimal launcher .py that loads params from JSON and runs Session
    - Opens a new macOS Terminal tab via osascript (preserves Terminal title)
    - The launcher script cleans up both temp files on exit
    """
    params = build_session_params(safe_sys, session_id)

    # Write params to a temp JSON file
    pf = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", prefix="codec_sess_", delete=False,
    )
    json.dump(params, pf)
    pf.close()
    params_path = pf.name

    repo = os.path.dirname(os.path.abspath(__file__))

    # Write a minimal launcher script (not the old 340-line generated one)
    lf = tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", prefix="codec_launch_", delete=False,
    )
    # atexit handler cleans up both temp files
    lf.write("import sys, json, os, atexit\n")
    lf.write(f"_PARAMS = {repr(params_path)}\n")
    lf.write(f"_SELF   = {repr(lf.name)}\n")
    lf.write("def _cleanup():\n")
    lf.write("    for p in (_PARAMS, _SELF):\n")
    lf.write("        try: os.unlink(p)\n")
    lf.write("        except Exception: pass\n")
    lf.write("atexit.register(_cleanup)\n\n")
    lf.write(f"sys.path.insert(0, {repr(repo)})\n")
    lf.write("from codec_session import Session\n")
    lf.write("with open(_PARAMS) as _pf: params = json.load(_pf)\n")
    lf.write("s = Session(**params)\n")
    lf.write("s.run()\n")
    lf.close()
    launcher_path = lf.name

    try:
        subprocess.Popen(
            ["osascript", "-e",
             f'tell application "Terminal"\nactivate\n'
             f'set w to do script "python3.13 {launcher_path}"\n'
             f'set custom title of selected tab of w to "{Q_TERMINAL_TITLE}"\n'
             f'end tell'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={**os.environ, "CODEC_TASK": task},
        )
    except Exception as e:
        log.error(f"[CODEC] Terminal launch error: {e}")
        # Clean up temp files on failure
        for p in (params_path, launcher_path):
            try:
                os.unlink(p)
            except Exception:
                pass
