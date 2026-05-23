# PR-3E-skills-misc — A-12 final tranche: self_improve + watcher + 4 skills (DESIGN)

**Status:** IMPLEMENTED. Migrated all 6: `codec_self_improve._draft_skill` (`call(retries=2)` → None on empty), `codec_watcher.handle_draft` (kept the 3× notify loop, swapped inner POST→`call`), `skills/translate` + `skills/create_skill` + `skills/skill_forge` (graceful → `call` + fallback), `skills/fact_extract._call_llm` (`call(retries=3, raise_on_error=True)` → `__ERR__` sentinel kept). Removed 3 now-dead imports; regenerated `skills/.manifest.json` (76 skills, `--check` clean). 7 tests; full suite 1492 passing, zero new; net-negative ruff. **A-12 COMPLETE** — every `chat/completions` text site is now on `codec_llm`; only vision POSTs (A-11) remain inline anywhere in the repo.
**Finding:** A-12 (closeout). The last 6 inline `chat/completions` text sites — all **non-hot, graceful** (none are voice/dashboard/agents). Closes A-12.
**Wave:** 3.

---

## 1. The 6 sites (all graceful non-stream)

| site | failure contract today | retry | migration |
|---|---|---|---|
| `codec_self_improve._draft_skill` | `retry_post(max_attempts=2)` + `raise_for_status` → `except: return None` | 2 | `call(retries=2)` + `if not raw: return None` |
| `codec_watcher.handle_draft` | **manual 3× loop** w/ osascript "Retrying" notifications + `time.sleep(2**n)` + `clean_draft`; retries while draft empty | 3 (loop) | keep the loop; swap the inner `requests.post` + `extract_content(r.json())` → `call(...)` (single attempt) |
| `skills/translate.py` | hardcoded `:8081`; `if not result: "Translation failed."` | none | `call(base_url="http://localhost:8081/v1", …)` |
| `skills/fact_extract._call_llm` | `retry_post(3)` + `raise_for_status` → `except: return "__ERR__:…"` (sentinel) | 3 | `call(retries=3, raise_on_error=True)` inside the try → `except` keeps the `__ERR__` sentinel |
| `skills/create_skill.py` | `if r.status_code != 200: "Failed to generate skill…"` | none | `call(...)` + `if not code: "Failed…"` |
| `skills/skill_forge.py` | `if != 200: f"Forge failed: LLM returned {status}…"` | none | `call(...)` + `if not raw: "Forge failed: no response…"` (status-code specificity dropped — documented) |

`codec_watcher.py:86` is a **vision** POST (`QWEN_VISION_URL`) → A-11/codec_vision, **not** this tranche.

## 2. Contracts (per the established patterns)
- **Graceful (never-raise → fallback):** self_improve (`None`), watcher (loop retries on empty), translate ("Translation failed."), create_skill ("Failed…"), skill_forge ("Forge failed…"). → `codec_llm.call` default (returns `""` → the existing fallback fires).
- **Sentinel-on-failure:** fact_extract returns `"__ERR__:…"`. → `call(raise_on_error=True)` inside the existing `try` so the `except` still builds the sentinel.
- All gain `<think>` strip + content→reasoning fallback for free (drop now-redundant inline `<think>` re.subs; keep markdown-fence strips + structured parsing).

## 3. Cleanups
- Remove now-unused `retry_post` imports (self_improve, fact_extract) and the now-unused `codec_watcher.extract_content` helper (if `handle_draft` was its only caller — verify).
- **Manifest regen (required):** the 4 skill files (`translate`, `fact_extract`, `create_skill`, `skill_forge`) change → their `sha256` in `skills/.manifest.json` must be regenerated (`python3 tools/generate_skill_manifest.py --write`) or PR-1A's load-time gate refuses them. Commit the manifest alongside; CI `--check` verifies no drift.

## 4. Test plan
- `tests/test_llm_skills_misc.py`: behavior — `self_improve._draft_skill` returns `None` when `codec_llm.call` → `""` (and a tuple on success); `fact_extract._call_llm` returns `"__ERR__:…"` when `call` raises (sentinel preserved) + content on success; `translate` / `create_skill` / `skill_forge` return their graceful fallback on `""`. All via monkeypatched `codec_llm.call`. Source invariants: all 6 use `codec_llm.call(`; the inline `/chat/completions` text POSTs gone (watcher's vision POST remains).
- Manifest: `python3 tools/generate_skill_manifest.py --check` passes after regen.
- Full suite: 23 known-baseline failures, **zero new**.

## 5. Risk + rollback
- **Blast radius:** 2 core modules (self_improve nightly, watcher draft) + 4 skills + `skills/.manifest.json`. All non-hot, graceful. Per-site fallbacks preserved.
- **Rollback:** single-commit revert (manifest reverts with it).
- **This closes A-12** — every `chat/completions` text site is on `codec_llm`; only vision POSTs (A-11) remain inline anywhere.
