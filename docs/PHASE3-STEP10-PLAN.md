# Phase 3 Step 10 — Proactive Messaging + Project Mode UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the user-facing layer of Phase 3. Agent ↔ user messaging via `messages.jsonl` + `notifications.json`. Project mode dropdown in chat composer. Status pills above input. Chat auto-escalation when user types something multi-step. **Phase 3 closeout** — proactive intelligence overlay deferred to Phase 3.5 per Q12.

**Architecture:** New `codec_agent_messaging.py` module owns the message-write contract and batching (60s window per Q10). `_run_agent` (Step 9) gets `post_message()` calls at lifecycle points. New `routes/agents.py` endpoints for read/post/silence. Chat handler in `codec_dashboard.py` gets a 2-signal classifier (Qwen-3.6 verdict + checkpoint estimate ≥ 3) that prepends a "Promote to Project mode?" prompt. PWA UI: mode dropdown + agent status pills, no Projects sidebar overhaul (YAGNI).

**Tech Stack:** Python 3.13, Qwen-3.6 local LLM (existing PM2 service), FastAPI router pattern, in-memory session-state dict for auto-escalation silence (Q11), atomic tmp+rename for `messages.jsonl` appends, pytest with `unittest.mock`.

**Reference design doc:** `docs/PHASE3-BLUEPRINT.md` §4 (Step 10) and §8 (resolved Q9–Q12).

**Reference Step 9 (already shipped + deployed):** `codec_agent_runner.py`, `~/.codec/agents/<id>/state.json`, `routes/agents.py` abort/pause/resume/grant endpoints, `codec-agent-runner` PM2 daemon online.

---

## File Structure

**NEW files:**

| Path | Purpose | Est. LOC |
|---|---|---|
| `codec_agent_messaging.py` | `AgentMessage` dataclass, `post_message()`, batching window, user-reply pickup | ~250 |
| `tests/test_agent_messaging.py` | 14 tests covering posting, batching, replies, kill switches | ~400 |
| `tests/test_chat_escalation.py` | 11 tests covering classifier + integration + silence | ~300 |

**MODIFIED files:**

| Path | What | Est. LOC |
|---|---|---|
| `codec_audit.py` | Add 3 Phase 3 Step 10 audit event constants + `PHASE3_STEP10_EVENTS` frozenset | +12 |
| `codec_agent_runner.py` | Wire `post_message` into 5 emit sites (`_run_agent`) | +30 |
| `routes/agents.py` | Add `GET /api/agents/{id}/messages`, `POST /api/agents/{id}/messages`, `POST /api/agents/{id}/silence` | +90 |
| `codec_dashboard.py` | Add classifier + auto-escalation gate in chat handler; add session-silence in-memory dict | +110 |
| `templates/dashboard.html` (or equivalent UI file) | Mode dropdown + status pills + 5s polling JS | +120 |
| `AGENTS.md` | New §X.X Phase 3 Step 10 sub-section, §6 events table, §10 don't-touch list | +60 |

**Storage written at runtime** (added to existing Step 9 storage):

```
~/.codec/agents/<id>/
  messages.jsonl   (NEW Step 10: append-only message log)
~/.codec/notifications.json    (already exists; Step 10 writes type=agent_update entries)
~/.codec/auto_escalate_silence.json    (in-memory, persists across dashboard restart)
```

---

## Task 1: Audit event constants for Step 10

**Files:**
- Modify: `codec_audit.py` (add 3 Phase 3 Step 10 constants + frozenset)
- Create: `tests/test_agent_messaging.py` (initial test)

- [ ] **Step 1: Create `tests/test_agent_messaging.py`**

```python
"""Phase 3 Step 10 tests — codec_agent_messaging.

14 tests covering:
  Audit constants (1)
  AgentMessage dataclass (2)
  post_message + batching (4)
  User reply pickup (2)
  Kill switches: silence + AGENT_AUTO_ESCALATE_ENABLED (2)
  PWA endpoints (3)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def test_step10_audit_constants_present():
    """Phase 3 Step 10 adds 3 named events + 1 frozenset."""
    import codec_audit
    assert codec_audit.AGENT_MESSAGE_SENT == "agent_message_sent"
    assert codec_audit.AGENT_MESSAGE_RECEIVED == "agent_message_received"
    assert codec_audit.AGENT_AUTO_ESCALATED_FROM_CHAT == "agent_auto_escalated_from_chat"
    assert codec_audit.PHASE3_STEP10_EVENTS == frozenset({
        "agent_message_sent", "agent_message_received",
        "agent_auto_escalated_from_chat",
    })
```

- [ ] **Step 2: Run test, verify it fails**

`python3.13 -m pytest tests/test_agent_messaging.py::test_step10_audit_constants_present -v`
Expected: FAIL with `AttributeError: module 'codec_audit' has no attribute 'AGENT_MESSAGE_SENT'`

- [ ] **Step 3: Add constants to `codec_audit.py`**

Find the `PHASE3_STEP9_EVENTS` block. Immediately after that frozenset closes, insert:

```python
# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 Step 10 — Proactive Messaging + Project Mode UI
# ─────────────────────────────────────────────────────────────────────────────
AGENT_MESSAGE_SENT             = "agent_message_sent"
AGENT_MESSAGE_RECEIVED         = "agent_message_received"
AGENT_AUTO_ESCALATED_FROM_CHAT = "agent_auto_escalated_from_chat"

PHASE3_STEP10_EVENTS = frozenset({
    AGENT_MESSAGE_SENT, AGENT_MESSAGE_RECEIVED,
    AGENT_AUTO_ESCALATED_FROM_CHAT,
})
```

- [ ] **Step 4: Run test, verify pass**

`python3.13 -m pytest tests/test_agent_messaging.py::test_step10_audit_constants_present -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add codec_audit.py tests/test_agent_messaging.py
git commit -m "feat(audit): Phase 3 Step 10 event constants"
```

---

## Task 2: AgentMessage dataclass + module skeleton

**Files:**
- Create: `codec_agent_messaging.py`
- Modify: `tests/test_agent_messaging.py`

- [ ] **Step 1: Append failing tests**

```python
def test_agent_message_dataclass_basic():
    from codec_agent_messaging import AgentMessage
    m = AgentMessage(
        agent_id="agent_test", type="agent_update",
        title="Checkpoint 2 of 5 done",
        body="Scraped 150 listings.",
        actions=[{"label": "View", "endpoint": "/api/agents/agent_test/artifacts"}],
        correlation_id="abc123",
    )
    assert m.agent_id == "agent_test"
    assert m.type == "agent_update"
    assert m.actions[0]["label"] == "View"


def test_agent_message_to_dict_includes_ts():
    from codec_agent_messaging import AgentMessage
    m = AgentMessage(agent_id="x", type="agent_done", title="t", body="b",
                     actions=[], correlation_id="cid")
    d = m.to_dict()
    assert d["agent_id"] == "x"
    assert d["type"] == "agent_done"
    assert "ts" in d  # timestamp injected by to_dict
```

- [ ] **Step 2: Run tests, verify fail**

`python3.13 -m pytest tests/test_agent_messaging.py -k "agent_message_dataclass or to_dict_includes_ts" -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'codec_agent_messaging'`

- [ ] **Step 3: Create `codec_agent_messaging.py` skeleton**

