# PR-7C — `/api/agents/grant` path-blocklist validation (Audit B / B-3)

> Wave 7. Closes the concrete, high-value part of **B-3**. Reference:
> `docs/audits/PHASE-1-PROJECTS-PILOT.md` B-3.

## The hole

`grant_permission` (`routes/agents.py`) appended `body.value` to the agent's
`grants.json` for any `kind` with **no path validation**:

```python
grants[body.kind] = sorted(set(grants.get(body.kind, []) + [body.value]))
```

So a caller could `POST /api/agents/{id}/grant {"kind":"write_paths","value":"/"}`
and turn a running agent into an **arbitrary-write primitive** — and it bypassed
the PR-1D `_PATH_BLOCKLIST_SUBSTRINGS` that plan-drafting already enforces.

## The fix

A module-level `_grant_path_unsafe(value)` predicate; the endpoint returns
**400** for `read_paths`/`write_paths` grants that fail it. Rejects:

1. empty / whitespace,
2. a `..` traversal segment,
3. `_cap._is_path_blocklisted(value)` — reuses the PR-1D segment-aware blocklist
   (`~/.ssh`, `~/.aws`, `~/.codec/*`, `/etc`, `/var`, `/private`, `/System`, …),
4. an **over-broad root**: the expanded path equals `/` or the bare `$HOME`, or
   sits under a system top (`/etc`, `/usr`, `/System`, `/Library`, …).

Uses the **expanded (not realpath'd)** path for the root check so the macOS
`/tmp → /private/tmp` alias doesn't false-positive a legit `/tmp` grant. Glob
grants (`~/Projects/app/*.py`) are handled by truncating at the first `*`.

`skills` / `network_domains` grants are unchanged (B-3 is about paths; skill
grants are already constrained by the registry at runtime).

## Scope / what's deferred

- **In:** path-grant blocklist + realpath-traversal + over-broad-root rejection
  (the "grant write_paths=/ → arbitrary write" hole — the concrete CRITICAL part).
- **Deferred (noted in the audit/HANDOFF):** per-agent *ownership* authz (any
  dashboard-authenticated caller can still grant to any agent). That only
  matters if the dashboard goes multi-user; today it's single-user behind the
  global `AuthMiddleware` + loopback binding (PR-2A/2D). Treating grant-widening
  as a *consent* action (vs outright reject) is also a later refinement —
  outright-rejecting the dangerous set is stronger for those paths.

## Test plan (`tests/test_grant_blocklist.py`)

- `_grant_path_unsafe`: parametrized unsafe (`/`, `~`, `~/.ssh/id_rsa`,
  `/etc/passwd`, `../etc`, …) vs safe (`~/Documents/x`, `~/Projects/*.py`,
  `/tmp/work`, …).
- Endpoint (TestClient + an approved agent): grant `write_paths=/` → **400**, not
  saved; `read_paths=~/.ssh/id_rsa` → **400**; `write_paths=~/Documents/out.md`
  → **200**, saved; `skills=calculator` → **200** (unaffected).

## Rollback

One predicate + one guard line in `routes/agents.py` + one test. `git revert`.
