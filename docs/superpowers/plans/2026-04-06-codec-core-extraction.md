# codec_core.py Extraction Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate code duplication between codec.py and skills/codec.py by extracting shared functions into codec_core.py.

**Architecture:** codec_core.py loads config, defines constants, and provides all shared functions. Both codec.py and skills/codec.py import from it instead of defining their own copies. Functions with meaningful differences between the two files stay in their respective files.

**Tech Stack:** Python 3.13, same deps as existing (sqlite3, requests, subprocess, pynput)

---

### Task 1: Create codec_core.py

**Files:**
- Create: `codec_core.py`

- [ ] **Step 1: Write codec_core.py**

Contains: config loading, all shared constants, and these functions:
- `strip_think()`, `is_draft()`, `needs_screen()`
- `init_db()`, `save_task()`, `get_memory()`, `get_recent_conversations()`
- `load_skills()` + `loaded_skills` global + `run_skill()`
- `transcribe()`, `speak_text()`
- `focused_app()`, `get_text_dialog()`, `terminal_session_exists()`, `close_session()`
- `build_session_script(safe_sys, session_id, wake_word_label="CODEC")` — parameterized

Does NOT contain (these differ between files):
- `screenshot_ctx()` — codec.py uses Gemini/local vision, skills/codec.py uses Qwen VL directly
- `wake_word_listener()` — codec.py uses SOX, skills/codec.py uses sounddevice
- `dispatch()` — different routing logic
- `check_skill()` — codec.py uses ranked matching, skills/codec.py uses simple matching
- `show_overlay()` — skills/codec.py has its own tkinter version
- Vision functions — codec.py only

- [ ] **Step 2: Verify codec_core.py imports cleanly**

```bash
cd ~/codec-repo && python3.13 -c "import codec_core; print('OK:', len(dir(codec_core)), 'names')"
```

### Task 2: Update codec.py

**Files:**
- Modify: `codec.py`

- [ ] **Step 1: Add import from codec_core, remove duplicated functions**

Replace the duplicated function definitions with:
```python
from codec_core import (
    strip_think, is_draft, needs_screen, DRAFT_KEYWORDS, SCREEN_KEYWORDS,
    init_db, save_task, get_memory, get_recent_conversations,
    loaded_skills, load_skills, run_skill,
    transcribe, speak_text, focused_app, get_text_dialog,
    terminal_session_exists, close_session, build_session_script,
)
```

Keep: `check_skill`, `check_skills_ranked`, vision functions, `screenshot_ctx`, `dispatch`, `wake_word_listener`, overlay imports, state dict, work queue, `on_press`, `on_release`, `main`.

- [ ] **Step 2: Verify codec.py still imports**

```bash
cd ~/codec-repo && python3.13 -c "import codec; print('OK')"
```

### Task 3: Update skills/codec.py

**Files:**
- Modify: `skills/codec.py`

- [ ] **Step 1: Add import from codec_core, remove duplicated functions**

Same import pattern. Keep: `show_overlay`, `check_skill`, `screenshot_ctx`, `dispatch`, `wake_word_listener`, state dict, work queue, `on_press`, `on_release`, `main`.

- [ ] **Step 2: Verify skills/codec.py still imports**

```bash
cd ~/codec-repo && python3.13 -c "
import sys; sys.path.insert(0, '.')
from skills import codec; print('OK')
"
```

### Task 4: Smoke test + PM2 sync

- [ ] **Step 1: Run smoke test**

```bash
python3.13 codec_smoke_test.py
```

- [ ] **Step 2: Sync and restart PM2**

```bash
./sync_to_pm2.sh
```

- [ ] **Step 3: Run smoke test again post-restart**

```bash
python3.13 codec_smoke_test.py
```