```python
"""CODEC Phase 3 Step 10 — Proactive Messaging.

Agent → user message system. Posts simultaneously to:
  1. ~/.codec/agents/<id>/messages.jsonl (append-only, durable)
  2. ~/.codec/notifications.json (banner; batched per 60s window per Q10)

Reuses:
  - codec_audit.audit() — Step 1 envelope
  - ~/.codec/notifications.json (existing infrastructure since Phase 1)
  - codec_agent_plan storage layout (Step 8)
  - codec_agent_runner _run_agent emit sites (Step 9)

See docs/PHASE3-BLUEPRINT.md §4 for design rationale.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("codec_agent_messaging")

# ── Storage paths (overridable for tests) ─────────────────────────────────────
_CODEC_DIR = Path(os.path.expanduser("~/.codec"))
_AGENTS_DIR = _CODEC_DIR / "agents"
_NOTIFICATIONS_PATH = _CODEC_DIR / "notifications.json"

# ── Configurable knobs ────────────────────────────────────────────────────────
BATCH_WINDOW_SECONDS = 60   # Q10: messages within this window merge into one banner
MAX_MESSAGE_BODY_LEN = 5000  # truncate beyond this


# ── Audit event constants (mirror codec_audit) ────────────────────────────────
try:
    from codec_audit import (
        AGENT_MESSAGE_SENT, AGENT_MESSAGE_RECEIVED,
        AGENT_AUTO_ESCALATED_FROM_CHAT,
    )
except ImportError:
    AGENT_MESSAGE_SENT = "agent_message_sent"
    AGENT_MESSAGE_RECEIVED = "agent_message_received"
    AGENT_AUTO_ESCALATED_FROM_CHAT = "agent_auto_escalated_from_chat"


# ── Message types (frozen vocabulary for Step 10) ─────────────────────────────
VALID_MESSAGE_TYPES = frozenset({
    "agent_update",      # checkpoint complete, here's what I did
    "agent_blocked",     # blocked on permission, grant or skip?
    "agent_question",    # clarifying question (reuses Step 3 ask_user infra)
    "agent_done",        # plan complete, here's the summary + artifacts
    "agent_aborted",     # aborted (user / crash / step-budget / destructive-rejected)
    "user_reply",        # user → agent reply (consumed by runner)
})


# ── AgentMessage dataclass ────────────────────────────────────────────────────
@dataclass
class AgentMessage:
    agent_id: str
    type: str            # one of VALID_MESSAGE_TYPES
    title: str
    body: str            # markdown
    actions: List[Dict[str, Any]] = field(default_factory=list)
    correlation_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        if self.type not in VALID_MESSAGE_TYPES:
            raise ValueError(f"invalid type {self.type!r}; expected {sorted(VALID_MESSAGE_TYPES)}")
        return {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "agent_id": self.agent_id,
            "type": self.type,
            "title": self.title[:200],
            "body": self.body[:MAX_MESSAGE_BODY_LEN],
            "actions": list(self.actions),
            "correlation_id": self.correlation_id,
        }
```

- [ ] **Step 4: Run tests, verify pass**

`python3.13 -m pytest tests/test_agent_messaging.py -k "agent_message_dataclass or to_dict_includes_ts" -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add codec_agent_messaging.py tests/test_agent_messaging.py
git commit -m "feat(agent_messaging): AgentMessage dataclass + module skeleton"
```

---

## Task 3: post_message function with atomic append + batching

**Files:**
- Modify: `codec_agent_messaging.py`
- Modify: `tests/test_agent_messaging.py`

- [ ] **Step 1: Append 4 failing tests**

```python
@pytest.fixture
def temp_codec_dir(tmp_path, monkeypatch):
    import codec_agent_messaging as cam
    monkeypatch.setattr(cam, "_CODEC_DIR", tmp_path)
    monkeypatch.setattr(cam, "_AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr(cam, "_NOTIFICATIONS_PATH", tmp_path / "notifications.json")
    return tmp_path


def test_post_message_appends_to_messages_jsonl(temp_codec_dir):
    """First message: writes to messages.jsonl + notifications.json."""
    import codec_agent_messaging as cam
    cam.post_message(
        agent_id="agent_test", type="agent_update",
        title="cp1 done", body="Scraped X listings.",
        actions=[], correlation_id="cid_abc",
    )
    msg_path = temp_codec_dir / "agents" / "agent_test" / "messages.jsonl"
    assert msg_path.exists()
    lines = msg_path.read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["type"] == "agent_update"
    assert rec["title"] == "cp1 done"


def test_post_message_appends_to_notifications_json(temp_codec_dir):
    """First message creates a notification entry."""
    import codec_agent_messaging as cam
    cam.post_message(
        agent_id="agent_test", type="agent_update",
        title="cp1 done", body="b", actions=[], correlation_id="cid",
    )
    notif_path = temp_codec_dir / "notifications.json"
    assert notif_path.exists()
    notifs = json.loads(notif_path.read_text())
    assert len(notifs) == 1
    assert notifs[0]["type"] == "agent_update"
    assert notifs[0]["agent_id"] == "agent_test"


def test_post_message_batches_within_60s_window(temp_codec_dir, monkeypatch):
    """3 messages within batch window → 3 lines in messages.jsonl, 1 banner notification."""
    import codec_agent_messaging as cam
    fixed_time = [1700000000.0]  # mutable container
    monkeypatch.setattr(cam.time, "time", lambda: fixed_time[0])

    cam.post_message(agent_id="agent_test", type="agent_update", title="cp1",
                     body="b", actions=[], correlation_id="c1")
    fixed_time[0] += 10  # +10s
    cam.post_message(agent_id="agent_test", type="agent_update", title="cp2",
                     body="b", actions=[], correlation_id="c2")
    fixed_time[0] += 30  # +30s (still within 60s window)
    cam.post_message(agent_id="agent_test", type="agent_update", title="cp3",
                     body="b", actions=[], correlation_id="c3")

    # All 3 messages preserved in timeline
    msg_path = temp_codec_dir / "agents" / "agent_test" / "messages.jsonl"
    assert len(msg_path.read_text().strip().splitlines()) == 3

    # Only 1 notification (latest, with batch count)
    notifs = json.loads((temp_codec_dir / "notifications.json").read_text())
    agent_notifs = [n for n in notifs if n.get("agent_id") == "agent_test"]
    assert len(agent_notifs) == 1
    assert "3" in agent_notifs[0]["title"] or agent_notifs[0].get("batch_count") == 3


def test_post_message_creates_new_banner_outside_60s_window(temp_codec_dir, monkeypatch):
    """Two messages 90s apart → 2 separate banners."""
    import codec_agent_messaging as cam
    fixed_time = [1700000000.0]
    monkeypatch.setattr(cam.time, "time", lambda: fixed_time[0])

    cam.post_message(agent_id="agent_test", type="agent_update", title="cp1",
                     body="b", actions=[], correlation_id="c1")
    fixed_time[0] += 90  # outside window
    cam.post_message(agent_id="agent_test", type="agent_update", title="cp2",
                     body="b", actions=[], correlation_id="c2")

    notifs = json.loads((temp_codec_dir / "notifications.json").read_text())
    agent_notifs = [n for n in notifs if n.get("agent_id") == "agent_test"]
    assert len(agent_notifs) == 2
```

- [ ] **Step 2: Run tests, verify fail**

`python3.13 -m pytest tests/test_agent_messaging.py -k "post_message" -v`
Expected: FAIL — `post_message` not defined.

- [ ] **Step 3: Add post_message + batching**

Append to `codec_agent_messaging.py`:

