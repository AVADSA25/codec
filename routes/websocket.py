"""CODEC Dashboard -- WebSocket routes (voice pipeline)."""
import hmac
import os

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from routes._shared import DASHBOARD_DIR, _NO_CACHE

router = APIRouter()


def _ws_authorized(websocket) -> bool:
    """N1 (re-audit): mirror the HTTP AuthMiddleware gate for the WS handshake.

    BaseHTTPMiddleware (AuthMiddleware's base) only runs on the `http` scope,
    NOT `websocket` — so /ws/voice was unauthenticated. This replicates the
    HTTP Layers 0/1/2: open when nothing is configured (loopback dev posture);
    else accept a matching dashboard_token (via `?token=` or an Authorization:
    Bearer header) OR a valid biometric session cookie (TOTP-verified per
    _verify_biometric_session). Never raises — returns False on any error.
    """
    # Imported lazily so test monkeypatches of these module attrs take effect.
    from routes._shared import (
        AUTH_ENABLED,
        _auth_available,
        _verify_biometric_session,
    )
    try:
        from codec_config import get_dashboard_token
        token = get_dashboard_token() or ""
    except Exception:
        token = ""
    biometric = bool(AUTH_ENABLED) and bool(_auth_available())

    # Layer 0 — nothing configured → open (loopback-only dev; the startup
    # safety gate refuses a public bind without a token or auth).
    if not token and not biometric:
        return True

    # Layer 1 — dashboard_token bearer, presented as ?token= or Authorization.
    if token:
        presented = websocket.query_params.get("token", "") or ""
        if not presented:
            hdr = websocket.headers.get("authorization", "") or ""
            if hdr.lower().startswith("bearer "):
                presented = hdr[7:]
        if presented and hmac.compare_digest(presented, token):
            return True

    # Layer 2 — biometric/PIN session cookie (TOTP-enforced inside the helper).
    if biometric:
        try:
            if _verify_biometric_session(websocket):
                return True
        except Exception:
            return False

    return False


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
    # N1 (re-audit): authenticate the handshake — AuthMiddleware can't (it's
    # HTTP-scope only). Reject before accept() so the voice→skill pipeline is
    # never reachable unauthenticated when the dashboard is exposed.
    if not _ws_authorized(websocket):
        await websocket.close(code=4401)  # 4401 = application "Unauthorized"
        return
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
