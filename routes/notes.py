"""CODEC Notes routes (docs/NOTES-DESIGN.md): the owner's saved notes, Tasks > Notes.

Thin HTTP layer over codec_notes. Every route sits behind the dashboard's normal login (cookie and
CSRF), so the phone works through the tunnel like the other pages. Nothing here is a skill or an MCP
tool. NOTES_ENABLED=false answers 404 and the Tasks page keeps its Notes tab hidden.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import codec_notes as notes

router = APIRouter()

# 100,000 characters can be 400 KB of UTF-8, and a client that escapes non-ASCII sends up to 12 bytes
# for one. This only stops a huge body before it is parsed; the character limit has the nice message.
_MAX_REQUEST_BYTES = 1_500_000


def _off() -> JSONResponse:
    return JSONResponse({"error": "Notes are switched off"}, status_code=404)


def _bad_body() -> JSONResponse:
    return JSONResponse({"error": "Send a JSON object"}, status_code=400)


async def _run(fn, *args, **kwargs):
    if not notes.enabled():
        return _off()
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except notes.NoteError as e:
        return JSONResponse({"error": e.message}, status_code=e.status)


async def _json(request: Request):
    """The JSON object in the request, or a ready-made refusal."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > _MAX_REQUEST_BYTES:
        return JSONResponse({"error": "That is too much text for one note"}, status_code=413)
    raw = await request.body()
    if len(raw) > _MAX_REQUEST_BYTES:
        return JSONResponse({"error": "That is too much text for one note"}, status_code=413)
    try:
        body = await request.json()
    except Exception:
        return _bad_body()
    return body if isinstance(body, dict) else _bad_body()


@router.get("/api/notes")
async def list_notes(q: str = "", limit: int = 200):
    return await _run(notes.list_notes, q, limit)


@router.post("/api/notes")
async def create_note(request: Request):
    if not notes.enabled():
        return _off()
    body = await _json(request)
    if isinstance(body, JSONResponse):
        return body
    return await _run(notes.create, body.get("body"), body.get("title", ""), body.get("source", "manual"))


@router.get("/api/notes/{note_id}")
async def read_note(note_id: str):
    return await _run(notes.get, note_id)


@router.put("/api/notes/{note_id}")
async def change_note(note_id: str, request: Request):
    if not notes.enabled():
        return _off()
    body = await _json(request)
    if isinstance(body, JSONResponse):
        return body
    return await _run(notes.update, note_id, body.get("title"), body.get("body"))


@router.delete("/api/notes/{note_id}")
async def delete_note(note_id: str):
    result = await _run(notes.trash, note_id)
    return {"ok": True} if result is None else result