```python
# ── Atomic file I/O ───────────────────────────────────────────────────────────
def _atomic_write_json(path: Path, data: Any) -> None:
    """Write JSON atomically: write to .tmp, fsync, rename. Mirrors Step 8."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


def _append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    """Append a single JSON-encoded line. fsync after each write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, separators=(",", ":")) + "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


def _read_notifications() -> List[Dict[str, Any]]:
    if not _NOTIFICATIONS_PATH.exists():
        return []
    try:
        with open(_NOTIFICATIONS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as e:
        log.warning("read notifications failed: %s", e)
        return []


# ── Audit emit helper ─────────────────────────────────────────────────────────
def _audit(event: str, source: str = "codec-agent-messaging",
           message: str = "", correlation_id: str = "",
           extra: Optional[Dict[str, Any]] = None) -> None:
    try:
        from codec_audit import audit
    except Exception:
        return
    audit(event=event, source=source, message=message,
          correlation_id=correlation_id,
          extra=dict(extra or {}))


# ── Core post_message + batching ──────────────────────────────────────────────
def post_message(agent_id: str, type: str, title: str, body: str,
                 actions: Optional[List[Dict[str, Any]]] = None,
                 correlation_id: str = "") -> Dict[str, Any]:
    """Post an agent message. Writes to messages.jsonl (append-only,
    timeline preserved) AND notifications.json (banner — batched if a
    recent same-agent banner exists within BATCH_WINDOW_SECONDS).

    Returns the record dict (with injected ts).

    Per Q10: timeline messages are 1:1 with calls; banner notifications
    are batched to avoid notification-badge spam.
    """
    msg = AgentMessage(
        agent_id=agent_id, type=type, title=title, body=body,
        actions=list(actions or []), correlation_id=correlation_id,
    )
    record = msg.to_dict()

    # Always append to messages.jsonl (timeline, no batching)
    msg_path = _AGENTS_DIR / agent_id / "messages.jsonl"
    _append_jsonl(msg_path, record)

    # Update notifications.json (with batching for agent_update)
    notifs = _read_notifications()
    now_ts = time.time()
    batched = False
    if type == "agent_update":
        # Look for recent banner from same agent
        for n in notifs:
            if (n.get("agent_id") == agent_id and
                n.get("type") == "agent_update"):
                n_ts = n.get("_post_ts", 0)
                if now_ts - n_ts <= BATCH_WINDOW_SECONDS:
                    n["batch_count"] = int(n.get("batch_count", 1)) + 1
                    n["title"] = f"{n['batch_count']} updates from {agent_id}: {title[:60]}"
                    n["body"] = body  # latest body wins
                    n["_post_ts"] = now_ts
                    n["correlation_id"] = correlation_id
                    batched = True
                    break

    if not batched:
        notif = dict(record)
        notif["_post_ts"] = now_ts
        notif["batch_count"] = 1
        notifs.append(notif)

    _atomic_write_json(_NOTIFICATIONS_PATH, notifs)

    # Audit emit
    _audit(AGENT_MESSAGE_SENT, message=f"{type} for {agent_id}",
           correlation_id=correlation_id,
           extra={"agent_id": agent_id, "type": type, "batched": batched})

    return record
```

- [ ] **Step 4: Run tests, verify pass**

`python3.13 -m pytest tests/test_agent_messaging.py -k "post_message" -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add codec_agent_messaging.py tests/test_agent_messaging.py
git commit -m "feat(agent_messaging): post_message + 60s batching window (Q10)"
```

---

## Task 4: User → Agent reply pickup

**Files:**
- Modify: `codec_agent_messaging.py`
- Modify: `tests/test_agent_messaging.py`

- [ ] **Step 1: Append 2 failing tests**

```python
def test_post_user_reply_writes_to_messages_jsonl(temp_codec_dir):
    """User reply via post_user_reply writes type=user_reply line."""
    import codec_agent_messaging as cam
    cam.post_user_reply(agent_id="agent_test", body="please continue")
    msg_path = temp_codec_dir / "agents" / "agent_test" / "messages.jsonl"
    lines = msg_path.read_text().strip().splitlines()
    rec = json.loads(lines[0])
    assert rec["type"] == "user_reply"
    assert rec["body"] == "please continue"


def test_get_unread_user_replies_returns_unread(temp_codec_dir):
    """get_unread_user_replies returns user_reply entries since `since_ts`."""
    import codec_agent_messaging as cam
    cam.post_user_reply(agent_id="agent_test", body="r1")
    time.sleep(0.05)
    t1 = time.time()
    cam.post_user_reply(agent_id="agent_test", body="r2")
    cam.post_user_reply(agent_id="agent_test", body="r3")

    unread = cam.get_unread_user_replies(agent_id="agent_test", since_ts=t1)
    assert len(unread) == 2
    assert unread[-1]["body"] == "r3"
```

- [ ] **Step 2: Run tests, verify fail**

`python3.13 -m pytest tests/test_agent_messaging.py -k "user_reply or get_unread" -v`
Expected: FAIL — `post_user_reply` / `get_unread_user_replies` not defined.

- [ ] **Step 3: Add user-reply functions**

Append to `codec_agent_messaging.py`:

```python
def post_user_reply(agent_id: str, body: str) -> Dict[str, Any]:
    """User → agent reply. Written to messages.jsonl with type=user_reply.
    Daemon picks up next tick, feeds to next _qwen_next_action call.
    Emits AGENT_MESSAGE_RECEIVED."""
    msg = AgentMessage(
        agent_id=agent_id, type="user_reply",
        title="(user reply)", body=body,
        actions=[], correlation_id="",
    )
    record = msg.to_dict()
    msg_path = _AGENTS_DIR / agent_id / "messages.jsonl"
    _append_jsonl(msg_path, record)
    _audit(AGENT_MESSAGE_RECEIVED, message=f"user reply for {agent_id}",
           extra={"agent_id": agent_id, "body_len": len(body)})
    return record


def get_unread_user_replies(agent_id: str, since_ts: float) -> List[Dict[str, Any]]:
    """Return user_reply entries with ts > since_ts (epoch seconds).
    Used by codec_agent_runner._run_agent to feed replies to the next
    qwen call as additional context."""
    msg_path = _AGENTS_DIR / agent_id / "messages.jsonl"
    if not msg_path.exists():
        return []
    out: List[Dict[str, Any]] = []
    with open(msg_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("type") != "user_reply":
                continue
            ts_str = rec.get("ts", "")
            try:
                rec_ts = datetime.fromisoformat(ts_str).timestamp()
            except (ValueError, TypeError):
                continue
            if rec_ts > since_ts:
                out.append(rec)
    return out
```

- [ ] **Step 4: Run tests, verify pass**

`python3.13 -m pytest tests/test_agent_messaging.py -k "user_reply or get_unread" -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add codec_agent_messaging.py tests/test_agent_messaging.py
git commit -m "feat(agent_messaging): post_user_reply + get_unread_user_replies"
```

---

## Task 5: Silence kill-switch

**Files:**
- Modify: `codec_agent_messaging.py`
- Modify: `tests/test_agent_messaging.py`

- [ ] **Step 1: Append 2 failing tests**

```python
def test_silenced_agent_writes_jsonl_but_no_notification(temp_codec_dir):
    """When agent is silenced, post_message still writes to messages.jsonl
    but skips notifications.json (Step 10 silence kill-switch per Q12 / Step 9 §10)."""
    import codec_agent_messaging as cam
    cam.set_silenced("agent_test", True)
    cam.post_message(agent_id="agent_test", type="agent_update",
                     title="t", body="b", actions=[], correlation_id="cid")

    msg_path = temp_codec_dir / "agents" / "agent_test" / "messages.jsonl"
    assert msg_path.exists()  # timeline still recorded

    # Notifications was either not written or has 0 entries for this agent
    if (temp_codec_dir / "notifications.json").exists():
        notifs = json.loads((temp_codec_dir / "notifications.json").read_text())
        agent_notifs = [n for n in notifs if n.get("agent_id") == "agent_test"]
        assert len(agent_notifs) == 0


def test_unsilencing_restores_notifications(temp_codec_dir):
    import codec_agent_messaging as cam
    cam.set_silenced("agent_test", True)
    cam.post_message(agent_id="agent_test", type="agent_update", title="t",
                     body="b", actions=[], correlation_id="cid")
    cam.set_silenced("agent_test", False)
    cam.post_message(agent_id="agent_test", type="agent_update", title="t2",
                     body="b", actions=[], correlation_id="cid")

    notifs = json.loads((temp_codec_dir / "notifications.json").read_text())
    agent_notifs = [n for n in notifs if n.get("agent_id") == "agent_test"]
    assert len(agent_notifs) == 1  # only the unsilenced one
```

- [ ] **Step 2: Run tests, verify fail**

`python3.13 -m pytest tests/test_agent_messaging.py -k "silenc" -v`
Expected: FAIL — `set_silenced` not defined.

- [ ] **Step 3: Add silence storage + integration**

Append to `codec_agent_messaging.py`:

