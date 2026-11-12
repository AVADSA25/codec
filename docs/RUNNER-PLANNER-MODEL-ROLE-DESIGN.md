# Project-mode runner and planner name their model role (E-14, 2026-11-11)

## What
`codec_agent_runner._qwen_model()` returns `codec_models.resolve("agent")` and
`codec_agent_plan._qwen_model()` returns `codec_models.resolve("plan")`, read on
every call. On an error each falls back to `codec_models.default_for(role)`. The
literal `"mlx-community/Qwen3.6-35B-A3B-4bit"` in each file is gone, and the two
lines leave the allowlist in `tests/test_model_resolve.py`.

## Why
P-057 put every default model id in `codec_models`, except these two (Step 9
design-gate files, so they waited for the owner's word: board card E-14). They read
`codec_config.QWEN_MODEL`, fixed when the process imported `codec_config`, with a
35B literal as the fallback (the buyer default is the 7B). After a model switch the
runner kept sending the old id until `codec-agent-runner` restarted, and the model
server loads whatever id a client names (the double-load AGENTS.md section 10
warns about).

## What does not change
- Both stay on the local model: `_local_llm()` still passes (base, model) through
  `codec_cloud_models.local_only`, so a chat switched to a cloud model does not move
  them (Step 8 Q1).
- The value: "agent" and "plan" read `llm_model`, the same key `QWEN_MODEL` came
  from. Only the moment it is read changes (per call, not at import).
- `QWEN_MODEL` / `QWEN_URL` module constants (kept for tests), the base URL (still
  `codec_config.QWEN_BASE_URL`), prompts, the loop.

## Tests
`tests/test_model_resolve.py`: both functions name their role and follow a config
change without a reload; the allowlist test fails if a literal comes back.
`tests/test_cloud_models.py` (local while on the cloud) unchanged and passing.

## Rollback
Revert the PR. No stored data changes.
