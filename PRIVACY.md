# Privacy

CODEC is a **local-first** AI agent. The defaults are aggressive: nothing leaves your Mac unless *you* configure it to. This document is the plain-English version of what that actually means in code.

If you find a gap between this document and the code, open an issue — we treat docs/code drift as a real bug.

## What CODEC stores on your Mac

All of this lives under `~/.codec/` (your home directory, not a system path):

| Path | What | Format | Notes |
|---|---|---|---|
| `~/.codec/qchat.db` | Conversation history (every Chat, Voice, Dictate, Project session) | SQLite, **FTS5 full-text searchable** | Local-only. No remote sync. Searchable from the dashboard. |
| `~/.codec/vibe.db` | Vibe code-IDE sessions | SQLite | Local-only. |
| `~/.codec/audit.log` | 16-category JSONL audit trail of every dangerous action, skill fire, network call, file write | JSONL, **HMAC-SHA256 line-signed**, 50MB rotated | `chmod 0600`. Secrets are redacted (`codec_audit.SecretFilter`) before write. |
| `~/.codec/config.json` | Your CODEC configuration (LLM endpoints, voice settings, MCP allowlists) | JSON | `chmod 0600`. Holds API keys for the LLM provider you chose. |
| `~/.codec/skills/` | User-authored skill plugins | Python | Loaded under the AST safety gate + SHA-256 manifest. |
| `~/.codec/plugins/` | User-authored hook plugins | Python | Trust-on-first-use with explicit allowlist. |
| `~/.codec/auth_sessions.json` | Persisted biometric login sessions (cookies tied to Touch ID approval) | JSON | `chmod 0600`. Per-device. |
| `~/.codec/photos/`, `~/.codec/pwa_*` | Camera, screenshot, and PWA-uploaded image landing zone | binary | Local. Cleared on user request. |
| macOS Keychain — service `codec.*` | Long-lived secrets (Google OAuth tokens for Calendar/Drive/Gmail, license JWT, dashboard token, EdDSA Sparkle keys) | OS-encrypted | Read only on macOS user unlock. See `codec_keychain.py`. |

## What CODEC does **not** collect

- **No telemetry.** CODEC does not phone home. There is no analytics SDK, no error reporter, no usage beacon. Search the codebase for `requests.post` or `httpx.post` — every outbound call is either to a host *you* configured (LLM provider, your iMessage server, your MCP client, Cloudflare tunnel, etc.) or refused.
- **No cloud sync.** Your conversation DB, audit log, and skills never leave your Mac unless *you* configure a sync mechanism yourself.
- **No remote crash reports.** Tracebacks land in `~/.pm2/logs/` and `~/.codec/audit.log` — local only.
- **No advertising IDs, fingerprinting, or third-party trackers.**

## What CODEC sends *off* the Mac (and why)

CODEC has to make outbound HTTPS calls to do useful work. These are the categories:

| Category | When | Where | What's in the payload |
|---|---|---|---|
| **LLM provider you chose** | Every Chat/Voice turn, every agent step | Whatever you set in `config.json` — by default `http://localhost:1234` (LM Studio on this Mac, so *also local*) | Your prompt + relevant conversation context. **If you configure a cloud provider** (OpenAI, Anthropic, Gemini, AVA cloud proxy), prompts go there subject to their privacy policy. |
| **Voice TTS (Kokoro)** | When `tts_engine` is on | Default: `http://localhost:8080` (local). Optional: ElevenLabs, etc. | The text CODEC will speak. Local-only by default. |
| **Whisper STT** | Wake-word mode, dictation, voice calls | Default: `http://localhost:8084` (local) | The audio buffer to transcribe. Local-only by default. |
| **Vision (Qwen-VL / Gemini Flash)** | "Read my screen", `screenshot_text` skill | Default: `http://localhost:8083` (local Qwen). Optional fallback: Gemini Flash via your Google API key | The screenshot (base64). Local-only by default; cloud fallback is config-gated. |
| **Google APIs (Calendar, Gmail, Drive, Keep, Sheets, Slides, Tasks)** | Only when you trigger a skill that uses them | Google | OAuth-scoped per-product calls. Tokens stored in Keychain, never on disk. |
| **MCP clients (Claude Desktop, Cursor, VS Code)** | When you expose CODEC via MCP | Loopback (stdio) or `codec-mcp.lucyvpa.com` (your Cloudflare tunnel) | Whatever skill argument you sent + the response. OAuth 2.1 gated on HTTP transport. |
| **Sparkle auto-update (since v3.2)** | Every 6h while dashboard runs | `https://github.com/AVADSA25/codec-updates/releases/latest/download/appcast.xml` | Two requests: a GET for the appcast XML (your User-Agent + IP visible to GitHub), then a GET for the signed `.dmg` if you click *Download*. The DMG is Ed25519-verified before install. |

