"""CODEC Sparkle auto-update API routes.

E1 / SR-47: extracted from codec_dashboard.py. Both endpoints proxy
through codec_update which handles the Ed25519 signature verification
(refuses tampered downloads). Best-effort error handling — any failure
during update check reports up-to-date instead of disrupting the PWA.
"""
from __future__ import annotations

import logging
import subprocess

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()
log = logging.getLogger("codec_dashboard")


@router.get("/api/update/check")
async def update_check():
    """Sparkle-compatible update check. Returns {update_available, current, latest?}.
    Best-effort: any failure (offline, no feed yet) reports up-to-date."""
    try:
        import codec_update
        info = codec_update.check_for_update()
        if info is None:
            return {"update_available": False, "current": codec_update._current_version()}
        return {"update_available": True,
                "current": codec_update._current_version(),
                "latest": info.version, "url": info.url, "title": info.title}
    except Exception as e:
        log.warning(f"update check failed: {e}")
        return {"update_available": False, "error": str(e)}


@router.post("/api/update/download")
async def update_download():
    """Download the latest update, Ed25519-verify it, and reveal it in Finder.
    Returns {ok, path, version} or {ok:false, error}. The verify step refuses
    any download whose signature doesn't match SUPublicEDKey."""
    try:
        import codec_update
        info = codec_update.check_for_update()
        if info is None:
            return {"ok": False, "error": "no update available"}
        dmg = codec_update.download_and_verify(info)   # raises if signature bad
        try:
            subprocess.Popen(["open", "-R", str(dmg)])  # reveal in Finder
        except Exception:
            pass
        return {"ok": True, "path": str(dmg), "version": info.version}
    except ValueError as e:
        # Signature/length verification failed — untrusted download
        log.warning(f"update download refused: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except Exception as e:
        log.warning(f"update download failed: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
