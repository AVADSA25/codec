# Hue Bridge Auto-Discovery + Self-Healing — Design

**Status:** approved (2026-06-08) · **Skill:** `philips_hue` · **New module:** `codec_hue_discovery.py`

## Problem
`philips_hue` stores a hard-coded `hue_bridge_ip`. When DHCP reassigns the bridge a new
address (observed in the wild: `.192 → .81`), every command fails with *"Could not reach
Hue Bridge"* until the user manually edits `~/.codec/config.json`. Worse for "any CODEC
user adding a Hue light":
- **Router DHCP reservation is not universally available** — e.g. stock **Starlink**
  routers expose no reservation UI at all.
- **Cloud discovery** (`discovery.meethue.com`) returns empty behind **CGNAT/Starlink**.
- First-run setup currently requires hand-editing an IP + API key into config.

We need bridge setup + recovery to be **automatic, router-independent, and standard**, so
it just works for every user on any network.

## Verified facts (on the live Starlink LAN, 2026-06-08)
- **mDNS works**: `dns-sd -B _hue._tcp local.` → `Hue Bridge - 9A05E2` (instantly, no cloud).
- **Cloud discovery fails** (empty) — CGNAT.
- Bridge answers `GET http://<ip>/api/config` unauthenticated with `{"bridgeid": ...}`.

→ **mDNS is the correct primary**; cloud is a bonus for non-CGNAT users; a subnet scan
matching `bridgeid` is the guaranteed local fallback.

## Design

### New module `codec_hue_discovery.py` (no new dependency)
Uses macOS built-in `dns-sd` (mDNS), stdlib `socket`, and `requests` (already a dep).

- `verify_bridge(ip, expected_id=None) -> bridgeid|None` — `GET /api/config`; returns the
  bridge id iff reachable (and matching `expected_id` when given). Never raises.
- `_discover_mdns()` / `_discover_cloud()` / `_discover_scan()` — each returns a list of
  candidate IPs; best-effort, `[]` on any failure.
- `discover_bridge(expected_id=None) -> {"ip","id"}|None` — runs the ladder
  **mDNS → cloud → scan**, returns the first candidate that `verify_bridge` confirms
  (and matches `expected_id` if provided).
- `rediscover_and_update_config(path) -> ip|None` — re-finds the bridge by the stored
  `hue_bridge_id`, writes the new `hue_bridge_ip` **atomically** (tmp+fsync+rename, 0600),
  backfills `hue_bridge_id` if missing. Returns the new IP or None.

### Skill changes `skills/philips_hue.py`
- Extract `_run_once(task, ip, user)` (raises on connection failure).
- `run()` calls `_run_once`; on `ConnectionError`/`Timeout` → `rediscover_and_update_config`
  → if a **new** IP is found, retry `_run_once` **once** at the new IP. Transparent
  self-heal; the user never sees a stale-IP error if the bridge is on the LAN.
- Single-bridge homes self-heal even without a stored id (`discover_bridge(None)` finds the
  only bridge); `hue_bridge_id` makes it precise for multi-bridge setups.

### Config schema (additive)
New optional key `hue_bridge_id` (string, e.g. `"ECB5FAFFFE9A05E2"`). Backfilled on the
first successful discovery/verify. Purely additive — old configs work unchanged.

## Test plan (TDD, mocked network)
`tests/test_hue_discovery.py`:
- `verify_bridge`: match / id-mismatch / unreachable.
- `discover_bridge`: ladder order, skips unverified candidates, matches by id.
- `rediscover_and_update_config`: writes new IP atomically; preserves other keys.

`tests/test_philips_hue.py` (extend):
- `run()` self-heals: first call raises `ConnectionError`, rediscovery yields a new IP,
  retry succeeds → returns success (not the stale-IP error).
- regression: codec-scene fix still holds.

## Migration / rollback
- **Migration:** additive config key; existing `hue_bridge_ip` keeps working. `hue_bridge_id`
  is backfilled automatically. No user action.
- **Rollback:** revert the skill + delete the module; `hue_bridge_id` is then ignored
  (harmless).

## Security
- Discovery is **read-only** and **local-LAN only** (scan is the host's `/24`; cloud call is
  the same official endpoint the Hue app uses).
- `bridgeid` matching prevents binding to the wrong bridge.
- No new inbound surface, no new dependency, no cloud control path.

## Out of scope (follow-up)
- PWA "Add a Hue light" button (frontend) — this PR delivers the discovery + self-heal
  engine + a `pair()` helper; the dashboard affordance is a separate UI task.
- Linux portability of mDNS (`avahi-browse`/`zeroconf`) when CODEC ships beyond macOS.
