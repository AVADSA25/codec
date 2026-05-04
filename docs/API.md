# CODEC Dashboard API Reference

**Base URL:** `http://localhost:8090`
**Interactive Docs:** `http://localhost:8090/docs` (Swagger UI)
**Auth:** Bearer token (`Authorization: Bearer <token>`) or biometric session cookie.

---

## Health & Status

### GET /api/health
Public health check. No auth required.
```bash
curl http://localhost:8090/api/health
```
Response: `{"status": "ok", "service": "CODEC Dashboard", "timestamp": "2026-04-06T..."}`

### GET /api/status
Check CODEC main process status.
```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:8090/api/status
```

### GET /api/services/status
Background service status (scheduler, heartbeat, watcher).
```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:8090/api/services/status
```

---

## Chat & Commands

### POST /api/chat
Send a message to the LLM. Supports streaming (SSE).
```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "What is the weather?"}' \
  http://localhost:8090/api/chat
```

### POST /api/command
Queue a command for CODEC to execute.
```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"command": "open Chrome", "source": "api"}' \
  http://localhost:8090/api/command
```

---

## Memory

### GET /api/memory/search?q={query}&limit={n}
Full-text search across conversation history.
```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8090/api/memory/search?q=weather&limit=10"
```

### GET /api/memory/recent?days={n}
Recent messages from the last N days.
```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8090/api/memory/recent?days=7"
```

### GET /api/memory/sessions
List distinct conversation sessions.
```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:8090/api/memory/sessions
```

### POST /api/memory/rebuild
Rebuild the FTS5 full-text search index.
```bash
curl -X POST -H "Authorization: Bearer $TOKEN" http://localhost:8090/api/memory/rebuild
```

---

## Skills

### GET /api/skills
List all installed skills with metadata.
```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:8090/api/skills
```

### POST /api/forge
Convert code to a CODEC skill via Skill Forge.
```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"code": "def run(task): return str(2+2)", "name": "calculator"}' \
  http://localhost:8090/api/forge
```

### POST /api/skill/review
Stage a skill for review before saving.

### POST /api/skill/approve
Approve and save a reviewed skill.

---

## Agents & Crews

### GET /api/agents/crews
List available agent crews.
```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:8090/api/agents/crews
```

### POST /api/agents/run
Start an agent crew execution.
```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"crew": "deep_research", "task": "Research AI trends 2026"}' \
  http://localhost:8090/api/agents/run
```

### GET /api/agents/status/{job_id}
Poll agent job status.
```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:8090/api/agents/status/abc123
```

### GET /api/agents/tools
List available agent tools.

### POST /api/agents/custom/save
Save a custom agent configuration.

### GET /api/agents/custom/list
List saved custom agents.

---

## Autonomous Agents (Phase 3 — drop-a-project mode)

The agent system added in Phase 3. User drops a project description; Qwen-3.6 drafts a structured plan with explicit permission manifest; user approves once; `codec-agent-runner` PM2 daemon executes autonomously with permission gate enforcement, tamper detection, and resume-after-restart.

For full design, see `docs/PHASE3-BLUEPRINT.md`. For runtime architecture, see `docs/ARCHITECTURE.md` (Phase 3 sequence diagram).

### POST /api/agents
Create a new agent and draft its plan via Qwen-3.6 (typical 2–10 s).

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title":"Marbella property bot","description":"Build a Telegram bot that monitors Marbella property listings under €500k and pings me on new ones"}' \
  http://localhost:8090/api/agents
# → {"agent_id": "agent_abc123", "status": "awaiting_approval"}
```

### GET /api/agents
List all agents with current status. Polled by the PWA every 5 s for the agent status pills above the chat input.

```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:8090/api/agents
# → {"agents": [{"agent_id":"...","title":"...","status":"running","created_at":"..."}]}
```

### GET /api/agents/{id}
Full agent state — manifest + plan + state + grants in one response. The PWA's "View plan" button calls this.

### POST /api/agents/{id}/approve
Approve drafted plan. Re-validates skills against registry, computes plan_hash (sha256), writes grants.json, transitions `awaiting_approval → approved`. The daemon picks up `approved` agents within 5 s.

### POST /api/agents/{id}/reject
Body: `{"reason": "..."}` (optional). Transitions to `rejected`; plan dir kept 7 days for review then auto-deleted.

### POST /api/agents/{id}/revise
Body: `{"edited_plan": { ... full Plan dict ... }}`. User-edited plan, re-validated, transitions `awaiting_approval → revised → awaiting_approval`.

### POST /api/agents/{id}/abort
Atomic transition to `aborted`. Daemon checks status before each operation.

### POST /api/agents/{id}/pause / /resume
`paused → running` (resume), or `running → paused` (pause). Idempotent.

### POST /api/agents/{id}/grant
Grant a missing permission to a `blocked_on_permission` agent. Per-agent only (not global).

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"kind":"skills","value":"calculator"}' \
  http://localhost:8090/api/agents/agent_abc123/grant
```

