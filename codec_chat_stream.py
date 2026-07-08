"""CODEC chat-stream token machine (A-6, PR-3D-c).

`SkillTagBuffer` is the stateful token processor extracted verbatim from
`codec_dashboard.chat_completion._stream_gen`. It does two things as content
tokens arrive from the streaming LLM:

1. Strips `<think>…</think>` reasoning across chunk boundaries.
2. Buffers `[SKILL:name:query]` tags char-by-char so a raw tag never leaks to
   the UI; when a complete, valid tag is seen it is resolved via an injected
   `resolve_skill_tag(raw_tag) -> str` callback (which runs the skill — that's
   I/O, hence injected) and the result is emitted instead.

`feed(token)` and `finish()` are generators that yield clean text fragments to
emit; the caller wraps each in an SSE `data: {...}` frame. `visible_chars` lets
the caller detect the "LLM emitted only tags and we dropped them all → blank
bubble" case.

Faithfulness notes (behavior preserved exactly from the inline version):
- `<think>` zeroes the rest of its token, so a `</think>` in the SAME chunk is
  not detected (the original assumed think open/close land in different chunks).
- Text adjacent to `<think>`/`</think>` is emitted but NOT counted toward
  `visible_chars` (the inline code emitted it via a direct frame, bypassing the
  visible counter).
- A resolved-to-empty (dropped) tag still yields `""` so the caller emits the
  same empty frame the inline version did.

The SSE/HTTP plumbing (POST, `iter_lines`, `data:`/`[DONE]` framing, keepalive,
JSON chunk parse, the blank-bubble fallback text) stays in `codec_dashboard`.
"""
from __future__ import annotations

import re
from typing import Callable, Iterator

# Shared by SkillTagBuffer (tag detection) and the dashboard's resolver.
SKILL_TAG_RE = re.compile(r'\[SKILL:(\w+):([^\]]+)\]')

_SKILL_PREFIX = "[SKILL:"
_MAX_BUF = 5000  # safety cap; newlines allowed inside a tag (multi-line scripts)


class SkillTagBuffer:
    """Stateful processor of streamed content tokens. See module docstring."""

    def __init__(self, resolve_skill_tag: Callable[[str], str]):
        self._resolve = resolve_skill_tag
        self.in_think = False
        self.skill_buf = ""
        self.buffering = False
        self.visible_chars = 0
        # Count of complete [SKILL:...] tags handed to the resolver — lets the
        # caller's blank-bubble fallback distinguish "all output was dropped
        # tool tags" from "the model produced nothing at all" (2026-07 fix).
        self.tags_resolved = 0
        # Train-of-thought side channel: <think> fragments captured for the
        # caller to (optionally) surface. Draining is opt-in — the clean-text
        # yields from feed() are identical whether or not anyone reads this.
        self._think_out: list = []

    def drain_think(self) -> list:
        """Return + clear <think> fragments captured since the last drain."""
        out = self._think_out
        self._think_out = []
        return out

    def _count(self, text: str) -> str:
        """Account visible chars (only non-empty), return the text unchanged.
        Mirrors the inline `_flush_emit` counting behaviour."""
        if text:
            self.visible_chars += len(text)
        return text

    def feed(self, token: str) -> Iterator[str]:
        """Process one content token; yield clean text fragments to emit."""
        # ── <think>…</think> handling (cross-chunk) ──
        if "<think>" in token:
            self.in_think = True
            parts = token.split("<think>", 1)
            before = parts[0]
            if before:
                yield before          # emitted but NOT counted (faithful)
            # Capture reasoning that shares this chunk (after the open tag).
            if len(parts) > 1 and parts[1]:
                self._think_out.append(parts[1])
            token = ""
        if self.in_think:
            if "</think>" in token:
                self.in_think = False
                head, after = token.split("</think>", 1)
                if head:
                    self._think_out.append(head)   # reasoning before the close
                if after:
                    yield after       # emitted but NOT counted (faithful)
            elif token:
                self._think_out.append(token)      # a pure reasoning chunk
            return                    # skip thinking content from clean text
        if not token:
            return

        # ── [SKILL:...] buffering, char by char ──
        i = 0
        while i < len(token):
            if self.buffering:
                self.skill_buf += token[i]
                i += 1
                # Validate prefix: while short it must stay a prefix of "[SKILL:";
                # once long it must start with "[SKILL:". On divergence → emit raw.
                if len(self.skill_buf) <= len(_SKILL_PREFIX):
                    if not _SKILL_PREFIX.startswith(self.skill_buf):
                        yield self._count(self.skill_buf)
                        self.skill_buf = ""
                        self.buffering = False
                        continue
                elif not self.skill_buf.startswith(_SKILL_PREFIX):
                    yield self._count(self.skill_buf)
                    self.skill_buf = ""
                    self.buffering = False
                    continue
                # Tag complete?
                if self.skill_buf.endswith("]"):
                    if SKILL_TAG_RE.search(self.skill_buf):
                        self.tags_resolved += 1
                        yield self._count(self._resolve(self.skill_buf))
                    else:
                        yield self._count(self.skill_buf)
                    self.skill_buf = ""
                    self.buffering = False
                # Buffer too long → safety cap (emit raw, stop buffering).
                elif len(self.skill_buf) > _MAX_BUF:
                    yield self._count(self.skill_buf)
                    self.skill_buf = ""
                    self.buffering = False
            else:
                idx = token.find("[", i)
                if idx == -1:
                    rest = token[i:]
                    if rest:
                        yield self._count(rest)
                    break
                else:
                    before = token[i:idx]
                    if before:
                        yield self._count(before)
                    self.skill_buf = "["
                    self.buffering = True
                    i = idx + 1

    def finish(self) -> Iterator[str]:
        """Flush any pending buffer at end-of-stream (resolve if it's a tag).
        Idempotent — a no-op once the buffer has been flushed."""
        if self.skill_buf:
            if SKILL_TAG_RE.search(self.skill_buf):
                self.tags_resolved += 1
            yield self._count(self._resolve(self.skill_buf))
            self.skill_buf = ""
            self.buffering = False
