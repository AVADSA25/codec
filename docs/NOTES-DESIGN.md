# Notes — design

Status: approved 21 Oct 2026 ("proceed", with the defaults in the decisions below). Built the same day.

## Goal

The owner asked (21 Oct 2026): "add a new section under Tasks, named Notes, a place where I can save things from CODEC and access them back after."

Notes is the owner's own shelf inside CODEC. He saves a chat reply with one click (or writes a note by hand), and finds it again later in a list, from the Mac or from his phone.

Assumptions, stated so they can be corrected:

- "Under Tasks, top right" means a new tab at the right end of the Tasks page tab bar: Schedules, Activity, Heartbeat, History, **Notes**.
- "Things from CODEC" means the text of a chat reply in this first version. Other sources (Today's Flash box, Vibe results, images) are follow-ups.

## What exists today (checked in the code, 21 Oct 2026)

- The Tasks page (`codec_tasks.html`) has four tabs built with `showTab()`; a tab named in the URL hash opens (`/tasks#history`). A fifth tab fits the same pattern.
- A chat reply has an action row (Copy, Regenerate, Speak, Schedule this, thumbs). "Schedule this" calls `CodecShell.schedule.open(...)`, a shell function any page can reuse. Saving to Notes follows the same pattern: `CodecShell.notes.save(...)`.
- The `notes` skill ("take a note", "my notes") writes to the Apple Notes app. It stays as it is. Notes in CODEC is a separate, local shelf.
- There is no `/api/notes` route and no notes file yet.
- `codec_jsonstore.atomic_write_json` and `file_lock` already give safe file writes (0600).

## Design

### 1. Storage

One JSON file per note: `~/.codec/notes/<id>.json`. Folder 0700, files 0600, written with `codec_jsonstore.atomic_write_json`. No change to `memory.db`, the audit envelope or any existing file.

```
{"id": "a1b2c3d4e5f6", "title": "...", "body": "...", "source": "chat" | "manual",
 "created": "2026-10-21T09:00:00+00:00", "updated": "..."}
```

- `id` is 12 lowercase hex characters made with `secrets.token_hex(6)`. Every route and every file path checks it against `^[0-9a-f]{12}$`, so an id can never point outside the folder.
- Limits: title 120 characters, body 100,000 characters, 2,000 notes. Over a limit answers 400 with a plain sentence.
- Delete moves the file to `~/.codec/notes/.trash/` (nothing is erased). There is no restore button in this version; a trashed note can be copied back by hand.
- The body is plain text. It is shown with `textContent` and edited in a text box, never inserted as HTML.

New module `codec_notes.py` holds the store (create, read, update, list with search, trash). `routes/notes.py` is the HTTP layer.

### 2. HTTP

All routes use the normal dashboard login (cookie plus CSRF), so the phone works through the tunnel exactly like the other pages.

| Route | What it does |
|---|---|
| `GET /api/notes?q=&limit=` | Newest first by `updated`. `q` is a case-insensitive match in title and body. Returns id, title, a 140-character preview, source, dates. Default 200, max 500. |
| `POST /api/notes` | `{title?, body, source?}`. An empty title becomes the first line of the body (up to 80 characters). Returns the note. |
| `GET /api/notes/{id}` | The whole note. |
| `PUT /api/notes/{id}` | `{title?, body?}`. Updates `updated`. |
| `DELETE /api/notes/{id}` | Moves it to the trash folder. |

Kill switch: `NOTES_ENABLED=false` makes every route answer 404 and the tab stays hidden.

Nothing in Notes is an MCP tool or a skill, and no agent can read or write it. The AI does not touch the owner's notes in this version (see decision 4).

### 3. Audit

Three new events, metadata only: `note_saved`, `note_updated`, `note_deleted`, with `extra = {id, source, chars}`. The text and the title are never logged. They are added to the event table in `docs/codec-architecture.md`; the audit envelope does not change.

### 4. Page

- `codec_tasks.html`: a "Notes" tab button and a panel. The tab starts hidden and `static/notes.js` shows it once `GET /api/notes?limit=1` answers 200, so nothing broken shows before the dashboard restart. `#notes` in the URL opens it.
- Panel on a desktop: left column is the list (a search box, a "New note" button, then one row per note: title, preview, date, small "chat" badge); right column is the editor (title box, text box, Save, Copy, Delete). Cmd or Ctrl plus S saves. Switching note with unsaved changes asks first.
- Panel on a phone: the list full width; tapping a note opens the editor full width with a Back button.
- Delete asks "Delete this note? It moves to the trash folder." before it runs.
- `static/notes.js` keeps list, search and title logic as plain functions so Node can test them (same approach as `vibe-agent.js`). It is added to the stamp list in `tools/stamp_static.py`.

### 5. Save from chat

- `codec_chat.html`: a "Save to Notes" button in the action row of each assistant reply, next to Schedule this. Bound with a closure like the other buttons (message text is never put in an inline `onclick`).
- `codec-shell.js`: `CodecShell.notes.save({text, source})` posts to `/api/notes` and shows the existing toast "Saved to Notes" with a link to `/tasks#notes`. On an error the toast says why.

### 6. Rollout

Pages and scripts are live as soon as the live folder is fast-forwarded. The routes are Python, so the tab stays hidden and the Save button answers "Notes need a dashboard restart" until the owner runs `pm2 restart codec-dashboard` once. After that I check the live routes with the internal token (create, search, read, trash), as for the Vibe agent.

## Decisions (defaults chosen; say if you want another)

1. **Where:** a tab at the right end of Tasks. Other option: its own page in the left menu.
2. **What can be saved in this version:** chat replies and hand-written notes. Follow-ups, each small: Today's Flash replies, Vibe agent summaries, generated images.
3. **Delete:** moves to a hidden trash folder, no restore button yet.
4. **Can CODEC read your notes?** No in this version (privacy and prompt-injection safety). A later step could let you say "what did I save about X" with its own design.
5. **Voice "take a note":** stays on Apple Notes. Other option: send it to CODEC Notes.

## Tests

- `tests/test_notes.py`: store (create, update, search, limits, trash, bad ids refused including `..` and absolute paths, file mode 0600, folder 0700), routes (each route, 404 with the kill switch, 400 for an empty body or too long text, audit events carry no text), nothing leaks between notes.
- `tests/test_notes_page.py`: Node checks of the list and search functions; the tab is hidden by default and shown only after the route answers; note text goes in with `textContent` (an HTML note stays text); `codec_chat.html` has the button and the closure binding; stamp check passes.
- Browser check of the Tasks page on a desktop and a 375 px phone width, then a console error read. Five tabs must still fit on a phone.
- `ruff check .` and the whole suite, as CI runs them.

## Rollback

Set `NOTES_ENABLED=false`, or revert the PR. No migration: the notes folder stays on disk and is never deleted by the code.

## Files

New: `codec_notes.py`, `routes/notes.py`, `static/notes.js`, `tests/test_notes.py`, `tests/test_notes_page.py`.
Changed: `codec_dashboard.py` (router include), `codec_tasks.html` (tab, panel, styles), `codec_chat.html` (button), `static/codec-shell.js` (`notes.save`), `tools/stamp_static.py`, `AGENTS.md` (short section and a don't-touch line for `~/.codec/notes/`), `docs/codec-architecture.md` (events).

## Changes made while building

- The shell's toast links only to `https://` addresses, so "Saved to Notes." carries an "Open Notes" button that goes to `/tasks#notes` instead of a link.
- A note's `updated` time is made strictly increasing inside the process (milliseconds, +1 when two saves land in the same one), so the list order never depends on a tie.
- A request body over 1.5 MB is refused with 413 before it is parsed; the 100,000-character limit still gives the readable message.
- Search needs every word (up to 8) in the title or the text, any case.
- The editor shows a "Back to notes" button on a phone. Leaving an edited note asks "Discard your changes?"; the tab also warns before the page is closed with unsaved words.
- `tests/conftest.py` sets `CODEC_NOTES_DIR` to a temp folder, like the user skills folder, so no test can write to the owner's notes.
