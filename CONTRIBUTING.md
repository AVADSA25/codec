# Contributing to CODEC

Thanks for your interest in contributing! CODEC is MIT licensed and welcomes contributions.

## Quick Start for Contributors

```bash
git clone https://github.com/AVADSA25/codec.git
cd codec
./install.sh
python3 -m pytest  # All tests should pass
```

## How to Contribute

### Report Bugs
Open an issue on GitHub with:
- What you expected
- What happened
- Your setup (macOS version, Python version, LLM provider)

### Write a Skill
Skills are Python files in `~/.codec/skills/`. Use the template:

```python
"""My Custom Skill"""
SKILL_NAME = "my_skill"
SKILL_TRIGGERS = ["trigger phrase 1", "trigger phrase 2"]
SKILL_DESCRIPTION = "What this skill does"

def run(task, app="", ctx=""):
    return "Result spoken back to user"
```

Drop it in `~/.codec/skills/` — CODEC loads it on restart.

### Submit a PR
1. Fork the repo
2. Create a branch: `git checkout -b feature/my-feature`
3. Make changes
4. Run tests: `python3 -m pytest`
5. Push and open a PR

### Code Style
- Python 3.10+
- No external dependencies unless absolutely necessary
- Every skill needs: SKILL_NAME, SKILL_TRIGGERS, SKILL_DESCRIPTION, run()
- Error handling: never bare `except:` — always `except Exception as e:`

## Project Structure

```
codec.py              — Entry point (imports modules)
codec_config.py       — Configuration and constants
codec_keyboard.py     — Keyboard listener and input handling
codec_dispatch.py     — Skill matching and dispatch
codec_agent.py        — LLM agent session builder
codec_overlays.py     — Tkinter overlay popups
codec_compaction.py   — Context compaction for memory
codec_memory.py       — FTS5 memory search
codec_agents.py       — Multi-agent crew framework
codec_voice.py        — Voice call WebSocket pipeline
codec_dashboard.py    — Web dashboard + API
codec_textassist.py   — Right-click text services
codec_search.py       — Web search (DuckDuckGo/Serper)
codec_mcp.py          — MCP server for external tools
codec_heartbeat.py    — System health monitoring
skills/               — 41 skill plugins
tests/                — 168+ pytest tests
```

## Running Tests

```bash
python3 -m pytest           # All tests
python3 -m pytest -v        # Verbose
python3 -m pytest -k "test_skills"  # Specific file
```
