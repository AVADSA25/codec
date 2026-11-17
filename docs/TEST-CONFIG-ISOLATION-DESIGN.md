# The test suite reads its own config.json, never the owner's (P-009 follow-up)

Status: built on the plan in docs/known-issues.md ("Tests still read the owner's live config.json", 12 Nov 2026), which is the follow-up named in docs/TEST-LIVE-STATE-DESIGN.md. One PR, for the owner's review; not merged by the agent.

## What and why

About 58 modules build their own `~/.codec/config.json` path (`os.path.expanduser("~/.codec/config.json")`, `_CODEC_DIR / "config.json"`, or a copy of `routes._shared.CONFIG_PATH` taken at import). A test that does not patch the right one of those names reads the owner's live settings on the Mac Studio: his models, `edition: paid`, `skills_off`, ports. CI has no `~/.codec`, so it reads nothing. Results can then differ between the Studio and CI, and every forgotten patch is a read of the owner's file. Writes are already refused by the P-009 guard; reads are not.

`tests/test_dashboard_host.py` also calls `importlib.reload(codec_config)`. The reload reads the config again and puts `codec_config.DB_PATH` back on the live `memory.db` for every test after it. In a worktree run the reload finds the main checkout's `codec_config.py` first on `sys.path` (skills insert `~/codec-repo` there), so the worktree's code is replaced by the main checkout's.

## Change

1. **One rule, one name.** A new module `codec_config_path.py` (standard library only) holds the rule:
   - `from_env()` returns `CODEC_CONFIG_PATH` (expanded) when that variable is set, else `os.path.expanduser("~/.codec/config.json")` (`DEFAULT_CONFIG_PATH`).
   - `codec_config.CONFIG_PATH = codec_config_path.from_env()`. With the variable unset this is the same string as before, byte for byte.
   - `config_path()` returns `codec_config.CONFIG_PATH` when `codec_config` is loaded (the one name tests patch), and `from_env()` when it is not.
   - Why a separate module rather than `import codec_config` in each reader: importing `codec_config` reads config.json, reads three Keychain items and imports pynput. The watchdog, the observer and the text-assist script do not load it today. Asking `config_path()` changes where their path comes from and nothing else: no new import side effect, and no import cycle for `codec_models`, `codec_cloud_models` and the other modules `codec_config` itself imports.
   - `CODEC_CONFIG_PATH` is for the test suite only, like `CODEC_LICENSE_DIR`. Nothing in CODEC sets it.
