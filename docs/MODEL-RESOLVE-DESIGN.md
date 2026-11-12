# Model ids by role: `codec_models.resolve(role)` (P-057)

Status: approved for build 2026-11-03 (owner: "go go build i trust ur judgment here").

## What

`codec_models` becomes the one place that holds CODEC's default model ids. Every caller that used to
write a hub id (`mlx-community/...`) as a default now asks for its role:

- `codec_models.default_for(role) -> str`: the built-in default for the role, config not read.
- `codec_models.resolve(role, cfg=None) -> str`: the id a caller sends. `cfg` is the config dict the
  caller already holds; `None` reads `~/.codec/config.json` (`codec_models.CONFIG_PATH`).

| role | config key | when the key is unset | default |
|---|---|---|---|
| `chat` | `llm_model` | default | `DEFAULT_MODEL` (the bundled Qwen2.5-7B) |
| `agent` | `llm_model` | default | `DEFAULT_MODEL` |
| `plan` | `llm_model` | default | `DEFAULT_MODEL` |
| `voice` | `voice_model` | the model the local server holds (`local_server_model`, P-013) | `DEFAULT_MODEL` |
| `vision` | `vision_model` | default | Qwen2.5-VL-7B-Instruct-4bit (`qwen-vl-7b` in the manifest) |
| `ui_tars` | `ui_tars_model` | default | UI-TARS-1.5-7B-4bit |
| `stt` | `stt_model` | default | whisper-large-v3-turbo |
| `tts` | `tts_model` | default | Kokoro-82M-bf16 |
| `research` | none | default | Qwen3.6-35B-A3B-4bit (Deep Research, `RESEARCH_MODELS`) |

Override order: the role's config key when it holds a non-empty string, sent byte for byte (never
stripped, never a snapshot path); then the role's rule (only `voice` has one); then the default. An
unknown role raises `ValueError` (a typo, caught by the tests). `chat`, `agent` and `plan` read the
same key today; the names say what the caller is, so one of them can get its own key later in one
place. Callers that must stay on this Mac keep applying `codec_cloud_models.local_only` themselves,
as before. The P-013 vision rule (`vision_model(cfg, url, default)`) is unchanged and now gets its
default from `default_for("vision")`.

Data that is not a default also moves into `codec_models`: `RESEARCH_MODELS` (the Deep Research
allowlist, `codec_agents.DEEP_RESEARCH_MODELS` points at a copy) and `TEXT_ONLY_MODELS` (what
`codec_vision` refuses to send a screenshot to). `codec_setup.BUNDLED_LOCAL_MODEL` is
`codec_models.DEFAULT_MODEL` (same id; `tests/test_setup_connect.py` keeps both in the manifest).
`codec_config` gains `STT_MODEL` next to `WHISPER_URL`.

## Why

Model ids were literals in 33 Python files (56 lines outside `codec_models.py`, recounted 3 Nov:
`git grep -n 'mlx-community/' -- '*.py' ':!tests' ':!codec_models.py'`), with two different chat
defaults (the 35B in 13 places, the bundled 7B in `codec_models`) and three vision defaults. A model
change always missed a call site.

## Call sites

Each call site sends on the Studio exactly what it sent before (checked per site and per role, read
only, against the live config; no switch and no restart needed). Sites that read a config key keep
the same key through `resolve`. Sites that ignored config:

- the wake word, `codec_core.transcribe`, live dictation (stt) and `codec_watcher`'s Kokoro (tts) now
  read `stt_model` / `tts_model` through their role (same id on the Studio);
- `skills/translate` names the model the local server holds (`local_server_model`) instead of the
  35B, on the same hard-coded local URL (same id on the Studio; on a Mac running another model it no
  longer forces a swap);
- `skills/screenshot_text` keeps sending the vision default (`default_for("vision")`), config not
  read, exactly as before: following `vision_model` would change the model it names on the Studio.
- `whisper_server.py` (the app's speech server) takes its default from `default_for("stt")`
  (`WHISPER_MODEL` in the environment still wins). It ships next to `codec_models.py` in the app;
  the Studio's running copy is `~/whisper_server.py`, outside the repo, and is not touched.

What changes, only where a key is missing from config: the 13 `llm_model` defaults move from the
35B to the bundled 7B (as `codec_models.get_active` and voice already do), and the three 35B
`vision_model` defaults (`codec_config.QWEN_VISION_MODEL`, `/api/upload_image`, `codec_watcher`'s
fallback) move to Qwen2.5-VL-7B like the other seven. A key set to `""` or `null` now gets the
default instead of being sent as is. The Studio sets all of these keys, so it sees no change. An
installed app (`codec_setup`) and `setup_codec.py` write `llm_model` but never `vision_model`, so
there the callers that send `codec_config.QWEN_VISION_MODEL` as is (`codec_core`'s session scripts,
`codec_watcher`, `codec_agent`) and `/api/upload_image` now name Qwen2.5-VL-7B instead of the 35B.
Neither is bundled (both are on-demand downloads; the VL-7B is the smaller one). `codec_vision` is
not affected there: with no `vision_model` it names the model the chat server holds (P-013).

Left as literals, on an explicit allowlist the test reads: `setup_codec.py` (the setup wizard's
prompts and values; it imports nothing from CODEC). `codec_agent_runner.py` / `codec_agent_plan.py`
were the other two until E-14 (docs/RUNNER-PLANNER-MODEL-ROLE-DESIGN.md): they now ask
`resolve("agent")` / `resolve("plan")` per call. Not Python and
not touched: `scripts/start_model_server.sh` (its own default stays the 35B), the manifests, and
`config.json.example`.

## Migration

None. No config key is written, renamed or added; the launcher and the switch code are unchanged.

## Test plan

`tests/test_model_resolve.py`: each role reads its key byte for byte and falls back to its default;
the voice rule; an unknown role raises; the defaults match the bundled-model manifest; every tracked
Python file outside `codec_models.py` and `tests/` holds no `mlx-community/` id except the allowlist
(with a positive control); the call sites name their role (config through `tmp_path`, never a fake
HOME). Existing: `tests/test_model_switching.py`, `tests/test_cloud_models.py`,
`tests/test_voice_vision_loaded_model.py`, `tests/test_setup_connect.py`, plus the test files that
import a changed module. Manual: the read-only per-role print on the Studio, before and after.

## Rollback

`git revert` the PR and restart the services that import the changed modules. `llm_model` and the
launcher were never touched.
