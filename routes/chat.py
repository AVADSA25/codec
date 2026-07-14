"""CODEC dashboard chat endpoint — /api/chat (the main LLM chat handler).

H1 / SR-59: extracted from codec_dashboard.py (the largest remaining
endpoint, ~250 LOC) together with its private helper cluster.

This module owns the full chat pipeline:
  - `_fetch_url_content`     — strip-and-fetch a URL for inline context
  - `_enrich_messages`       — auto-inject memory / URL / web-search context
  - `CHAT_SKILL_ALLOWLIST`   — the set of skills a chat message may auto-fire
  - `_try_skill`             — pre-LLM skill hijack (shared with /api/command)
  - `_try_skill_by_name`     — post-LLM [SKILL:...] tag resolver (+ calc fallback)
  - `_chat_vision_response`  — image → vision-model branch (A-11 pending)
  - `_build_chat_system_prompt` — override + step-budget + observer suffixes
  - `chat_completion`        — the POST /api/chat endpoint itself

Safety-critical surfaces preserved verbatim from the in-dashboard original:
  * pre-LLM skill hijack consumes one step budget; destructive skills gate on
    `codec_consent.chat_consent_ok` before running
  * post-LLM [SKILL:...] tags are allowlist-gated, budget-gated, and DROPPED
    (never leaked raw) on any failure — both stream + non-stream paths
  * the streaming `<think>` / [SKILL:...] token machine is `codec_chat_stream.
    SkillTagBuffer`; this module only wires raw `codec_llm.stream` deltas through it
  * per-turn `_StepBudget` is constructed once and threaded through every path

Cross-module deps that would cycle with codec_dashboard (CHAT_SYSTEM_PROMPT)
are lazy-imported at call time. codec_dashboard re-exports the helper names
back for the /api/command caller and the existing test surface.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from codec_audit import log_event
import codec_llm  # A-12 canonical LLM caller
from codec_chat_stream import SkillTagBuffer, SKILL_TAG_RE  # A-6 token machine
from codec_chat_pipeline import _StepBudget, _is_conversational  # B6-P2
from codec_chat_pipeline import (  # Step 10 Q11 wiring (2026-07)
    _should_escalate_to_project,
    silence_session_autoescalate,
)
from routes._shared import CONFIG_PATH

router = APIRouter()
log = logging.getLogger("codec_dashboard")



def _url_host_is_public(url: str) -> bool:
    """SSRF guard: True only if `url` is http(s) AND every IP its host resolves
    to is a public, routable address. Rejects loopback / private / link-local
    (incl. 169.254.169.254 cloud-metadata) / reserved / multicast / unspecified.

    J1 (re-audit, CWE-918): `_enrich_messages` auto-fetches URLs found in chat
    content — the prompt-injection vector. Without this an injected link could
    drive server-side GETs against `http://127.0.0.1:8083/...` or other local
    `~/.codec` services. CODEC is loopback-only by default, but this keeps the
    `dashboard_host: 0.0.0.0` opt-in safe. (Residual: DNS-rebinding TOCTOU
    between this check and httpx's own resolve is accepted for a local app.)
    """
    import ipaddress
    import socket
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = parsed.hostname
        if not host:
            return False
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
        if not infos:
            return False
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
                return False
        return True
    except Exception as e:
        log.warning(f"URL host validation failed ({url}): {e}")
        return False


def _fetch_url_content(url: str, max_chars: int = 8000) -> str:
    """Fetch a URL and return stripped text content.

    SSRF-hardened (J1): the host is validated as public BEFORE the fetch, and
    redirects are followed manually (≤5 hops) so each Location is re-validated
    — `follow_redirects=True` would let a public URL 30x-redirect to an
    internal one, defeating the pre-check.
    """
    try:
        import httpx
        from html.parser import HTMLParser

        class _Stripper(HTMLParser):
            def __init__(self):
                super().__init__()
                self._skip = False
                self.chunks = []
            def handle_starttag(self, tag, attrs):
                if tag in ('script', 'style', 'nav', 'footer'):
                    self._skip = True
            def handle_endtag(self, tag):
                if tag in ('script', 'style', 'nav', 'footer'):
                    self._skip = False
            def handle_data(self, data):
                if not self._skip:
                    stripped = data.strip()
                    if stripped:
                        self.chunks.append(stripped)

        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                   "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
        cur = url
        r = None
        with httpx.Client(timeout=15, follow_redirects=False) as client:
            for _hop in range(5):
                if not _url_host_is_public(cur):
                    log.warning(f"URL fetch blocked (non-public host): {cur}")
                    return ""
                r = client.get(cur, headers=headers)
                if r.is_redirect and "location" in r.headers:
                    cur = str(r.url.join(r.headers["location"]))
                    continue
                break
            else:
                log.warning(f"URL fetch aborted (too many redirects): {url}")
                return ""
        if r is None:
            return ""
        if 'text/html' in r.headers.get('content-type', ''):
            parser = _Stripper()
            parser.feed(r.text)
            text = ' '.join(parser.chunks)
        else:
            text = r.text
        return text[:max_chars]
    except Exception as e:
        log.warning(f"URL fetch failed ({url}): {e}")
        return ""




# Memory-injection hygiene (2026-07-10 trailer incident). Two pollution classes
# were being injected as "memory" and derailing replies:
#   1. SELF-ECHO — the FTS/LIKE lookups matched the user's OWN current message
#      (and its re-sends), so the model saw its question repeated 3-4x wrapped
#      in [MEMORY] tags, a hall-of-mirrors that reads as "this matters a lot".
#   2. AGENT-STATUS NOISE — Project/crew status lines ("running Agent started…",
#      "Plan approved…") saved to chat history got replayed as conversational
#      memory, contexts that have nothing to do with the user's question.
_AGENT_NOISE_PREFIXES = (
    "running ", "done ", "granted ", "plan approved", "project drafted",
    "here's my plan", "here’s my plan", "[codec_agent_plan", "paused:",
    "task stopped", "agent error", "blocked:",
)


def _mem_noise(content: str, last_text: str) -> bool:
    """True when a candidate memory row should NOT be injected: empty,
    a near-duplicate of the current message (self-echo), or agent-status
    chrome rather than real conversation."""
    c = (content or "").strip()
    if not c:
        return True
    if c.lower()[:60] == (last_text or "").strip().lower()[:60]:
        return True
    return c.lower().startswith(_AGENT_NOISE_PREFIXES)


def _degenerate_tail(text: str) -> bool:
    """True when the tail of `text` is stuck repeating itself — the signature
    of a 4-bit sampling collapse (the same phrase/list item emitted over and
    over, e.g. an endless list of movie titles). Cheap deterministic check:
    probe = the last 64 chars; degenerate when that exact probe already occurs
    5+ times within the trailing window. Used by the chat SSE stream to cut a
    runaway generation with an honest message instead of letting it grind out
    28k tokens of garbage in front of the user (2026-07-10 trailer incident)."""
    if len(text) < 900:
        return False
    probe = text[-64:]
    if len(probe.strip()) < 12:
        return False
    return text[-2400:].count(probe) >= 5


def _enrich_messages(messages: list, config: dict, force_search: bool = False) -> list:
    """
    Auto-detect URLs, search intent, and memory recall in the last user message.
    Injects context messages before the last user message when content is found.
    force_search=True bypasses intent detection and always searches.
    Returns a (possibly modified) copy of the messages list.
    """
    import re as _re
    if not messages:
        return messages

    # Find last user message
    last_user_idx = None
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            last_user_idx = i
            break
    if last_user_idx is None:
        return messages

    last_text = messages[last_user_idx].get("content", "")
    if not isinstance(last_text, str):
        return messages

    context_parts = []
    memory_parts = []

    # ── Memory recall ──────────────────────────────────────────────────────────
    # Inject relevant memory context from ALL sources (voice, chat, vibe) for
    # full cross-session recall.
    lower = last_text.lower()
    memory_triggers = [
        'remember', 'recall', 'earlier', 'before', 'last time',
        'previously', 'we talked', 'we discussed', 'you said',
        'did i', 'did we', 'have i', 'have we', 'my previous',
        'past conversation', 'history', 'do you know my',
        'what was', 'what did', 'when did',
    ]
    # Word-boundary match, and only scan the FIRST 300 chars — the user's own
    # intent lives at the front of the message, not inside pasted content.
    # (2026-07-10 trailer incident: a pasted movie-trailer transcript contained
    # "I remembered something" + "human history", substring-fired 'remember' +
    # 'history', and the resulting memory dump of old film chats derailed the
    # model into rambling about other movies instead of the pasted script.)
    _trigger_zone = lower[:300]
    has_memory_trigger = any(
        re.search(r"\b" + re.escape(t) + r"\b", _trigger_zone)
        for t in memory_triggers
    )

    # 1. Voice memory (FTS5 via CodecMemory) — always inject recent, targeted on trigger
    try:
        from codec_memory import CodecMemory
        mem = CodecMemory()
        if has_memory_trigger:
            mem_context = mem.get_context(last_text, n=8)
            if mem_context:
                memory_parts.append(f"[MEMORY — RELEVANT PAST CONVERSATIONS (VOICE)]\n{mem_context}\n[END MEMORY]")
                log.info(f"Memory recall injected (voice targeted): {len(mem_context)} chars")
        recent = mem.search_recent(days=3, limit=5)
        if recent:
            lines = ["[RECENT MEMORY — VOICE (LAST 3 DAYS)]"]
            for r in recent:
                if _mem_noise(r["content"], last_text):
                    continue
                ts = r["timestamp"][:16].replace("T", " ")
                snippet = r["content"][:200].replace("\n", " ")
                lines.append(f"  [{ts}] {r['role'].upper()}: {snippet}")
            if len(lines) > 1:
                lines.append("[END RECENT MEMORY]")
                memory_parts.append("\n".join(lines))
                log.info(f"Recent memory injected: {len(lines) - 2} messages")
    except Exception as e:
        log.warning(f"Memory enrichment (voice) failed: {e}")

    # 2. Dashboard chat history (qchat.db) — targeted search on trigger, recent always
    try:
        from routes.qchat import qchat_db as _qchat_db; _qc = _qchat_db()
        if has_memory_trigger:
            keyword = f"%{last_text[:80]}%"
            qrows = _qc.execute(
                "SELECT role, content, timestamp FROM qchat_messages "
                "WHERE content LIKE ? COLLATE NOCASE ORDER BY id DESC LIMIT 6",
                (keyword,)
            ).fetchall()
            if qrows:
                lines = ["[MEMORY — RELEVANT PAST CHATS]"]
                for r in qrows:
                    if _mem_noise(r[1], last_text):
                        continue
                    ts = (r[2] or "")[:16].replace("T", " ")
                    snippet = (r[1] or "")[:200].replace("\n", " ")
                    lines.append(f"  [{ts}] {(r[0] or '').upper()}: {snippet}")
                if len(lines) > 1:
                    lines.append("[END MEMORY]")
                    memory_parts.append("\n".join(lines))
                    log.info(f"Memory recall injected (chat targeted): {len(lines) - 2} msgs")
        # Recent chat messages for continuity
        qrecent = _qc.execute(
            "SELECT role, content, timestamp FROM qchat_messages ORDER BY id DESC LIMIT 5"
        ).fetchall()
        if qrecent:
            lines = ["[RECENT MEMORY — CHAT]"]
            for r in qrecent:
                if _mem_noise(r[1], last_text):
                    continue
                ts = (r[2] or "")[:16].replace("T", " ")
                snippet = (r[1] or "")[:200].replace("\n", " ")
                lines.append(f"  [{ts}] {(r[0] or '').upper()}: {snippet}")
            if len(lines) > 1:
                lines.append("[END RECENT MEMORY]")
                memory_parts.append("\n".join(lines))
                log.info(f"Recent chat memory injected: {len(lines) - 2} messages")
    except Exception as e:
        log.warning(f"Memory enrichment (chat) failed: {e}")

    # 3. Vibe IDE history (vibe.db) — targeted search on trigger only (less relevant day-to-day)
    if has_memory_trigger:
        try:
            from routes.vibe import vibe_db as _vibe_db; _vc = _vibe_db()
            keyword = f"%{last_text[:80]}%"
            vrows = _vc.execute(
                "SELECT role, content, timestamp FROM vibe_messages "
                "WHERE content LIKE ? COLLATE NOCASE ORDER BY id DESC LIMIT 4",
                (keyword,)
            ).fetchall()
            if vrows:
                lines = ["[MEMORY — RELEVANT VIBE/CODE CONVERSATIONS]"]
                for r in vrows:
                    ts = (r[2] or "")[:16].replace("T", " ")
                    snippet = (r[1] or "")[:200].replace("\n", " ")
                    lines.append(f"  [{ts}] {(r[0] or '').upper()}: {snippet}")
                lines.append("[END MEMORY]")
                memory_parts.append("\n".join(lines))
                log.info(f"Memory recall injected (vibe targeted): {len(vrows)} msgs")
        except Exception as e:
            log.warning(f"Memory enrichment (vibe) failed: {e}")

    # ── URL detection ──────────────────────────────────────────────────────────
    urls = _re.findall(r'https?://[^\s\)\]>,"\']+', last_text)
    for url in urls[:3]:  # cap at 3 URLs per message
        content = _fetch_url_content(url)
        if content:
            context_parts.append(f"[URL CONTENT: {url}]\n{content}\n[END URL CONTENT]")
            log.info(f"Chat URL fetched: {url} ({len(content)} chars)")

    # ── Search intent detection ────────────────────────────────────────────────
    search_triggers = [
        'search for', 'search the web', 'google', 'look up', 'find out',
        'what is the latest', 'current news', 'recent', 'today\'s', 'right now',
        'who won', 'stock price', 'weather in', 'news about'
    ]
    lower = last_text.lower()
    should_search = (any(t in lower for t in search_triggers) or force_search) and not urls
    if should_search:
        try:
            import sys
            import os as _os
            # J1 fix: this module lives in routes/ now, so the repo root (where
            # codec_search.py is) is TWO levels up, not one. The pre-extraction
            # original was at repo root → single dirname. Match web_search.py.
            repo_dir = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
            if repo_dir not in sys.path:
                sys.path.insert(0, repo_dir)
            from codec_search import search, format_results
            results = search(last_text, max_results=5)
            if results:
                context_parts.append(f"[WEB SEARCH RESULTS]\n{format_results(results, max_snippets=5)}\n[END WEB SEARCH RESULTS]")
                log.info(f"Chat search injected for: {last_text[:80]}")
        except Exception as e:
            log.warning(f"Chat search failed: {e}")

    if not context_parts and not memory_parts:
        return messages

    enriched = list(messages)

    # Inject memory + other context as a single user message (hidden context)
    # Using role "user" with clear [INTERNAL] framing so local LLMs don't choke on mid-conversation "system" role
    all_context = memory_parts + context_parts
    if all_context:
        prefix = ("(INTERNAL CONTEXT — do not echo this block. Use it to inform your answer naturally. "
                   "Never show raw [MEMORY] or [RECENT MEMORY] tags to the user.)\n\n")
        context_msg = {"role": "user", "content": prefix + "\n\n".join(all_context)}
        enriched.insert(last_user_idx, context_msg)

    return enriched




CHAT_SKILL_ALLOWLIST = {
    # Core utilities
    "calculator", "weather", "web_search", "bitcoin_price",
    "system", "network_info", "memory_search", "time",
    "timer", "translate", "file_search", "notes",
    "reminders", "clipboard", "password_generator",
    "qr_generator", "json_formatter", "pomodoro",
    # Terminal / shell (goes through is_dangerous safety check)
    "terminal",
    # File operations (read, write, append, list — path-restricted)
    "file_ops",
    # NOTE: python_exec is intentionally NOT on this allowlist (audit C3).
    # It stays a local skill but is no longer auto-firable from a chat message
    # (pre-LLM hijack / post-LLM [SKILL:...] tag both gate on this set), so an
    # injection-style chat message can't drive arbitrary code execution.
    # SKILL_MCP_EXPOSE=False already keeps it off MCP.
    # Google services
    "google_calendar", "google_gmail", "google_docs",
    "google_drive", "google_sheets", "google_keep",
    "google_tasks", "google_slides",
    # Browser control
    "chrome_automate", "chrome_click_cdp", "chrome_read",
    "chrome_extract", "chrome_fill", "chrome_scroll",
    "chrome_open", "chrome_close", "chrome_tabs", "chrome_search",
    # System control (volume, brightness, apps — NO mouse_control)
    "screenshot_text", "app_switch",
    "brightness", "volume_brightness", "process_manager",
    "ax_control",
    # PM2 service management
    "pm2_control",
    # Smart home & media
    "philips_hue", "music",
    # Self-improvement & meta
    "ai_news_digest", "scheduler",
    # Skill creation & delegation
    # re-audit (CHAIN-002): skill_forge writes forged code to disk WITHOUT the
    # review gate, so it must not be auto-firable from a chat [SKILL:...] tag —
    # skill creation goes through create_skill's review-and-approve flow only
    # (PR-1B). ask_codec_to_build had no backing skill file (stale entry).
    "create_skill", "delegate",
    # Phase 2 Step 7 — end-of-day shift report (read-only, no destructive side effects)
    "shift_report",
    # Observer recall — "what was I doing 20 min ago?" (read-only, reads the
    # observer buffer). Without this the phrase fell through to the LLM, which
    # fabricated an answer from chat memory instead of the real buffer.
    "observer_recall",
    # Phase 2 Step 6 — first declarative trigger (clipboard URL → web_fetch).
    # Read-only network fetch, gated by codec_ask_user.ask consent on auto-fire.
    "clipboard_url_fetch",
    # Daybreak — morning kickoff (read-only aggregation) + working-thread
    # capture (facts-table writes only). docs/DAYBREAK-DESIGN.md.
    "daily_kickoff", "thread_note",
}


def _try_skill(user_text: str):
    """Check if user_text matches a skill. Returns (skill_name, result) or (None, None).
    Skips skill matching for conversational messages to prevent false triggers."""
    if _is_conversational(user_text):
        return None, None
    try:
        from codec_dispatch import check_skill, run_skill
        skill = check_skill(user_text)
        if skill and skill.get("name") in CHAT_SKILL_ALLOWLIST:
            # re-audit A2: destructive skills need explicit consent (reuses the
            # AskUserQuestion PWA panel; blocks this worker thread until answered).
            import codec_consent
            if not codec_consent.chat_consent_ok(skill["name"], user_text):
                return skill["name"], (
                    f"⚠ '{skill['name']}' is a destructive operation and wasn't "
                    "confirmed — skipped."
                )
            result = run_skill(skill, user_text, app="CODEC Chat")
            if result is not None:
                return skill["name"], str(result)
    except Exception as e:
        log.warning(f"[Chat] Skill check error: {e}")
    return None, None




def _try_skill_by_name(name: str, query: str):
    """Execute a specific skill by name (for LLM-routed skill calls).

    For calculator specifically: LLMs often pass natural-language descriptions
    like "sum of Facebook (4900 + 6100), LinkedIn ...". The calculator skill
    can't parse that. We try the raw query first, then fall back to extracting
    every number out of the string and summing/computing locally so the user
    always gets a number instead of a raw [SKILL:...] tag leaking through.
    """
    try:
        from codec_dispatch import run_skill
        skill = {"name": name}
        # re-audit A2: a destructive skill emitted via a post-LLM [SKILL:...] tag
        # (the prompt-injection vector) needs explicit consent before it runs —
        # reuses the AskUserQuestion PWA panel. Blocks until answered.
        import codec_consent
        if not codec_consent.chat_consent_ok(name, query):
            return name, (
                f"⚠ '{name}' is a destructive operation and wasn't confirmed — skipped."
            )
        result = run_skill(skill, query, app="CODEC Chat (LLM-routed)")
        if result is not None:
            return name, str(result)
    except Exception as e:
        log.warning(f"[Chat] LLM skill route error ({name}): {e}")

    # Calculator-specific fallback: rescue messy LLM-routed inputs like
    #   "sum of Facebook (4900 + 6100), LinkedIn (4127 + 3900), ..."
    # The LLM almost always means "give me the total" → extract every number
    # and sum. If the query plainly contains a single arithmetic expression,
    # we eval that instead.
    if name == "calculator":
        try:
            import re as _re_calc
            q_lower = query.lower()
            # Detect a clean arithmetic expression like "47*89" with no other words
            stripped = _re_calc.sub(r"[^0-9+\-*/().\s]", "", query).strip()
            stripped = _re_calc.sub(r"\s+", "", stripped)
            looks_like_clean_expression = (
                stripped
                and _re_calc.fullmatch(r"[0-9+\-*/().]+", stripped)
                and _re_calc.search(r"[+\-*/]", stripped)
            )
            if looks_like_clean_expression:
                try:
                    val = eval(stripped, {"__builtins__": {}}, {})  # noqa: S307
                    return name, f"{val:,}"
                except Exception:
                    pass

            # Otherwise: pull every number out and decide an op based on intent
            nums = [float(n) for n in _re_calc.findall(r"\d+(?:\.\d+)?", query)]
            if len(nums) >= 2:
                # Default to sum (covers grand total / how many / count / etc).
                # If user said "product" / "multiply" / "times" → multiply.
                if any(_re_calc.search(rf"\b{kw}\b", q_lower)
                       for kw in ("product", "multiply", "multiplied", "times")):
                    val = 1.0
                    for n in nums:
                        val *= n
                else:
                    val = sum(nums)
                # Format integer-clean if no decimals were involved
                val_int = int(val)
                if val == val_int:
                    return name, f"{val_int:,}"
                return name, f"{val:,.2f}"
        except Exception as e:
            log.warning(f"[Chat] calculator fallback failed: {e}")

    return name, None




def _chat_vision_response(body: dict, messages: list):
    """If the request carries images, route to the vision model and return the
    response dict; else return None. Fix #8 (intra-file CC reduction):
    extracted verbatim from chat_completion, behavior-preserving. The inline
    vision POST is an A-11-pending site and stays in codec_dashboard."""
    images = body.get("images", [])
    if not images:
        return None
    import requests as rq2
    config2 = {}
    try:
        with open(CONFIG_PATH) as f:
            config2 = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.warning(f"Config read failed; proceeding without overrides: {e}")
    vision_url = config2.get("vision_base_url", "http://localhost:8083/v1")
    vision_model = config2.get("vision_model", "mlx-community/Qwen2.5-VL-7B-Instruct-4bit")
    # Build multimodal message: last user text + all images
    last_text = ""
    for m in reversed(messages):
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            last_text = m["content"]
            break
    if not last_text:
        last_text = "Describe and analyze this image in detail."
    mm_content = []
    for img_b64 in images:
        mm_content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}})
    mm_content.append({"type": "text", "text": last_text})
    v_payload = {
        "model": vision_model,
        "messages": [{"role": "user", "content": mm_content}],
        "max_tokens": 4000,
        "temperature": 0.7
    }
    # re-audit N7: guard the vision-backend call + parse. This helper runs
    # OUTSIDE chat_completion's try/except, so a non-200 / malformed response
    # (model not loaded, OOM, timeout) previously surfaced as a raw 500 with no
    # JSON body. Return a graceful 502 instead.
    try:
        vr = rq2.post(f"{vision_url}/chat/completions", json=v_payload,
                      headers={"Content-Type": "application/json"}, timeout=120)
        vr.raise_for_status()
        vdata = vr.json()
        vanswer = vdata["choices"][0]["message"]["content"].strip()
    except Exception as e:
        log.warning(f"[chat] vision backend call failed: {e}")
        return JSONResponse(
            {"error": f"Vision model unavailable: {type(e).__name__}"},
            status_code=502,
        )
    import re as re2
    vanswer = re2.sub(r'<think>[\s\S]*?</think>', '', vanswer).strip()
    return {"response": vanswer, "model": vision_model}


# Think-mode reasoning scaffold. Appended as the ABSOLUTE LAST instruction of the
# system prompt (recency beats the base prompt's "answer directly" / emoji rules,
# which otherwise suppress it — verified against the live 4-bit Qwen3.6). Forces
# the model to reason inside <thinking> then emit "### FINAL ANSWER:", which the
# chat UI splits into the train-of-thought reveal + the clean answer.
_REASON_SCAFFOLD = (
    "\n\n### OUTPUT FORMAT — THIS OVERRIDES ALL EARLIER STYLE INSTRUCTIONS\n"
    "You MUST structure EVERY reply in exactly two parts and use NO emoji:\n"
    "<thinking>\n"
    "Do ALL of your reasoning here. First identify what the user is really trying to "
    "ACCOMPLISH and check for any hidden physical or logical dependencies (things that "
    "must be true or present for the goal to work); never give a glib surface answer. "
    "Keep it to a few lines for simple questions and do not loop.\n"
    "</thinking>\n"
    "### FINAL ANSWER:\n"
    "(your clean answer for the user, no emoji)\n"
    "The very first characters of your reply MUST be \"<thinking>\". Never reason outside "
    "the tags. This format is mandatory and overrides any earlier instruction to "
    "\"answer directly\" or to use emoji."
)


def _build_chat_system_prompt(config: dict, budget, has_attachment: bool,
                              last_user_text: str) -> str:
    """Build the chat system prompt: override-aware base + per-turn step-budget
    warnings + attachment / content-rewrite / observer-injection suffixes.

    Fix #8 (intra-file CC reduction): extracted verbatim from chat_completion;
    behavior-preserving. `budget` is mutated exactly as before — warn_now() and
    consume('llm_call') happen here, once, where they ran inline.
    """
    from datetime import datetime as _dt
    # D4 / SR-45: helper moved to routes.prompts; lazy-import at call time.
    from routes.prompts import _load_prompt_overrides
    # H1 / SR-59: CHAT_SYSTEM_PROMPT stays in codec_dashboard (routes.prompts
    # also imports it from there). Lazy-import at call time to avoid a load-time
    # cycle — by request time codec_dashboard is fully imported.
    from codec_dashboard import CHAT_SYSTEM_PROMPT
    _overrides = _load_prompt_overrides()
    _chat_prompt = _overrides.get("chat", CHAT_SYSTEM_PROMPT)
    sys_prompt = _chat_prompt.format(date=_dt.now().strftime("%A, %B %d, %Y"))
    # Daybreak working-threads context (docs/DAYBREAK-DESIGN.md): compact
    # ≤150-token block of the user's open threads, so chat shares the same
    # live memory voice already gets via [ACTIVE FACTS]. "" when disabled.
    try:
        from codec_daybreak import get_working_context
        _wc = get_working_context()
        if _wc:
            sys_prompt += "\n\n" + _wc
    except Exception:
        pass
    if budget.warn_now():
        sys_prompt += (
            "\n\n⚠ 1 step remaining in this turn. Wrap up — do NOT "
            "emit additional [SKILL:...] tags."
        )
    budget.consume("llm_call")
    if budget.at_limit():
        sys_prompt += (
            "\n\n## Step Budget Exhausted\n"
            "You've hit the per-turn step budget. Summarize what you "
            "accomplished and any blockers in one short paragraph. "
            "DO NOT emit [SKILL:...] tags or call additional tools."
        )
    if has_attachment:
        sys_prompt += (
            "\n\n## This Turn\n"
            "The user has attached a file or image and its content is already "
            "embedded in their message between [IMAGE ANALYSIS]/[DOCUMENT] markers. "
            "Respond conversationally about the attached content. "
            "DO NOT emit [SKILL:...] tool-calling tags in this response."
        )
    _u_text_lower = (last_user_text or "").lower()
    _content_rewrite_intent = any(
        kw in _u_text_lower for kw in (
            "format my email", "format this email", "format my message",
            "reformat", "rewrite", "reword", "redraft", "polish",
            "proofread", "edit my email", "fix my email", "fix the grammar",
            "make this sound", "translate this", "translate the following",
            "draft a reply", "draft an email", "draft this",
        )
    )
    if _content_rewrite_intent:
        sys_prompt += (
            "\n\n## This Turn\n"
            "The user is asking you to generate or rewrite text directly "
            "(format/edit/draft/translate/polish their email or message). "
            "Respond with the rewritten content as plain prose. "
            "DO NOT emit [SKILL:...] tool-calling tags in this response — "
            "the answer IS the rewritten text, no tools needed."
        )
    try:
        from codec_observer import maybe_inject_observation_summary
        _obs_transport = "local" if "localhost" in (config.get("llm_base_url") or "") else "chat"
        _obs_summary, _obs_reason = maybe_inject_observation_summary(
            user_prompt=last_user_text or "",
            transport=_obs_transport,
            skill_name=None,           # post-LLM tag path, no skill resolved yet
            skill_module=None,
        )
        if _obs_summary:
            sys_prompt += f"\n\n{_obs_summary}"
    except Exception as _e:
        log.debug(f"[observer] injection failed (non-fatal): {_e}")
    return sys_prompt




import re as _re_esc

_ESCALATE_HINT_RE = _re_esc.compile(
    r"\b(build|create|research|plan|organi[sz]e|automate|migrate|design|"
    r"set\s?up|write me|make me|prepare|launch|develop)\b", _re_esc.IGNORECASE)


def _maybe_escalate_suggestion(user_text: str, session_id: str):
    """Step 10 auto-escalation, finally wired (2026-07). Runs AFTER the reply
    so it never adds latency to the answer itself. The regex prefilter keeps
    the Qwen classifier call off casual messages — only task-shaped text
    (>= 60 chars + an action verb) pays for classification. Returns the
    suggestion dict for the UI chip, or None."""
    try:
        # 2026-07 fix: the floor was 60, which silently dropped legitimate complex
        # asks like "plan and build me a 5-page competitor report with charts"
        # (56 chars) — the offer never fired, the prompt just degenerated in chat.
        # 24 still filters trivial single-verb messages; the action-verb regex +
        # the Qwen classifier are the real gate.
        if len(user_text or "") < 24 or not _ESCALATE_HINT_RE.search(user_text):
            return None
        verdict = _should_escalate_to_project(user_text, session_id)
        if not verdict.get("escalate"):
            return None
        log_event("agent_auto_escalated_from_chat", "codec-dashboard",
                  f"Suggested Project promotion ({verdict.get('estimated_checkpoints')} checkpoints)",
                  extra={"session_id": session_id,
                         "estimated_checkpoints": verdict.get("estimated_checkpoints"),
                         "verdict": verdict.get("reason", "")[:200],
                         "silenced": False})
        return {"estimated_checkpoints": verdict.get("estimated_checkpoints"),
                "reason": (verdict.get("reason") or "")[:200]}
    except Exception as e:
        log.debug(f"escalation check failed (non-fatal): {e}")
        return None


@router.post("/api/chat/escalate_silence")
async def escalate_silence(request: Request):
    """Q11: user said "No thanks" to a Project suggestion — silence the
    prompt for the rest of this chat session (in-memory, resets on restart)."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    sid = str(body.get("session_id") or "")
    if sid:
        silence_session_autoescalate(sid)
    return {"ok": True, "silenced": bool(sid)}


@router.post("/api/pick-folder")
async def pick_folder():
    """Open the native macOS folder chooser and return the selected POSIX path.

    Powers the "+" button in Chat and Vibe: the user allocates a working folder
    to the conversation (like a working directory), which the frontend then sends
    with each message so file operations can be scoped to it. Returns
    {ok:false, cancelled:true} if the user dismisses the dialog."""
    import asyncio

    script = (
        'POSIX path of (choose folder with prompt '
        '"Allocate a working folder to this CODEC chat")'
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=120)
    except asyncio.TimeoutError:
        return JSONResponse({"ok": False, "error": "folder chooser timed out"}, status_code=504)
    except FileNotFoundError:
        return JSONResponse({"ok": False, "error": "folder chooser unavailable (macOS only)"},
                            status_code=501)
    if proc.returncode != 0:
        # User cancelled → osascript exits non-zero with "User canceled".
        msg = (err or b"").decode(errors="replace")
        if "cancel" in msg.lower():
            return {"ok": False, "cancelled": True}
        return JSONResponse({"ok": False, "error": msg.strip()[:200] or "no folder chosen"},
                            status_code=400)
    path = (out or b"").decode(errors="replace").strip().rstrip("/")
    if not path:
        return {"ok": False, "cancelled": True}
    return {"ok": True, "path": path}


@router.post("/api/chat")
async def chat_completion(request: Request):
    """Direct LLM chat with full context window + tool calling"""
    from codec_metrics import metrics
    metrics.inc("codec_chat_requests_total")
    body = await request.json()
    _session_id = request.query_params.get("s") or ""
    messages = body.get("messages", [])
    if not messages:
        return JSONResponse({"error": "No messages"}, status_code=400)

    # Phase 1 Step 3 §3 — per-turn step budget. One counter for the
    # entire request; consumed by skill_hijack, llm_call, and each
    # post-LLM [SKILL:] tag resolution. Budget enforcement is non-
    # blocking (each path still runs) but audit-event-emitting +
    # warn-at-N-1 prompt suffix injection. See _StepBudget docstring.
    _budget = _StepBudget(
        route="chat",
        correlation_id=secrets.token_hex(6),
    )

    # Bind before the use_tools gate so the non-stream / system-prompt paths
    # below never hit an UnboundLocalError when a client sends {"tools": false}
    # (re-audit J1: was a silent opaque 500 — _build_chat_system_prompt is
    # called with both names regardless of the tools flag).
    last_user_text = ""
    has_attachment = False

    # ── Tool Calling: check if last user message matches a skill ──
    use_tools = body.get("tools", True)  # frontend can disable with tools:false
    if use_tools:
        for m in reversed(messages):
            if m.get("role") == "user" and isinstance(m.get("content"), str):
                last_user_text = m["content"]
                break
        # ── Slash commands (BEFORE skill check / attachment check) ──
        # Type /help, /skills, /cost, /version, /status, /who, /clear in chat
        # to invoke meta-controls without an LLM round-trip. Slash dispatch
        # runs first so /version still works even if the user has an image
        # attached in the same turn.
        if last_user_text:
            try:
                from codec_slash_commands import parse_slash, dispatch as slash_dispatch
                parsed = parse_slash(last_user_text)
            except Exception as e:
                log.warning(f"slash parser unavailable: {e}")
                parsed = None
            if parsed is not None:
                cmd_name, cmd_args = parsed
                slash_md = await asyncio.to_thread(slash_dispatch, cmd_name, cmd_args)
                log.info(f"[Chat] Slash /{cmd_name} handled ({len(slash_md)} chars)")
                stream_mode = body.get("stream", False)
                if stream_mode:
                    from starlette.responses import StreamingResponse as _SlashSR
                    async def _slash_stream():
                        yield f"data: {json.dumps({'slash': cmd_name})}\n\n"
                        yield f"data: {json.dumps({'token': slash_md})}\n\n"
                        yield "data: [DONE]\n\n"
                    return _SlashSR(_slash_stream(), media_type="text/event-stream")
                return {"response": slash_md, "slash": cmd_name}

        # Skip skill routing when the user attached a file / image — otherwise the
        # IMAGE ANALYSIS / DOCUMENT context text triggers false-positive skill hits
        # (e.g. a screenshot describing "system dashboard" routes to system_info).
        # Bugfix 2026-04-16: image attachments were being hijacked by skill router.
        has_attachment = last_user_text and (
            "[IMAGE ANALYSIS" in last_user_text
            or "[DOCUMENT:" in last_user_text
            or "[END IMAGE]" in last_user_text
            or "[END DOCUMENT]" in last_user_text
        )
        if last_user_text and not has_attachment:
            skill_name, skill_result = await asyncio.to_thread(_try_skill, last_user_text)
            if skill_result:
                _budget.consume("skill_hijack")   # pre-LLM hijack consumes 1
                log.info(f"[Chat] Skill '{skill_name}' handled: {skill_result[:80]}")
                stream_mode = body.get("stream", False)
                if stream_mode:
                    from starlette.responses import StreamingResponse as _SkillSR
                    # Return skill result as SSE stream (same format as LLM stream)
                    async def _skill_stream():
                        # Send skill indicator
                        yield f"data: {json.dumps({'skill': skill_name})}\n\n"
                        # Send the result as a single token, then LLM follow-up
                        skill_prefix = f"**⚡ {skill_name}**: {skill_result}\n\n"
                        yield f"data: {json.dumps({'token': skill_prefix})}\n\n"
                        yield "data: [DONE]\n\n"
                    return _SkillSR(_skill_stream(), media_type="text/event-stream")
                else:
                    return {"response": f"**⚡ {skill_name}**: {skill_result}", "skill": skill_name}

    # Check for images — route to vision model (extracted, Fix #8)
    vision_resp = _chat_vision_response(body, messages)
    if vision_resp is not None:
        return vision_resp

    try:
        config = {}
        try:
            with open(CONFIG_PATH) as f: config = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            log.warning(f"Config read failed; proceeding without overrides: {e}")
        base_url = config.get("llm_base_url", "http://localhost:8083/v1")
        model = config.get("llm_model", "mlx-community/Qwen3.6-35B-A3B-4bit")
        # PR-2B (D-15 partial): keychain-aware live read.
        from codec_config import get_llm_api_key as _kc_get_llm
        api_key = _kc_get_llm()
        kwargs = config.get("llm_kwargs", {})
        # (A-12 PR-3E-chat-stream: the `import requests as rq` + `headers` here are
        # gone — both chat POSTs now go through codec_llm, which builds its own.)
        force_search = body.get("force_search", False)
        messages = _enrich_messages(messages, config, force_search=bool(force_search))

        # Build the system prompt (override + step-budget + attachment /
        # content-rewrite / observer suffixes) — extracted to a helper for
        # readability (Fix #8). Consumes the llm_call step budget internally.
        sys_prompt = _build_chat_system_prompt(
            config, _budget, has_attachment, last_user_text
        )

        # Working folder allocated to this chat via the "+" button. Tell the model
        # so file operations (save/read with relative names) resolve there — the
        # chat's working directory, like Claude Code.
        _workdir = str(body.get("workdir") or "").strip()
        if _workdir:
            sys_prompt += (
                f"\n\nWORKING FOLDER: {_workdir}\n"
                f"When the user asks to save, create, read, or edit a file without an "
                f"absolute path, use this folder — e.g. save to '{_workdir}/<name>'. "
                f"Treat it as the current working directory for this conversation."
            )

        # Prepend system message (or replace existing one)
        if messages and messages[0].get("role") == "system":
            messages[0]["content"] = sys_prompt + "\n\n" + messages[0]["content"]
        else:
            messages.insert(0, {"role": "system", "content": sys_prompt})

        # Think mode: append the reasoning scaffold LAST so it wins over the base
        # prompt's "answer directly"/emoji rules. Frontend sends reason_scaffold
        # = Think-toggle state; it parses the resulting <thinking>/### FINAL ANSWER.
        if body.get("reason_scaffold") and messages and messages[0].get("role") == "system":
            messages[0]["content"] += _REASON_SCAFFOLD

        stream_mode = body.get("stream", False)
        # Dashboard chat & Vibe benefit from thinking mode (deeper answers).
        # Frontend can send thinking=false to override for speed.
        thinking = body.get("thinking", True)
        # Train-of-thought reveal: when the frontend's Thoughts toggle is ON,
        # also stream the model's <think> reasoning as separate SSE `think`
        # events. Off by default → no think frames → identical to before.
        show_thoughts = bool(body.get("show_thoughts", False))

        # A-12 (PR-3E-chat-stream): build the shared codec_llm args ONCE so the
        # stream + non-stream branches can't drift. top_p/frequency_penalty are
        # explicit but kwargs may override them (matches the old payload.update);
        # enable_thinking is the codec_llm param applied last → frontend toggle
        # wins (matches the old chat_template_kwargs assignment after the update).
        _extra = {"top_p": 0.9, "frequency_penalty": 1.1,
                  **{k: v for k, v in kwargs.items() if k != "chat_template_kwargs"}}
        # 2026-07 chat-visibility fix: max_tokens + timeout are operator-tunable
        # via ~/.codec/config.json:chat.{max_tokens, llm_timeout_s}. Note the
        # cap includes <think> tokens when thinking mode is on — deep answers
        # that burn a lot of reasoning eat into the visible-reply budget.
        _chat_cfg = config.get("chat", {}) if isinstance(config.get("chat"), dict) else {}
        _common = dict(base_url=base_url, model=model, api_key=api_key,
                       max_tokens=int(_chat_cfg.get("max_tokens", 28000)),
                       temperature=0.7, enable_thinking=thinking,
                       extra_kwargs=_extra,
                       timeout=float(_chat_cfg.get("llm_timeout_s", 300)))

        if stream_mode:
            # SSE streaming — keeps Cloudflare tunnel alive, sends tokens as they arrive
            def _stream_gen():
                # A-6 (PR-3D-c): the <think> + [SKILL:...] token machine lives in
                # codec_chat_stream.SkillTagBuffer; _resolve_skill_tag (below) is
                # injected (it runs the skill: budget + allowlist + dispatch).
                # A-12 (PR-3E-chat-stream): the SSE POST + keepalive are now
                # codec_llm.stream(keepalive=True); this generator just wires the
                # raw tokens through the buffer and frames them.

                def _frame(tok):
                    return f"data: {json.dumps({'token': tok})}\n\n"

                def _resolve_skill_tag(raw_tag):
                    """Run the skill inline and return its string result.

                    On any failure we DROP the tag (return empty string) instead
                    of leaking the raw [SKILL:...] tag into the UI. The LLM's
                    own follow-up prose usually contains the answer anyway.
                    Bugfix 2026-04-26: previously returned raw_tag on failure,
                    causing "[SKILL:calculator:sum of...]" to appear in chat.

                    Phase 1 Step 3 §3 — each resolved tag consumes one step
                    from the chat-turn budget. If exhausted, the tag is
                    dropped (so the LLM doesn't continue burning steps);
                    step_budget_exhausted audit was already emitted by
                    _budget.consume.
                    """
                    m = SKILL_TAG_RE.search(raw_tag)
                    if not m:
                        return raw_tag  # not a skill tag at all — emit as-is
                    if not _budget.consume("post_llm_skill_tag"):
                        log.info("[Chat] step_budget exhausted — dropping [SKILL:...] tag")
                        return raw_tag.replace(m.group(0), "")
                    s_name, s_query = m.group(1), m.group(2)
                    if s_name not in CHAT_SKILL_ALLOWLIST:
                        log.info(f"[Chat] LLM tried disallowed skill {s_name!r} — dropping tag")
                        return raw_tag.replace(m.group(0), "")
                    try:
                        _, s_result = _try_skill_by_name(s_name, s_query)
                        if s_result:
                            return raw_tag.replace(m.group(0), f"**{s_result}**")
                        log.info(f"[Chat] Skill {s_name!r} returned None for {s_query[:60]!r} — dropping tag")
                    except Exception as e:
                        log.warning(f"[Chat] Skill {s_name!r} crashed: {e}")
                    # Drop the tag silently — never leak raw [SKILL:...] to UI
                    return raw_tag.replace(m.group(0), "")
                buf = SkillTagBuffer(_resolve_skill_tag)
                try:
                    # codec_llm.stream yields raw content deltas (it owns the SSE
                    # POST + data:/[DONE] parsing), the KEEPALIVE sentinel on
                    # empty thinking-chunks (keepalive=True) to hold the tunnel,
                    # and — 2026-07 chat-visibility fix — STREAM_ERROR /
                    # FINISH_LENGTH sentinels so an interrupted or truncated
                    # reply is SAID to the user instead of silently rendering
                    # as an empty / mid-sentence bubble.
                    stream_died = False
                    hit_token_cap = False
                    degenerate = False
                    # Degeneracy circuit-breaker state: raw deltas accumulated
                    # (tail only) and re-checked every ~40 deltas.
                    _acc = ""
                    _since_check = 0
                    for item in codec_llm.stream(messages, **_common,
                                                 keepalive=True,
                                                 error_sentinel=True,
                                                 inline_reasoning=show_thoughts):
                        if item is codec_llm.KEEPALIVE:
                            yield ": keepalive\n\n"
                            continue
                        if item is codec_llm.STREAM_ERROR:
                            stream_died = True
                            continue
                        if item is codec_llm.FINISH_LENGTH:
                            hit_token_cap = True
                            continue
                        for s in buf.feed(item):
                            yield _frame(s)
                        if show_thoughts:
                            for t in buf.drain_think():
                                yield f"data: {json.dumps({'think': t})}\n\n"
                        _acc += item
                        _since_check += 1
                        if _since_check >= 40:
                            _since_check = 0
                            if len(_acc) > 6000:
                                _acc = _acc[-4000:]   # tail is all the check needs
                            if _degenerate_tail(_acc):
                                degenerate = True
                                log.warning("[Chat] degenerate repetition loop detected — cutting stream")
                                break
                    # Stream ended ([DONE] or close): flush, then blank-bubble net.
                    for s in buf.finish():
                        yield _frame(s)
                    if show_thoughts:
                        for t in buf.drain_think():
                            yield f"data: {json.dumps({'think': t})}\n\n"
                    if degenerate:
                        yield _frame(
                            "\n\n*I caught myself repeating the same text in a "
                            "loop and stopped — that was a local-model glitch, not "
                            "a real answer. Please ask again (rephrasing slightly "
                            "usually fixes it).*"
                        )
                    if hit_token_cap:
                        yield _frame(
                            "\n\n⚠️ *Reply truncated — the model hit the "
                            "`chat.max_tokens` cap. Raise it in "
                            "`~/.codec/config.json` (chat → max_tokens) for "
                            "longer replies.*"
                        )
                    if stream_died:
                        yield _frame(
                            "\n\n⚠️ *Reply interrupted — the connection to the "
                            "local model dropped mid-answer. Ask me to continue, "
                            "or retry. (If this repeats: `pm2 logs qwen3.6`.)*"
                        )
                    # Blank-bubble net. Distinguish the two empty cases
                    # (2026-07): dropped tool tags vs. the model producing
                    # nothing at all — the old single message blamed a "tool"
                    # even when the LLM was just down/overloaded.
                    if buf.visible_chars == 0 and not stream_died:
                        if buf.tags_resolved:
                            yield _frame(
                                "I tried to use a tool that didn't apply here. "
                                "Could you rephrase, or just ask me to write it directly?"
                            )
                        else:
                            yield _frame(
                                "*The model returned an empty reply — it may be "
                                "busy, restarting, or out of context. Please try "
                                "again in a moment.*"
                            )
                    # Step 10 Q11 (2026-07): post-reply Project suggestion.
                    _sugg = _maybe_escalate_suggestion(last_user_text, _session_id)
                    if _sugg:
                        yield f"data: {json.dumps({'escalate_project': _sugg})}\n\n"
                    yield "data: [DONE]\n\n"
                except Exception as e:
                    yield f"data: {json.dumps({'error': str(e)})}\n\n"
            from starlette.responses import StreamingResponse as _SR
            return _SR(_stream_gen(), media_type="text/event-stream",
                       headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

        # Non-streaming fallback (A-12 PR-3E-chat-stream): canonical codec_llm.call.
        # raise_on_error=True preserves the original raise-on-failure (was an
        # r.json() KeyError) → outer except → 500. codec_llm strips <think>; the
        # `### FINAL ANSWER:` marker is dashboard-specific so it stays.
        import re
        answer = codec_llm.call(messages, **_common, raise_on_error=True)
        answer = re.sub(r'###\s*FINAL ANSWER:\s*', '', answer).strip()

        # ── Post-LLM skill routing ──
        # If the LLM outputs [SKILL:name:query], execute and inline the result.
        skill_tag = re.search(r'\[SKILL:(\w+):([^\]]+)\]', answer)
        if skill_tag:
            s_name, s_query = skill_tag.group(1), skill_tag.group(2)
            if s_name in CHAT_SKILL_ALLOWLIST and not _budget.consume("post_llm_skill_tag"):
                # re-audit medium: the non-streaming path previously skipped the
                # step budget that the streaming _resolve_skill_tag enforces, so
                # stream:false could run skills past the per-turn cap. Mirror the
                # stream path: budget exhausted → drop the tag.
                log.info("[Chat] step_budget exhausted — dropping [SKILL:...] tag (non-stream)")
                answer = answer.replace(skill_tag.group(0), "")
            elif s_name in CHAT_SKILL_ALLOWLIST:
                try:
                    _, s_result = await asyncio.to_thread(_try_skill_by_name, s_name, s_query)
                    if s_result:
                        answer = answer.replace(skill_tag.group(0), f"**{s_result}**")
                except Exception as e:
                    # A-22 fix: was a silent `pass` — if skill resolution blows
                    # up, the raw [SKILL:...] tag leaks into the user's chat with
                    # no footprint. Surface it (log + audit); behavior unchanged
                    # (tag stays, chat still returns).
                    log.warning(
                        f"Post-LLM skill tag resolution failed for {s_name!r}: {e}")
                    try:
                        log_event(
                            "post_llm_skill_tag_failed", source="codec-dashboard",
                            message=f"Skill tag resolution failed: {s_name}",
                            level="warning", outcome="error",
                            extra={"skill": s_name, "error": str(e)[:200]},
                        )
                    except Exception:
                        pass
            else:
                # J1 parity: a non-allowlisted skill name is never executed
                # (the invariant holds via the two branches above) AND its raw
                # tag is stripped — the streaming path's _resolve_skill_tag
                # already drops disallowed tags; the non-stream path used to
                # leave "[SKILL:foo:...]" visible in the chat bubble.
                log.info(f"[Chat] LLM tried disallowed skill {s_name!r} (non-stream) — dropping tag")
                answer = answer.replace(skill_tag.group(0), "")

        return {"response": answer, "model": model}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