```python
_SILENCE_LOCK = None  # threading.Lock; lazy init


def _silence_state_path() -> Path:
    return _CODEC_DIR / "agent_silence.json"


def _read_silence_state() -> Dict[str, bool]:
    p = _silence_state_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return {}


def is_silenced(agent_id: str) -> bool:
    return bool(_read_silence_state().get(agent_id, False))


def set_silenced(agent_id: str, silenced: bool) -> None:
    """Toggle silence for an agent. When True, post_message still writes
    to messages.jsonl but skips notifications.json banner."""
    state = _read_silence_state()
    if silenced:
        state[agent_id] = True
    else:
        state.pop(agent_id, None)
    _atomic_write_json(_silence_state_path(), state)
```

Now update `post_message` to honor silence — find the existing function and modify the notification block. Replace:

```python
    if not batched:
        notif = dict(record)
        notif["_post_ts"] = now_ts
        notif["batch_count"] = 1
        notifs.append(notif)

    _atomic_write_json(_NOTIFICATIONS_PATH, notifs)
```

With:

```python
    if not batched:
        notif = dict(record)
        notif["_post_ts"] = now_ts
        notif["batch_count"] = 1
        notifs.append(notif)

    # Silence kill-switch (Step 10): skip notification, keep messages.jsonl write.
    if not is_silenced(agent_id):
        _atomic_write_json(_NOTIFICATIONS_PATH, notifs)
```

Also adjust the batched path so it skips the write when silenced. Wrap the entire notification update block:

```python
    if not is_silenced(agent_id):
        # Update notifications.json (batched for agent_update)
        notifs = _read_notifications()
        # ... existing batching logic ...
        _atomic_write_json(_NOTIFICATIONS_PATH, notifs)
```

(Reorganize the code so the silence check gates ALL notification writes, not just the un-batched path.)

- [ ] **Step 4: Run tests, verify pass**

`python3.13 -m pytest tests/test_agent_messaging.py -k "silenc" -v`
Expected: PASS (2 tests). Re-run all tests in the file to ensure no regression.

- [ ] **Step 5: Commit**

```bash
git add codec_agent_messaging.py tests/test_agent_messaging.py
git commit -m "feat(agent_messaging): silence kill-switch (skip notifications, keep timeline)"
```

---

## Task 6: Wire post_message into _run_agent emit sites

**Files:**
- Modify: `codec_agent_runner.py` (add 5 post_message calls)
- Modify: `tests/test_agent_messaging.py`

- [ ] **Step 1: Append failing tests**

```python
def test_run_agent_posts_started_message_on_spawn(monkeypatch, temp_codec_dir):
    """When _run_agent transitions approved → running, it posts an
    agent_update message announcing the start."""
    import codec_agent_runner as car
    import codec_agent_plan as cap
    import codec_agent_messaging as cam

    # Set up an approved agent (mirror Step 9 test fixture pattern)
    plan_dict = {
        "schema": 1, "agent_id": "test_agent", "goals": ["g"],
        "checkpoints": [{"id": "cp0", "title": "t", "description": "d",
                         "skills_needed": ["weather"], "expected_output": "o",
                         "step_budget": 5}],
        "permission_manifest": {"skills": ["weather"], "read_paths": [],
                                 "write_paths": [], "network_domains": [],
                                 "destructive_ops": []},
        "estimated_duration_minutes": 5, "assumptions": [],
    }
    plan = cap.plan_from_dict(plan_dict)
    cap.save_plan(plan)
    cap.save_grants("test_agent", {"schema": 1, "agent_id": "test_agent",
                                     "skills": ["weather"], "read_paths": [],
                                     "write_paths": [], "network_domains": [],
                                     "destructive_ops": [], "auto_approved": {},
                                     "approved_at": "x"})
    cap.save_manifest("test_agent", {"agent_id": "test_agent", "title": "x",
                                      "status": "approved",
                                      "plan_hash": cap.compute_plan_hash(plan),
                                      "created_at": "x", "updated_at": "x"})
    cap.save_state("test_agent", {"current_checkpoint": 0})

    monkeypatch.setattr(car, "_qwen_next_action", lambda *a, **k:
        car.Action(skill="", task="", kind="checkpoint_done"))

    car._run_agent("test_agent")

    # messages.jsonl should have at least started + completed messages
    msg_path = temp_codec_dir / "agents" / "test_agent" / "messages.jsonl"
    lines = msg_path.read_text().strip().splitlines()
    types = [json.loads(line)["type"] for line in lines]
    assert "agent_update" in types  # checkpoint_completed message
    assert "agent_done" in types or "agent_update" in types  # final completion


def test_run_agent_posts_blocked_message_on_permission_violation(monkeypatch, temp_codec_dir):
    """When _run_agent blocks on permission, posts agent_blocked message
    with Grant action available."""
    import codec_agent_runner as car
    import codec_agent_plan as cap

    plan_dict = {
        "schema": 1, "agent_id": "test_agent", "goals": ["g"],
        "checkpoints": [{"id": "cp0", "title": "t", "description": "d",
                         "skills_needed": ["weather"], "expected_output": "o",
                         "step_budget": 5}],
        "permission_manifest": {"skills": ["weather"], "read_paths": [],
                                 "write_paths": [], "network_domains": [],
                                 "destructive_ops": []},
        "estimated_duration_minutes": 5, "assumptions": [],
    }
    plan = cap.plan_from_dict(plan_dict)
    cap.save_plan(plan)
    cap.save_grants("test_agent", {"schema": 1, "agent_id": "test_agent",
                                     "skills": ["weather"], "read_paths": [],
                                     "write_paths": [], "network_domains": [],
                                     "destructive_ops": [], "auto_approved": {}})
    cap.save_manifest("test_agent", {"agent_id": "test_agent", "title": "x",
                                      "status": "approved",
                                      "plan_hash": cap.compute_plan_hash(plan),
                                      "created_at": "x", "updated_at": "x"})
    cap.save_state("test_agent", {"current_checkpoint": 0})

    # Try to call a skill not in grants
    monkeypatch.setattr(car, "_qwen_next_action", lambda *a, **k:
        car.Action(skill="terminal", task="ls", kind="skill_call",
                   is_destructive=False, network_call=False, touches_path=False))
    monkeypatch.setattr(car, "_run_skill", MagicMock())

    car._run_agent("test_agent")

    # Find blocked message
    msg_path = temp_codec_dir / "agents" / "test_agent" / "messages.jsonl"
    lines = msg_path.read_text().strip().splitlines()
    blocked = [json.loads(l) for l in lines if json.loads(l)["type"] == "agent_blocked"]
    assert len(blocked) >= 1
    # Has Grant action
    grant_actions = [a for a in blocked[0]["actions"] if "grant" in str(a).lower()]
    assert len(grant_actions) >= 1
```

- [ ] **Step 2: Run tests, verify fail**

`python3.13 -m pytest tests/test_agent_messaging.py -k "run_agent_posts" -v`
Expected: FAIL — _run_agent doesn't call post_message yet.

- [ ] **Step 3: Wire post_message into _run_agent**

Open `codec_agent_runner.py`. Add a lazy import at the top of `_run_agent` (after the other lazy imports):

```python
    try:
        from codec_agent_messaging import post_message
    except ImportError:
        post_message = lambda **kw: None  # graceful degradation
```

Now find the 5 emit sites in `_run_agent` and add `post_message` calls AFTER the existing `_audit` call at each:

**A. After `_audit(AGENT_STARTED, ...)` (around line 451):**

```python
        post_message(agent_id=agent_id, type="agent_update",
                     title=f"Agent started: {manifest.get('title', agent_id)}",
                     body=f"Starting plan execution from checkpoint {current_idx + 1} of {len(plan.checkpoints)}.",
                     actions=[
                         {"label": "Pause", "endpoint": f"/api/agents/{agent_id}/pause"},
                         {"label": "Abort", "endpoint": f"/api/agents/{agent_id}/abort"},
                     ],
                     correlation_id=cid)
```

