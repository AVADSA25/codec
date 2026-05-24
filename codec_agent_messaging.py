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
    """Write JSON atomically: write to .tmp (0600), fsync, rename. Mirrors Step 8.
    (B-10: 0600 file + 0700 dir — covers agent_silence.json + the notifications
    fallback path; agent state must not be world-readable.)"""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=False)
        f.flush()
        os.fsync(f.fileno())
    try:
        os.chmod(tmp_path, 0o600)
    except OSError:
        pass
    os.replace(tmp_path, path)


def _append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    """Append a single JSON-encoded line. fsync after each write. (B-10: 0600
    file + 0700 dir — messages.jsonl holds user replies + skill results.)"""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    line = json.dumps(record, separators=(",", ":")) + "\n"
    # O_APPEND|O_CREAT with 0o600 so a freshly-created log isn't world-readable.
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())
    try:
        os.chmod(path, 0o600)  # defensive: a pre-existing log may predate this change
    except OSError:
        pass


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


def _notifications_lock():
    """B-11: cross-process flock for the notifications.json read-modify-write so a
    runner banner + a scheduler/heartbeat/ask_user notification can't clobber each
    other (every other writer already goes through codec_jsonstore.file_lock per
    PR-4C). Nullcontext fallback if codec_jsonstore is unavailable (headless/CI) —
    same shape as codec_agent_plan._status_lock (PR-7D)."""
    try:
        import codec_jsonstore
        return codec_jsonstore.file_lock(_NOTIFICATIONS_PATH)
    except Exception:
        import contextlib
        return contextlib.nullcontext()


def _write_notifications(notifs: List[Dict[str, Any]]) -> None:
    """B-10/B-11: persist notifications.json 0600 via the shared cross-process
    store when available (also chmods 0600), else the local atomic writer (also
    0600). Caller MUST hold _notifications_lock() around the read+write."""
    try:
        import codec_jsonstore
        codec_jsonstore.atomic_write_json(_NOTIFICATIONS_PATH, notifs)
    except Exception:
        _atomic_write_json(_NOTIFICATIONS_PATH, notifs)


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
    batched = False
    if not is_silenced(agent_id):
        # B-11: the whole read → batch-merge → write is ONE cross-process critical
        # section, shared with every other notifications writer (scheduler,
        # heartbeat, ask_user, dashboard) via the same flock — otherwise a racing
        # write drops this banner (the user's only "agent needs you" surface).
        with _notifications_lock():
            notifs = _read_notifications()
            now_ts = time.time()
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

            _write_notifications(notifs)

    # Phase 3.5 — multi-channel notification dispatch.
    # Reads agent's notification_channels from manifest. Each non-`pwa`
    # channel gets its own dispatch (best-effort; failures don't block).
    if not is_silenced(agent_id):
        try:
            channels = _agent_notification_channels(agent_id)
            for ch in channels:
                if ch == "pwa":
                    continue   # already covered by notifications.json above
                try:
                    _dispatch_to_channel(ch, agent_id, title, body, type)
                except Exception as e:
                    log.debug("[%s] channel %s dispatch failed: %s", agent_id, ch, e)
        except Exception as e:
            log.debug("[%s] channel dispatch wrapper failed: %s", agent_id, e)

    # Audit emit
    _audit(AGENT_MESSAGE_SENT, message=f"{type} for {agent_id}",
           correlation_id=correlation_id,
           extra={"agent_id": agent_id, "type": type, "batched": batched})

    return record


def _agent_notification_channels(agent_id: str) -> List[str]:
    """Read manifest.notification_channels. Defaults to ['pwa']."""
    manifest_path = _AGENTS_DIR / agent_id / "manifest.json"
    if not manifest_path.exists():
        return ["pwa"]
    try:
        data = json.loads(manifest_path.read_text())
        chs = data.get("notification_channels") or ["pwa"]
        return [c for c in chs if isinstance(c, str)] or ["pwa"]
    except Exception:
        return ["pwa"]


def _dispatch_to_channel(channel: str, agent_id: str,
                         title: str, body: str, msg_type: str) -> None:
    """Best-effort dispatch to a single channel. Raises on hard failures.

    Supported channels:
      - "macos": macOS notification banner via osascript display notification
      - "imessage": send via codec_imessage.send_message helper if available
      - "telegram": send via codec_telegram.send_message helper if available

    Phase 3.5 multi-channel notifications. Each channel is OPTIONAL —
    if the underlying tooling isn't configured, dispatch is a no-op.
    """
    short_body = (body or "")[:200]
    short_title = (title or f"CODEC agent {agent_id}")[:80]

    if channel == "macos":
        # macOS notification banner via osascript. No external dependencies.
        import subprocess
        # Sanitize for AppleScript single-quoting
        def _esc(s: str) -> str:
            return s.replace("\\", "\\\\").replace('"', '\\"')
        script = (
            f'display notification "{_esc(short_body)}" '
            f'with title "{_esc(short_title)}" '
            f'subtitle "agent: {_esc(agent_id)}"'
        )
        subprocess.run(["osascript", "-e", script], timeout=5,
                       capture_output=True, check=False)
        return

    if channel == "imessage":
        # Reuse the imessage_send skill's _send helper. Recipient is read
        # from ~/.codec/config.json:notifications.imessage_recipient
        # (phone number or Apple ID). If unset, skip silently.
        recipient = _channel_config("imessage_recipient")
        if not recipient:
            log.debug("notifications.imessage_recipient not configured; skipping imessage")
            return
        try:
            import sys as _sys
            from pathlib import Path as _Path
            skills_dir = str(_Path(__file__).resolve().parent / "skills")
            if skills_dir not in _sys.path:
                _sys.path.insert(0, skills_dir)
            import imessage_send as _ims
            _ims._send(recipient, f"[{short_title}]\n{short_body}")
        except Exception as e:
            log.debug("imessage send failed: %s", e)
        return

    if channel == "telegram":
        # Send via Telegram Bot API directly (avoids tight coupling to
        # codec_telegram.py's daemon internals). Reads token + chat_id
        # from ~/.codec/config.json:notifications.{telegram_token,telegram_chat_id}.
        token = _channel_config("telegram_token")
        chat_id = _channel_config("telegram_chat_id")
        if not token or not chat_id:
            log.debug("notifications.telegram_{token,chat_id} not configured; skipping telegram")
            return
        try:
            import requests
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id,
                      "text": f"*{short_title}*\n{short_body}",
                      "parse_mode": "Markdown"},
                timeout=5,
            )
        except Exception as e:
            log.debug("telegram send failed: %s", e)
        return

    log.debug("unknown notification channel: %s", channel)


def _channel_config(key: str) -> str:
    """Read ~/.codec/config.json:notifications.<key>. Empty string if unset."""
    cfg_path = _CODEC_DIR / "config.json"
    if not cfg_path.exists():
        return ""
    try:
        data = json.loads(cfg_path.read_text())
        return str((data.get("notifications") or {}).get(key) or "")
    except Exception:
        return ""


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
