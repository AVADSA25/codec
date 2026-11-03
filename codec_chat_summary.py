"""Notes on the earlier part of a long Deep Chat (P-038, docs/CHAT-SUMMARY-DESIGN.md).

P-034 keeps one chat turn's prompt under config.json:chat_prompt_token_budget by
leaving out the oldest turns. This module gives those turns back as notes: after
a reply, a background job asks the local model for notes on the oldest turns,
and later turns send the notes right after the system message, then as many of
the newest turns as fit (P-034's fit, unchanged). Chat only: voice keeps its own
20-turn trim, and no notes call starts while a voice session is open.

Nothing is written to disk. Notes live in this process's memory, keyed by a hash
of the exact messages they cover, so an edited earlier message is never answered
from stale notes, and a stored chat is never changed. Log lines carry counts only.
"""
from __future__ import annotations

import collections
import hashlib
import json
import logging
import math
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger("codec_dashboard")

VOICE_SESSION_PATH = os.path.expanduser("~/.codec/voice_session.json")
NOTES_START = "(NOTES ON THE EARLIER PART OF THIS CHAT"
# The same markers as routes.chat (a test pins them equal): this turn's context
# message, and the blocks codec_chat.html writes for an attached file or image.
INTERNAL_CONTEXT_START = "(INTERNAL CONTEXT"
_ATTACHMENT_RE = re.compile(
    r"\[DOCUMENT: ([^\n]*?)\]\n(.*?)\n\[END DOCUMENT\]"
    r"|\[IMAGE ANALYSIS of ([^\n]*?)\]\n(.*?)\n\[END IMAGE\]", re.DOTALL)
CHARS_PER_TOKEN = 3.5            # P-034's estimate
SOON_SHARE = 0.85                # plan a job when the fit at 85% of the budget leaves out un-noted turns
TARGET_SHARE = 0.6               # a job covers what the fit at 60% of the budget leaves out
CHUNK_TOKENS = 3000              # chat text in one notes call, whole turns
NOTES_CAP_DIVISOR = 4            # notes longer than a quarter of the budget are merged
MERGE_SHARE = 0.6                # a merge aims at 60% of that cap
MESSAGE_CHARS = 6000             # one message is cut to this in a notes call
CACHE_SIZE = 64
CALL_TIMEOUT = 240.0
_VOICE_CHECK_S = 0.25             # how often a streaming notes call looks for the voice marker
_OFF_WORDS = {"false", "off", "no", "0"}

_SEGMENT_RULES = (
    "You write notes on a long chat between a user and CODEC, their assistant. Later, when the chat is "
    "too long to send in full, these notes replace the messages, so anything you leave out is lost.\n"
    "Write one line per fact, each starting with \"- \". Keep every specific detail exactly as written: "
    "names of people, places, businesses, pets and things; numbers, amounts, prices, quantities, dates, "
    "times, codes and references; decisions, choices, plans, preferences, requests and open questions. "
    "Say who said or decided it (the user or CODEC) when it matters. When a message changes something "
    "said earlier, write the new value and say that it replaces the old one. Keep CODEC's specific answers, "
    "such as a number it suggested, but leave out its general advice and tips. For a draft, a post or a "
    "list CODEC wrote, note only what it was for, not its text. Leave out greetings and repeats. Do not add "
    "anything that is not in the messages. At most {words} words. Write only the notes.")
_MERGE_RULES = (
    "These are notes on a long chat between a user and CODEC, written part by part. Rewrite them in at "
    "most {words} words. Keep every specific detail exactly as written: names, numbers, amounts, dates, "
    "times, codes, decisions, preferences and open questions. Drop general advice and repeats. Where a later "
    "line changes an earlier one, keep only the later value. One fact per line, each starting with \"- \". "
    "Write only the notes.")