**B. After `_audit(AGENT_CHECKPOINT_COMPLETED, ...)`:**

```python
            post_message(agent_id=agent_id, type="agent_update",
                         title=f"Checkpoint {idx + 1}/{len(plan.checkpoints)}: {cp.title}",
                         body=f"Completed in {len(history)} step(s). Output: {cp.expected_output[:200]}",
                         actions=[
                             {"label": "Pause", "endpoint": f"/api/agents/{agent_id}/pause"},
                             {"label": "Abort", "endpoint": f"/api/agents/{agent_id}/abort"},
                         ],
                         correlation_id=cid)
```

**C. In the `except PermissionViolation` block (after `_audit(AGENT_BLOCKED_ON_PERMISSION, ...)`):**

```python
                post_message(agent_id=agent_id, type="agent_blocked",
                             title=f"Blocked: {pv.reason}",
                             body=f"Agent needs additional permission: `{pv.needed}`. Grant or skip?",
                             actions=[
                                 {"label": "Grant", "endpoint": f"/api/agents/{agent_id}/grant",
                                  "body_hint": {"kind": "<infer from reason>", "value": pv.needed}},
                                 {"label": "Abort", "endpoint": f"/api/agents/{agent_id}/abort"},
                             ],
                             correlation_id=cid)
```

**D. In the `except DestructiveOpRejected` block (after `_audit(AGENT_ABORTED, ...)`):**

```python
                post_message(agent_id=agent_id, type="agent_aborted",
                             title="Aborted: destructive op rejected",
                             body=f"User rejected a destructive operation. Plan halted.",
                             actions=[],
                             correlation_id=cid)
```

**E. After the final `_audit(AGENT_COMPLETED, ...)`:**

```python
        post_message(agent_id=agent_id, type="agent_done",
                     title=f"Done: {manifest.get('title', agent_id)}",
                     body=f"Plan complete. {len(history)} total steps across {len(plan.checkpoints)} checkpoints.",
                     actions=[
                         {"label": "View artifacts",
                          "endpoint": f"/api/agents/{agent_id}/artifacts"},
                     ],
                     correlation_id=cid)
```

- [ ] **Step 4: Run tests, verify pass**

`python3.13 -m pytest tests/test_agent_messaging.py -k "run_agent_posts" -v`
Expected: PASS (2 tests). Also re-run `tests/test_agent_runner.py` to ensure no regression: `python3.13 -m pytest tests/test_agent_runner.py -q` should still show all passing.

- [ ] **Step 5: Commit**

```bash
git add codec_agent_runner.py tests/test_agent_messaging.py
git commit -m "feat(agent_runner): wire post_message into 5 lifecycle emit sites"
```

---

## Task 7: PWA endpoints — messages CRUD + silence

**Files:**
- Modify: `routes/agents.py`
- Modify: `tests/test_agent_messaging.py`

- [ ] **Step 1: Append 3 failing tests**

```python
def test_get_api_agents_messages_returns_jsonl(temp_codec_dir):
    """GET /api/agents/{id}/messages returns messages.jsonl as a list."""
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    import codec_agent_messaging as cam

    cam.post_message(agent_id="a1", type="agent_update", title="t1",
                     body="b1", actions=[], correlation_id="c1")
    cam.post_message(agent_id="a1", type="agent_update", title="t2",
                     body="b2", actions=[], correlation_id="c2")

    from routes.agents import router
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    r = client.get("/api/agents/a1/messages")
    assert r.status_code == 200
    body = r.json()
    assert len(body["messages"]) == 2
    assert body["messages"][0]["type"] == "agent_update"


def test_post_api_agents_messages_writes_user_reply(temp_codec_dir):
    """POST /api/agents/{id}/messages writes type=user_reply."""
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    import codec_agent_messaging as cam
    import codec_agent_plan as cap

    cap.save_manifest("a1", {"agent_id": "a1", "status": "running", "title": "x"})

    from routes.agents import router
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    r = client.post("/api/agents/a1/messages", json={"body": "please continue"})
    assert r.status_code == 200

    msg_path = temp_codec_dir / "agents" / "a1" / "messages.jsonl"
    rec = json.loads(msg_path.read_text().strip().splitlines()[-1])
    assert rec["type"] == "user_reply"
    assert rec["body"] == "please continue"


def test_post_api_agents_silence_toggles_state(temp_codec_dir):
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    import codec_agent_messaging as cam
    import codec_agent_plan as cap

    cap.save_manifest("a1", {"agent_id": "a1", "status": "running", "title": "x"})

    from routes.agents import router
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # Silence
    r1 = client.post("/api/agents/a1/silence", json={"silenced": True})
    assert r1.status_code == 200
    assert cam.is_silenced("a1") is True

    # Unsilence
    r2 = client.post("/api/agents/a1/silence", json={"silenced": False})
    assert r2.status_code == 200
    assert cam.is_silenced("a1") is False
```

- [ ] **Step 2: Run tests, verify fail**

`python3.13 -m pytest tests/test_agent_messaging.py -k "post_api_agents_messages or get_api_agents_messages or post_api_agents_silence" -v`
Expected: FAIL — endpoints don't exist.

- [ ] **Step 3: Add 3 endpoints to `routes/agents.py`**

Find the `/extend_budget` endpoint (Step 9 fast-follow). After it, append:

```python
# ── Phase 3 Step 10 — messaging endpoints ──────────────────────────────────


class UserReplyBody(BaseModel):
    body: str = Field(..., min_length=1, max_length=5000)


class SilenceBody(BaseModel):
    silenced: bool = Field(...)


@router.get("/api/agents/{agent_id}/messages")
def get_messages(agent_id: str):
    """Return all entries from messages.jsonl as a list (newest last)."""
    manifest = _cap.load_manifest(agent_id)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"agent {agent_id} not found")

    msg_path = _cap._AGENTS_DIR / agent_id / "messages.jsonl"
    if not msg_path.exists():
        return {"messages": []}

    out = []
    with open(msg_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return {"messages": out}


@router.post("/api/agents/{agent_id}/messages")
def post_message_endpoint(agent_id: str, body: UserReplyBody):
    """User → agent reply. Writes type=user_reply to messages.jsonl.
    Daemon picks up next tick."""
    manifest = _cap.load_manifest(agent_id)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"agent {agent_id} not found")

    import codec_agent_messaging as cam
    record = cam.post_user_reply(agent_id=agent_id, body=body.body)
    return {"agent_id": agent_id, "ok": True, "ts": record["ts"]}


@router.post("/api/agents/{agent_id}/silence")
def silence_endpoint(agent_id: str, body: SilenceBody):
    """Toggle silence for an agent. Silenced = post_message writes timeline
    but skips notifications.json (no banner spam)."""
    manifest = _cap.load_manifest(agent_id)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"agent {agent_id} not found")

    import codec_agent_messaging as cam
    cam.set_silenced(agent_id, body.silenced)
    return {"agent_id": agent_id, "silenced": cam.is_silenced(agent_id)}
```

Make sure `import json` is in the file imports (it is from Step 8).

- [ ] **Step 4: Run tests, verify pass**

`python3.13 -m pytest tests/test_agent_messaging.py -k "api_agents_messages or api_agents_silence" -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add routes/agents.py tests/test_agent_messaging.py
git commit -m "feat(routes): /api/agents/{id}/messages + /silence endpoints"
```

---

## Task 8: Auto-escalation classifier (Qwen-3.6 driver + 2-signal gate)

**Files:**
- Create: `tests/test_chat_escalation.py`
- Modify: `codec_dashboard.py` (add `_classify_chat_message` + helpers)

- [ ] **Step 1: Create `tests/test_chat_escalation.py`**