2. **Every reader asks at call time.** Each module drops its own path and calls `config_path()` where it opens the file (wrapped in `Path(...)` where the code uses `Path` methods). The module-level constants are removed, so a test that still patches an old name fails at once (`monkeypatch.setattr` raises) instead of silently patching nothing. Default arguments bound at definition time (`codec_hue_discovery.rediscover_and_update_config(path=CONFIG_PATH)`, `setup_codec.save_config(path=CONFIG_PATH)`) become `path=None`, resolved inside. Modules that read config once at import into constants (`codec_voice`'s voice block, `skills/tts_say`) keep doing so; only where the path comes from changes.
3. **Modules changed** (runtime, routes, skills, tools):
   - Engine: `codec.py`, `codec_agent_messaging`, `codec_agent_plan` (project-root override only), `codec_agents` (3 places), `codec_alerts`, `codec_ask_user`, `codec_ava_client`, `codec_briefing` (2), `codec_browser_policy`, `codec_chat_pipeline`, `codec_checkin`, `codec_cloud_models`, `codec_compare`, `codec_cookbook/serve`, `codec_daybreak`, `codec_email_triage`, `codec_heartbeat`, `codec_hue_discovery`, `codec_image`, `codec_imessage`, `codec_mcp_consent`, `codec_models`, `codec_observer`, `codec_push`, `codec_scheduler`, `codec_search` (an unused constant), `codec_session`, `codec_setup`, `codec_skill_switches`, `codec_slash_commands`, `codec_supervisor`, `codec_telegram`, `codec_textassist`, `codec_tts`, `codec_update`, `codec_voice`, `codec_watchdog`, `codec_watcher`.
   - Routes: `routes/_shared` and the ten modules that imported its `CONFIG_PATH` (`auth`, `chat`, `config`, `health`, `heartbeat`, `media`, `tts`, `upload`, `vision`, plus `codec_dashboard`), `routes/compare`, `routes/connections`, `routes/memory_page`, `routes/usage`.
   - Skills: `create_skill`, `fact_extract`, `health_check`, `philips_hue`, `pm2_control`, `shift_report`, `skill_forge`, `timer`, `tts_say`, `weather`. The skill manifest is regenerated (`tools/generate_skill_manifest.py --write`).
   - Scripts and tools: `setup_codec.py`, `setup_hue.py`, `scripts/feature_audit.py` (an unused constant), `tools/chat_summary_eval.py`.
   - Left as they are, each with its own redirectable root: `codec_doctor` (its `Paths` object; tests pass a fake home), `codec_backup` (`CODEC_HOME`, which conftest already points at a temp folder; the state tar names `config.json` relative to it), `codec_license` (`CODEC_LICENSE_DIR`), `packaging/macos/first_run.py` (a `home` argument), `scripts/start_model_server.sh` (shell, `CODEC_CONFIG`). The `"/.codec/config.json"` entry in `codec_agent_plan._PATH_BLOCKLIST_SUBSTRINGS` is a security list, not a path, and is not touched; no section-10 list changes.
4. **The suite reads `{}`.** `tests/conftest.py` sets `CODEC_CONFIG_PATH` to a missing file in a fresh temp folder before anything imports a CODEC module (child processes inherit it), and an autouse fixture deletes that file before each test, so one test's write never reaches the next. Tests that need settings write their own file under `tmp_path` and patch `codec_config.CONFIG_PATH`; their old per-module patches move to that one name.
5. **The guard refuses reads too.** `tests/live_state_guard.py` gains `READ_PROTECTED_NAMES = ("config.json",)`: any open of the owner's `~/.codec/config.json*`, read or write (`open`, `io.open`, `os.open`), raises `LiveStateRefused` and fails the test. A new module with its own path therefore fails the suite on the Studio as soon as a test reaches it. A static test also fails on CI: no runtime file outside the listed exceptions may hold a `config.json` path literal, and none may import `CONFIG_PATH` by name.
6. **`tests/test_dashboard_host.py`** no longer reloads `codec_config`. It runs this checkout's `codec_config.py` under another module name (the pattern `tests/test_live_state_guard.py` uses for `codec_license`) and checks that copy's default, so the real module, its `DB_PATH` and its code stay as conftest left them.

## Production behaviour

Unchanged when `CODEC_CONFIG_PATH` is unset, which is always outside the test suite:

- Every reader opens `os.path.expanduser("~/.codec/config.json")`, the same string it built before. Readers that ran inside a process with `codec_config` loaded get the value `codec_config` computed at its import; the others compute it on the call.
- No module gains an import of `codec_config`. Values read at import time are the same values.
- No file is created, moved or rewritten; no config key, schema, audit event, port or service changes.

## Tests

- `tests/test_config_path.py` (new): `from_env()` gives the default with the variable unset and the variable's path when set; `config_path()` follows a patched `codec_config.CONFIG_PATH` and falls back to `from_env()` when `codec_config` is not loaded; `codec_config.CONFIG_PATH` equals `from_env()`; the static scan above.
- `tests/test_live_state_guard.py`: the suite's config path is outside the real `~/.codec` and missing; a guard on a temp root refuses read opens of `config.json` and still allows reads of the other protected names; on the Mac the installed guard refuses a read-only open of the live `config.json`.
- Tests that patched a module's own config name now patch `codec_config.CONFIG_PATH`; source checks that read `codec_config.py` for the literal read `codec_config_path.py`'s `DEFAULT_CONFIG_PATH`.
- Proof on the Studio: one full run compared test by test with a run of `main` on the same machine, `python3 scripts/smoke.py`, and the live `config.json` and `memory.db` mtime and size recorded before and after.

## Rollback

`git revert` of the PR. Nothing on the owner's Mac changes, so nothing needs undoing.