_NOTES_INTRO = (
    NOTES_START + ". The chat is too long to send in full, so CODEC wrote these notes on its first {n} "
    "messages, in the order of the chat; where a later note changes an earlier one, the later note is right. "
    "The messages after the notes are given word for word; where they differ from the notes, "
    "the messages are right. When asked about the earlier part of the chat, answer from these notes; if "
    "a detail is not in them, say that part of the chat is no longer available to you rather than guess.)")


@dataclass(frozen=True)
class Notes:
    covered: int     # notes on history[:covered]
    text: str


_LOCK = threading.Lock()
_CACHE: "collections.OrderedDict[str, Notes]" = collections.OrderedDict()
_RUNNING: Dict[str, Any] = {"job": None}


# ── Settings and conditions ──────────────────────────────────────────────────

def enabled(config: Optional[dict]) -> bool:
    """config.json:chat_summary, default on; false, 0, "off", "no" turn it off."""
    raw = (config or {}).get("chat_summary", True)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return raw != 0
    if isinstance(raw, str):
        return raw.strip().lower() not in _OFF_WORDS
    return True


def voice_session_open() -> bool:
    """A voice call is on (codec_voice touches the marker at start, removes it at the end)."""
    return os.path.exists(VOICE_SESSION_PATH)


def _is_local(base_url: str) -> bool:
    try:
        import codec_cloud_models
        return codec_cloud_models.is_local_url(base_url)
    except Exception:
        return False


def _tokens(text: str) -> int:
    return math.ceil(len(text or "") / CHARS_PER_TOKEN) + 4


# ── The parts of one turn's messages ─────────────────────────────────────────

def _bounds(messages: list) -> Tuple[int, int]:
    """(start, end) of the chat history in `messages`, as routes.chat._fit_prompt_budget reads it:
    after the system message, before this turn's (INTERNAL CONTEXT ...) message and the latest
    user message. (0, 0) when there is no user message."""
    last = next((i for i in range(len(messages) - 1, -1, -1) if messages[i].get("role") == "user"), None)
    if last is None:
        return 0, 0
    start = 1 if last > 0 and messages[0].get("role") == "system" else 0
    end = last
    before = messages[last - 1] if last - 1 >= start else None
    if (before and before.get("role") == "user" and isinstance(before.get("content"), str)
            and before["content"].startswith(INTERNAL_CONTEXT_START)):
        end = last - 1
    return start, end


def _key_part(m: dict) -> bytes:
    content = m.get("content")
    if not isinstance(content, str):
        content = json.dumps(content, sort_keys=True, default=str)
    return f"{m.get('role', '')}\x00{content}\x01".encode("utf-8", "replace")


def _prefix_keys(history: list) -> List[str]:
    """keys[i] identifies history[:i] exactly (role and text of each message, in order)."""
    h = hashlib.sha256(b"codec-chat-notes-1")
    keys = [h.copy().hexdigest()]
    for m in history:
        h.update(_key_part(m))
        keys.append(h.copy().hexdigest())
    return keys


def _lookup(keys: List[str]) -> Optional[Notes]:
    """The notes covering the longest start of this history, if any."""
    with _LOCK:
        for i in range(len(keys) - 1, 0, -1):
            notes = _CACHE.get(keys[i])
            if notes is not None and notes.covered == i:
                _CACHE.move_to_end(keys[i])
                return notes
    return None


def _store(key: str, notes: Notes) -> None:
    with _LOCK:
        _CACHE[key] = notes
        _CACHE.move_to_end(key)
        while len(_CACHE) > CACHE_SIZE:
            _CACHE.popitem(last=False)


def clear() -> None:
    """Forget every note (tests; a dashboard restart does the same)."""
    with _LOCK:
        _CACHE.clear()


def notes_message(notes: Notes) -> dict:
    return {"role": "user", "content": _NOTES_INTRO.format(n=notes.covered) + "\n\n" + notes.text}


def is_notes_message(m: Any) -> bool:
    return (isinstance(m, dict) and m.get("role") == "user" and isinstance(m.get("content"), str)
            and m["content"].startswith(NOTES_START))