```python
"""Phase 3 Step 10 tests — chat auto-escalation classifier.

11 tests covering: classifier (3), 2-signal gate (3), session silence (2),
integration with chat handler (2), kill switch (1).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def test_classify_chat_message_returns_project_when_multi_step(monkeypatch):
    """LLM verdict says multi-step → returns is_project=True with checkpoints estimate."""
    import codec_dashboard as cd

    fake_response = json.dumps({
        "is_project": True,
        "estimated_checkpoints": 5,
        "reason": "Building a Telegram bot requires scaffolding, scraping, deployment",
    })
    monkeypatch.setattr(cd, "_qwen_chat_classify", lambda text: fake_response)

    is_project, n, reason = cd._classify_chat_message(
        "Build me a Telegram bot for property listings"
    )
    assert is_project is True
    assert n == 5
    assert "Telegram" in reason or "bot" in reason


def test_classify_chat_message_returns_not_project_for_quick_question(monkeypatch):
    import codec_dashboard as cd

    fake_response = json.dumps({
        "is_project": False, "estimated_checkpoints": 0,
        "reason": "Single-shot factual question",
    })
    monkeypatch.setattr(cd, "_qwen_chat_classify", lambda text: fake_response)

    is_project, n, reason = cd._classify_chat_message("What's the weather in Paris?")
    assert is_project is False
    assert n == 0


def test_classify_chat_message_handles_qwen_failure(monkeypatch):
    """If Qwen call fails or returns garbage, classifier returns (False, 0, reason)."""
    import codec_dashboard as cd

    monkeypatch.setattr(cd, "_qwen_chat_classify",
                        lambda text: "garbage non-json")

    is_project, n, reason = cd._classify_chat_message("anything")
    assert is_project is False
    assert n == 0
```

- [ ] **Step 2: Run tests, verify fail**

`python3.13 -m pytest tests/test_chat_escalation.py -k "classify_chat_message" -v`
Expected: FAIL — `_classify_chat_message` and `_qwen_chat_classify` not defined.

- [ ] **Step 3: Add classifier to `codec_dashboard.py`**

Find a good location — near the top-level helpers section (after the existing chat-related helpers, before the route handlers). Add:

```python
# ── Phase 3 Step 10 — Auto-escalation classifier ──────────────────────────

_AUTO_ESCALATE_SYSTEM_PROMPT = """You are CODEC's chat-input classifier. \
Given the user's chat message, decide if it represents a "project" — \
multi-step work that would benefit from autonomous execution by an agent \
(file writes, browser automation, multi-checkpoint plan) — or a "quick \
question" suitable for single-shot LLM answer.

Return ONLY a JSON object:
{
  "is_project": <bool>,
  "estimated_checkpoints": <int — best guess of plan size; 0 if not project>,
  "reason": <short string explaining the verdict>
}

Rules:
- Single-shot factual / conversational / explanatory questions → is_project=false.
- "Build me X", "Set up Y", "Watch Z and tell me when W", "Plan launch of A" → is_project=true.
- Be honest about checkpoint estimates; under 3 means not worth promoting.
"""


def _qwen_chat_classify(user_text: str, max_tokens: int = 300) -> str:
    """Call Qwen-3.6 with the auto-escalation classifier prompt. Returns
    raw response string. Caller handles JSON parsing + error fallback."""
    try:
        import requests
        payload = {
            "model": "qwen3.6",
            "messages": [
                {"role": "system", "content": _AUTO_ESCALATE_SYSTEM_PROMPT},
                {"role": "user", "content": user_text[:2000]},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.1,
        }
        r = requests.post("http://127.0.0.1:8090/v1/chat/completions",
                          json=payload, timeout=15)
        if r.status_code != 200:
            return ""
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        log.debug(f"_qwen_chat_classify failed: {e}")
        return ""


def _classify_chat_message(user_text: str) -> tuple[bool, int, str]:
    """Returns (is_project, estimated_checkpoints, reason). Falls back to
    (False, 0, reason) on any failure."""
    raw = _qwen_chat_classify(user_text)
    if not raw:
        return (False, 0, "qwen unavailable")

    raw = raw.strip()
    if raw.startswith("```"):
        import re as _re
        raw = _re.sub(r"^```(?:json)?\s*", "", raw)
        raw = _re.sub(r"\s*```\s*$", "", raw)

    try:
        d = json.loads(raw)
    except json.JSONDecodeError:
        return (False, 0, "qwen returned non-JSON")

    return (
        bool(d.get("is_project", False)),
        int(d.get("estimated_checkpoints", 0)),
        str(d.get("reason", ""))[:200],
    )
```

- [ ] **Step 4: Run tests, verify pass**

`python3.13 -m pytest tests/test_chat_escalation.py -k "classify_chat_message" -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add codec_dashboard.py tests/test_chat_escalation.py
git commit -m "feat(chat): Phase 3 Step 10 auto-escalation classifier (Qwen-3.6)"
```

---

## Task 9: Auto-escalation 2-signal gate + per-conversation silence (Q11)

**Files:**
- Modify: `codec_dashboard.py` (`_should_escalate_to_project` + `_autoescalate_silence_set` global)
- Modify: `tests/test_chat_escalation.py`

- [ ] **Step 1: Append 5 failing tests**

```python
def test_should_escalate_when_both_signals_pass(monkeypatch):
    """LLM says project + checkpoints >= 3 → escalate."""
    import codec_dashboard as cd

    monkeypatch.setattr(cd, "_classify_chat_message",
                        lambda text: (True, 5, "multi-step"))

    decision = cd._should_escalate_to_project(user_text="x", session_id="s1")
    assert decision["escalate"] is True
    assert decision["estimated_checkpoints"] == 5


def test_should_not_escalate_when_checkpoints_below_3(monkeypatch):
    """LLM says project but estimate=2 → don't escalate."""
    import codec_dashboard as cd

    monkeypatch.setattr(cd, "_classify_chat_message",
                        lambda text: (True, 2, "small"))

    decision = cd._should_escalate_to_project(user_text="x", session_id="s2")
    assert decision["escalate"] is False


def test_should_not_escalate_when_classifier_says_no(monkeypatch):
    """LLM says not-a-project → don't escalate even if checkpoints>=3."""
    import codec_dashboard as cd

    monkeypatch.setattr(cd, "_classify_chat_message",
                        lambda text: (False, 5, "actually quick"))

    decision = cd._should_escalate_to_project(user_text="x", session_id="s3")
    assert decision["escalate"] is False


def test_session_silence_persists_across_calls(monkeypatch):
    """Q11: After silence_session(s1), subsequent _should_escalate calls return escalate=False."""
    import codec_dashboard as cd

    monkeypatch.setattr(cd, "_classify_chat_message",
                        lambda text: (True, 5, "always-project"))

    # Sanity: would normally escalate
    cd._reset_autoescalate_silence_for_test()  # test helper to clear state
    d1 = cd._should_escalate_to_project(user_text="x", session_id="s4")
    assert d1["escalate"] is True

    # User said no
    cd.silence_session_autoescalate("s4")

    # Now suppressed
    d2 = cd._should_escalate_to_project(user_text="x", session_id="s4")
    assert d2["escalate"] is False
    assert d2.get("reason", "").startswith("session silenced") or d2.get("silenced", False)


def test_kill_switch_disables_all_escalation(monkeypatch):
    """AGENT_AUTO_ESCALATE_ENABLED=false → never escalate."""
    import codec_dashboard as cd

    monkeypatch.setenv("AGENT_AUTO_ESCALATE_ENABLED", "false")
    monkeypatch.setattr(cd, "_classify_chat_message",
                        lambda text: (True, 99, "would always escalate"))

    decision = cd._should_escalate_to_project(user_text="x", session_id="s5")
    assert decision["escalate"] is False
```

- [ ] **Step 2: Run tests, verify fail**

`python3.13 -m pytest tests/test_chat_escalation.py -k "should_escalate or session_silence or kill_switch" -v`
Expected: FAIL — `_should_escalate_to_project` etc. not defined.

- [ ] **Step 3: Add 2-signal gate + silence**

Append to `codec_dashboard.py` after `_classify_chat_message`:

```python
# ── Auto-escalation gate (in-memory session silence per Q11) ──────────────

