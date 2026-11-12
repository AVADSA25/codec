# Project mode: plan path guard (R-02, 2026-11-12)

Owner approval: decision board card R-02, 12 Nov 2026. Covers the plan path
blocklist (`_PATH_BLOCKLIST_SUBSTRINGS` and its segment matcher in
`codec_agent_plan.py`): (a) compare case-folded; (b) refuse a read/write grant
that contains a blocked folder; (c) anchor the system entries at the filesystem
root. Keep the D-16 segment rule (`notes_ssh` is not `.ssh`).

## What
All in `codec_agent_plan.py`, behind `_is_path_blocklisted` (its one entry point):

- `_path_segments_match` compares segments case-folded, on the path made
  absolute (`expanduser` + `abspath`: a relative path is read against the
  process's folder, as the runner's realpath reads a relative grant, so
  `../../etc/passwd` is still caught). An entry in the new
  `_PATH_BLOCKLIST_ROOT_ANCHORED` (`/etc/`, `/var/`, `/private/`, `/System/`,
  `/usr/local/etc`) matches only at the filesystem root; every other entry still
  matches as a segment sequence anywhere.
- `_grant_contains_blocked(path)` is new. The grant's root is the path before its
  first `*`, `?` or `[`; a glob inside a segment (`~/.co*/**`) makes the folder
  above the root, and a bare glob (`**`, `*.md`) is rooted at `/`, because the
  runner's `_path_allowed` reads it that way. The grant is
  refused when that root is a blocked folder or a parent of one. Blocked folders:
  each anchored entry as written, every other entry both under the home folder
  (`~/.ssh`, `~/Library/Keychains`) and at `/` (`/Library/Keychains`).
- An empty grant is not refused (the runner skips it); an unparseable one is
  (fail-safe, as before).

## Why
`_is_path_blocklisted` compared segments exactly, so `~/.SSH/**` passed on the
Mac's case-insensitive disk. A parent passed too (`~/**`, `~/.codec/**`), and the
runtime gate (realpath prefix) then let the agent read `~/.ssh/...` under it. And
`/var/` matched a folder named `var` anywhere, so `~/work/var/site` was refused.
(docs/known-issues.md, P-098 review.)

## Behaviour change
- Refused now: `~/.SSH/**`, `/ETC/passwd`; `~`, `~/**`, `~/.codec/**`,
  `~/Library/**`, `~/.config/**`, `/`, `/**`, `**`, `/Users/**`, `/usr/**`,
  `/Library/**`, `~/*/skills/**`.
- Accepted now: `~/work/var/site/**`, `~/work/etc/...`, `~/src/private/...`.
- Unchanged: `~/Documents/notes_ssh/**`, `~/Documents/**`, `/tmp/agent-work`
  (lexical, not realpath'd, so `/tmp` is not `/private/tmp`).
- Every caller follows it: typed paths (`extract_user_paths` drops them),
  `plan_from_dict` and `approve_plan` (a drafted plan ends `plan_failed`, an edited
  one is refused with 400, approval writes no grants), and the `/grant` and
  global-grant routes (`routes/agents.py:_grant_path_unsafe`, 400). The runner's
  `permission_gate` does not read the list; nothing changes there.
- A stored plan that lists a newly refused path no longer loads, as P-098 made
  it for the older entries: a run of it is aborted. On the owner's Mac (read-only
  count, 12 Nov) one of 34 plans is affected, and it is already aborted.
- Not covered: `~/.codec/notes/**` and other `~/.codec` folders that are not on
  the list (they contain no blocked entry), and a symlink whose target is a
  parent of a blocked folder (the check is lexical; realpath would turn `/tmp`
  into `/private/tmp`).

## Tests
`tests/test_plan_path_guard.py`: refused and accepted paths through
`plan_from_dict` (read and write), a relative grant read from the working folder,
typed paths, the `/grant` and global-grant routes, and `approve_plan` writing no
grants for `~/**`. 28 of its 37 cases fail on the old matcher. The existing
D-14 / D-16 tests (`test_agent_plan.py`, `test_project_gate.py`,
`test_grant_blocklist.py`) pass unchanged.

## Rollback
`git revert` of the PR. Nothing stored changes. Takes effect after the owner
restarts `codec-dashboard` and `codec-agent-runner`.