`kind` ∈ `skills` / `read_paths` / `write_paths` / `network_domains`.

### POST /api/agents/{id}/extend_budget
Bump current checkpoint's step_budget. Only valid when `status=paused` AND `status_reason=step_budget_exhausted`.

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"additional_steps":20}' \
  http://localhost:8090/api/agents/agent_abc123/extend_budget
```

Returns `{previous_budget, new_budget, status:"running"}`. Override is written to `state.json` (plan stays immutable; tamper-hash check intact).

### GET /api/agents/{id}/messages
Return the full message timeline from `~/.codec/agents/{id}/messages.jsonl`.

```json
{"messages":[
  {"ts":"2026-05-03T12:15:00Z","type":"agent_update","title":"Checkpoint 2/5: Scaffolded bot","body":"...","actions":[...]}
]}
```

`type` ∈ `agent_update` / `agent_blocked` / `agent_question` / `agent_done` / `agent_aborted` / `user_reply`.

### POST /api/agents/{id}/messages
User reply — daemon picks up between checkpoints.

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -d '{"body":"please skip the email step and continue"}' \
  http://localhost:8090/api/agents/agent_abc123/messages
```

### POST /api/agents/{id}/silence
Toggle banner silence per-agent. Silenced = timeline messages still written; notifications.json banner skipped (no badge spam).

```bash
curl -X POST -d '{"silenced":true}' http://localhost:8090/api/agents/agent_abc123/silence
```

### Global allowlist (cross-agent permissions, Q4)

#### GET /api/agent_global_grants
Read the global allowlist.

#### POST /api/agent_global_grants
Add an entry. Body: `{"kind":"network_domains","value":"github.com"}`. Items added here are auto-approved on every future plan.

#### DELETE /api/agent_global_grants
Remove an entry. Same body shape.

`kind` ∈ `network_domains` / `read_paths` / `write_paths` / `skills`.

---

## Schedules

### GET /api/schedules
List all scheduled tasks.
```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:8090/api/schedules
```

### POST /api/schedules
Create a new scheduled task.
```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "Daily briefing", "cron": "0 8 * * *", "command": "run daily_briefing crew"}' \
  http://localhost:8090/api/schedules
```

### PUT /api/schedules/{id}
Update a scheduled task.

### DELETE /api/schedules/{id}
Delete a scheduled task.

### POST /api/schedules/{id}/run
Manually trigger a scheduled task.

### GET /api/schedules/history
View schedule execution history.

---

## History & Audit

### GET /api/history
Recent task execution history.
```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:8090/api/history
```

### GET /api/conversations?limit={n}&offset={o}
Paginated conversation history. Max limit: 500.
```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8090/api/conversations?limit=50&offset=0"
```

### GET /api/audit
Audit log entries.
```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:8090/api/audit
```

---

## Media & Vision

### GET /api/screenshot
Capture current screen.

### POST /api/vision
Send an image to the vision model for analysis.

### GET /api/clipboard
Read macOS clipboard contents.

### POST /api/clipboard
Set macOS clipboard contents.

### GET /api/tts?text={text}
Generate speech audio from text.

---

## Authentication

### GET /api/auth/check
Check available auth methods (Touch ID, PIN, TOTP).

### POST /api/auth/verify
Trigger Touch ID biometric verification.

### POST /api/auth/pin
Verify PIN code.

### POST /api/auth/totp/setup
Generate TOTP secret and QR code for 2FA setup.

### POST /api/auth/totp/verify
Verify TOTP code during login.

### POST /api/auth/logout
Invalidate current session.

### GET /api/auth/status
Check current session validity.

---

## WebSocket

### WS /ws/voice
Real-time voice pipeline. Send audio chunks, receive transcription + LLM responses.

Query params:
- `resume=<session_id>` — resume a previous session within 120s

---

## Notifications

### GET /api/notifications
List all notifications.

### GET /api/notifications/count
Get unread notification count.

### POST /api/notifications/{id}/read
Mark a notification as read.

### POST /api/notifications/read-all
Mark all notifications as read.

---

## Configuration

### GET /api/config
Get current editable configuration.

### PUT /api/config
Update configuration values.
```bash
curl -X PUT -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"tts_voice": "af_heart"}' \
  http://localhost:8090/api/config
```

---

## Heartbeat

### GET /api/heartbeat/config
Get heartbeat configuration.

### PUT /api/heartbeat/config
Update heartbeat settings.

### GET /api/heartbeat/alerts
Get configured heartbeat alerts.

### PUT /api/heartbeat/alerts
Update heartbeat alert configuration.
