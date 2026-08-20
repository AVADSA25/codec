# CODEC status — gaps and in-progress work

Current as of 2026-08-20. Changing state — update in place, do not let it go stale.

### Other known gaps (tracked for Phase 3.5 follow-on)
- **Project mode UI** — `codec_dashboard.html` does not yet have a mode-dropdown selector or agent status pills. Backend supports project dispatch via `POST /api/agents`; UI affordances deferred to Phase 3.5 alongside proactive overlay.
- **Proactive intelligence overlay** — observer-driven contextual nudges ("you've been on this Notion doc 30 min, want a summary?") deferred per Q12. Step 10 backend done; Phase 3.5 layers proactive on top.
- **`blocked_on_qwen` dedicated status** (Step 9 review C2) — Qwen unavailability currently maps to `blocked_on_permission` with reason. Phase 3.5 may introduce dedicated status with daemon-driven auto-resume.
- **Read-paths runtime enforcement** (Step 9 review M4) — `PermissionManifest.read_paths` declared but not gated; documented inline. Phase 3.5 may add `Action.reads_path` field + LLM prompt update.
- No formal teammate / sub-agent recursion — Crew is the only multi-agent primitive
- (Phase 3 backend complete after Step 10 ships; Phase 3.5 = UI + proactive + Step 9 review polish)

### Gap notes moved out of CLAUDE.md

- **§3 Custom agents** — Live job state lives in the in-memory `_agent_jobs` dict and does NOT survive a `codec-dashboard` restart.
- **§3 Step 10** — PWA HTML for the Project-mode dropdown + status pills is deferred to Phase 3.5, alongside the proactive intelligence overlay.
- **§4 Adding a new skill** — Hot-reload is not supported — adding or editing a skill needs `codec-dashboard` and `open-codec` restarted.
- **§7 Keychain (PR-2B-2)** — Residual: `alerts.telegram.bot_token` (a SEPARATE nested key under `alerts`, not the audit-named `telegram.bot_token`) is still plaintext. The `auth_pin_hash` argon2id refactor remains deferred.
