# Vibe Projects — undo, live preview, hideable code — design

Status: approved 18 Oct 2026. Steps 1, 2a and 2b are built (the "Step 1", "Step 2a" and "Step 2b" sections describe what shipped). Steps 3 and 4 are direction only and get their own design doc before any build.

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

Built in two parts so the visible part ships first and nothing that runs a program rides along with it.

### Step 2a — panes, hideable code, live preview (built)

- **Three panes** side by side on a wide screen: chat | code (file tabs, line numbers) | live preview. A **Hide code / Show code** button in the header hides the code pane and gives the room to the preview (chat plus a large preview, like Bolt); the choice is remembered (`localStorage` key `codec-vibe-code`). The preview pane is open from the start with an empty-state line, and has its own refresh, inspect and close buttons so everything still works with the code hidden.
- **Phone:** one pane at a time from three tabs, Chat | Preview | Code (it opens on Chat, so the code starts hidden). A dot on the Preview tab says the preview changed while another tab was open; an AI change does not pull the phone away from the chat.
- **The preview follows the files:** it refreshes after an AI change, an undo, a restore, an opened project, and about 1.5 s after the person stops typing (the same autosave that writes the file). Only while the preview is open; an automatic refresh never complains when there is no page, and goes back to the empty state when every page was undone.
- Still the sandboxed `srcdoc` frame (`sandbox="allow-scripts"`, no `allow-same-origin`) with local css and js inlined, so it works on the phone through the tunnel and keeps Inspect and error capture. The editor does not follow the typing when it cannot be seen (a hidden editor kept scrolled to its end).
- Not built here: the localhost server and the dev server below.

### Step 2b — localhost preview and "Run project" (built)

- **Localhost preview for static projects** ("Show the files on localhost" in the Run dialog, the play button in the preview header): a read-only file server on `127.0.0.1:<port>` (range 5300–5399, one at a time), shown in the preview frame with `sandbox="allow-scripts allow-forms allow-modals"` (no `allow-same-origin`). It serves only the project folder (never `.vibe`, never a dotfile, never a link that leads out), answers only GET and HEAD, refuses any `Host` that is not `127.0.0.1` or `localhost` on its own port, and sends `Content-Security-Policy: sandbox ...` so a page opened on its own is sandboxed too, plus `Access-Control-Allow-Origin: *` (read-only files) so the sandboxed page can fetch its own files. Gives images, several pages and `fetch` of local files, which `srcdoc` cannot. The frame reloads after an edit is saved.
- **Run a command (node, python):** the command is typed or confirmed in the dialog and **that string in the request is the only thing executed**. `vibe.json` (`start`, `port`), `package.json` scripts or a likely file name (`app.py`, `server.js`) only fill in the dialog's starting text, so the AI can never start anything by itself. It runs with `bash -c` in the project folder, in its own process group, with `PORT` and `HOST=127.0.0.1` set (a free port from 5300–5399, or the port the owner names), secret-looking environment variables removed, and `is_dangerous` as a typo catcher (as `/api/run_code`); one line, up to 500 characters. Its output goes to the output panel, it is stopped on request or after 30 minutes without the page asking about it, only one thing is live at a time (starting another stops the first), the whole process group is stopped, and a process left by a dashboard restart is recorded in `.vibe/run.json` and stopped the next time anything starts (only if its start time still matches, so a reused number is never touched). A running app is framed with `allow-same-origin` so it can use its own storage; it is a different origin from the dashboard.
- **CSP:** the dashboard policy had no `frame-src`, so framing fell back to `default-src 'self'`; it now has `frame-src 'self' http://127.0.0.1:*` (additive).
- **Mac only:** serve, run, live and stop answer 403 for a request that came through the tunnel or from another address (same rule as `/api/run_code`); the play button is hidden unless the page itself is on `localhost`, so on the phone the pane keeps the inline preview from step 2a.
- Console errors and Inspect work only for the inline preview; the localhost frame is a real site. Anything the command prints (including its errors) is in the output panel.

## Test plan

- Python: path containment (`..`, absolute path, symlink out, `.git/`, size and count caps), apply writes all or nothing behind checkpoints, restore keeps history, undo walks back one AI change at a time, delete goes to the trash, blocked names refused, audit events carry no names or code; each rule is checked by breaking it on purpose. Tests use temp folders (`CODEC_VIBE_ROOT`, `CODEC_VIBE_TRASH`). Never run the suite with a fake `HOME` on a Mac: `codec_audit` then cannot find its keychain secret and starts replacing it.
- Browser pane at 1280, 412 and 375 px: AI change then Undo returns the exact old text; Restore then Undo-restore round trip; Code show/hide persists after reload; preview reloads after an edit; a console read at the end of every check (CSP lesson).
- Phone: the Chat / Preview / Code switch, no horizontal scroll, composer in flow.
- Whole suite stays green.

## Rollback

`VIBE_PROJECTS_ENABLED=false` serves the old single-file Vibe (an older server answers 404 and the page falls back the same way). Projects are plain folders, so nothing is locked in. Revert the PR to remove the code; the `project_id` column on `vibe_sessions` is harmless if left.

## Direction only (separate design doc each, not part of this approval)

**Step 3 — coding agent.** Designed in `docs/VIBE-AGENT-DESIGN.md` (19 Oct 2026). It does not reuse the Project-mode runner process, only its rules: after reading the runner, a polling background process with a per-path permission list does not fit an interactive coding loop.

**Step 4 — CODEC edits CODEC.** A project that is a git worktree of this repo on a branch `vibe/<name>`. Live preview of page changes with tests (`pytest`) before anything leaves the Mac. Before a push: tests green, a scan of the diff for secrets and personal data (the repo is public), and the owner clicks "Open pull request" after reading the diff. Branch and pull request only, never `main`; CODEC never restarts itself. Open risk to solve first: a second dashboard instance running AI-written code can read the Keychain, so preview of backend changes needs real isolation or stays out.

## Decisions I need

1. **Auto-apply AI changes**, with Undo always available (recommended), or ask before each change.
2. **Mac-only preview** in this version (recommended), or wait for the tunnel-safe preview before shipping.
3. **Project folder** `~/codec-workspace/vibe/` (recommended).
