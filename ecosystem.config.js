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

    // ── Dictation & hotkey listener (keyboard shortcuts, wake word) ──
    // NOTE: codec-heartbeat and codec-scheduler are unified into codec-dashboard
    {
      name: "codec-dictate",
      script: "python3",
      args: "codec_keyboard.py",
      cwd: __dirname,
      max_memory_restart: "64M",
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

    // ── LLM Server (Qwen via mlx_lm) ──
    {
      name: "qwen35b",
      script: "bash",
      args: "-c 'python3 -m mlx_lm.server --model mlx-community/Qwen3.5-35B-A3B-4bit --port 8081'",
      cwd: __dirname,
      max_memory_restart: "8G",
      restart_delay: 10000,
      max_restarts: 5,
      autorestart: true,
    },

    // ── Vision Model Server ──
    {
      name: "qwen-vision",
      script: "bash",
      args: "-c 'python3 -m mlx_lm.server --model mlx-community/Qwen2.5-VL-7B-Instruct-4bit --port 8082'",
      cwd: __dirname,
      max_memory_restart: "4G",
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
    {
      name: "kokoro-82m",
      script: "python3",
      args: "kokoro_server.py",
      cwd: __dirname,
      max_memory_restart: "512M",
      restart_delay: 5000,
      max_restarts: 5,
      autorestart: true,
    },
  ],
};
