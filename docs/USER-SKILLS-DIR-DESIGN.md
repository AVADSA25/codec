# User skills folder: approved skills go to ~/.codec/skills, and CODEC loads it

Asked by Mickael on 2026-10-07 ("point approve at ~/.codec/skills") after the Mac merge pass found that approved
skills land in the repo's `skills/` folder (docs/known-issues.md).

## Why

- `POST /api/skill/approve` (the review-and-approve flow, and Settings > Learning's Approve and install) writes to
  `codec_config.SKILLS_DIR`. That is `config.json:skills_dir`, or the repo's own `skills/` folder when the key is not
  set, as on this Mac. An approved skill becomes an untracked file in the live git tree, next to the hash-pinned
  built-ins.
- Every process builds its skill registry with `SkillRegistry(SKILLS_DIR)`, and the registry scans that one folder.
  So `~/.codec/skills`, which AGENTS.md calls the user folder and which the marketplace installs into, is never
  loaded on this Mac. Writing approved skills there alone would make them dead files.

## What

1. `codec_config.USER_SKILLS_DIR`: `config.json:user_skills_dir`, default `~/.codec/skills`.
2. `SkillRegistry(skills_dir, user_dir=None)`: when `user_dir` is given and differs from `skills_dir`, `scan()` reads
   the built-in folder first, then the user folder. A user file whose skill name is already a built-in is skipped
   (logged), so a user file can never shadow a built-in. User files are never trusted by the manifest: every load
   runs the AST gate (`is_dangerous_skill_code`), as unmanifested files do today. Files starting with `_` and
   sub-folders are not scanned, as today.
3. The registries in `codec_dispatch`, `codec_mcp`, `codec_voice`, `codec_agents`, `codec_autopilot` and
   `codec_slash_commands` pass `USER_SKILLS_DIR`.
4. `/api/skill/approve` writes to `USER_SKILLS_DIR` (folder 0700, file 0600). The built-in-name refusal and the
   write-time AST gate stay as they are. The audit line `skill_approved` gains `dir: "user"`.
5. Settings > Learning's "installed" check and `codec_self_improve`'s existing-name scan look in both folders.
6. AGENTS.md §4 and the Learning page text say where approved skills go.

No change to the manifest, the built-in folder, the AST gate, the security lists or MCP exposure: a user skill's
`SKILL_MCP_EXPOSE` is honoured as before, and over HTTP the blocked and ask-first lists apply.

## Migration (needs Mickael's OK: it is his folder)

`~/.codec/skills` holds 6 old files (March-April 2026) that have never been loaded on this Mac. Five fail the AST
gate. `lucy.py` passes, so it would start running. Before the first restart they move to
`~/.codec/skills/_dormant-2026-10-07/` (not scanned; nothing deleted). Any of them can be moved back by hand.

## Deploy

Restart `codec-dashboard` and `open-codec`. Until Mickael restarts `codec-mcp-http`, `codec-agent-runner` and
`codec-autopilot`, those keep scanning the repo folder only, so an approved skill is not yet reachable from Claude
over MCP, project agents or autopilot.

## Test plan

`tests/test_user_skills_dir.py`: the registry finds built-in and user skills; a built-in wins a name clash; a user
file is never trusted by hash and a dangerous one is refused at load; `_` files and sub-folders are skipped; approve
writes to the user folder with 0600 and still refuses a built-in name; the Learning installed flag and the
self-improve name scan see user skills. Full suite with an empty HOME (CI has no ~/.codec).

## Rollback

Revert the PR. Skills approved meanwhile stay in `~/.codec/skills` and are no longer loaded; move them to the repo
folder by hand if needed.