The audit log records every one of these calls. Open the dashboard's **Audit** tab to see them.

## Skills, plugins, and MCP — the user-curated trust boundary

CODEC executes Python code you author or download:

- **Built-in skills** in `skills/*.py` are SHA-256-pinned in `trusted_skill_manifest.json` and checked at every CI build. Tampering breaks the build.
- **User skills** in `~/.codec/skills/` are loaded under an AST safety gate (`codec_config.is_dangerous_skill_code`) — patterns like `eval`, `exec`, `subprocess` without sandboxing, raw `os.system` are refused at load time.
- **MCP tool calls** from external clients go through the same gate plus an explicit per-call allowlist (`mcp_allowed_tools` / `mcp_default_allow` in `config.json`).
- **Dangerous shell commands** are refused by a normalize-then-layered guard (`codec_config.is_dangerous`).

CODEC will not silently execute attacker-supplied code that arrives in an email, a webpage, or a chat message. Every skill fire is auditable. See `SECURITY.md` and `AGENTS.md §6` for the full threat model.

## What CODEC can see when a skill runs

A skill running with your operator privileges can — by definition — read what your Mac can read:

- The file the skill opens (e.g., `read_file_content` reads exactly the path you handed it)
- The screen, if it calls `screenshot_text` (vision goes to local Qwen by default, *or* your configured cloud fallback)
- The clipboard, if it calls `clipboard.read`
- Your Google account data, if you've consented to OAuth and the skill calls the matching scope (Calendar, Gmail, Drive, etc.)
- The microphone, if voice mode is on and you've granted mic permission

What it **cannot** see without an explicit OS prompt: your Keychain secrets (TCC-gated), other users' files, files outside `~/Documents/`/`~/Desktop/`/`~/Downloads/` without `NSDesktopFolderUsageDescription` consent, and anything macOS Full Disk Access would normally require.

## Children & sensitive use

CODEC is not designed for, marketed to, or appropriate for children under 13. The audit log is not sufficient evidence in regulated environments (HIPAA, PCI-DSS, FERPA) — CODEC is consumer/prosumer software, not a compliance-grade system.

If you're using CODEC for medical, financial, or legal work, your data still stays on your Mac, but you're responsible for the operator-level controls your regulator expects.

## Your controls

You can:

- **Inspect everything.** Open the dashboard's Audit tab. Or `cat ~/.codec/audit.log | jq .` from any terminal.
- **Delete everything.** `rm -rf ~/.codec/qchat.db ~/.codec/vibe.db ~/.codec/audit.log* ~/.codec/photos/` wipes your local history. The Sparkle update cache lives at `~/Library/Application Support/Sovereign AI Workstation/` — delete that too if you want a clean slate. **Keychain entries** under service prefix `codec.*` are deleted with `security delete-generic-password -s codec.<n>`.
- **Disable network egress.** Set `tts_engine: "disabled"`, point `llm_provider` at `http://localhost:1234` (local LM Studio / Ollama), revoke Google OAuth in your Google account settings, and CODEC operates fully offline.
- **Block specific MCP tools.** `mcp_default_allow: false` + an explicit `mcp_allowed_tools` array in `config.json`.
- **Audit any commit.** This is open-source. Every line that touches your data is at `github.com/AVADSA25/codec`.

## Changes to this document

We version this with the rest of the codebase. Changes go through the same review process as code. Subscribe to the repo (GitHub → Watch → Custom → Releases) to be notified when `PRIVACY.md` changes.

## Questions

- **Security disclosure:** see `SECURITY.md` — `security@avadigital.ai` or the GitHub Security tab.
- **Anything else:** open an issue at `github.com/AVADSA25/codec/issues` or email `farina.mickael@gmail.com`.

---

*Last updated: 2026-05-28. Reflects engine `codec` v3.2+.*
