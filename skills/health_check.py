"""health_check skill — check CODEC service health across all subsystems."""
import sys
import os
import json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SKILL_NAME = "health_check"
SKILL_DESCRIPTION = "Check health of all CODEC services: Qwen LLM, Vision, Whisper STT, Kokoro TTS, Dashboard, memory DB, heartbeat. Returns status summary."
SKILL_TRIGGERS = [
    "system health", "health check", "check services", "are services running",
    "service health", "check health", "codec health", "is everything running",
    "status check", "check all services"
]
SKILL_MCP_EXPOSE = True


def run(task: str = "", context: str = "") -> str:
    """Check health of all CODEC subsystems."""
    from urllib.request import urlopen, Request
    import sqlite3

    results = {}

    # 1. Dashboard
    try:
        req = Request("http://localhost:8090/api/health")
        resp = urlopen(req, timeout=5)
        data = json.loads(resp.read().decode())
        results["dashboard"] = f"OK ({data.get('status', '?')})"
    except Exception as e:
        results["dashboard"] = f"DOWN ({type(e).__name__})"

    # 2. Qwen LLM
    try:
        req = Request("http://localhost:8083/v1/models")
        resp = urlopen(req, timeout=5)
        results["qwen_llm"] = "OK"
    except Exception as e:
        results["qwen_llm"] = f"DOWN ({type(e).__name__})"

    # 3. Qwen Vision
    try:
        req = Request("http://localhost:8083/v1/models")
        resp = urlopen(req, timeout=5)
        results["qwen_vision"] = "OK"
    except Exception as e:
        results["qwen_vision"] = f"DOWN ({type(e).__name__})"

    # 4. Whisper STT
    try:
        req = Request("http://localhost:8084/openapi.json")
        resp = urlopen(req, timeout=5)
        results["whisper_stt"] = "OK"
    except Exception as e:
        results["whisper_stt"] = f"DOWN ({type(e).__name__})"

    # 5. Kokoro TTS — port from config (tts_url), default :8085. The old
    # hardcoded :8880 was stale (kokoro has served on 8085 for months), so
    # health_check falsely reported TTS DOWN — caught 2026-07-02 when a
    # Project-mode dry run wrote "kokoro_tts is DOWN" into its status brief
    # while the service was healthy.
    try:
        from urllib.parse import urlparse
        tts_base = "http://localhost:8085"
        try:
            with open(os.path.expanduser("~/.codec/config.json")) as f:
                # tts_url is the FULL speech endpoint
                # (e.g. http://localhost:8085/v1/audio/speech) — keep only
                # scheme://host:port for the models probe.
                _u = urlparse(json.load(f).get("tts_url", ""))
                if _u.scheme and _u.netloc:
                    tts_base = f"{_u.scheme}://{_u.netloc}"
        except Exception:
            pass
        req = Request(f"{tts_base}/v1/models")
        resp = urlopen(req, timeout=5)
        results["kokoro_tts"] = "OK"
    except Exception as e:
        results["kokoro_tts"] = f"DOWN ({type(e).__name__})"

    # 6. Memory DB
    try:
        db_path = os.path.expanduser("~/.codec/memory.db")
        conn = sqlite3.connect(db_path, timeout=3)
        conn.execute("SELECT 1")
        row_count = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        conn.close()
        results["memory_db"] = f"OK ({row_count} conversations)"
    except Exception as e:
        results["memory_db"] = f"ERROR ({type(e).__name__})"

    # 7. Cortex health (aggregated)
    # PR-2D (D-11 closure): replace `x-internal: codec` literal with HMAC token.
    try:
        from codec_keychain import get_internal_token
        _ipc_token = get_internal_token() or ""
    except Exception:
        _ipc_token = ""
    try:
        req = Request("http://localhost:8090/api/cortex/health",
                       headers={"x-internal-token": _ipc_token})
        resp = urlopen(req, timeout=5)
        data = json.loads(resp.read().decode())
        results["cortex"] = data
    except Exception as e:
        results["cortex"] = f"ERROR ({type(e).__name__})"

    # Build summary
    total = len([v for k, v in results.items() if k != "cortex"])
    healthy = len([v for k, v in results.items() if k != "cortex" and isinstance(v, str) and v.startswith("OK")])

    lines = [f"CODEC Health Check: {healthy}/{total} services healthy\n"]
    for svc, status in results.items():
        if svc == "cortex":
            if isinstance(status, dict):
                lines.append(f"  Cortex: {json.dumps(status)}")
            else:
                lines.append(f"  Cortex: {status}")
        else:
            icon = "OK" if isinstance(status, str) and status.startswith("OK") else "ISSUE"
            lines.append(f"  [{icon}] {svc}: {status}")

    return "\n".join(lines)
