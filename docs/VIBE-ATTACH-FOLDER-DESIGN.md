# Vibe: attach an existing folder as a project (P-050) — design

Status: **for the owner's approval** (board card E-12, plan item P-050). No code before his word. It touches two AGENTS.md section 10 zones: the command sandbox (`codec_vibe_agent_cmd.profile`) and `<project>/.vibe/`.

Goal (plan line): an attached folder works with a stat-only scan and a detach (not trash); created projects behave byte-identically; real-sandbox tests pass.

## Why

Today a Vibe project is always a folder Vibe made under `~/codec-workspace/vibe/<id>/`. To work on an existing folder (a git repo in `~/Projects`, say) the owner has to copy files in, and a real repo does not fit: this repo has 1,184 tracked files (44 MB, deepest path 6 levels) against a cap of 200.

## What exists today (checked in the code, 11 Nov 2026)

- `codec_vibe_projects.project_dir` accepts only `<vibe root>/<id>` with a `.vibe/project.json`, real path checked. It is both where the files are and where `.vibe` is; about 20 callers in `codec_vibe_agent.py` and `codec_vibe_runner.py` use it both ways (run logs, `out/` files, notes, `run.json`, the folder a command runs in).
- `_walk` skips the folders in `_SCAN_IGNORED_DIRS` (`.vibe`, `.git`, `node_modules`, build and cache folders), links and files over 1 MB. It skips `.git` only as a folder: the `.git` *file* of a worktree or submodule is tracked. `_scan` reads and hashes every file and raises 413 past `MAX_FILES` (200); `list_files` is stat-only (P-066) with the same 413. `_snapshot_locked` runs `_scan` at every checkpoint, so every checkpoint reads every file.
- `_restore_locked` writes the target's files back and unlinks every tracked file the target does not have (the files as they were are saved first as `before_restore`).
- `safe_rel` refuses `.vibe`, `.git`, `node_modules`, `.ssh`, `.gnupg`, `.aws`, `.kube`, `.gcloud`, secret-looking names (`.env*`, keys, `.pem` ...), more than 8 levels and 200 characters. `_agent_rel` adds the D-23 names (`.vscode`, `.idea`, `.husky`, `.devcontainer`, `.github`, `.mcp.json`, `.claude`) and the root `AGENTS.md` / `CLAUDE.md`; chat-mode `apply_blocks` uses `safe_rel` only.
- `codec_vibe_agent_cmd.profile`: reads close the home folder and the whole vibe root, then open the toolchains, the project and the command's temp folder, then close keys, `~/.codec` and `<project>/.vibe`. Writes: the project and the temp folder, never `<project>/.vibe`, the `<project>/.git` entry, `.git/config` or `.git/hooks` (top level only).
- `codec_vibe_agent.read_notes` puts `.vibe/rules.md` and the root `AGENTS.md` into `[NOTES]` blocks, which the prompt trusts more than data.
- `trash_project` (`DELETE /api/vibe/projects/{id}`) moves the whole folder to `~/.codec/trash/vibe/<id>-<time>`.
- `codec_write_policy.check_write`: AI-driven writes only under `ALLOWED_ROOTS` (Documents, Desktop, Downloads, Projects, codec-workspace, /tmp), never in the Obsidian `_system` folder.

## 1. Where the record and `.vibe` live

