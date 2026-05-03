# Phase 2 Step 6 — Runtime trigger mute config

**Status:** Live. Follow-up to Phase 2 Step 6 (Trigger System).
**Module:** `codec_triggers.py`
**Audit event:** `trigger_muted` (`PHASE2_STEP6_EVENTS`)
**Config file:** `~/.codec/triggers.json`

---

## 0 · Why this exists

Phase 2 Step 6 lets skills declare a `SKILL_OBSERVATION_TRIGGER` that
auto-fires on observer signals. In practice some triggers turn out to
be too noisy for a given user — the obvious example is
`clipboard_url_fetch`, which prompts on every URL copied. Pre-mute, the
only remediations were:

1. Comment out the `SKILL_OBSERVATION_TRIGGER` block in the skill file
   (loses the declaration entirely; PR #38 used this).
2. PWA-toggle the per-trigger kill switch (silent, persistent, but
   per-pattern-hash — editing the pattern resets it).
3. Set `TRIGGERS_ENABLED=false` (kills ALL triggers).

None of these scale — option 1 needs a code edit, option 2 hides the
state from the audit log entirely, and option 3 is a sledgehammer.

The runtime mute config gives users a fourth path: **soft-disable a
skill's auto-fire by name, leave the skill code untouched, and keep the
suppression visible in the audit log.**

---

## 1 · Config shape

Path: `~/.codec/triggers.json`

```json
{
  "muted_skills": ["clipboard_url_fetch"],
  "muted_until": {
    "stripe_dashboard_helper": "2026-12-01T00:00:00Z",
    "csv_validator": "2026-06-15T18:00:00+02:00"
  }
}
```

Fields:

| Field | Type | Meaning |
|---|---|---|
| `muted_skills` | `list[str]` | Skill names that are **permanently** muted until removed from the list. |
| `muted_until` | `dict[str, str]` | Skill names mapped to ISO-8601 timestamps (UTC `Z` or `±HH:MM` offsets). The skill is muted **until** that timestamp passes. |

Either field may be absent; both default to empty in user-supplied
configs.

A skill is muted if:

- it appears in `muted_skills`, **OR**
- `muted_until[skill]` parses to a future timestamp.

Past timestamps in `muted_until` are silently ignored (the skill is no
longer muted; the entry is left in the file as a record).

---

## 2 · Defaults

When `~/.codec/triggers.json` does not exist, the code falls back to:

```python
_DEFAULT_MUTE_CONFIG = {
    "muted_skills": ["clipboard_url_fetch"],
    "muted_until": {},
}
```

This preserves the old PR #38 behavior (`clipboard_url_fetch` does not
auto-fire) without requiring a code edit. **As soon as you write a
`triggers.json` file, your file is the source of truth — defaults are
not merged in.** If you create a triggers.json with empty
`muted_skills`, `clipboard_url_fetch` will start auto-firing.

This is intentional: explicit > implicit. The user file is canonical.

---

## 3 · Examples

### 3a · Mute the noisy clipboard URL fetcher (default behavior)

No config file needed — the default applies. Or, if you have a config
file:

```json
{
  "muted_skills": ["clipboard_url_fetch"]
}
```

### 3b · Mute multiple skills

```json
{
  "muted_skills": ["clipboard_url_fetch", "stripe_dashboard_helper"]
}
```

### 3c · Snooze a skill until next month

```json
{
  "muted_until": {
    "csv_validator": "2026-06-01T00:00:00Z"
  }
}
```

### 3d · Re-enable `clipboard_url_fetch`

Write the file with the skill removed from `muted_skills`:

```json
{
  "muted_skills": []
}
```

Then restart `codec-observer` (or the process running `evaluate()`) so
the cache picks up the new file. The trigger declaration in
[skills/clipboard_url_fetch.py](../skills/clipboard_url_fetch.py) is
already active — no code edit needed.

---

## 4 · Caching + reload

The parsed config is cached in process memory after the first
`_load_mute_config()` call. To pick up hand-edits to
`~/.codec/triggers.json`:

- **Recommended:** restart the service that runs `evaluate()` —
  typically `codec-observer` (`pm2 restart codec-observer`).
- **For tests / interactive sessions:** call
  `codec_triggers._refresh_mute_cache()`.

A future setter API (e.g. `POST /api/triggers/mute/{skill}` parallel to
the existing `POST /api/triggers/{key}/kill`) would invalidate the
cache automatically; that is intentionally not in this PR.

---

## 5 · Audit visibility

When a trigger matches the observer snapshot but is suppressed by mute,
the evaluation pipeline emits the new `trigger_muted` audit event:

```json
{
  "ts": "2026-05-03T12:14:23.451+00:00",
  "schema": 1,
  "event": "trigger_muted",
  "source": "codec-triggers",
  "outcome": "warning",
  "level": "warning",
  "extra": {
    "trigger_key": "clipboard_url_fetch:8a3f1b22",
    "skill_name": "clipboard_url_fetch",
    "trigger_type": "clipboard_pattern",
    "mute_source": "muted_skills",
    "correlation_id": "<inherited from observer poll>"
  }
}
```

For `mute_source = "muted_until"`, the event also carries the raw
`muted_until` timestamp string in `extra.muted_until`.

Why warning, not info? Mute is a deliberate user signal that suppresses
otherwise-eligible automation. Surfacing it at warning lets the
audit_report skill flag it for review (you may want to re-enable it,
or you may have forgotten the entry exists).

The pre-existing `trigger_evaluated` event still fires *before* the
mute check, so you can see in the audit log:

1. `trigger_evaluated` — the snapshot matched the pattern
2. `trigger_muted` — but the user has it suppressed

If you want a trigger fully silenced (no audit emit at all), use the
per-trigger kill switch (`POST /api/triggers/{key}/kill`) instead.

---

## 6 · Comparison: kill vs mute

| Property | Kill switch (`triggers_killed.json`) | Mute config (`triggers.json`) |
|---|---|---|
| Granularity | Per-trigger (skill + pattern hash) | Per-skill name |
| Persistence | Atomic-write to disk | User-edited JSON |
| API setter | `POST /api/triggers/{key}/kill` | None in this PR (manual edit) |
| Audit on block | Silent (no emit) | Emits `trigger_muted` (warning) |
| Reset on pattern edit | Yes (key changes) | No (skill name unchanged) |
| Time-bounded | No | Yes (`muted_until`) |
| Default state | All triggers enabled | `clipboard_url_fetch` muted |
| Use when… | You changed your mind about a specific pattern | You want a whole skill silenced (or temporarily snoozed) |

Both layers compose: a killed trigger is silently skipped before the
match check; a muted skill is loudly skipped after the match check
(so audit captures relevance).

---

## 7 · Implementation notes

- **No file is auto-created.** The first `_load_mute_config()` call on
  a fresh install reads defaults from code — it does not write a
  starter file. Users opt in to a config file by creating it.
- **Fail-open on parse errors.** If `triggers.json` is malformed JSON,
  the code logs a warning and applies defaults. This avoids accidentally
  un-muting a noisy trigger because of a missing comma.
- **No merging.** A user-supplied config replaces defaults entirely. To
  preserve the default mute *and* add your own, copy the default list:
  `{"muted_skills": ["clipboard_url_fetch", "your_skill"]}`.
- **Timezone handling.** Timestamps without an explicit timezone are
  treated as UTC. Use `Z` or `+00:00` for clarity.
- **Mute is checked AFTER `trigger_evaluated` but BEFORE cooldown,**
  so muted triggers do not consume cooldown budget and do not get
  `trigger_blocked: cooldown` emits.
