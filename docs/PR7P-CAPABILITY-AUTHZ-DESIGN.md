# PR-7P — B-2 capability table (server-derived resource gating) + B-3 forensic audit

**Status:** design → TDD → ship
**Closes the deferred remainders of:** Audit-B **B-2** (permission_gate trusts LLM-declared
resource flags) + **B-3** (per-agent ownership authz). Decisions made by Mickael 2026-05-24:
B-2 = **central capability table, default-deny**; B-3 = **defer ownership authz + forensic audit**.
**Branch:** `fix/pr7p-capability-authz`
**Touches:** `codec_agent_runner.py` (B-2) + `routes/agents.py` (B-3).

## B-2 — central `SKILL_CAPABILITIES` table + OR-upgrade default-deny

**Problem:** `permission_gate` only checks paths/domains when `action.touches_path` /
`reads_path` / `network_call` are True — all LLM-declared. The model can set them False to
skip the gate while the skill still acts (amplified by prompt injection feeding skill
output back into the next action prompt).

**Fix:** a curated `SKILL_CAPABILITIES` table (single dict in `codec_agent_runner`) maps
each skill → the resource categories it can use (`{"writes_path","reads_path","network"}`).
`permission_gate` computes the **effective** want as `LLM_flag OR table_capability` — the
**same OR-upgrade pattern as `_effective_destructive`** (the LLM can only *raise* risk,
never lower it). A skill that the server knows writes paths is gated on `write_paths`
**even if the LLM said `touches_path:false`**; a write-capable action with no declared path
fails `_path_allowed("")` → `PermissionViolation` (default-deny — forces declaration, which
is then gated).

**Classification by exfil surface (judgment call, documented):**
- `writes_path` / `reads_path`: the local-FS skills (`file_write`, `file_ops`, `file_search`)
  and the do-anything skills (`terminal`, `python_exec`, `process_manager`, `pm2_control`,
  `ax_control`, `pilot`, `create_skill`, `skill_forge`, `self_improve`, `screenshot_text`,
  `qr_generator`).
- `network`: genuinely exfil-capable skills — `web_fetch`, `web_search`, `ai_news_digest`,
  `clipboard_url_fetch`, all `chrome_*`, all `google_*`, `imessage_send`, `translate`,
  `philips_hue`, `health_check`, plus the do-anything skills above (they can `curl`).
- **Intentionally NOT network-gated:** benign read-only public-data skills (`weather`,
  `bitcoin_price`) — they send only a non-sensitive query and return public data (no
  user-data exfil surface). This also keeps `weather` usable as the test fixture without
  forcing a domain grant on every test. Documented risk acceptance.
- Everything else (calculator, timers, clipboard, volume, etc.) → **no caps** → unchanged
  behavior (LLM flags only — and they have no real resource surface anyway).

**Why this is safe for existing flows:** the OR-upgrade only *adds* gating when a
capable skill's action *under-declares* (the bypass signature). A legitimately-declared
write (`touches_path:true` + path) is gated exactly as before. Unclassified skills are
unchanged. The residual — extracting the *exact* path/URL a skill will act on from its
free-text `task` (vs trusting `action.path`) — still needs structured skill invocation and
is documented as the remaining B-2 work (mitigated by manifest grants + realpath/glob
confinement (PR-7L) + the `file_write` blocklist (PR-1C)).

## B-3 — defer ownership authz + forensic audit

Per the decision, per-agent **ownership** authz stays out of scope for the single-user
threat model (loopback + global `AuthMiddleware` + PR-7C grant-value blocklist are the
boundary). This PR adds **forensic visibility**: state-changing `/api/agents/*` mutations
(`approve`, `abort`, `grant`) emit an `agent_mutation` audit event with the caller IP
(`request.client.host`), so a localhost-foothold abuse is detectable after the fact. A
code comment + the finding note document ownership-authz as deferred-until-multi-user.

## Schema / API changes

- New `SKILL_CAPABILITIES` dict + `_skill_capabilities(name)` in `codec_agent_runner`;
  `permission_gate` gains the OR-upgrade (no signature change).
- `approve_plan` / `abort_agent` / `grant_permission` gain a `request: Request` param (to
  read the caller IP) + an `agent_mutation` audit emit. New audit event name
  `agent_mutation` (info; `extra={agent_id, mutation, client_ip}`).
- No on-disk schema change.

## Rollback

Revert the single commit. The OR-upgrade only tightens (revert loosens to LLM-flag-only);
the audit emit + Request params are additive. No data migration.

## Test plan (TDD)

`tests/test_capability_gate.py` (B-2):
1. `test_write_capable_skill_gated_despite_false_flag` — `file_write` action with
   `touches_path=False` + a path outside grants → `PermissionViolation` (bypass closed).
2. `test_network_capable_skill_gated_despite_false_flag` — `web_fetch` with
   `network_call=False` + ungranted domain → `PermissionViolation`.
3. `test_unclassified_skill_unaffected` — a no-caps skill with all flags False → passes
   (no behavior change).
4. `test_benign_read_skill_not_network_gated` — `weather` with `network_call=False` →
   passes (documented exclusion; keeps fixtures green).
5. `test_capabilities_table_covers_dangerous_skills` — `terminal`/`python_exec`/`file_write`/
   `web_fetch` are all classified (guard against an empty/forgotten table).

`tests/test_mutation_audit.py` (B-3):
6. `test_grant_emits_mutation_audit_with_ip` — `grant_permission` emits `agent_mutation`
   with the caller IP.

Full suite: zero new failures vs the 41-failed baseline (the runner suite must stay green —
the OR-upgrade must not break legitimately-declared actions). Ruff: zero delta.
