/**
 * PM2 Ecosystem Configuration for CODEC
 *
 * Usage:
 *   pm2 start ecosystem.config.js          # Start all services
 *   pm2 start ecosystem.config.js --only codec-dashboard  # Start one service
 *   pm2 stop ecosystem.config.js           # Stop all
 *   pm2 restart ecosystem.config.js        # Restart all
 *
 * Requires: Python 3.10+, mlx-lm, whisper, kokoro installed
 */
module.exports = {
  apps: [
    // ── Core CODEC (voice + text agent) ──
    {
      name: "codec",
      script: "python3",
      args: "codec.py",
      cwd: __dirname,
      max_memory_restart: "512M",
      restart_delay: 3000,
      max_restarts: 10,
      autorestart: true,
    },

    // ── Dashboard API (FastAPI on :8090) ──
    {
      name: "codec-dashboard",
      script: "python3",
      args: "-m uvicorn codec_dashboard:app --host 0.0.0.0 --port 8090",
      cwd: __dirname,
      max_memory_restart: "256M",
      restart_delay: 2000,
      max_restarts: 15,
      autorestart: true,
    },

    // ── CODEC MCP HTTP bridge (remote Claude access via Cloudflare) ──
    {
      name: "codec-mcp-http",
      script: "/usr/local/bin/python3.13",
      args: "codec_mcp_http.py",
      cwd: __dirname,
      env: {
        CODEC_MCP_HTTP_HOST: "127.0.0.1",
        CODEC_MCP_HTTP_PORT: "8091",
      },
      max_memory_restart: "512M",
      restart_delay: 3000,
      max_restarts: 10,
      autorestart: true,
    },

    // ── Dictation & hotkey listener (keyboard shortcuts, wake word) ──
    // NOTE: codec-heartbeat and codec-scheduler are unified into codec-dashboard
    {
      name: "codec-dictate",
      script: "/usr/local/bin/python3.13",
      args: "-u codec_dictate.py",
      cwd: __dirname,
      max_memory_restart: "768M",
      restart_delay: 2000,
      max_restarts: 10,
      autorestart: true,
    },

    // ── MCP Server (tool integration) ──
    {
      name: "codec-mcp",
      script: "python3",
      args: "codec_mcp.py",
      cwd: __dirname,
      max_memory_restart: "128M",
      restart_delay: 3000,
      max_restarts: 10,
      autorestart: true,
    },

    // ── LLM + Vision Server (Qwen 3.6 via mlx_vlm) ──
    // Single unified server on :8083 — the Qwen 3.6 35B model handles both
    // text reasoning and vision, so one mlx_vlm.server replaces the old
    // split mlx_lm(:8081) + VL(:8082) layout. config.json's llm_base_url
    // and vision_base_url both point here.
    {
      name: "qwen3.6",
      script: "bash",
      args: "-c 'python3 -m mlx_vlm.server --model mlx-community/Qwen3.6-35B-A3B-4bit --port 8083'",
      cwd: __dirname,
      max_memory_restart: "8G",
      restart_delay: 10000,
      max_restarts: 5,
      autorestart: true,
    },

    // ── Whisper STT Server ──
    {
      name: "whisper-stt",
      script: "python3",
      args: "whisper_server.py",
      cwd: __dirname,
      max_memory_restart: "1G",
      restart_delay: 5000,
      max_restarts: 5,
      autorestart: true,
    },

    // ── Kokoro TTS Server ──
    // LS-2 / SR-4: served by the installed `mlx_audio.server` module.
    // Previously referenced `kokoro_server.py` which was never committed,
    // so the service permanently errored on fresh clones.
    {
      name: "kokoro-82m",
      script: "python3",
      args: "-m mlx_audio.server --host 0.0.0.0 --port 8085",
      cwd: __dirname,
      max_memory_restart: "512M",
      restart_delay: 5000,
      max_restarts: 5,
      autorestart: true,
    },

    // ── iMessage Integration (polls Messages DB, replies via CODEC) ──
    {
      name: "codec-imessage",
      script: "bash",
      args: "-c 'python3 codec_imessage.py'",
      cwd: __dirname,
      max_memory_restart: "128M",
      restart_delay: 5000,
      max_restarts: 10,
      autorestart: true,
    },

    // ── Telegram Bot (CODEC via Telegram) ──
    {
      name: "codec-telegram",
      script: "bash",
      args: "-c 'python3 codec_telegram.py'",
      cwd: __dirname,
      max_memory_restart: "128M",
      restart_delay: 5000,
      max_restarts: 10,
      autorestart: true,
    },

    // ── Autopilot (ambient scheduler — fires skills at configured times) ──
    {
      name: "codec-autopilot",
      script: "/usr/local/bin/python3.13",
      args: "-u codec_autopilot.py",
      cwd: __dirname,
      max_memory_restart: "128M",
      restart_delay: 5000,
      max_restarts: 10,
      autorestart: true,
    },

    // ── Watchdog (kills stuck/zombie processes hogging RAM) ──
    {
      name: "codec-watchdog",
      script: "python3",
      args: "codec_watchdog.py",
      cwd: __dirname,
      max_memory_restart: "64M",
      restart_delay: 5000,
      max_restarts: 10,
      autorestart: true,
    },

    // ── Observer (Phase 2 Step 5 — continuous observation loop) ──
    // Polls active_window + screenshot OCR + clipboard delta + recent
    // files into a 10-min RAM-only ring buffer. Cadence: 60s active /
    // 5min idle (per Q1 + Q4). Kill switch: OBSERVER_ENABLED=false.
    // METADATA-ONLY audit emits — no titles, no OCR text, no clipboard
    // content, no file paths leaked to ~/.codec/audit.log.
    {
      name: "codec-observer",
      script: "/usr/local/bin/python3.13",
      args: "-u codec_observer.py",
      cwd: __dirname,
      max_memory_restart: "128M",
      restart_delay: 5000,
      max_restarts: 10,
      autorestart: true,
      env: {
        OBSERVER_ENABLED: "true",
      },
    },
    // ── Pilot Runner (browser automation HTTP API on :8094) ──
    // Headless Chromium on CDP port 9223, indexed-DOM snapshots,
    // screencast recording. Local-only after the 2026-05-24 RCE
    // remediation (no Cloudflare ingress — pilot.lucyvpa.com removed).
    // LS-6 / SR-5: pilot module is vendored at <repo>/pilot/. Was: cwd
    // hardcoded to /Users/mickaelfarina/codec (non-portable across
    // machines). Now: __dirname so the module resolves on any clone.
    {
      name: "pilot-runner",
      script: "python3",
      args: "-m pilot.pilot_runner",
      cwd: __dirname,
      max_memory_restart: "512M",
      restart_delay: 5000,
      max_restarts: 10,
      autorestart: true,
      env: {
        HEADLESS: "1",
        PYTHONUNBUFFERED: "1",
      },
    },

    // ── Agent Runner (Phase 3 Step 9 — autonomous plan execution) ──
    // PM2 daemon picks up status=approved plans (from Step 8), executes
    // their checkpoints autonomously via Qwen-3.6 ↔ skill loops with
    // permission gate enforcement. Resume after PM2 restart from last
    // atomic checkpoint (Q5). Multi-agent cap = 3 concurrent (Q6, Q8).
    // Plan-hash tamper detection at run start (Q13).
    // Kill switch: AGENT_RUNNER_ENABLED=false (daemon idles).
    {
      name: "codec-agent-runner",
      script: "/usr/local/bin/python3.13",
      args: "-u codec_agent_runner.py",
      cwd: __dirname,
      max_memory_restart: "256M",
      restart_delay: 5000,
      max_restarts: 10,
      autorestart: true,
      env: {
        AGENT_RUNNER_ENABLED: "true",
        AGENT_RUNNER_MAX_CONCURRENT: "3",
        PYTHONUNBUFFERED: "1",
      },
    },
  ],
};
