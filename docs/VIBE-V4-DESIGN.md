# Vibe V4: rewind, fork, rename, export, private chats, purge and retention (P-077)

Plan item P-077 (audit 25 Oct 2026). Files can be restored, but the chat cannot go back with them, there is no way to
try something on a copy, a saved chat has no way out, and build output is versioned. This note covers what changes,
where, and how to undo it. The owner's go of 3 Nov 2026 ("go go build") approves this note.

## What the owner gets

| Feature | Where on the page | What happens |
|---|---|---|
| Rewind to here | A link under each answer that changed files (also after a reload) | The files go back to that answer's checkpoint through the normal restore (the files as they were are saved first) and the later messages are hidden. Hidden rows stay in `vibe.db` (`archived=1`); nothing is deleted. |
| Undo rewind | A line at the end of a rewound chat | The files go back to how they were before the rewind and the hidden messages come back. Offered while nothing new was said after the rewind. |
| File chips after a reload | Under a reloaded answer | The chips and the Revert link are drawn from the checkpoint the row stores (P-064 left this here). |
| Copy to a new project (fork) | Versions drawer, any version | A new project holds exactly that version's files and the history up to it. The original is unchanged. |
| Rename | Pencil button in the Projects list | Changes the shown name only: the project id and folder stay. |
| Export | Download button in the Projects list | A Markdown file of the visible conversation. |
| Private chat | "+ New private chat" in the Projects list | Nothing of the chat is written to `vibe.db`, and an agent run writes no run log. Files in a project still keep their versions; such a project is not in the Projects list (the list is made of saved chats), and `/vibe?project=<id>` opens it. |
| Not versioned | Versions drawer | "N files are not versioned" (build folders, files over 1 MB, links). |
| Where you left off | A card when a project opens | The last agent run's own report, read from its log: no model call. Runs undone by a rewind are listed as rewound. |

## Data

`vibe_messages` gets two columns:

- `checkpoint_id TEXT` (NULL when the row produced no checkpoint): for a chat answer in a project, the checkpoint of
  its applied files; for an agent run's report, the run's newest checkpoint (its start checkpoint if it changed nothing).
- `archived INTEGER NOT NULL DEFAULT 0`: 1 = hidden by a rewind. Every read the session page does skips them.

Migration (`routes/vibe.py:vibe_db`): a new database is created with both columns. An older one gets them by
`ALTER TABLE ... ADD COLUMN`, after a copy of `vibe.db` made with SQLite's backup API to
`vibe.db.pre-v4-YYYYMMDD-HHMMSS.bak` beside it (created 0600). The copy is made only when an ALTER is needed and the
database holds rows; once the columns exist the code never copies again. If the copy fails, nothing is altered and the
session routes answer 500 until it works (no change without a copy).

Rewind records live in the project's own history: the restore checkpoint a rewind takes carries
`rewind: {session, rows}` (the vibe.db row ids it hid). Undo rewind takes a restore checkpoint with `unrewind: <id>`.
A checkpoint between a rewind's target and the rewind is marked `rewound` in the Versions list, and so is an agent run
whose start checkpoint falls there.

The save route's full sync no longer deletes and re-inserts every row: it keeps the rows the page sent unchanged
(same role, text and checkpoint, from the first row on) and replaces only the rest of the visible rows. Hidden rows are
never touched by a save. This keeps row ids stable, so hidden rows come back in their place.

## API