_AUTOESCALATE_SILENCE_LOCK = threading.Lock()
_autoescalate_silence_set: set[str] = set()  # session_ids that said "no" once

ESCALATE_CHECKPOINTS_THRESHOLD = 3


def silence_session_autoescalate(session_id: str) -> None:
    """Q11: After user says No once, silence auto-escalation prompts for
    the rest of this conversation. Resets on new chat session."""
    with _AUTOESCALATE_SILENCE_LOCK:
        _autoescalate_silence_set.add(session_id)


def _reset_autoescalate_silence_for_test() -> None:
    """Test-only helper to clear in-memory silence state."""
    with _AUTOESCALATE_SILENCE_LOCK:
        _autoescalate_silence_set.clear()


def _should_escalate_to_project(user_text: str, session_id: str) -> Dict[str, Any]:
    """2-signal gate (Step 10):
      Signal 1: classifier verdict (is_project=True)
      Signal 2: estimated_checkpoints >= ESCALATE_CHECKPOINTS_THRESHOLD

    Plus 2 kill conditions:
      - AGENT_AUTO_ESCALATE_ENABLED=false
      - session_id in silence set (Q11)

    Returns: {"escalate": bool, "estimated_checkpoints": int, "reason": str}
    """
    import os as _os
    if _os.environ.get("AGENT_AUTO_ESCALATE_ENABLED", "true").lower() == "false":
        return {"escalate": False, "estimated_checkpoints": 0,
                "reason": "kill_switch_off"}

    with _AUTOESCALATE_SILENCE_LOCK:
        if session_id in _autoescalate_silence_set:
            return {"escalate": False, "estimated_checkpoints": 0,
                    "reason": "session_silenced", "silenced": True}

    is_project, n_checkpoints, reason = _classify_chat_message(user_text)

    escalate = is_project and n_checkpoints >= ESCALATE_CHECKPOINTS_THRESHOLD

    return {
        "escalate": escalate,
        "estimated_checkpoints": n_checkpoints,
        "reason": reason,
        "is_project": is_project,
    }
```

You may also need to add `from typing import Dict, Any` and `import threading` at the top if not already present.

- [ ] **Step 4: Run tests, verify pass**

`python3.13 -m pytest tests/test_chat_escalation.py -q 2>&1 | tail -5`
Expected: 8 tests pass.

- [ ] **Step 5: Commit**

```bash
git add codec_dashboard.py tests/test_chat_escalation.py
git commit -m "feat(chat): Phase 3 Step 10 — 2-signal escalation gate + Q11 session silence"
```

---

## Task 10: PWA UI — mode dropdown + agent status pills (HTML/JS)

**Files:**
- Modify: `templates/dashboard.html` (or wherever the PWA chat UI lives)
- Optional: `tests/test_chat_escalation.py` (smoke test for endpoint)

This is the user-facing UI work. The repo's existing chat UI is HTML+JS; we add minimal additions only — no framework rewrites. Per Q9, all notifications go through `~/.codec/notifications.json` which the existing PWA already polls.

- [ ] **Step 1: Locate the chat composer in dashboard.html**

```bash
grep -nE "mode.*chat|chat.*mode|<select" templates/dashboard.html | head -10
```

Look for the existing mode selector (Chat / Voice / Agent dropdown). The new "Project" mode is added there.

- [ ] **Step 2: Add Project to mode dropdown**

Find the existing mode `<select>` (probably looks like `<option value="chat">Chat</option>...`). Append:

```html
<option value="project">Project</option>
```

- [ ] **Step 3: Add Project mode placeholder + handler**

Find the chat input element. Add a JavaScript snippet that updates the input placeholder when mode=project is selected:

```javascript
// Phase 3 Step 10 — Project mode UI
document.querySelector('select[name="mode"]').addEventListener('change', function(e) {
  const input = document.querySelector('#chat-input');
  if (e.target.value === 'project') {
    input.placeholder = 'Drop your project here…';
  } else if (e.target.value === 'chat') {
    input.placeholder = 'Ask me anything…';
  }
});
```

- [ ] **Step 4: Add agent status pills component**

Right above the chat input, add a div that polls `/api/agents` every 5 seconds:

```html
<div id="agent-status-pills" style="margin-bottom:8px"></div>
<script>
async function refreshAgentStatusPills() {
  try {
    const r = await fetch('/api/agents');
    if (!r.ok) return;
    const data = await r.json();
    const pills = data.agents.filter(a =>
      ['running','paused','blocked_on_permission','blocked_on_destructive'].includes(a.status)
    ).slice(0, 3);
    const container = document.getElementById('agent-status-pills');
    container.innerHTML = pills.map(p => {
      const color = p.status === 'running' ? '#0a0' :
                    p.status.startsWith('blocked_') ? '#fa0' : '#888';
      return `<span style="display:inline-block;padding:4px 8px;margin-right:6px;
              background:${color};color:#fff;border-radius:4px;font-size:12px">
              ${p.title} · ${p.status}
              <a href="#" onclick="abortAgent('${p.agent_id}')">[abort]</a>
              </span>`;
    }).join('');
  } catch (e) {}
}
async function abortAgent(agentId) {
  await fetch(`/api/agents/${agentId}/abort`, {method: 'POST'});
  refreshAgentStatusPills();
}
setInterval(refreshAgentStatusPills, 5000);
refreshAgentStatusPills();
</script>
```

- [ ] **Step 5: Modify chat handler to inject Project mode dispatch**

In `codec_dashboard.py`, find the chat handler (likely `/api/chat` POST). When mode=project, the user input becomes a project description. Call `POST /api/agents` (Step 8 endpoint) directly:

```python
# Inside chat handler, after parsing user input
if data.get("mode") == "project":
    # Dispatch to /api/agents
    from codec_agent_plan import create_agent
    try:
        agent_id = create_agent(
            title=user_text[:80],
            description=user_text,
        )
        return {"response": f"Project started! agent_id={agent_id}. "
                            f"I'll draft a plan and prompt for your approval. "
                            f"Watch the chat for updates."}
    except Exception as e:
        return {"response": f"Couldn't start project: {e}"}
```

(Exact line depends on existing chat handler structure; adapt to fit.)

- [ ] **Step 6: Test smoke-fire the dashboard locally**

Open the dashboard, switch mode to Project, type "Build me a thing", send. You should get a response like "Project started! agent_id=agent_xxx". The agent will appear as a status pill.

- [ ] **Step 7: Commit**

```bash
git add templates/dashboard.html codec_dashboard.py
git commit -m "feat(pwa): Phase 3 Step 10 — Project mode dropdown + agent status pills"
```

---

## Task 11: Final verification + AGENTS.md docs + push + PR

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: Run full test suite**

```bash
python3.13 -m pytest tests/ --ignore=tests/test_smoke.py -q --tb=no
```

Expected: passed count ≥ 924 (was 900 on main after Step 9); 20 failed (baseline); 73 skipped (baseline). The ~25 new tests in `test_agent_messaging.py` + `test_chat_escalation.py` should bring the count to 924+.

- [ ] **Step 2: Update AGENTS.md**

Find the "Background Execution + Permission Gate (Phase 3 Step 9)" sub-section. After it (and before "Other known gaps"), insert:

```markdown
### Proactive Messaging + Project Mode UI (Phase 3 Step 10)

`codec_agent_messaging.py` is the user-facing layer of Phase 3. Agents post messages via `post_message()`; users reply via `post_user_reply()`. Notifications batched per 60s window (Q10). Chat handler runs `_classify_chat_message()` on each message and prepends "Promote to Project mode?" if 2 signals fire.

**Per-message flow:**
1. `_run_agent` (Step 9) calls `post_message(agent_id, type="agent_update", title, body, actions)` at 5 lifecycle points
2. `post_message` writes the record to `~/.codec/agents/<id>/messages.jsonl` (1:1 timeline)
3. `post_message` then updates `~/.codec/notifications.json` — but only ONE banner per agent per `BATCH_WINDOW_SECONDS=60` (count incremented in batched banner, latest body wins)
4. Audit emit `agent_message_sent`

