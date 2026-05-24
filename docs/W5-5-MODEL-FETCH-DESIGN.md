# W5-5 — model-pack downloader (design)

> Wave 5 (Audit E). Closes **E-8** (multi-GB models have no distribution
> strategy) at the *mechanism* level. The exact model **list** is the open
> model-strategy decision (`docs/HANDOFF-MICKAEL.md §2`) — this PR ships a
> recommended **example manifest** + the tool; Mickael confirms/pins the list.

## Strategy (per E-8 option a/c)

- **Bundle the minimum** always-needed set so the app works offline on first
  launch: STT (Whisper-turbo) + TTS (Kokoro) + a 7B-class 4-bit LLM (~8 GB total).
- **Download larger models on demand** (35B LLM, vision) with explicit consent +
  a size warning + resumable progress.
- **Models live in `~/.codec/models/`, NOT in the `.app`** — notarization can't
  practically sign multi-GB binaries, and the bundle must stay small. The
  `DiskSpace` + `FileTimestamp` privacy reasons for this path were already
  declared in W5-1's `PrivacyInfo.xcprivacy`.

## Files

- **`packaging/macos/models.json`** — the manifest. Each entry:
  `{name, kind (stt|tts|llm|vision), repo (HF), revision, tier (bundled|on_demand), approx_gb}`.
  Ships as a **recommended example** (uses the real `mlx-community/Qwen3.5-35B-A3B-4bit`
  + `Qwen2.5-VL-7B-Instruct-4bit` from `ecosystem.config.js`; the rest flagged).
  **TODO before launch:** confirm exact repos + pin `revision` to commit SHAs
  (supply-chain), once Mickael fixes the model strategy.
- **`packaging/macos/fetch_models.py`** — the downloader. Pure, testable core
  (`load_manifest` / `select(tier)` / `total_gb` / `consent_text` / `model_dest`)
  + a `download()` that **lazy-imports `huggingface_hub`** and calls
  `snapshot_download(repo_id, revision, local_dir=…)` — which gives **resumable +
  etag-integrity** downloads for free. CLI: `--tier bundled|on_demand|all`,
  `--dest` (default `~/.codec/models`), `--manifest`, `--dry-run`, `--yes`.

## Consent + safety

- **`--dry-run` is safe** and imports nothing heavy: prints the plan (models,
  sizes, total GB, destination) and exits.
- A real download **requires `--yes`** (consent gate); the tool prints the total
  size first so a 20 GB pull is never a surprise.
- Integrity + resume are delegated to `huggingface_hub` (etag/commit-hash
  verified; interrupted downloads resume).

## Test plan (`tests/test_model_fetch.py`, hermetic — no network, no HF import)

- Manifest valid: every entry has the required keys; tiers ∈ {bundled, on_demand};
  ≥1 bundled and ≥1 on_demand; includes the two known real repos.
- `select("bundled")` ⊆ all and only bundled; `select("all")` = all.
- `total_gb` sums; `consent_text` names models + total + destination.
- CLI `--dry-run --tier bundled` lists the bundled set + total and downloads
  nothing (works even without `huggingface_hub` installed — dry-run never imports it).

Wired into the CI packaging step.

## Scope boundaries

- Not auto-run; first-run invocation (download bundled set if missing) is **W5-6**.
- On-demand fetch at model-selection time is wired in the runtime later.
- The model **list + revision pins** are Mickael's decision (flagged).

## Rollback

Net-new `packaging/macos/{models.json,fetch_models.py}` + one test + CI line +
audit/handoff notes. No runtime/daemon code; `git revert`.