| Route | Notes |
|---|---|
| `POST /api/vibe/save` | Messages may carry `checkpoint_id` (`c` + digits, else ignored). `temporary: true` writes nothing. |
| `GET /api/vibe/session/{sid}` | Visible rows with `checkpoint_id`; `hidden` (count) and `rewind` (the rewind Undo can take back, or null). |
| `POST /api/vibe/session/{sid}/rewind` | `{index, checkpoint}`: the visible row at `index` must store `checkpoint`. |
| `POST /api/vibe/session/{sid}/rewind/undo` | `{checkpoint}`: the rewind's restore checkpoint. 409 once the chat went on. |
| `POST /api/vibe/session/{sid}/rename` | `{title}`: the session's title, and its project's shown name. |
| `GET /api/vibe/session/{sid}/export` | `text/markdown` attachment of the visible rows. |
| `DELETE /api/vibe/session/{sid}` | Writes the session and all its rows (hidden ones too) to `~/.codec/trash/vibe/sessions/<id>-<ts>.json` (folder 0700, file 0600; `CODEC_VIBE_TRASH` moves it), then deletes the rows. |
| `GET /api/vibe/projects/{pid}/checkpoints/{cid}/changes` | Per-file changes and the checkpoint to revert to (a run's changes count from its start). |
| `POST /api/vibe/projects/{pid}/fork` | `{checkpoint}`: returns the new project. |
| `GET /api/vibe/projects/{pid}/unversioned` | Counts of files the scan skips (`.vibe` and `.git` left out), walk capped at 20,000 entries. |
| `GET /api/vibe/projects/{pid}/agent/left-off` | The newest ended run that was not rewound: goal, status, report, time; plus the rewound runs after it. |
| `POST /api/vibe/projects/{pid}/agent/run` | `temporary: true`: the run keeps its events in memory only (no `.vibe/runs` log, no kept command output). |

Rewind, undo rewind, fork, rename and purge answer 409 while an agent run holds the project (`agent_claim`). Every file
write goes through the existing paths: `_restore_locked` (rewind, undo), `_write_bytes_locked` and `safe_rel` (fork).
With `VIBE_PROJECTS_ENABLED=false`, rewind and undo rewind answer 404 like every project route, and rename changes the
chat's title only (no `project.json` write). A rewind that would hide more than 5,000 rows (what its record keeps)
answers 409. The page saves nothing between a rewind (or its undo) and the reload that follows it, so a save in that
moment cannot copy hidden rows back or delete the rows the undo showed again.

## Retention

`~/.codec/config.json` `vibe.retention_days`, default 0 = off: nothing is deleted, as today. With a number N > 0, a
sweep purges sessions whose `updated_at` is older than N days, each through the same trash-first purge, and skips a
session whose project an agent run holds. It runs when the Vibe page lists its sessions, at most once every 6 hours
per dashboard process, and writes one `vibe_retention_sweep` audit line per run (counts only). It never runs while
the setting is 0.

## Audit

New, source `codec-vibe`, ids and counts only (docs/codec-architecture.md): `vibe_project_fork`
(`project`, `from`, `checkpoint`, `files`), `vibe_session_trash` (`messages`, `reason` `owner`|`retention`),
`vibe_retention_sweep` (`days`, `sessions`, `messages`, `skipped`). A rewind and its undo are `vibe_restore` lines.

## Ignored folders

`dist`, `build`, `target` and `coverage` join `_SCAN_IGNORED_DIRS` (its own commit). A checkpoint made before that
still restores every file it holds, including files in those folders (test from a fixture written by the old code).
After a restore such files are on disk but no longer part of new checkpoints.

## Not in this item

- The Save version button (P-064's). A live agent run card gets no Rewind link; its report row gets one after a reload,
  and the card keeps Undo run. Rewind in a private chat: not offered (no rows); Revert still works.
- Memory recall (`routes/chat.py`, `skills/memory_search.py`) still reads hidden rows: follow-up.

## Tests

`tests/test_vibe_v4.py`, every test with a temp `CODEC_VIBE_ROOT`, a temp `CODEC_VIBE_TRASH`, `routes.vibe.VIBE_DB`
patched and `_vibe_conn` reset: migration copy (once, 0600, rows kept), rewind (rows still in the table, files equal
to the checkpoint, a new Restore checkpoint), undo rewind (rows back in order, file hashes equal to before), run rewind
(later run marked rewound, its log kept), fork (sha256 of every file, history up to the id), rename (id and folder
unchanged), export (Markdown), 409 for rewind, fork, rename and purge during a run, private save and run (row and file
counts unchanged), purge and sweep (trash JSON written before rows go; default 0 deletes nothing; one audit line),
unversioned counts, left-off with a model stub that fails if called. Page functions run in Node; one headless Chrome
check against a dashboard started from the worktree on a free port, ending with a console read.

## Rollback

Revert the PR. Hidden rows come back with `UPDATE vibe_messages SET archived=0`; purged sessions come back from their
trash JSON; the `vibe.db` copy made before the first ALTER restores the rest. The two new columns are harmless to the
old code. Deploy and rollback need a `codec-dashboard` restart (the owner's word); the owner copies `vibe.db` before
the first restart on the new code.
