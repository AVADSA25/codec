# Vibe Projects — undo, live preview, hideable code — design

Status: approved 18 Oct 2026. Step 1 is built (this document, "Step 1" section, describes what shipped); step 2 is next. Steps 3 and 4 are direction only and get their own design doc before any build.

## Goal

Make Vibe work like Replit / Bolt, with a safety net:

1. Every AI change can be undone in one click.
2. The project being built runs live next to the chat, and the code is visible with line numbers and can be hidden.
3. Later: a coding agent (Codex / Claude Code style), then CODEC editing its own repo through a branch and a pull request.

## What exists today (checked in the code and in the running page, 18 Oct 2026)

- Vibe is one Monaco editor with one file, a chat on the left and a `sessions` list. The AI answer replaces the editor text: `ed.setValue(code)` at `codec_vibe.html:590` and `:611`.
- **No way back.** In the running page, after two `setValue` calls `canUndo` is `false` and undo restores nothing. `/api/vibe/save` does `INSERT OR REPLACE` on one `code` column (`routes/vibe.py:82`), so the old text is gone from the database too.
- Preview: `/api/preview` writes one HTML file to `~/.codec/preview.html`, served by `/preview_frame` under a strict CSP. One file, no assets, no multi-page.
- Run: `/api/run_code` runs one temp file in 8 languages. Mac only, 30 s, resource limits, secrets stripped from the environment, `is_dangerous` gate.
- The working-folder chip is a text hint added to the chat prompt (`routes/chat.py:1239`). The AI cannot write files there. Saving goes through `/api/save_file`, blocklisted.
- Skills: `create_skill` plus review-and-approve writes new skills to `~/.codec/skills`. `self_improve` drafts proposals and never deploys.
- Project mode (plan, permission manifest, background runner) exists. It can call skills such as `file_write` and `terminal`, but it has no patch, search or test-run tools, no git step, and it runs on the local model only.
- Nothing in CODEC edits CODEC's own code, makes commits, or pushes.

## Step 1 — Projects and checkpoints (the safety net)

**Project = a folder** `~/codec-workspace/vibe/<slug>-<6 hex>/` with a hidden `.vibe/` folder inside (`project.json`, `checkpoints.jsonl`, `objects/<sha256>`). Outside `~/.codec` and the repo, so every existing path blocklist still applies. *(First draft said "its own local git repository". Changed to plain-file checkpoints: a customer Mac may have no git, and macOS then pops up an install window the first time git runs. Same behaviour, no dependency, and the tests can break each rule on purpose.)*

