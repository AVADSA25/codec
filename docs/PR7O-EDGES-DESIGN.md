# PR-7O — Interface edges: crew/Project collision guard + outbound-content opt-in (Audit B / B-16 + B-17)

**Status:** design → TDD → ship
**Closes:** Audit-B **B-16** (two agent runtimes share one URL namespace + storage dir) +
**B-17** (outbound channel dispatch uses a plaintext token + exfils agent content).
**Branch:** `fix/pr7o-edges` (stacked on `fix/pr7n-runner-loop` until #127 merges).
**Touches:** `routes/agents.py` (B-16) + `codec_agent_messaging.py` (B-17).

## What

1. **B-16 (MEDIUM)** — in-memory crews/custom agents (`agents/<id>.json`) and on-disk
   Phase-3 Projects (`agents/<id>/`) share `~/.codec/agents/` with no collision guard: a
   custom-agent slug can shadow a Project. **Scope (judgment call):** the full URL
   namespacing (`/api/crews` vs `/api/projects`) is a breaking, PWA-facing rename — deferred
   as a larger refactor. This PR closes the actual **safety** gap: refuse to save a custom
   agent whose id shadows an existing Project (and document the separation).

2. **B-17 (LOW)** — `post_message` best-effort POSTs agent **title+body** (which can include
   read file contents / fetched data) to Telegram/iMessage, using a token from plaintext
   `config.json`. Two fixes: (a) **gate outbound agent CONTENT behind an explicit per-agent
   opt-in** (default OFF — local-first); without opt-in, off-device channels get a
   content-free ping that points at the PWA. (b) **prefer the Keychain-backed token**
   (`codec_config.get_telegram_bot_token`, PR-2B-2) over the plaintext config key.

## Why it matters

- B-16: a slug collision silently entangles two unrelated agents in the same storage
  namespace — confusing at best, a wrong-agent-operation hazard at worst.
- B-17: the local-first contract is that user data doesn't leave the machine unless the user
  explicitly routes it out. Auto-exfiltrating agent output (file contents!) to Telegram the
  moment a channel is listed violates that; the token also sat outside the Keychain
  hardening.

## Design

### B-16 — custom/Project collision guard (`routes/agents.py`)

```python
def _custom_id_shadows_project(safe_id: str) -> bool:
    """A custom-agent id must not shadow a Phase-3 Project (agents/<id>/manifest.json)."""
    return os.path.isfile(os.path.join(_AGENTS_DIR, safe_id, "manifest.json"))
```

`save_custom_agent`: after computing `safe_id`, if `_custom_id_shadows_project(safe_id)` →
**409** (don't write the file). Non-breaking; no endpoint rename. The deeper
`/api/crews` vs `/api/projects` split is noted as deferred.

### B-17 — outbound-content opt-in + Keychain token (`codec_agent_messaging.py`)

- `_REMOTE_CHANNELS = {"telegram", "imessage"}` (leave the device; `macos` is a local banner).
- `_outbound_content_allowed(agent_id) -> bool` — reads manifest `allow_outbound_content`
  (default **False**).
- In `_dispatch_to_channel`, for a remote channel when not allowed, replace the title/body
  with a content-free ping (`"You have a new agent update. Open the CODEC dashboard…"`)
  **before** the channel-specific send — so no agent content is exfiltrated. macOS banners
  (local) are unaffected.
- Telegram token: `get_telegram_bot_token()` (Keychain) preferred, falling back to the
  plaintext `notifications.telegram_token`. `chat_id` stays in config (not a secret).

## Schema / API changes

- New manifest field `allow_outbound_content` (bool, default False) — additive; absent =
  False (safe). New helpers `_custom_id_shadows_project`, `_outbound_content_allowed`,
  constant `_REMOTE_CHANNELS`. No on-disk-schema / audit change. `save_custom_agent` now can
  return 409.

## Rollback

Revert the single commit. The opt-in defaults off (so reverting only *loosens* to the prior
always-exfil behavior); the collision guard is additive. No data migration.

## Test plan (TDD — `tests/test_edges.py`)

B-16:
1. `test_custom_id_shadows_project_detects_collision` — `agents/foo/manifest.json` exists →
   `_custom_id_shadows_project("foo")` True, `"bar"` False.
2. `test_save_custom_agent_refuses_project_collision` — `save_custom_agent({"name":"foo"})`
   with a Project `foo` present → 409, no `foo.json` written.

B-17:
3. `test_remote_channel_redacts_content_without_optin` — no `allow_outbound_content`; the
   Telegram POST body contains the generic ping, NOT the agent body text.
4. `test_remote_channel_sends_content_with_optin` — `allow_outbound_content: true`; the POST
   body contains the real agent text.
5. `test_telegram_token_prefers_keychain` — `get_telegram_bot_token` → "KCTOKEN"; the POST
   URL uses KCTOKEN, not the plaintext config token.

Full suite: zero new failures vs the 41-failed baseline. Ruff: zero delta vs origin/main.
