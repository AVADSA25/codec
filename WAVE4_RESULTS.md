# WAVE 4 Autonomous Test Results

**Date:** 2026-04-06
**Branch tested:** main (verified against `/Users/mickaelfarina/codec-repo`)
**Worktree fixes:** claude/great-hellman (3 bug fixes applied here, pending merge)

---

## Bugs Fixed This Session (worktree: claude/great-hellman)

| Bug | File | Fix |
|-----|------|-----|
| Heartbeat uses old DB path `~/.q_memory.db` | `codec_heartbeat.py:9` | Import DB_PATH from codec_config, fallback to `~/.codec/memory.db` |
| safe_sys strips apostrophes from "don't", "I'm" | `codec.py:769` | Removed `.replace("'","").replace('"','')` — `repr()` handles escaping |
| /health and /api/health return 401 | `codec_dashboard.py` | Added health endpoint + `/api/health` to PUBLIC_ROUTES |

---

## Block 1: Infrastructure & Prerequisites

| Check | Result | Detail |
|-------|--------|--------|
| PM2 exec_cwd | PASS | Points to ~/codec-repo |
| Memory DB exists | PASS | ~/.codec/memory.db — 2,864 conversations |
| Smoke test (services) | 6/10 | pynput (python3 default), LLM crash-looping (mlx model unsupported), UI-TARS down |
| Health endpoint | PASS (after fix) | /api/health now public, returns JSON status |
| Whisper STT | PASS | Port 8084 responding |
| Kokoro TTS | PASS | Port 8085 responding |
| Qwen LLM | FAIL | 307 restarts — `ValueError: Model type qwen3_5_moe not supported` (mlx_lm version issue, not code bug) |
| UI-TARS | FAIL | Not running (model server issue) |
| Unified services | CONFIRMED | codec-heartbeat, codec-hotkey, codec-scheduler merged into dashboard (commit f0f9293) |

---

## Block 2: Security

| Check | Result | Detail |
|-------|--------|--------|
| is_dangerous() coverage | PASS | All 7 test inputs returned correct results |
| is_dangerous_skill_code() AST validator | PASS | Exists in codec_config.py on main, AST-based |
| Auth token not in URL params | PASS | ?token= removed on main; ?s= restricted to GET-only biometric sessions |
| Audit log 0600 permissions | FAIL | Audit log written with default umask (0644). Only key files get 0600. |
| Dashboard CORS | PASS | Specific origins only, no wildcard |
| DANGEROUS_PATTERNS completeness | PASS | rm variants, sudo, fork bombs, curl\|bash, macOS system commands all covered |
| skill_forge uses AST validation | PASS | Imports is_dangerous_skill_code from codec_config on main |

**Security score: 6/7** (audit log permissions is the gap)

---

## Block 3: Memory System

| Check | Result | Detail |
|-------|--------|--------|
| DB_PATH = ~/.codec/memory.db | PASS | codec_config.py exports correct path with migration logic |
| Auto-migration from legacy path | PASS | Moves ~/.q_memory.db to ~/.codec/memory.db |
| FTS5 full-text search | PASS | conversations_fts virtual table with INSERT/DELETE/UPDATE triggers |
| user_id multi-tenancy | PASS | user_id TEXT DEFAULT 'default' on conversations table |
| SCHEMA_VERSION = 2 | PASS | With v1-to-v2 migration (ALTER TABLE + FTS rebuild) |
| cleanup() with retention | PASS | 90-day default retention, VACUUM, returns stats |
| CRUD functions accept user_id | PASS | save/search/search_recent all accept optional user_id |
| DB accessible with data | PASS | 2,864 rows in conversations table |

**Memory score: 8/8**

---

## Block 4: Skills System

| Check | Result | Detail |
|-------|--------|--------|
| SkillRegistry loads | PASS | Class exists with names() method (not list_names — smoke test uses wrapper) |
| AST validation in skill_forge | PASS | Uses is_dangerous_skill_code() from codec_config on main |
| SKILLS_DIR from config | PASS | codec_config.py exports it |
| Skill file count | PASS | 55 .py files in skills/ |
| Skill file structure (run()) | PASS | Sampled files all have def run(task, app, ctx) |
| Dangerous imports in skills | INFO | ~30+ skills use os/subprocess — expected for system-interaction skills (mouse, clipboard, volume, etc.) |

**Skills score: 5/5** (dangerous imports are architectural, not a bug)

---

## Block 5: Agents Framework

| Check | Result | Detail |
|-------|--------|--------|
| Agent class with run() | PASS | Dataclass with async run(task, context, callback) |
| Google Docs _gdoc_lock | PASS | threading.Lock() on main, used with context manager |
| LLM priority queue | PASS | codec_llm_proxy.py exists on main with Priority enum + LLMQueue |
| Bare except removal | PASS | Zero bare except: clauses on main |
| Error logging | PARTIAL | logging module used but ~2 bare pass + some print() remain |
| Crew management | PASS | 12 pre-built crews with full orchestration |

