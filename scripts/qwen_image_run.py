#!/usr/bin/env python3
"""One Create-image job with Qwen-Image 2.1, run by codec_image in its own process.

It runs under the image model's Python (config `image.python`, a venv with
diffusers + torch MPS), never under the dashboard's. The process loads the model
(~1 min, ~35 GB), writes the images and exits, which frees the memory.
docs/CHAT-CREATE-IMAGE-DESIGN.md

Input: one JSON object on stdin
    {"model_path": "...", "out_dir": "...", "prompt": "...", "refs": ["a.png"],
     "size": 512, "steps": 20, "count": 1, "seed": 7, "rgba": false}
Output: one JSON object per line on stdout
    {"stage": "loading"} / {"stage": "loaded", "seconds": 58.1}
    {"stage": "generating", "image": 1, "of": 1, "step": 5, "steps": 20}
    {"stage": "image", "file": "01.png", "seconds": 46.0, "width": 512, "height": 512}
    {"stage": "done"}
Anything else (warnings, tracebacks) goes to stderr.
"""
import json
import os
import sys
import threading
import time


def emit(**fields):
    print(json.dumps(fields), flush=True)


def _exit_when_orphaned():
    """If the dashboard dies mid-job, stop instead of holding 35 GB for nobody."""
    parent = os.getppid()
    while True:
        time.sleep(2)
        if os.getppid() != parent:
            os._exit(3)


def main():
    job = json.loads(sys.stdin.read())
    threading.Thread(target=_exit_when_orphaned, daemon=True).start()
    out_dir = job["out_dir"]
    size, steps = int(job.get("size", 512)), int(job.get("steps", 20))
    count, seed = int(job.get("count", 1)), int(job.get("seed", 7))
    prompt = job["prompt"]
    if job.get("rgba"):
        # Qwen-Image 2.1's own transparent mode is asked for in the prompt.
        prompt = (f"This is an RGBA image with transparency. {prompt} "
                  "The image has alpha channel and the background is transparent.")

    emit(stage="loading")
    t0 = time.time()
    import torch
    from diffusers import QwenImage21Pipeline
    from PIL import Image
    pipe = QwenImage21Pipeline.from_pretrained(job["model_path"], dtype=torch.bfloat16).to("mps")
    emit(stage="loaded", seconds=round(time.time() - t0, 1))

    refs = [Image.open(p).convert("RGB") for p in job.get("refs") or []]
    for k in range(count):
        def on_step(_pipe, step, _t, kwargs, _k=k):
            emit(stage="generating", image=_k + 1, of=count, step=step + 1, steps=steps)
            return kwargs

        args = dict(prompt=prompt, num_inference_steps=steps, output_resolution=size,
                    generator=torch.Generator("cpu").manual_seed(seed + k),
                    callback_on_step_end=on_step)
        if refs:
            # Width and height follow the first reference's aspect ratio.
            args["image"] = refs
        else:
            args["width"] = args["height"] = size
        t1 = time.time()
        img = pipe(**args).images[0]
        name = f"{k + 1:02d}.png"
        img.save(os.path.join(out_dir, name))
        emit(stage="image", file=name, seconds=round(time.time() - t1, 1),
             width=img.width, height=img.height)
    emit(stage="done")


if __name__ == "__main__":
    main()
