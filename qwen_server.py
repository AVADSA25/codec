#!/usr/bin/env python3
"""
Qwen Auto-Recycling Server — keeps model in memory for speed but auto-restarts
after MAX_REQUESTS to prevent MLX state accumulation that causes hangs.

The MLX framework accumulates KV caches and GPU state between generate() calls.
After ~5-10 requests, generate() can hang forever. This server auto-recycles
(exits cleanly, PM2 restarts it) every MAX_REQUESTS to stay healthy.

Port: 18081 (internal, proxy on 8081 forwards here)
"""
import asyncio
import os
import time
import uuid
import logging
from concurrent.futures import ThreadPoolExecutor
from aiohttp import web

logging.basicConfig(level=logging.INFO, format="%(asctime)s [QwenSrv] %(message)s")
log = logging.getLogger("qwen_server")

PORT = 18081
MODEL_ID = "mlx-community/Qwen3.5-35B-A3B-4bit"
MAX_REQUESTS = 5  # Recycle after this many inferences to prevent hangs
MAX_TOKENS_CAP = 300  # Cap tokens for Flash Chat speed

_executor = ThreadPoolExecutor(max_workers=1)
_model = None
_tokenizer = None
_model_list = None
_request_count = 0
_shutting_down = False


def _load_model():
    global _model, _tokenizer, _model_list
    import mlx.nn as nn
    original_load = nn.Module.load_weights
    def patched_load(self, weights, strict=True):
        return original_load(self, weights, strict=False)
    nn.Module.load_weights = patched_load

    from mlx_lm import load
    log.info(f"Loading {MODEL_ID}...")
    _model, _tokenizer = load(MODEL_ID)
    log.info("Model loaded successfully")
    _model_list = {
        "object": "list",
        "data": [{"id": MODEL_ID, "object": "model", "created": int(time.time())}]
    }


def _do_generate(messages, max_tokens=300, enable_thinking=False):
    from mlx_lm import generate
    import mlx.core as mx

    # Clear GPU state before each request
    mx.clear_cache()

    prompt = _tokenizer.apply_chat_template(
        messages,
        add_generation_prompt=True,
        enable_thinking=enable_thinking,
        tokenize=False
    )
    prompt_tokens = len(_tokenizer.encode(prompt))
    effective_max = min(max_tokens, MAX_TOKENS_CAP)

    response = generate(_model, _tokenizer, prompt=prompt, max_tokens=effective_max)
    completion_tokens = len(_tokenizer.encode(response))

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "model": MODEL_ID,
        "created": int(time.time()),
        "choices": [{
            "index": 0,
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": response, "reasoning": "", "tool_calls": []}
        }],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "prompt_tokens_details": {"cached_tokens": 0}
        }
    }


async def handle_models(request):
    return web.json_response(_model_list)


async def handle_completions(request):
    global _request_count, _shutting_down

    if _shutting_down:
        return web.json_response({"error": "Server recycling, retry in 10s"}, status=503)

    try:
        body = await request.json()
        messages = body.get("messages", [])
        max_tokens = body.get("max_tokens", 300)
        kwargs = body.get("chat_template_kwargs", {})
        enable_thinking = kwargs.get("enable_thinking", False)

        if not messages:
            return web.json_response({"error": "No messages"}, status=400)

        _request_count += 1
        log.info(f"Inference #{_request_count}/{MAX_REQUESTS}: {len(messages)} msgs, max_tokens={min(max_tokens, MAX_TOKENS_CAP)}")

        loop = asyncio.get_event_loop()
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(_executor, lambda: _do_generate(messages, max_tokens, enable_thinking)),
                timeout=45
            )
        except asyncio.TimeoutError:
            log.error(f"Inference TIMEOUT (45s) — recycling server")
            _schedule_recycle()
            return web.json_response({"error": "Inference timeout — server recycling"}, status=504)

        content = result["choices"][0]["message"]["content"][:80]
        log.info(f"Done #{_request_count}: {result['usage']['completion_tokens']} tokens, content={repr(content)}")

        # Auto-recycle after MAX_REQUESTS
        if _request_count >= MAX_REQUESTS:
            log.info(f"Reached {MAX_REQUESTS} requests — recycling to prevent state buildup")
            _schedule_recycle()

        return web.json_response(result)

    except Exception as e:
        log.error(f"Inference error: {e}")
        return web.json_response({"error": str(e)}, status=500)


def _schedule_recycle():
    """Schedule a clean exit — PM2 will restart us with fresh state."""
    global _shutting_down
    _shutting_down = True

    async def _do_exit():
        await asyncio.sleep(1)
        log.info("Exiting for recycle...")
        os._exit(0)

    asyncio.get_event_loop().create_task(_do_exit())


async def handle_health(request):
    return web.json_response({
        "status": "ok", "model": MODEL_ID,
        "requests": _request_count, "max_requests": MAX_REQUESTS,
        "recycling": _shutting_down
    })


def _warmup():
    """Run a tiny inference to compile Metal shaders and warm up the GPU pipeline.
    Without this, the first real request takes 60s+ and times out."""
    from mlx_lm import generate
    import mlx.core as mx
    log.info("Warming up GPU (first inference compiles Metal shaders)...")
    t0 = time.time()
    prompt = _tokenizer.apply_chat_template(
        [{"role": "user", "content": "hi"}],
        add_generation_prompt=True,
        enable_thinking=False,
        tokenize=False
    )
    generate(_model, _tokenizer, prompt=prompt, max_tokens=3)
    mx.clear_cache()
    log.info(f"Warmup done in {time.time()-t0:.1f}s — ready for requests")


def main():
    _load_model()
    _warmup()
    app = web.Application()
    app.router.add_get("/v1/models", handle_models)
    app.router.add_post("/v1/chat/completions", handle_completions)
    app.router.add_get("/health", handle_health)
    log.info(f"Qwen Auto-Recycling Server on :{PORT} (recycle every {MAX_REQUESTS} requests)")
    web.run_app(app, host="127.0.0.1", port=PORT, print=None)


if __name__ == "__main__":
    main()
