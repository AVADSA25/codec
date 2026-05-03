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


# ── Silence storage ───────────────────────────────────────────────────────────
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

    # Silence kill-switch (Step 10): skip notification, keep messages.jsonl write.
    if not is_silenced(agent_id):
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
    else:
        batched = False

    # Audit emit
    _audit(AGENT_MESSAGE_SENT, message=f"{type} for {agent_id}",
           correlation_id=correlation_id,
           extra={"agent_id": agent_id, "type": type, "batched": False if is_silenced(agent_id) else False})

    return record


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