def with_notes(messages: list, notes: Notes) -> list:
    """`messages` with the notes message right after the system message (a new list)."""
    start, _ = _bounds(messages)
    return messages[:start] + [notes_message(notes)] + messages[start:]


# ── One turn ─────────────────────────────────────────────────────────────────

def fit_with_notes(messages: list, budget: int, config: Optional[dict], *, fit: Callable,
                   llm: Optional[dict] = None, persona: bool = False):
    """One chat turn's prompt (P-038 on top of P-034). Returns (messages to send, trim dict or
    None, NotesJob or None).

    `fit` is routes.chat._fit_prompt_budget. When it leaves messages out and notes exist for the
    start of this exact history, the notes go right after the system message and the fit runs
    again; the trim dict then says how many left-out messages the notes cover (`summarised`)
    and how many have none (`dropped`). A job is returned when the chat is near the budget and
    the notes do not reach far enough; the caller starts it after the reply. `llm` holds
    base_url, model and api_key; no job unless the URL is on this Mac and no persona is on."""
    sent, trim = fit(messages, budget)
    if budget <= 0 or not enabled(config):
        return sent, trim, None
    start, end = _bounds(messages)
    history = messages[start:end]
    if len(history) < 2:
        return sent, trim, None
    keys = _prefix_keys(history)
    notes = _lookup(keys)
    noted = with_notes(messages, notes) if notes is not None else messages
    if notes is not None and trim and trim.get("dropped"):
        sent, trim = fit(noted, budget)
        if trim is not None:
            dropped = int(trim.get("dropped") or 0)
            trim = {**trim, "summarised": min(dropped, notes.covered),
                    "dropped": max(0, dropped - notes.covered)}
    job = None
    if llm and not persona and _is_local(str(llm.get("base_url") or "")):
        job = _plan(noted, history, keys, notes, budget, fit, llm)
    return sent, trim, job


def _plan(noted: list, history: list, keys: List[str], notes: Optional[Notes], budget: int,
          fit: Callable, llm: dict) -> Optional["NotesJob"]:
    covered = notes.covered if notes is not None else 0
    _, soon = fit(noted, int(budget * SOON_SHARE))
    soon_dropped = int(soon.get("dropped") or 0) if soon else 0
    if soon_dropped <= covered:
        return None
    _, far = fit(noted, int(budget * TARGET_SHARE))
    upto = min(len(history), max(soon_dropped, int(far.get("dropped") or 0) if far else 0))
    while upto < len(history) and history[upto].get("role") != "user":
        upto += 1           # whole turns only
    if upto <= covered:
        return None
    snapshot = [{"role": m.get("role"), "content": m.get("content")} for m in history[:upto]]
    return NotesJob(snapshot, keys[:upto + 1], covered, notes.text if notes is not None else "",
                    budget, dict(llm))


# ── Writing notes ────────────────────────────────────────────────────────────

def _plain(m: dict) -> str:
    content = m.get("content")
    if isinstance(content, list):
        content = " ".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    text = content if isinstance(content, str) else ""

    def _marker(mt):
        name = (mt.group(1) if mt.group(1) is not None else mt.group(3)) or "attachment"
        return f"[file attached: {name.strip() or 'attachment'}]"

    text = _ATTACHMENT_RE.sub(_marker, text)
    if len(text) > MESSAGE_CHARS:
        text = text[:MESSAGE_CHARS] + " [...]"
    who = "User" if m.get("role") == "user" else "CODEC"
    return f"{who}: {text.strip()}"


