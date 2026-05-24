# F-15 — pyproject.toml (project metadata)

**Closes:** Investor-readiness audit **F-15** (no Python project metadata; version not
introspectable). **Repo:** codec-repo. **Greenlit by Mickael 2026-05-25.**

## The honest constraint
CODEC is an **application run via PM2 from a checkout**, not a pip-installable library:
- 57 flat `codec_*.py` modules that import each other as top-level names (no `src/` layout).
- `skills/` is a **runtime-loaded directory** (no `__init__.py`) — the SkillRegistry AST-loads
  files from it + `~/.codec/skills/` at runtime; they are not package data.

So a pyproject that claims to vendor all 57 flat modules into a wheel would be both fiddly and
misleading. F-15 instead delivers what actually has value: **declarative, introspectable
project metadata + a single dependency manifest + a valid modern build-system** — the
"this is a real, maintained project" signal investors/enterprises look for — without faking
library distribution.

## What ships
`pyproject.toml`:
- **`[project]`** — name, description, `license = MIT`, `requires-python = ">=3.10"` (matches
  the README claim), authors, keywords, trove classifiers (incl. Python 3.10–3.13), URLs.
- **`dynamic = ["version"]`** ← `[tool.setuptools.dynamic] version = {file = "VERSION"}`, so the
  version flows from the **F-5 single source of truth** (no second place to bump).
- **`dependencies`** — the core runtime set mirrored from `requirements.txt`; the optional
  stacks (TTS / STT / Google) become `[project.optional-dependencies]` extras.
- **`[build-system]`** — setuptools; **`[tool.setuptools] py-modules = []`** documents that the
  flat modules are intentionally not vendored (keeps the metadata build valid, avoids
  flat-layout auto-discovery errors).

`pytest.ini` is left as-is (test discovery config); the F-4 ruff config stays in `ruff.toml`
(ruff prefers `ruff.toml` over `[tool.ruff]` in pyproject, so they don't conflict).

## Verification
- `tests/test_pyproject.py` (5 tests, stdlib `tomllib`) — pins the `[project]` shape, the
  build-system, dynamic-version-from-VERSION, the core deps, and the `>=3.10` claim. CI-gated.
- **Builds a valid wheel**: `pip wheel . --no-deps` →
  `codec-2.3.0-py3-none-any.whl` (version resolved from VERSION). So the metadata + build-system
  are correct, not just well-formed text.

## Not done (deliberate)
- Full library packaging of the 57 flat modules — CODEC isn't consumed as `import codec`; see
  the constraint above. If CODEC is ever refactored to a `src/codec/` package, revisit.
