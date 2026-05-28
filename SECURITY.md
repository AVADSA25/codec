# Security Policy

CODEC is a local-first AI agent that executes code, controls the keyboard/mouse, reads the screen, and writes files on the user's behalf. We take security reports seriously and appreciate responsible disclosure.

## Supported versions

| Version | Supported |
|---|---|
| Engine `codec` v3.2+ (current) | ✅ |
| Engine `codec` v3.0–v3.1 (recent past) | ✅ |
| Anything pre-v3.0 | ❌ |

The engine and the macOS app bundle now share a single version line — both read from the [`VERSION`](VERSION) file at build time (see commit `b41844f`). Security fixes land on the current release line. There is no long-term-support branch yet.

## Reporting a vulnerability

**Please do not open a public issue for security reports.** Use one of these private channels:

1. **GitHub Private Vulnerability Reporting (preferred):** on this repo, go to the **Security** tab → **Report a vulnerability**. This opens a private advisory only the maintainers can see.
2. **Email:** `security@avadigital.ai` — encrypt with our PGP key if you have sensitive details (key on request).

Please include: affected component/version, reproduction steps or PoC, impact, and any suggested remediation.

### Our commitment

- **Acknowledge** your report within **72 hours**.
- **Triage + severity assessment** within **7 days**.
- **Fix** high/critical issues within **30 days** (lower severity on a best-effort basis), and credit you in the advisory unless you prefer to remain anonymous.

## Scope

**In scope** (engine code we ship and control):
- `codec_*.py` engine modules, `routes/`, the dashboard + PWA (`codec_dashboard.py`, `codec_dashboard.html`).
- The MCP transports (`codec_mcp.py` stdio, `codec_mcp_http.py` HTTP) and the OAuth 2.1 flow (`codec_oauth_provider.py`).
- The skill execution + load-time safety gate (`codec_skill_registry.py`, `codec_config.is_dangerous_skill_code`) and the plugin trust model (`codec_hooks.py`).
- The audit log integrity + secret-redaction layer (`codec_audit.py`), Keychain handling (`codec_keychain.py`), and the sandboxed exec path (`codec_sandbox.py`, `python_exec`).
- The agent permission gate (`codec_agent_runner.py`) and dangerous-command guard (`codec_config.is_dangerous`).

**Out of scope** (the user's own trust boundary, by design):
- User-authored skills in `~/.codec/skills/` and plugins in `~/.codec/plugins/` (the user curates these; the load-time AST gate + SHA-256 allowlist is the documented control, not a sandbox).
- The user's chosen LLM provider, API keys, and any data the user routes to a cloud model.
- Self-XSS / attacks requiring the user to paste attacker-supplied commands into their own machine.
- Findings that require prior root/Keychain-extraction-grade access to the user's Mac (that threat level is out of model — see `AGENTS.md §7`).

## Hardening already in place

For context, CODEC ships with: a load-time AST safety gate on skill loading, a SHA-256 manifest/allowlist for built-in skills + plugins, `sandbox-exec` + `rlimit`-confined `python_exec`, a normalize-then-layered dangerous-command blocker, HMAC-SHA256-signed + secret-redacted audit logs (0600), macOS-Keychain secret storage, per-process HMAC internal-IPC tokens, Touch ID / PIN dashboard auth, and an OAuth 2.1 MCP HTTP transport. See `docs/audits/` and `AGENTS.md §6-§10`.

## Related documents

- **[`PRIVACY.md`](PRIVACY.md)** — what data CODEC stores locally, what it sends off the Mac when, and the controls you have.
- **`AGENTS.md`** — engine architecture, plugin trust model, agent permission gates (§6–§10).
- **`docs/audits/`** — Wave 1 + Wave 2 hardening evidence, Pilot adversarial audit (PP-1…PP-12).