def _complete(messages: list, llm: dict, max_tokens: int) -> str:
    """One notes call on the local model. "" when it failed or a voice session opened."""
    import codec_llm
    if voice_session_open():
        return ""           # the voice rule: no notes call starts while a voice session is open
    out: List[str] = []
    gen = codec_llm.stream(messages, base_url=llm["base_url"], model=llm["model"],
                           api_key=llm.get("api_key") or "", max_tokens=max_tokens, temperature=0.2,
                           timeout=CALL_TIMEOUT, enable_thinking=False, error_sentinel=True)
    checked, cut = 0.0, False
    try:
        for item in gen:
            if item is codec_llm.STREAM_ERROR:
                return ""
            if item is codec_llm.FINISH_LENGTH:
                cut = True
                break
            if isinstance(item, str):
                out.append(item)
            now = time.monotonic()
            if now - checked >= _VOICE_CHECK_S:
                checked = now
                if voice_session_open():
                    log.info("[chat notes] a voice session opened: notes call dropped")
                    return ""
    finally:
        if hasattr(gen, "close"):
            gen.close()     # closes the connection when the loop stopped early
    text = codec_llm.strip_think("".join(out)).strip()
    if cut and "\n" in text:
        text = text[:text.rfind("\n")].strip()   # the last line was cut off by the token cap
    return text


class NotesJob:
    """Notes on history[start:end] of one chat, written after the reply. `run()` does the work
    in the caller's thread (tests, the eval); `start()` runs it in one background thread."""

    def __init__(self, history: list, keys: List[str], start: int, notes: str, budget: int, llm: dict):
        self.history, self.keys, self.start_at, self.end = history, keys, start, len(history)
        self.notes, self.budget, self.llm = notes, budget, llm
        self.stopped: Optional[str] = None

    def chunks(self) -> List[Tuple[int, int]]:
        """Whole turns from start to end, about CHUNK_TOKENS of text each (one turn at least)."""
        out, a, size, i = [], self.start_at, 0, self.start_at
        while i < self.end:
            j = i + 1
            while j < self.end and self.history[j].get("role") != "user":
                j += 1
            cost = sum(_tokens(_plain(m)) for m in self.history[i:j])
            if size and size + cost > CHUNK_TOKENS:
                out.append((a, i))
                a, size = i, 0
            size += cost
            i = j
        if a < self.end:
            out.append((a, self.end))
        return out

    def start(self) -> bool:
        if voice_session_open():
            log.info("[chat notes] not started: a voice session is open")
            return False
        with _LOCK:
            if _RUNNING["job"] is not None:
                return False
            _RUNNING["job"] = self
        threading.Thread(target=self._thread, name="codec-chat-notes", daemon=True).start()
        return True

    def _thread(self) -> None:
        try:
            self.run()
        except Exception as e:
            log.warning("[chat notes] job failed: %s", type(e).__name__)
        finally:
            with _LOCK:
                if _RUNNING["job"] is self:
                    _RUNNING["job"] = None

    def run(self) -> Optional[Notes]:
        text, done = self.notes, None
        cap = max(200, self.budget // NOTES_CAP_DIVISOR)
        for a, b in self.chunks():
            if voice_session_open():
                self.stopped = "voice"
                break
            part = "\n".join(_plain(m) for m in self.history[a:b])
            words = max(60, min(300, _tokens(part) // 10))
            ask = [{"role": "system", "content": _SEGMENT_RULES.format(words=words)},
                   {"role": "user", "content": (
                       (f"Notes so far, for context only (do not repeat them):\n{text}\n\n" if text else "")
                       + "The part of the chat to write notes on:\n\n" + part)}]
            segment = _complete(ask, self.llm, int(words * 2) + 64)
            if not segment:
                self.stopped = "voice" if voice_session_open() else "no reply"
                break
            text = (text + "\n" + segment).strip() if text else segment
            if _tokens(text) > cap:
                target = int(cap * MERGE_SHARE * CHARS_PER_TOKEN / 6)
                merged = _complete([{"role": "system", "content": _MERGE_RULES.format(words=target)},
                                    {"role": "user", "content": text}], self.llm, int(target * 2) + 64)
                if merged and len(merged) < len(text):
                    text = merged
            done = Notes(b, text)
            _store(self.keys[b], done)
            log.info("[chat notes] notes on messages 1-%d: %d words", b, len(text.split()))
        if self.stopped:
            log.info("[chat notes] stopped (%s)", self.stopped)
        return done
