"""CODEC Dashboard -- WebSocket routes (voice pipeline)."""
import os

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from routes._shared import DASHBOARD_DIR, _NO_CACHE

router = APIRouter()


@router.get("/voice", response_class=HTMLResponse)
async def voice_page():
    """Serve the voice call UI."""
    voice_path = os.path.join(DASHBOARD_DIR, "codec_voice.html")
    with open(voice_path) as f:
        return HTMLResponse(f.read(), headers=_NO_CACHE)


@router.websocket("/ws/voice")
async def voice_websocket(websocket: WebSocket):
    """WebSocket endpoint -- one VoicePipeline per connection.
    Pass ?resume=<session_id> to resume a dropped session."""
    await websocket.accept()
    from codec_metrics import metrics
    metrics.inc("codec_voice_sessions_total")
    resume_id = websocket.query_params.get("resume")
    print(f"[Voice] WebSocket connected{' (resume=' + resume_id + ')' if resume_id else ''}")
    from codec_voice import VoicePipeline
    pipeline = VoicePipeline(websocket, resume_session_id=resume_id)
    try:
        await pipeline.run()
    except WebSocketDisconnect:
        print("[Voice] WebSocket disconnected cleanly")
    except Exception as e:
        print(f"[Voice] WebSocket error: {e}")
    finally:
        pipeline.save_to_memory()
        await pipeline.close()