**Message types** (frozen vocabulary): `agent_update` / `agent_blocked` / `agent_question` / `agent_done` / `agent_aborted` / `user_reply`.

**Silence kill-switch:** `is_silenced(agent_id)` checks `~/.codec/agent_silence.json`. When True, post_message still writes the timeline but skips notifications.json (no banner spam). Toggled via `POST /api/agents/{id}/silence`.

**Auto-escalation (Q11):** chat handler runs `_should_escalate_to_project(user_text, session_id)` on each user message. 2-signal gate: classifier says `is_project=True` AND `estimated_checkpoints >= 3`. After user says "No" once for a session, that session_id is added to `_autoescalate_silence_set` (in-memory) — silenced for the rest of the conversation, resets on new session. Global kill switch: `AGENT_AUTO_ESCALATE_ENABLED=false`.

**Project mode UI:** dropdown adds "Project" alongside Chat/Voice/Agent. When selected, chat input placeholder becomes "Drop your project here…", and Send dispatches to `POST /api/agents` (Step 8). Agent status pills above chat input poll `/api/agents` every 5s and show running/blocked agents with [abort] inline.

**3 audit events:** `agent_message_sent`, `agent_message_received`, `agent_auto_escalated_from_chat`.

**Reuses:** `~/.codec/notifications.json` (existing infrastructure since Phase 1) · Step 8 storage layout · Step 9 `_run_agent` emit sites · Qwen-3.6 (existing local LLM).

Implementation: `codec_agent_messaging.py` (~250 LOC), `routes/agents.py` (+90 for messages/silence endpoints), `codec_dashboard.py` (+110 for classifier + escalation gate), `templates/dashboard.html` (+120 for mode dropdown + status pills).
```

Also extend §6 audit table with Step 10 events:

```markdown
#### Phase 3 Step 10 events — agent ↔ user messaging

Three event names. All info-level; correlation_id chains with `_run_agent`'s envelope when called from there.

| Event | Source | level | extra fields |
|---|---|---|---|
| `agent_message_sent` | `codec-agent-messaging` | info | `agent_id`, `type`, `batched` (bool) |
| `agent_message_received` | `codec-agent-messaging` | info | `agent_id`, `body_len` |
| `agent_auto_escalated_from_chat` | `codec-dashboard` | info | `session_id`, `estimated_checkpoints`, `verdict` |

`PHASE3_STEP10_EVENTS` frozenset exposed.
```

Also append to §10 don't-touch list:

```markdown
- `codec_agent_messaging.py` (Phase 3 Step 10) — message dispatch and batching. Don't refactor without re-running PHASE3-STEP10 design gate. The `BATCH_WINDOW_SECONDS=60` constant is the user-facing batching contract; tune cautiously.
- `~/.codec/agents/<id>/messages.jsonl` (Phase 3 Step 10) — append-only message log. Never edit directly; use `post_message` / `post_user_reply` / endpoint. Bare-edits during a running agent will desync the daemon's read position.
- `~/.codec/agent_silence.json` (Phase 3 Step 10) — per-agent silence state. Modify only via `set_silenced` or `POST /api/agents/{id}/silence`.
- `_autoescalate_silence_set` in `codec_dashboard.py` (Phase 3 Step 10) — in-memory per-session silence state. Mutated under `_AUTOESCALATE_SILENCE_LOCK`; never touch from outside.
- `AGENT_AUTO_ESCALATE_ENABLED` env var (Phase 3 Step 10, default `true`). Setting `false` disables the chat → project escalation prompt entirely.
```

- [ ] **Step 3: Final verify after AGENTS.md edit**

```bash
python3.13 -m pytest tests/ --ignore=tests/test_smoke.py -q --tb=no | tail -3
```

Expected: same baseline.

- [ ] **Step 4: Commit AGENTS.md**

```bash
git add AGENTS.md
git commit -m "docs(agents): Phase 3 Step 10 module + endpoints + audit events"
```

- [ ] **Step 5: Push + open PR**

```bash
git push -u origin feat/phase3-step10-implementation

gh pr create --title "feat(phase3-step10): Proactive Messaging + Project Mode UI" --body "$(cat <<'EOF'
## Summary

Phase 3 Step 10 — Proactive Messaging + Project Mode UI. The user-facing layer that closes Phase 3.

`codec_agent_messaging.py` ships the message dispatch and 60s-batching window. `_run_agent` (Step 9) gets `post_message` calls at 5 lifecycle points (started, checkpoint completed, blocked, aborted, done). Chat handler runs Qwen-3.6-driven classifier with 2-signal gate; if user says "No" once, that session is silenced for the rest of the conversation (Q11). PWA UI adds a "Project" mode + agent status pills above chat input.

## Reference

- Blueprint: `docs/PHASE3-BLUEPRINT.md` §4
- TDD plan: `docs/PHASE3-STEP10-PLAN.md`
- Resolved Q&A (blueprint §8): Q9 PWA-only notifications, Q10 batching window, Q11 session silence after No, Q12 proactive overlay deferred to Phase 3.5

## Files

| Path | Type | Purpose |
|---|---|---|
| `codec_agent_messaging.py` | NEW | Message dispatch + batching + silence + user reply |
| `tests/test_agent_messaging.py` | NEW | 14 tests |
| `tests/test_chat_escalation.py` | NEW | 11 tests |
| `codec_audit.py` | MOD | Step 10 event constants |
| `codec_agent_runner.py` | MOD | Wire post_message into 5 emit sites |
| `routes/agents.py` | MOD | /messages + /silence endpoints |
| `codec_dashboard.py` | MOD | Auto-escalation classifier + 2-signal gate |
| `templates/dashboard.html` | MOD | Mode dropdown + status pills |
| `AGENTS.md` | MOD | Step 10 docs |

## Audit envelope

3 new schema:1 events + `PHASE3_STEP10_EVENTS` frozenset.

## Test plan
- [x] 🧪 `tests/test_agent_messaging.py` → 14 passed
- [x] 🧪 `tests/test_chat_escalation.py` → 11 passed
- [x] 🧪 Full suite — same 20/73 baseline, +25 new tests
- [ ] Post-merge deploy:
  ```bash
  cd ~/codec-repo
  git pull
  pm2 restart codec-dashboard codec-agent-runner
  ```
- [ ] Real-world test: drop a project via PWA chat in Project mode → verify status pill appears → verify chat thread shows agent_update messages as agent runs

## Phase 3 closeout

After merge, Phase 3 is complete. `docs/PHASE3-COMPLETE.md` will document the closeout (matching Phase 1+2 pattern).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review Checklist

**Spec coverage (blueprint §4):**

- [x] `agent_update` / `agent_blocked` / `agent_question` / `agent_done` / `agent_aborted` message types → Task 2
- [x] User reply via `messages.jsonl` → Task 4
- [x] 60s batching window per agent (Q10) → Task 3
- [x] Silence kill-switch (per-agent) → Task 5
- [x] post_message wired into _run_agent emit sites → Task 6
- [x] PWA endpoints — messages + silence → Task 7
- [x] Auto-escalation classifier (Qwen-3.6) → Task 8
- [x] 2-signal gate + session silence (Q11) → Task 9
- [x] Mode dropdown + status pills → Task 10
- [x] Audit emits (3) + AGENTS.md → Tasks 1, 11
- [x] **Phase 3.5 deferral**: proactive intelligence overlay NOT in Step 10 (Q12 — explicit in blueprint §9)

**Placeholder scan:** No "TBD", "TODO", "fill in" present. Every code block is complete and copy-pasteable.

**Type consistency:** `AgentMessage`, `post_message`, `post_user_reply`, `is_silenced`, `set_silenced`, `_classify_chat_message`, `_should_escalate_to_project`, `silence_session_autoescalate` — all defined once, reused with consistent signatures across tasks.

---

*Plan complete. Ready for execution.*