- Old single-file sessions keep working and open as before. The first message sent from one creates a project and seeds it with what the editor shows (`index.html`, `main.py`, ...), so that text is the first checkpoint. A session remembers its project (`vibe_sessions.project_id`); if the folder is gone the session opens with its last saved code.
- **Checkpoint before and after every AI turn.** Before: anything the user changed by hand ("Before: <your message>", taken only if something changed). After: the AI's files ("<your message>", kind `ai`). Nothing leaves the Mac.
- **Restore = a new checkpoint** that sets the files back to an old one (files added since are removed, files deleted since come back, exact bytes). History is never rewritten and nothing is deleted, so a restore can itself be undone. Undo takes the newest AI change that is still in effect; a restore to an older version drops the newer AI changes from the Undo stack (they stay in Versions).
- **Delete project** (the delete button in the project list) moves the folder to `~/.codec/trash/vibe/<id>-<time>`; it is not removed.
- **AI writes files, not "Apply to editor".** The prompt asks for file blocks with a path (` ```file:index.html `). The server writes them only inside the project folder: real-path check, no `..`, no symlink out, no `.git/`, 1 MB per file, 200 files per project, and the same blocked names as `/api/save_file`.
- **UI:** an "Undo AI change" button and a Versions button in the header; file tabs above the editor (one editor model per file, so Cmd-Z also takes back an AI write); a Versions drawer (list, coloured diff, "Restore this version", always asking first); a "Revert to before this" link on each AI message; per-file change chips ("index.html +34 −2"); Stop mid-answer puts every file back as it was. The preview inlines local css and js so a multi-file page still shows.
- **Prompt:** in project mode the model gets the project files (up to 60,000 characters, open file first) and is asked for one labelled block per file. Older turns are sent without their code, so a long session does not repeat every file each time.

New API (`routes/vibe_projects.py`, same dashboard auth as the rest of Vibe):
`POST /api/vibe/projects`, `GET /api/vibe/projects`, `GET /api/vibe/projects/{id}` (files, can_undo), `GET|PUT /api/vibe/projects/{id}/file`, `POST .../checkpoint`, `POST .../apply`, `GET .../checkpoints`, `GET .../diff?from=&to=`, `POST .../restore`, `POST .../undo`, `DELETE /api/vibe/projects/{id}` (to trash).

Storage: a nullable `project_id` on `vibe_sessions`, added with the same try/except `ALTER TABLE` pattern the file already uses (an old database is upgraded in place). The project list is the folders themselves, so there is no `vibe_projects` table. No change to `memory.db` or the audit envelope. New audit events, metadata only (project id, checkpoint id, counts, never a label, a file name or code): `vibe_checkpoint`, `vibe_apply`, `vibe_restore`, `vibe_project_trash` (documented in `docs/codec-architecture.md`); `vibe_project_run` and `vibe_project_stop` come with step 2.

## Step 2 — Replit-style layout and live preview

- **Three panes:** chat | live preview | code (file tree, Monaco with line numbers, tabs). A header switch **Code: show / hide** (remembered in `localStorage`, key `codec-vibe-code`). Hidden = chat plus a large preview, like Bolt. Phone: one pane at a time with a Chat / Preview / Code switch; code starts hidden.
- **Live preview, static projects:** a small supervised static server on `127.0.0.1:<port>` (port range 5300–5399, one at a time), shown in an iframe with `sandbox="allow-scripts allow-forms allow-modals"` and no `allow-same-origin`, so project code cannot read the dashboard or its cookies. The page polls `GET .../mtime` every second and reloads the frame after a change (no script injected into the project).
- **Dev-server projects (node, python):** `vibe.json` holds `{"start": "...", "port": N}`. The first run shows the exact command and asks for a click; after that it starts without asking. Same hardening as `/api/run_code` (secrets stripped, own process group, killed on stop), bound to `127.0.0.1`, stopped after 30 minutes idle, logs shown in the output panel. The AI never starts a process by itself in this version.
- **Console errors** in the preview reuse the error reporter Vibe already injects (`_pvAgent`, `codec_vibe.html:682`).
- **CSP:** the dashboard policy has no `frame-src`, so framing falls back to `default-src 'self'` and a `127.0.0.1` frame would be blocked. Add `frame-src 'self' http://127.0.0.1:*` (additive).
- **Mac only in this version.** Start, stop and run return 403 for tunnel requests (same rule as `/api/run_code`), and the iframe is plain http. On the phone the preview pane says the project runs on the Mac. A tunnel-safe preview needs a reverse proxy with its own auth: separate design.

## Test plan

- Python: path containment (`..`, absolute path, symlink out, `.git/`, size and count caps), apply writes and commits, restore creates a new commit and keeps history, delete goes to trash, remote request gets 403 on run/stop, blocked names refused. Tests use a temp root (`CODEC_VIBE_ROOT`) and run once with an empty `HOME` (CI has no `~/.codec`).
- Browser pane at 1280, 412 and 375 px: AI change then Undo returns the exact old text; Restore then Undo-restore round trip; Code show/hide persists after reload; preview reloads after an edit; a console read at the end of every check (CSP lesson).
- Phone: the Chat / Preview / Code switch, no horizontal scroll, composer in flow.
- Whole suite stays green.

## Rollback

`VIBE_PROJECTS_ENABLED=false` serves the old single-file Vibe. Projects are plain folders with git, so nothing is locked in. Revert the PR to remove the code; the `vibe_projects` table is harmless if left.

## Direction only (separate design doc each, not part of this approval)

**Step 3 — coding agent.** Reuse the Project-mode runner (plan, permission manifest, checkpoints, ask-the-user) with tools limited to the project folder: read, list, search, write, apply patch, run tests or build from an allowlist, checkpoint. Each agent step is a commit, so Undo already covers it. Default model stays local; the cloud fallback model can be chosen, with its monthly cap. A local model will not match Codex or Claude Code on large changes.

**Step 4 — CODEC edits CODEC.** A project that is a git worktree of this repo on a branch `vibe/<name>`. Live preview of page changes with tests (`pytest`) before anything leaves the Mac. Before a push: tests green, a scan of the diff for secrets and personal data (the repo is public), and the owner clicks "Open pull request" after reading the diff. Branch and pull request only, never `main`; CODEC never restarts itself. Open risk to solve first: a second dashboard instance running AI-written code can read the Keychain, so preview of backend changes needs real isolation or stays out.

## Decisions I need

1. **Auto-apply AI changes**, with Undo always available (recommended), or ask before each change.
2. **Mac-only preview** in this version (recommended), or wait for the tunnel-safe preview before shipping.
3. **Project folder** `~/codec-workspace/vibe/` (recommended).
