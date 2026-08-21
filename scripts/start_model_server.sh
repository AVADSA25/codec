#!/bin/bash
# CODEC model server launcher (PM2 process `qwen3.6`).
#
# Reads the ACTIVE model from ~/.codec/config.json instead of hardcoding one, so
# `codec_models.set_active()` can switch models by writing the config and
# restarting this process. A restart is how CODEC reclaims memory: swapping
# models in-place leaks, and repeated in-place switching grew the server to
# 52 GB against a 19 GB model.
#
# Preloads by the SAME identifier the clients send (the hub id, e.g.
# mlx-community/Qwen3.6-35B-A3B-4bit). mlx_vlm caches by exact identifier, so
# preloading a snapshot path while chat requests the hub id makes the first
# request evict the preloaded copy and load the same 19 GB again — the previous
# launcher did exactly that, and it is where the 39 GB footprint peak came from.
#
# uvicorn binds the port only after FastAPI's lifespan finishes, and the preload
# happens in lifespan. So "the port accepts a connection" already means "the
# model is loaded" — that is the readiness signal restart_server() waits on.
set -euo pipefail

CONFIG="${CODEC_CONFIG:-$HOME/.codec/config.json}"
VENV="${CODEC_MODEL_VENV:-$HOME/codec-qwen38-venv}"
DEFAULT_MODEL="mlx-community/Qwen3.6-35B-A3B-4bit"
DEFAULT_PORT=8083
HOST="${CODEC_MODEL_HOST:-0.0.0.0}"

# Model and port both come from config.json so there is ONE source of truth.
# Any failure here (missing file, bad JSON, no key) falls through to the
# defaults rather than refusing to start — a mute CODEC is the worse outcome.
read -r MODEL PORT <<<"$(
  CONFIG="$CONFIG" DEFAULT_MODEL="$DEFAULT_MODEL" DEFAULT_PORT="$DEFAULT_PORT" \
  python3 - <<'PY' 2>/dev/null || echo "$DEFAULT_MODEL $DEFAULT_PORT"
import json, os
from urllib.parse import urlparse
cfg = {}
try:
    with open(os.environ["CONFIG"]) as f:
        cfg = json.load(f)
except Exception:
    pass
model = cfg.get("llm_model") or os.environ["DEFAULT_MODEL"]
try:
    port = urlparse(cfg.get("llm_base_url", "")).port or int(os.environ["DEFAULT_PORT"])
except Exception:
    port = int(os.environ["DEFAULT_PORT"])
print(model, port)
PY
)"

[ -n "${MODEL:-}" ] || MODEL="$DEFAULT_MODEL"
[ -n "${PORT:-}" ] || PORT="$DEFAULT_PORT"

if [ -f "$VENV/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
else
  echo "start_model_server: venv not found at $VENV — using system python3" >&2
fi

echo "start_model_server: model=$MODEL port=$PORT host=$HOST" >&2
exec python -m mlx_vlm.server --model "$MODEL" --port "$PORT" --host "$HOST"
