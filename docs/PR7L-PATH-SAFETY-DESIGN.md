# PR-7L — Path safety: open-folder realpath confinement + glob-grant precision (Audit B / B-15 + B-18)

**Status:** design → TDD → ship
**Closes:** Audit-B **B-15** (`open-folder` runs `open` on a stored path with no
revalidation) + **B-18** (`_path_allowed` collapses glob grants to their directory root).
**Branch:** `fix/pr7l-path-safety`
**Touches:** `routes/agents.py` (B-15) + `codec_agent_runner.py` (B-18).

## What

1. **B-15 (MEDIUM)** — `open_folder` runs `subprocess.Popen(["open", project_dir])` on
   `manifest.project_dir` with no confinement. `project_dir` is derived from the
   (slugified) title or could come from a tampered manifest; `open` will launch
   apps/bundles for non-dir targets — a low-effort local-trigger primitive (esp. combined
   with B-3's missing per-agent authz). Confine `project_dir` (realpath) under the
   configured project root (`_cap._PROJECT_ROOT`) and reject symlinks before `open`.

2. **B-18 (LOW)** — `_path_allowed` strips a grant's glob suffix to its directory root and
   accepts anything under it: a grant of `~/Documents/*.md` silently authorizes
   `~/Documents/secrets.key`. Match the **full glob** (`fnmatch` on realpath) **in
   addition to** the existing realpath-containment check, so a specific glob is enforced.

## Why it matters

- B-15: `open` is an execution primitive. Pointing it at an arbitrary path (or a
  `.app`/`.command` bundle) outside the sandboxed project tree is a local trigger an
  attacker could chain. Read-only `os.listdir` endpoints are lower risk and **out of scope
  here** (B-15 is specifically the `open` exec).
- B-18: a write-path grant should mean what it says; collapsing `*.md` to the whole
  directory widens the blast radius of every glob grant.

## Design

### B-15 — confine project_dir before `open`

New helper in `routes/agents.py`:

```python
def _project_dir_confined(project_dir: str) -> bool:
    """Realpath-confine project_dir under _cap._PROJECT_ROOT and reject symlinks,
    so `open` can't be aimed at an arbitrary path / app bundle via a tampered
    manifest or slug collision (B-15)."""
    if not project_dir:
        return False
    try:
        root_real = os.path.realpath(os.path.expanduser(str(_cap._PROJECT_ROOT)))
        pd = os.path.expanduser(project_dir)
        if os.path.islink(pd):
            return False                       # explicit: reject symlinked project dirs
        pd_real = os.path.realpath(pd)
    except (OSError, ValueError):
        return False
    return pd_real == root_real or pd_real.startswith(root_real + os.sep)
```

`open_folder`: after the existing `isdir` check, if `not _project_dir_confined(project_dir)`
→ emit a best-effort `open_folder_blocked` audit and return **400** (not run `open`).
Confinement uses realpath on both sides (so a symlink that escapes the root is also caught
by containment, belt-and-suspenders with the explicit `islink` reject).

### B-18 — enforce the glob, keep PR-1D's safety

`_path_allowed` keeps **both** PR-1D guards (reject `..`; realpath the action) and the
realpath-containment check. It **adds** an fnmatch test for glob grants:

```python
prefix = grant_expanded[:glob_idx]                       # before first '*'
grant_real_root = realpath(prefix.rstrip(os.sep) or os.sep)
under_root = action_real == grant_real_root or action_real.startswith(grant_real_root + os.sep)
if not under_root:        # PR-1D containment (prevents symlink/`..` escape)
    continue
if glob_idx < 0:          # plain dir/file grant → authorizes its subtree (unchanged)
    return True, ""
pattern = grant_real_root + os.sep + grant_expanded[glob_idx:]   # realpath-anchored glob
if fnmatch.fnmatch(action_real, pattern):                # B-18: the glob must actually match
    return True, ""
# else: under the root but doesn't match the glob → keep checking other grants
```

**This only ever tightens.** The fnmatch test is an *additional* constraint layered on top
of the containment check, so nothing PR-1D accepted-for-safety can now be accepted that
wasn't already contained — it can only *reject* a path that's under the root but doesn't
match the specific glob (exactly B-18's intent). Key invariants preserved:
- `..` rejection and action-realpath: unchanged.
- `**` grants (incl. production's default `{project_dir}/**`): fnmatch translates `*`→`.*`
  (crosses `/`), so `{root}/**` still matches the whole subtree. **No regression** to the
  common case.
- Plain directory grant (`~/Documents`, no glob): still authorizes its subtree.
- Plain file grant (`~/x/report.md`, no glob): still authorizes exactly that file.
- Only **specific** globs (`*.md`, `*/notes.txt`) newly enforce their pattern.

## Schema / API changes

None. New internal helper `_project_dir_confined`; `_path_allowed` behavior tightens for
specific-glob grants only; `import fnmatch` added to the runner. No on-disk/schema/audit
changes (the `open_folder_blocked` emit reuses the generic audit envelope).

## Migration / rollback

None needed. Revert the single commit to restore prior behavior. No data shape changes.

## Test plan (TDD — `tests/test_path_safety.py`)

B-15 (against `routes.agents.open_folder`, `subprocess.Popen` spied):
1. `test_open_folder_allows_in_root` — project_dir under `_PROJECT_ROOT` → `open` invoked.
2. `test_open_folder_rejects_outside_root` — dir outside root → `open` NOT invoked, 400.
3. `test_open_folder_rejects_symlink` — in-root symlink → `open` NOT invoked (islink reject).

B-18 (against `_path_allowed`):
4. `test_specific_glob_enforced` — grant `{p}/*.md`: `a.md` allowed, `a.key` **denied** (the fix).
5. `test_recursive_glob_allows_subtree` — grant `{p}/**`: deep file allowed (production default — no regression).
6. `test_plain_dir_grant_allows_subtree` — grant `{p}` (no glob): child allowed (unchanged).
7. `test_dotdot_still_rejected` — `..` in action path → `path_traversal` (PR-1D not regressed).

Full suite: zero new failures vs the 41-failed baseline. Ruff: zero delta vs origin/main.