**Recommendation: a record under the vibe root with a pointer; nothing is written into the attached folder except the files the owner or the agent change.** The record is `~/codec-workspace/vibe/<id>/.vibe/` with `attached.json` (title, the folder's real path, created and updated times, `allowed_commands`, the `agent` block), `checkpoints.jsonl`, `objects/`, `runs/`, `run.json`, `rules.md`. `project_dir` is split in two: `files_dir(pid)` (the attached folder) and `record_dir(pid)` (the record). For a created project both return today's path.

Why not `.vibe` inside the folder:
- **Safety.** A cloned repo could ship its own `.vibe/project.json` with `allowed_commands` filled in, a `.vibe/rules.md` the agent would trust as the owner's notes, or a made-up history. With the record outside, an in-folder `.vibe` is never read: the scan skips it, `safe_rel` refuses it, the profile closes it (all as today).
- **Git.** No `.vibe/` shows up as untracked, so no `.gitignore` or `.git/info/exclude` edit is needed.
- **Size.** Objects (every file version, see section 3) stay out of the repo folder and out of whatever syncs or backs up that folder.
- **Detach** is moving the record away; the folder is not touched (section 6).
- **Fits what exists.** The profile already closes the vibe root to commands, and `list_projects` and `codec_vibe_runner._reap_stale_once` already walk the vibe root.

Cost: if the owner moves or renames the folder, the record points at nothing; the project then opens with "The attached folder is missing" and offers Detach. The meta file is `attached.json`, not `project.json`, so an older build (a rollback) neither lists nor opens the record.

## 2. Which folders may be attached

**Recommendation:** attach is the owner's own act: a path he types or pastes in an "Attach a folder" dialog (a browser cannot hand the page a folder path), at this Mac only (403 otherwise, the `_is_remote_request` rule `routes/vibe_runner.py:_local` uses). No skill, agent, MCP tool, playbook or timer can attach. The path is resolved with `os.path.realpath` and must:
- be a folder, not a link itself, owned by the owner;
- sit at least one level inside one of the write-policy roots except `/tmp` (`~/Documents`, `~/Desktop`, `~/Downloads`, `~/Projects`, `~/codec-workspace`), so never `/`, the home folder, `~/Library`, `~/.codec`, `/Volumes`, a root itself, or the folder CODEC runs from;
- neither be inside nor contain the vibe root, the Vibe trash, `~/.codec`, the Obsidian `_system` folder or another attached folder (a folder that *contains* one of these would put it inside a command's write rule);
- pass the scan limits (section 3).

The same rules are checked again each time the project opens (`files_dir`), so a folder later swapped for a link, or moved somewhere refused, stops opening. Attaching a folder that already has a record opens that record.

## 3. Scan and limits

- **Listing is stat-only** (names, sizes, `stat`), as `list_files` already is. Attaching, opening the file tree and the agent's file map read no file. A file's content is read only when the owner opens it, the agent reads or searches it, or a version must be stored.
- **Skipped as today:** `_SCAN_IGNORED_DIRS`, links, files over 1 MB (listed as not versioned, `unversioned`). **Also skipped in an attached folder:** a `.git` entry at any depth, file or folder (worktrees and submodules), and secret-looking files (named but never read, hashed or copied; section 4).
- **Limits for an attached folder** (created projects keep 200 files and 8 levels): 2,000 tracked files, 100 MB of tracked content, 16 levels. Past a limit at attach time: **refused**, with the count and "attach a subfolder". Past a limit later (a build or a command added files): the project is **read-only** (read, search and chat still work; edits, applies, agent runs, restores and commands are refused with the reason) until it is back under; a run that crosses it ends `failed`, as P-064 does today.
- **First version at the first change, not at attach.** The Start checkpoint (every tracked file read and stored once) is taken by the first owner save, chat apply or agent run, or by "Save version". Until then the Versions drawer says "Versions start with your first change".
- **Later checkpoints read only what changed.** The record keeps a stat cache (path -> size, mtime, ctime, inode, sha256); a checkpoint reads and hashes only files whose entry differs. Ctime is in the key because a command can set mtime back (`touch -r`) but not ctime.

## 4. Writes

- **Same single writer.** Every write still goes through `_write_bytes_locked` (`safe_rel`, `_no_symlinks`, atomic replace) against `files_dir`. Refused for everyone, owner included: the `.git` entry and everything under it at any depth, `.vibe`, `node_modules`, `.ssh` and the other `_WRITE_BLOCKED_SEGMENTS`, secret-looking names.
- **The agent** (`agent_write`, `agent_delete`) keeps `_agent_rel` (D-23 names, root `AGENTS.md`/`CLAUDE.md`) and `_not_playbooks`, unchanged. **Chat apply in an attached folder** goes through `_agent_rel` too: in a real repo a `.github/workflows` file runs on the next push. The owner's own edits in the editor stay as free as today.
- **Notes.** `.vibe/rules.md` lives in the record (the owner's). The folder's own `AGENTS.md`/`CLAUDE.md` were written by the repo's authors, so they become `[NOTES]` only if the owner ticks "use this folder's notes" when attaching; otherwise the agent reads them as plain files (data).
- **Checkpoints, objects, the stat cache and run logs** are written only into the record. Secret-looking files and `.git` entries are never in a manifest, so restore never writes or deletes them.
- **Undo and Rewind are scoped** in an attached folder: they put back only the files Vibe's own AI changes (chat applies, agent steps and their commands) touched since that point, so work from git, the editor or a build stays. Restore from the Versions drawer stays whole-folder and first says how many other files it would change. The `before_restore` checkpoint is taken as today.
- The 409 hold (`agent_claim`) keeps other Vibe writers out during a run; it cannot keep other programs out. Outside changes simply show up in the next checkpoint.

## 5. Commands (the sandbox profile)

- **Write scope: the attached folder only**, plus the command's own temp folder, exactly the shape of today's rule. Never the folder's parent, the record (the vibe root stays closed to reads and was never writable), a worktree's real git folder outside the attached folder, or `~/.gitconfig`. Network is as today (none, or web ports and name lookups when the owner ticks it).
- **Attached-only lines that take rights away** (a new `attached=True` argument; without it `profile()` returns today's text byte for byte): writes refused to any `.git` entry at any depth, to `.git/config` and `.git/hooks` at any depth, and to `.git/modules/**/config|hooks` (submodules, a follow-up D-23 listed); reads refused to secret-looking files (`.env*`, keys, `.pem`, credentials) inside the folder.
- **This needs the owner's word, stated plainly.** Pointing the profile's write rule at a folder Vibe did not create is a section 10 change, and this note asks for it. Nothing else is widened. Any further widening (a second folder, the parent, git's own config) needs his word again.
- The Run button (step 2b, `codec_vibe_runner`) is unchanged: the owner's own typed command, outside the sandbox, in `files_dir`; its dialog says the command runs this folder's own scripts. The static server keeps its rules (no dotfiles, no `.vibe`, no links out).

## 6. Delete becomes Detach

For an attached project the delete button reads **Detach**, and `DELETE /api/vibe/projects/{id}` detaches. Nothing in the folder is moved, renamed or deleted, including any `.vibe` someone else left there. The record (history, objects, run logs, allowed commands) moves to `~/.codec/trash/vibe/<id>-<time>` like a deleted project today, so nothing is erased. Refused with 409 while an agent run holds the project; a served or running command of the project is stopped first. Attaching the folder again starts a new record with a fresh history. A fork of an attached project (Versions, "Copy to a new project") is a normal created project under the vibe root.

## 7. Created projects stay byte-identical

Everything above is behind `attached`. For a created project `files_dir == record_dir == project_dir`, the scan hashes every file at every checkpoint, the limits stay 200 files and 8 levels, chat apply keeps `safe_rel`, and `profile()` without `attached` returns today's text. Pinned by: every existing test in `tests/test_vibe_projects.py`, `test_vibe_v4.py`, `test_vibe_runner.py`, `test_vibe_agent*.py` and `test_vibe_agent_cmd.py` passing with no line edited (the D-23 proof); a new test comparing `profile()` for a created project with a copy of today's output for fixed made-up paths; a new test that a created project's folder after create, apply, undo and restore holds the same files and checkpoint lines as before (times left out) and no stat cache.

## 8. Tests, rollback, build order

**Tests.** Temp folders only: the allowed roots, vibe root and trash pointed at pytest's temp folder through the existing overrides (`CODEC_VIBE_ROOT`, `CODEC_VIBE_TRASH`) plus a new one for the roots; never a fake `HOME`. Sandbox tests run on a Mac with `sandbox-exec`, and every command carries `vibe-run-test`.
- Attach refused for each rule of section 2, each broken on purpose (a link, the home folder, a root, a folder holding `_system`, the vibe root, a folder inside another attached one, a folder over the limits).
- Listing reads no file (reads counted); `.git` as a file and as a folder skipped at any depth; a planted in-folder `.vibe/project.json` with `allowed_commands` and a planted `.vibe/rules.md` never reach a run (canary text).
- Writes: owner and agent refused on `.git`, `.vibe`, secret names; agent and chat apply refused on the D-23 names; the owner's edit allowed.
- Real sandbox, attached folder: a normal file written; refused on `.git`, `.git/config`, `.git/hooks/x`, `sub/.git`, `sub/.git/config`, `.git/modules/m/hooks/x`, the parent folder, a sibling, the record; `.env` not readable.
- Stat cache: a command that changes a file and puts its mtime back is still caught.
- Scoped undo leaves a file changed outside Vibe as it is; detach leaves the folder identical (every file, dotfiles included, with mtimes) and puts the record in the trash; detach during a run answers 409.

**Rollback.** `VIBE_ATTACH_ENABLED=false`: attach answers 404 and attached records are neither listed nor opened; the folders and records are left alone. Reverting the PRs: older code looks for `.vibe/project.json`, so it never sees an attached record and can never write into it. Nothing to migrate back; the files the owner or the agent changed in an attached folder stay (his work). Deploy and rollback need a `codec-dashboard` restart (his word).

**Build order, one small PR each, after his word on this note:**
1. Split `project_dir` into `files_dir` / `record_dir` in `codec_vibe_projects`, `codec_vibe_agent`, `codec_vibe_runner`. No feature; existing tests unchanged.
2. Engine: attach (section 2 rules), `attached.json`, stat-only listing and limits, detach. No route.
3. Checkpoints for attached folders: first version at the first change, stat cache, secret files and `.git` entries left out, scoped undo and rewind.
4. Sandbox `attached=True` lines, chat apply through `_agent_rel`, the notes tick; real-sandbox tests.
5. Routes (Mac-only attach, detach on DELETE), the page (Attach a folder, Detach), audit events `vibe_project_attach` / `vibe_project_detach` (project id and counts, never a path), AGENTS.md sections 7 and 10, `docs/codec-architecture.md`.

Done means his live check: attach a clone of a real repo in `~/Projects`, an agent run with one approved command, Undo, Detach, and `git status` in the folder showing only the changes he kept.

## Questions for the owner

1. **Record location:** keep `.vibe` in a record under `~/codec-workspace/vibe/<id>/` with a pointer, and write nothing into the folder but the files you or the agent change? (Recommended: yes. A cloned repo cannot plant settings, and git sees no `.vibe/`.)
2. **Which folders:** only folders at least one level inside Documents, Desktop, Downloads, Projects or codec-workspace (never `/tmp`, a root itself, the home folder, Library, `~/.codec`, the CODEC code folder, the vibe root or trash, anything holding the Obsidian `_system`); real path, no link, owned by you; attached only from this Mac, by you typing the path? (Recommended: yes.)
3. **Limits:** 2,000 files, 100 MB, 16 levels; refuse at attach, read-only if the folder grows past later? (Recommended: yes.)
4. **First version** taken at the first change, not at attach, so attaching and browsing read no file? (Recommended: yes.)
5. **Undo and Rewind** in an attached folder put back only the files Vibe's AI changes touched; whole-folder Restore stays and says first how many other files it would change? (Recommended: yes. Otherwise an Undo can revert a `git pull`.)
6. **Secret files** (`.env`, keys) in an attached folder: never copied into versions, and agent commands may not read them? (Recommended: yes. A dev server that needs `.env` then fails inside an agent check; the Run button still works.)
7. **AI writes:** chat-mode apply in an attached folder also refuses `.github`, `.vscode`, `.husky` and the other D-23 names, as the agent does? (Recommended: yes; created projects unchanged.)
8. **The folder's own `AGENTS.md`/`CLAUDE.md`** become agent notes only if you tick it at attach; otherwise plain files? (Recommended: off by default.)
9. **Sandbox:** you agree the command write rule may point at a folder you attach, plus the attached-only refusals (`.git` entries, hooks and config at any depth, submodules; secret-file reads), and nothing widened beyond that? (Recommended: yes; any later widening comes back to you.)
10. **Detach** moves the record (history, run logs, allowed commands) to the CODEC trash, touches nothing in the folder, and re-attaching starts a fresh history? (Recommended: yes.)