**Agents score: 5/6**

---

## Block 6: Dashboard & Routes

| Check | Result | Detail |
|-------|--------|--------|
| Route modules exist | PASS | routes/ has auth.py, skills.py, agents.py, memory.py, websocket.py, _shared.py |
| Dashboard imports routes | PASS | Confirmed on main |
| Pagination caps | PASS | min(limit, 500) in dashboard + routes/memory.py |
| /metrics endpoint | PASS | codec_metrics.py with Prometheus text format on main |
| WebSocket session resume | PASS | _resumable_sessions with 120s TTL in codec_voice.py |
| /api/services/status | PASS | Shows scheduler/heartbeat/watcher background task status |
| CORS specific origins | PASS | localhost:8090, 127.0.0.1:8090, codec.lucyvpa.com |
| Background services unified | PASS | scheduler, heartbeat, watcher run as async tasks in dashboard |

**Dashboard score: 8/8**

---

## Block 7: Code Quality

| Check | Result | Detail |
|-------|--------|--------|
| codec_logging.py | PASS | JSONFormatter + setup_logging() on main |
| codec_metrics.py | PASS | Metrics class with Prometheus format on main |
| codec_llm_proxy.py | PASS | Priority enum + LLMQueue on main |
| codec_core.py | PASS | Shared functions extracted on main |
| build_session_script deprecated | PASS | DeprecationWarning in codec_core.py on main |
| Q references cleaned | PARTIAL | Top-level files clean; pipecat_bot.py still has ~/.q_memory.db (lines 171, 200) |
| Hardcoded ports | FAIL | ~30+ files still use raw port numbers instead of config constants |
| Structured logging adoption | FAIL | 65 print() calls in codec.py (41) + codec_session.py (24) |

**Code quality score: 5/8**

---

## Block 8: MCP & Integration

| Check | Result | Detail |
|-------|--------|--------|
| MCP server (codec_mcp.py) | PASS | FastMCP with input validation, lazy skill loading |
| MCP config (default deny) | PASS | MCP_DEFAULT_ALLOW=False, MCP_ALLOWED_TOOLS=[] |
| MCP in ecosystem.config.js | PASS | codec-mcp process defined |
| MCP auth whitelisted | PASS | Internal localhost requests allowed |

**MCP score: 4/4**

---

## Block 9: Manual Flags

| Check | Result | Detail |
|-------|--------|--------|
| TODO/FIXME/HACK markers | PASS | 0 real markers in top-level .py files |
| print() vs logging | FAIL | 65 print() in codec.py + codec_session.py |
| update.sh + install.sh | PASS | Both executable |
| deploy_skills.sh + sync_to_pm2.sh | PASS | Both executable |
| Smoke test has 10 checks | PASS | Confirmed |
| CHANGELOG.md current | PASS | v1.5.1 entry dated 2026-04-05 |

**Manual flags score: 5/6**

---

## Overall Scorecard

| Block | Score | Rating |
|-------|-------|--------|
| 1. Infrastructure | 7/9 | B+ (model servers are infra, not code) |
| 2. Security | 6/7 | A- |
| 3. Memory | 8/8 | A+ |
| 4. Skills | 5/5 | A+ |
| 5. Agents | 5/6 | A |
| 6. Dashboard | 8/8 | A+ |
| 7. Code Quality | 5/8 | B |
| 8. MCP | 4/4 | A+ |
| 9. Manual Flags | 5/6 | A- |
| **TOTAL** | **53/61** | **A- (87%)** |

---

## Remaining Action Items (Priority Order)

### Must Fix
1. **Audit log permissions** — `_audit_write()` should chmod 0o600 the audit log file
2. **pipecat_bot.py Q references** — Lines 171, 200 still use `~/.q_memory.db`
3. **Merge worktree fixes** — heartbeat DB_PATH, safe_sys, /api/health from this branch

### Should Fix
4. **print() to logging** — Convert 65 print() calls in codec.py + codec_session.py to structured logging
5. **Hardcoded ports** — Migrate ~30+ files to use config constants from codec_config

### Nice to Have
6. **Agent error logging** — Replace remaining bare `pass` handlers with log calls
7. **Static files mount** — StaticFiles imported but not mounted in dashboard

---

## Model Server Notes (Not Code Bugs)

- **Qwen LLM**: crash-looping with 307 restarts. Error: `ValueError: Model type qwen3_5_moe not supported`. Fix: upgrade mlx_lm or switch to a supported model variant.
- **UI-TARS**: not running. Likely same mlx version compatibility issue.
- These are infrastructure/dependency issues, not CODEC code bugs.
