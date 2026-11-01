"""Settings > About (P-056, docs/RELEASE-NOTICES-DESIGN.md): the version and the third-party notices.

The notices file is THIRD-PARTY-NOTICES.md at the repo root (Contents/Resources/app in the
app, where the build also keeps a copy one level up at Contents/Resources). Read-only: the
page links to it and nothing here writes it.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, PlainTextResponse

router = APIRouter()

_ROOT = Path(__file__).resolve().parent.parent
NOTICES_NAME = "THIRD-PARTY-NOTICES.md"


def notices_path() -> Path | None:
    for base in (_ROOT, _ROOT.parent):
        p = base / NOTICES_NAME
        if p.is_file():
            return p
    return None


@router.get("/api/about")
async def about():
    import codec_version
    return {"version": codec_version.__version__, "notices": notices_path() is not None}


@router.get("/api/about/notices")
async def about_notices():
    p = notices_path()
    if p is None:
        return PlainTextResponse("The third-party notices file is not in this copy of CODEC.", status_code=404)
    return FileResponse(p, media_type="text/plain; charset=utf-8", headers={"X-Content-Type-Options": "nosniff"})
