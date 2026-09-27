# Create image in chat (Qwen-Image 2.1, local) — design

Status: approved 2026-09-28 and implemented (codec_image.py, routes/image.py, scripts/qwen_image_run.py).

## What

A fourth mode on the chat page, next to Chat, Agents and Project: **Image**.
In that mode the owner types a description, optionally attaches up to 3
reference images, and gets the picture back in the chat. It is generated on
this Mac by Qwen-Image 2.1. Nothing leaves the Mac.

## Why

- Image is the last phase in CODEC's planned order: Cookbook, Compare, Email
  triage, then Image.
- Qwen-Image 2.1 already runs on the Mac Studio:
  - weights in `~/models/Qwen-Image-2.1`;
  - its own venv, `~/models/qwen-image-venv`;
  - `~/models/qimg.py` as a command-line runner.
- There is no way to use it from CODEC today.

Reference images need no new model. Checked 2026-09-27 in the installed
diffusers, `QwenImage21Pipeline.__call__` takes `image=`: "One or more
condition images … encoded by the text encoder as vision context and by the
VAE into latent tokens". `qimg.py` just does not expose it.

## Constraints

- **Memory.** A run holds about 35 GB. It must never run next to a loaded 35B
  LLM (the Mac has 64 GB). Measured on 26 Sep: 512 px at 20 steps takes about
  46 s per image, plus about 1 min to load the model.
- **No resident server.** Per the owner's rule, "up = run it, down = the
  process ends": each job is one process that exits and frees the memory.
- **Dependencies.** The model needs its own Python (diffusers dev, torch MPS).
  The dashboard cannot import it and gets no new dependencies.

## How

1. **Runner:** `scripts/qwen_image_run.py`, new, in the repo. It runs with the
   image venv's Python.
   - It reads one job as JSON: prompt, reference image paths, size, steps,
     count, seed and transparent background.
   - It loads the model once, writes the PNGs, and prints one JSON line per
     image plus progress lines.
   - The model path and Python path come from `config.json:image` (defaults
     are the paths above), so nothing personal is in the repo.

2. **Job queue:** `codec_image.py`, new, in the dashboard process.
   - There is one worker thread, so jobs run one at a time and later jobs
     wait their turn.
   - Each job starts the runner as a child process: argument list, no shell,
     prompt passed on stdin.
   - Files go to `~/.codec/images/YYYY-MM-DD/<job>/` (references and results).
     They are kept until the owner deletes them.
   - **Memory guard, before a job:** it refuses when less than 36 GB is free
     (`vm_stat`: free plus inactive). A loaded 35B model leaves less than
     that, and a small local model does not. The refusal message says what to
     do: switch chat to MiMo, or pick a smaller local model. CODEC never stops
     a service itself.
   - **Memory guard, during a job:** `~/.codec/image_job.lock` exists, and
     `codec_models.set_active` refuses to load a local model while it does
     ("an image is being generated, try again when it finishes"). This covers
     the chat model picker and any other local tool that switches models
     through the same function. A switch to MiMo is still allowed.
   - **Cancel** stops that job's own child process.
   - One audit event, `image_generated`, with metadata only: size, steps,
     count, number of references, seconds, outcome. It does not record the
     prompt text.

3. **Routes:** `routes/image.py`, new, behind the normal dashboard auth.
   - `GET /api/image/status`: available, why not, queue length.
   - `POST /api/image/jobs`: the prompt plus up to 3 references as base64,
     each image at most 10 MB, PNG, JPEG or WebP only.
     With a reference, the output keeps the first reference's aspect ratio
     (the size sets its resolution); without one, it is square.
   - `GET /api/image/jobs/{id}`: state, stage (loading / generating / done)
     and the result URLs.
   - `POST /api/image/jobs/{id}/cancel`.
   - `GET /api/image/file/{job}/{name}`: serves a result, restricted to that
     job's folder.

4. **Chat page** (`codec_chat.html`):
   - An **Image** mode button with a line-SVG icon, no emoji.
   - In Image mode, the composer sends to `/api/image/jobs` instead of
     `/api/chat`. The existing attach button keeps images as references
     (downscaled to 1280 px with the existing helper) instead of sending them
     to vision analysis.
   - A size choice: 512 (about 2 min for the first image), 768 or 1024
     (slower).
   - The reply bubble shows the stage, then the image with a download link.
   - The button is hidden when the model or its Python is missing, which is
     the case on a buyer's Mac.

**Not in this change:**

- a `create_image` skill for voice and MCP;
- a gallery page;
- deleting old images.

## Tests

- A new file, `tests/test_image_create.py`, with a fake runner, so CI needs
  no model. It covers:
  - jobs run one at a time;
  - the memory guard refuses (LLM server up, low memory);
  - input limits (count, size, type) are enforced;
  - file paths stay inside the job folder;
  - cancel stops the child process;
  - the button is hidden when the model is missing.
- Live on the Mac Studio:
  - one 512 px image from a prompt;
  - one image with a reference photo;
  - one job refused while the local LLM is loaded.

## Rollback

Revert the PR. Nothing else changes: there are no schema changes, and
`config.json:image` is optional.
