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
