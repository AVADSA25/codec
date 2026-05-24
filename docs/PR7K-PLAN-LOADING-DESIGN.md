# PR-7K — Robust plan loading: schema migration ladder + dataclass-splat guard (Audit B / B-13 + B-19)

**Status:** design → TDD → ship
**Closes:** Audit-B **B-13** (no plan/grants schema migration ladder) + **B-19** (dataclass
splat of user/LLM JSON raises unhandled `TypeError`).
**Branch:** `fix/pr7k-plan-loading`
**Touches:** `codec_agent_plan.py` only.

## What

Two coupled robustness fixes in `plan_from_dict` (the single inverse-of-`to_dict` loader
that every create / revise / on-disk-load path funnels through):

1. **B-13 — schema migration ladder.** `plan_from_dict` hard-rejects `schema != 1` with
   `ValueError`. The moment `PLAN_SCHEMA_VERSION` bumps to 2, **every existing on-disk
   plan becomes permanently unloadable** — every in-flight + historical Project breaks on
   upgrade. Add an ordered migration ladder (analogue of `codec_config._CONFIG_MIGRATIONS`)
   run *before* the strict check. Give per-agent grants a real `GRANTS_SCHEMA_VERSION`
   constant (today it carries a bare literal `"schema": 1`).

2. **B-19 — dataclass splat guard.** `Checkpoint(**cp)` / `PermissionManifest(**d[...])`
   splat raw JSON. An extra key (the LLM emits `priority`; a malformed PWA `revise`
   payload) raises `TypeError` that not every caller catches → a 500 / unhandled error
   instead of a clean failure. Filter each dict to the dataclass's known fields before
   splatting, and convert any residual construction error to a clean `ValueError`.

## Why it matters

- B-13: a schema bump is a *when*, not an *if* (the runtime is young). Without a ladder,
  the first bump silently bricks every saved plan — the worst kind of upgrade regression.
- B-19: `load_plan → plan_from_dict` (the on-disk path) does **not** wrap its result, so a
  malformed plan (LLM drift, hand-edit, partial write) throws a raw `TypeError` up the
  stack. Centralizing the guard in `plan_from_dict` makes every caller safe at once.

## Design

### B-13 — plan migration ladder

```python
_PLAN_MIGRATIONS: Dict[int, Callable[[Dict[str, Any]], Dict[str, Any]]] = {}
# keyed by SOURCE version N → migrates dict from vN to vN+1, setting d["schema"]=N+1.
# Empty at v1 (first version). To add v1→v2: write _migrate_plan_v1_to_v2, register
# it as _PLAN_MIGRATIONS[1] = ..., and bump PLAN_SCHEMA_VERSION to 2.

def _migrate_plan_dict(d):
    ver = d.get("schema")
    if not isinstance(ver, int):
        return d                      # non-int → caller's strict check rejects cleanly
    while ver < PLAN_SCHEMA_VERSION and ver in _PLAN_MIGRATIONS:
        d = _PLAN_MIGRATIONS[ver](d)
        ver = int(d.get("schema", ver + 1))
    return d
```

`plan_from_dict` runs `d = _migrate_plan_dict(d)` **before** the `schema != PLAN_SCHEMA_VERSION`
check. A schema *newer* than we understand, or an old schema with a gap in the ladder, is
left unchanged → the existing strict check rejects it with a clear `ValueError` (we never
silently load a plan we can't actually migrate).

### B-13 — grants version constant

Add `GRANTS_SCHEMA_VERSION = 1`; `approve_plan` writes `"schema": GRANTS_SCHEMA_VERSION`
instead of the bare literal. **Deliberately NOT** adding a `load_grants` migration hook in
this PR: `compute_grants_hash` (B-4 tamper detection) hashes the loaded grants, so a
load-time mutation would change the hash and risk a false tamper-abort. The constant gives
future grants migrations a home; whoever adds the first one must re-sync the B-4 hash in
the same change (documented inline).

### B-19 — field-filtered construction

```python
_CHECKPOINT_FIELDS = {f.name for f in fields(Checkpoint)}
_MANIFEST_FIELDS   = {f.name for f in fields(PermissionManifest)}

def _checkpoint_from_dict(cp): return Checkpoint(**{k: v for k, v in cp.items() if k in _CHECKPOINT_FIELDS})
def _manifest_from_dict(m):    return PermissionManifest(**{k: v for k, v in m.items() if k in _MANIFEST_FIELDS})
```

`plan_from_dict` wraps the whole construction in `try/except (TypeError, KeyError)` →
`raise ValueError(f"malformed plan: {e}")`. So:
- extra keys (LLM `priority`, etc.) → **filtered, loads fine**;
- missing required keys → clean `ValueError` (which `PlanValidationError` extends, and
  which every existing caller's `except (KeyError, ValueError, TypeError)` already handles).

## Schema / API changes

- New module-level `_PLAN_MIGRATIONS`, `_migrate_plan_dict`, `_checkpoint_from_dict`,
  `_manifest_from_dict`, `_CHECKPOINT_FIELDS`, `_MANIFEST_FIELDS`, `GRANTS_SCHEMA_VERSION`.
- `plan_from_dict` behavior: now migrates-then-validates, filters unknown keys, raises
  `ValueError` (not bare `TypeError`) on malformed structure. No signature change.
- `PLAN_SCHEMA_VERSION` stays **1** (no on-disk format change in this PR).

## Migration

None needed now (ladder is empty at v1). The point is forward-safety: a future bump won't
brick existing plans. Existing v1 plans load unchanged.

## Test plan (TDD — `tests/test_plan_loading.py`)

1. `test_v1_plan_still_loads` — schema=1 round-trips (no regression).
2. `test_migration_ladder_upgrades_old_schema` — register a fake `_PLAN_MIGRATIONS[old]`
   via monkeypatch; an old-schema dict is upgraded + loads (mechanism proven for the real
   future bump).
3. `test_future_schema_rejected` — schema far above current with no migration → `ValueError`.
4. `test_unknown_checkpoint_key_tolerated` — checkpoint with extra `priority` → loads.
5. `test_unknown_manifest_key_tolerated` — manifest with extra key → loads.
6. `test_malformed_plan_raises_valueerror` — checkpoint missing `id` → `ValueError`, not `TypeError`.
7. `test_grants_carry_version_constant` — after `approve_plan`, `grants["schema"] == GRANTS_SCHEMA_VERSION`.

Full suite: zero new failures vs the 41-failed baseline. Ruff: zero delta vs origin/main.

## Rollback

Revert the single commit. All additions are internal helpers; `plan_from_dict`'s external
contract is strictly more lenient (accepts extra keys, migrates old schema) and raises a
cleaner error type — reverting only re-introduces the brittleness.
