# Keeping the paid Mac app in lockstep with codec-repo

**Goal:** the paid / Apple-distributed "Sovereign AI Workstation" and the
open-source CODEC engine are **the same code at the same version, every time** —
never a hand-maintained parallel fork.

## The model: one source of truth, a thin paid layer

```
codec-repo @ <VERSION>          ← single source of truth for ALL CODEC code
        │
        ├── OSS build            = codec-repo as-is (MIT, free, local-first)
        │
        └── Paid build           = codec-repo @ <VERSION>  +  licensing overlay
                                    (the ONLY things that differ are config/flags,
                                     never forked .py code)
```

The paid build differs from OSS in exactly three places — all **configuration or
feature-flags**, never duplicated code:

1. **License enforcement** — client validates the signed JWT, honours expiry +
   tier, with an offline grace window. *(client-side enforcement: still a gap —
   see `docs/audits/PHASE-1-APPLE-APP.md`.)*
2. **Cloud-first default config** — `~/.codec/config.json` ships with
   `proxy_url` + `default_cloud_model` so a buyer works out-of-the-box without a
   23 GB local-model download.
3. **Optional "no local model" mode** — Qwen download deferred to Settings.

## Why this guarantees "same same every time"

The `VERSION` file is the sync anchor (F-5 single source of truth):

- `release_macos.sh` defaults its build version to the repo-root `VERSION` file,
  so a release **always** produces `Sovereign-AI-Workstation-<VERSION>.dmg` built
  from *this* checkout. Bump `VERSION` → the paid DMG version moves with it. No
  separate version to forget.
- The paid app therefore = codec-repo at the tagged commit. There is no second
  copy of `codec_*.py` to drift.

## The two pipelines (must converge)

| | `codec-repo/packaging/macos/` | `~/ava-stack/installer-gui/` |
|---|---|---|
| Builds | the **bundled CODEC app** (Sovereign AI Workstation.app) from codec-repo | a thin Swift **bootstrapper installer** (license activation + config writer) |
| Notary profile | `ava-codec` (default, real) | `ava-codec` |
| Version | from `VERSION` file | should consume the codec-repo build |

**Convergence plan:**
1. `codec-repo/packaging/macos/release_macos.sh` builds + signs + notarizes the
   real app from codec-repo@VERSION (notary profile aligned to `ava-codec`).
2. The ava-stack installer wraps **that build** (or downloads the codec-repo
   release at the pinned tag) — it provides license activation + cloud-first
   config, then drops the codec-repo-built app into `/Applications`.
3. Result: one artifact, versioned from one number, identical code free vs paid.

## Operator checklist for a paid release

1. `git tag vX.Y.Z` on codec-repo (matches `VERSION`).
2. `packaging/macos/release_macos.sh --identity "Developer ID Application: AVA Digital L.L.C (PUY3J6235N)"`
   → produces `dist/Sovereign-AI-Workstation-X.Y.Z.dmg`, signed + notarized via `ava-codec`.
3. ava-stack installer points at that DMG/app (bundle or download-at-tag).
4. Flip `ava-license` server out of `env=dev`; ship the client-side license
   enforcement (the remaining code gap).

## What still differs / is pending (not code-fork issues — product work)

- **Client-side license enforcement** — currently displays the JWT but doesn't
  validate/expire/gate. Highest-priority paid gap.
- **W5-11 Swift setup wizard** — not started.
- **W5-13 Sparkle auto-update** — not started (needs an EdDSA key + appcast feed).
- **W5-10 per-customer tunnels** — only a shared tunnel today.

These are intentionally config/overlay concerns layered on the *same* codec-repo
code — keep them that way and the two builds never drift.
