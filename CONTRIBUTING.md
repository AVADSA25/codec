# Contributing to CODEC (Sovereign AI Workstation engine)

Thanks for your interest in contributing! **Sovereign AI Workstation** is the product brand; **CODEC** is the open-source engine codename — that's what you see in code paths, file names, and the `~/.codec/` config directory.

The repo is MIT licensed and welcomes contributions.

If anything here is unclear, [open a Discussion](https://github.com/AVADSA25/codec/discussions) — we treat doc gaps as real bugs.

## Code of conduct

Be kind. Assume good faith. CODEC's project ethos is **local-first, user-sovereign, privacy by default** — keep those principles in mind when proposing features.

---

## Quick start for contributors

```bash
git clone https://github.com/AVADSA25/codec.git
cd codec
./install.sh                                # First-time only — installs PM2 services + permissions
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt         # Runtime deps + pytest + ruff
~/.pyenv/shims/pytest                       # Full suite (~1m30s)
```

The full test suite must pass before any PR. Current baseline: **1,300+ pytest tests passing, 80 skipped (env-dependent), 0 failed**.

---

## Project ground rules

1. **Local-first.** Zero data leaves the user's machine unless they explicitly route through a cloud provider. New features that require an outbound API call MUST be opt-in via `~/.codec/config.json`.
2. **No new top-level dependencies without discussion.** CODEC keeps a small dependency surface for security and Mac App Store distribution. Prefer stdlib (`sqlite3`, `pathlib`, `subprocess`) where possible.
3. **Every action that touches the user's filesystem, processes, or external services emits an audit line** to `~/.codec/audit.log` via `codec_audit.audit(...)`. New code paths that bypass the audit log will be asked to add it.
4. **Don't commit user-specific data.** The repo is public; assume any string in a `.py` file is world-readable. Personal context lives in `~/.codec/config.json` and `~/.codec/prompt_overrides.json`.
5. **Security-critical paths require tests.** Anything touching `is_dangerous`, `chat_consent_ok`, the AST safety gate, the audit log, or the OAuth flow needs a regression test before merge.

---

## How to add a skill

A skill is a single Python file that does one thing well. CODEC currently has 76 built-in skills.

### Step 1: Copy the template

```bash
cp skills/_template.py skills/my_skill.py
```

### Step 2: Fill in the required exports

```python
SKILL_NAME = "my_skill"
SKILL_DESCRIPTION = "One-sentence description of what the skill does."
SKILL_TRIGGERS = [
    "my skill",         # Voice/wake-word matchers; longest match wins.
    "do the thing",     # Be specific — generic triggers collide with other skills.
]
SKILL_MCP_EXPOSE = True   # True = available over MCP; False = local-only.

# Optional: declare destructive operations for the §1.7 strict-consent gate.
# SKILL_DESTRUCTIVE = True

# Optional: auto-fire on observer signals (Phase 2 Step 6).
# SKILL_OBSERVATION_TRIGGER = {"type": "window_title_match", "pattern": r"...", "cooldown_seconds": 600}


def run(task: str, app: str = "", ctx: str = "") -> str:
    """Required entry point. Receives the user's task text and returns a string."""
    return f"Did the thing for: {task}"
```

### Step 3: Add a test

Create `tests/test_skill_my_skill.py`:

```python
def test_my_skill_runs():
    from skills import my_skill
    result = my_skill.run("test input")
    assert isinstance(result, str)
    assert result  # non-empty
```

### Step 4: Regenerate the trusted manifest (built-in skills only)

Built-in skills in `skills/` are hash-pinned in `skills/.manifest.json` for the PR-1A safety gate. After adding or modifying a skill in the repo:

```bash
python3 tools/generate_skill_manifest.py --write
```

Commit `skills/.manifest.json` alongside your skill file. CI verifies no drift via `tools/generate_skill_manifest.py --check`.

**User skills** in `~/.codec/skills/` don't need the manifest — they go through the AST safety gate at load instead.

### Step 5: Run the test suite

```bash
~/.pyenv/shims/pytest tests/test_skill_my_skill.py -v
~/.pyenv/shims/pytest tests/                      # Full suite, no regressions
```

### Step 6: Make it discoverable

For voice triggers, no action needed — `codec_skill_registry` AST-parses every `.py` file at startup and indexes the triggers.

For MCP exposure, no action needed — `codec_mcp._load_skill_tools_into` auto-registers skills with `SKILL_MCP_EXPOSE = True`.

### Safety boundary checklist

Before submitting a skill that touches files, processes, or external services:

- [ ] **Shell:** if you shell out, use `subprocess.run([...], shell=False)` with a list-form argv. **Never** use `shell=True`, `os.system`, or f-string interpolation into shell strings.
- [ ] **Filesystem writes:** confirm target paths are under `$HOME` or `/tmp`. Mirror `skills/file_write.py:_is_safe_target` — no writes to `~/.codec/`, `~/.ssh/`, `/etc/`, etc.
- [ ] **HTTP calls:** read URLs from `codec_config` (not hardcoded). Route LLM-style chat/completions calls through `codec_llm.call/stream/acall/astream`.
- [ ] **Code execution:** if your skill executes user-supplied code, set `SKILL_MCP_EXPOSE = False`. See `skills/python_exec.py` for the sandboxing pattern (AST gate + `sandbox-exec` + RLIMIT_CPU/AS/NOFILE).
- [ ] **Destructive operations:** set `SKILL_DESTRUCTIVE = True` so the §1.7 strict-consent gate engages on the chat path.

---

## How to add a crew (multi-agent workflow)

Crews are defined in `codec_agents.py` and registered in `CREW_REGISTRY` (currently 12 crews at `codec_agents.py:1696-1709`).

### Step 1: Write a builder function

```python
def my_crew(query: str = "default query") -> Crew:
    researcher = Agent(
        name="Researcher",
        role="You research topics thoroughly using web search.",
        tools=[web_search_tool, web_fetch_tool],
        max_tool_calls=5,
    )
    writer = Agent(
        name="Writer",
        role="You synthesize research into clear summaries.",
        tools=[google_docs_create_tool],
        max_tool_calls=3,
    )
    return Crew(
        agents=[researcher, writer],
        tasks=[f"Research: {query}", "Write a 3-paragraph summary based on the research"],
        mode="sequential",          # or "parallel"
        max_steps=8,
        allowed_tools=["web_search", "web_fetch", "google_docs_create"],
    )
```

### Step 2: Register in CREW_REGISTRY

```python
CREW_REGISTRY = {
    # ...
    "my_crew": {
        "builder": my_crew,
        "description": "Researches a topic and writes a summary",
    },
}
```

### Step 3: Add a voice trigger (optional)

In `codec_voice.py` `_CREW_TRIGGERS`:

```python
_CREW_TRIGGERS = {
    # ...
    "research and summarize": "my_crew",
}
```

### Step 4: Add a runtime test

```python
def test_my_crew_builds():
    from codec_agents import CREW_REGISTRY
    crew = CREW_REGISTRY["my_crew"]["builder"](query="test")
    assert crew.mode in ("sequential", "parallel")
    assert len(crew.agents) >= 1
    assert crew.allowed_tools  # tool allowlist must be set
```

### Step 5: Bump the test floor

If you're adding a crew, update `tests/test_security.py:255` from `assert len(crew_defs) >= 12` to the new minimum so future regressions catch a crew removal.

---

## How to propose a substantive change

For anything beyond a skill or crew — architectural changes, new endpoints, schema changes, security-relevant code — follow the **design-first workflow**:

1. **Write a design doc** at `docs/<change-name>-DESIGN.md` covering: what, why, schema/API changes, migration plan, test plan, rollback plan.
2. **Open a Discussion** with the doc.
3. **Wait for at least one maintainer ack** before implementing.
4. **Implement with tests passing after each file change.**
5. **PR description references the design doc.**

Examples to model after: `docs/PHASE1-STEP1-DESIGN.md`, `docs/PHASE2-STEP5-DESIGN.md`, `docs/PHASE3-STEP9-DESIGN.md`.

---

## Code conventions

### Python style

- **Python 3.11+**, type hints encouraged on new code.
- **Snake_case** everywhere.
- **One concern per commit**, conventional commit messages preferred (`feat:`, `fix:`, `security:`, `refactor:`, `docs:`).
- **No bare `except:`.** Use `except Exception:` only at trust-boundary surfaces (audit emit, JSON parse, network IO). Prefer specific exception types (`OSError`, `ValueError`, `requests.HTTPError`).
- **Use the structured logger,** not `print()`. `log = logging.getLogger("codec_<area>")` at module top. `print()` only in user-facing CLI tools (e.g. `codec_marketplace.py`).

### File organization

- **Engine modules:** `codec_<area>.py` at repo root.
- **HTTP routes:** `routes/<area>.py`.
- **Skills:** `skills/<name>.py`, one file per skill.
- **Tests:** `tests/test_<area>.py`, one per module under test.
- **Design docs:** `docs/<feature>-DESIGN.md`.

### Configuration

- All ports, URLs, timeouts go through `codec_config`. Don't hardcode service URLs in your module — read `codec_config.QWEN_BASE_URL`, `KOKORO_URL`, etc.
- Secrets go through `codec_keychain` (macOS) with a `~/.codec/secrets.enc.json` fallback for headless environments. Never read raw plaintext from `~/.codec/config.json` for secrets.

### Testing

- `pytest` from repo root: `~/.pyenv/shims/pytest`.
- Tests live under `tests/`, names start with `test_`.
- Use `pytest.parametrize` for variant coverage.
- Mock network calls (`requests.post`, `httpx.AsyncClient`) — tests should run offline.
- Don't write to `~/.codec/` in tests; use `tmp_path` fixtures.

### Audit emits

If your code performs an action that touches the user's filesystem, processes, or external services, emit an audit line:

```python
from codec_audit import audit

audit(
    event="my_event_name",
    source="codec-<my-area>",
    outcome="ok",         # or "error", "denied", "timeout", "warning"
    level="info",         # or "warning", "error"
    message="Optional ≤500 char description",
    extra={"key": "value"},
)
```

`event` is required. Schema details in `codec_audit.py` and `docs/PHASE1-STEP1-DESIGN.md`.

---

## Pull request workflow

1. Branch from `main`: `git checkout -b my-change`.
2. Make your changes, commit logically.
3. Run the full test suite: `~/.pyenv/shims/pytest tests/`.
4. Push and open a PR.
5. Fill in the PR template (Summary + Test plan).
6. CI runs `ruff` lint + the full pytest suite. Both must pass.
7. A maintainer reviews. For security-relevant changes, expect a second reviewer.
8. After approval, the maintainer squash-merges.

### What we look for in reviews

- **Tests for new behavior.** If you can't write a test, that's a design smell.
- **No silent failure modes.** Errors should be logged or surfaced.
- **Backward compat.** Don't break existing CODEC installations without a clear migration path and a `config_version` bump in `codec_config.CONFIG_SCHEMA_VERSION`.
- **Doc updates.** If you change behavior, update `FEATURES.md` / `README.md` / `CLAUDE.md` accordingly.
- **No new top-level dependencies** unless the maintainer pre-approved.

### Common PR rejection reasons

- Touches a security boundary (`is_dangerous`, `chat_consent_ok`, AST gate, audit envelope) without regression tests.
- Adds an outbound HTTP call without an opt-in config flag.
- Hardcodes a service URL or port instead of reading from `codec_config`.
- Adds a built-in skill without regenerating `skills/.manifest.json`.
- Skips the audit emit on an action that touches the filesystem/processes/network.

---

## Architecture overview

For deeper context, read these files in order:

1. **`CLAUDE.md`** (also called `AGENTS.md`) — the in-tree architecture doc. ~3000 lines covering every module, every chokepoint, every don't-touch zone.
2. **`docs/PHASE1-STEP1-DESIGN.md`** through **`docs/PHASE3-STEP10-DESIGN.md`** — design docs for the major substrate additions.
3. **`SECURITY.md`** — the threat model and hardening waves (PR-1A through PR-2G).
4. **`PRIVACY.md`** — what CODEC sends off-Mac, when, and to whom.

### Module map quick reference

```
codec.py              — Main process (wake word, hotkeys, dispatch)
codec_dashboard.py    — FastAPI server (port 8090, PWA frontend)
codec_voice.py        — WebSocket voice pipeline
codec_agents.py       — Agent + Crew runtime (no CrewAI dependency)
codec_skill_registry  — AST-gated skill loader
codec_dispatch        — Single skill execution chokepoint
codec_audit           — Unified audit envelope (schema:1, HMAC-signed)
codec_config          — Config + dangerous-pattern detector + secrets accessors
codec_memory          — SQLite + FTS5
codec_mcp             — MCP server (FastMCP, auto-registers all SKILL_MCP_EXPOSE=True)
codec_hooks           — Plugin lifecycle hooks (pre/post tool, on_error, etc.)
codec_textassist      — Right-click text services
codec_dictate         — F5 live-dictation + draft refinement
pilot/                — Browser automation runtime (vendored module)
skills/               — 76 built-in skill plugins (hash-pinned in .manifest.json)
routes/               — HTTP endpoint groups extracted from codec_dashboard.py
tests/                — 156 test files, 1,300+ tests
```

---

## Reporting security issues

**Don't open a public issue.** Email `security@avadigital.ai` or use [GitHub Private Vulnerability Reporting](https://github.com/AVADSA25/codec/security/advisories/new).

---

## Getting help

- **Bug?** [Open an issue](https://github.com/AVADSA25/codec/issues).
- **Question?** [Start a Discussion](https://github.com/AVADSA25/codec/discussions).

Thanks for contributing to CODEC.
