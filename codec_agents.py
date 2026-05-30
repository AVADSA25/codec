"""
CODEC Agents — Local multi-agent framework
Replaces CrewAI with ~300 lines. Zero external dependencies.
Uses CODEC skills as tools + Qwen 3.6 35B with thinking mode.
"""
import asyncio
import contextvars
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional

import hashlib
import logging
import threading
import httpx

from codec_audit import audit as _audit_core
from codec_hooks import HookVeto, run_with_hooks
from codec_llm_proxy import llm_queue, Priority

log = logging.getLogger("codec_agents")

# ── Tool-call validation ──
_VALID_TOOL_NAME_RE = re.compile(r'^[A-Za-z0-9_.\-]+$')
_MAX_TOOL_NAME_LEN = 100
_MAX_TOOL_INPUT_LEN = 50000

# ── CONFIG ──
CONFIG_PATH = os.path.expanduser("~/.codec/config.json")
try:
    from codec_config import SKILLS_DIR
except ImportError:
    SKILLS_DIR = os.path.expanduser("~/.codec/skills")
DB_PATH     = os.path.expanduser("~/.codec/memory.db")

def _cfg():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except Exception as e:
        log.warning("Config load failed: %s", e)
        return {}

def _qwen_base():
    # A-12 (PR-3E-async): base URL (no /chat/completions) for codec_llm.acall.
    # (Replaced the old _qwen_url() — both its callers now use codec_llm, which
    # appends /chat/completions itself.)
    return _cfg().get("llm_base_url", "http://localhost:8083/v1")

def _qwen_model():
    return _cfg().get("llm_model", "mlx-community/Qwen3.6-35B-A3B-4bit")

# PR-2B-2 (D-15): Keychain-aware getter (cfg→Keychain migration + env fallback).
def _serper_api_key() -> str:
    try:
        from codec_config import get_serper_api_key
        return get_serper_api_key()
    except Exception:
        return os.environ.get("SERPER_API_KEY", "")

SERPER_API_KEY = _serper_api_key()

# ── HTTP connection pools (reuse TCP connections across calls) ──
_HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
_sync_http  = httpx.Client(timeout=30, follow_redirects=True, headers=_HTTP_HEADERS)
_async_http = httpx.AsyncClient(timeout=180)

# ── AUDIT LOGGER ──
# Crew/agent events route through codec_audit.audit() — writes to
# ~/.codec/audit.log via the unified envelope (schema:1) per
# docs/PHASE1-STEP1-DESIGN.md. The legacy duplicate audit.log writer
# (formerly _AUDIT_LOG_PATH at this position) is gone: one writer, one
# rotation, one threading.Lock. See codec_audit.py for the actual write.

# Correlation-id propagation: a top-level operation (Crew.run, Agent.run when
# called outside a crew) sets _correlation_id_var; nested emits inherit it
# automatically. Using contextvars keeps the ID intact across asyncio task
# boundaries and run_in_executor calls. See design §1.4.
#
# A5 / SR-5: the canonical home for this contextvar moved to codec_audit so
# downstream readers (codec_ask_user, codec_observer, codec_triggers) can
# import it without dragging codec_agents into a cycle. Re-exported here for
# back-compat with any external importer that grabbed
# `codec_agents._correlation_id_var` directly.
from codec_audit import (
    _correlation_id_var as _correlation_id_var,  # noqa: F401 — re-export
    _new_correlation_id as _new_correlation_id,  # noqa: F401 — re-export
)


def _audit(event_type: str, **kwargs):
    """Shim over codec_audit.audit() for crew/agent runtime events.

    Translates the historic crew kwargs (`elapsed`, free-form keys) into the
    unified envelope. Pulls correlation_id from the contextvar when not
    passed explicitly. Never raises.
    """
    elapsed = kwargs.pop("elapsed", None)
    duration_ms = kwargs.pop("duration_ms", None)
    if duration_ms is None and isinstance(elapsed, (int, float)):
        duration_ms = float(elapsed) * 1000.0

    cid = kwargs.pop("correlation_id", None) or _correlation_id_var.get()
    tool = kwargs.pop("tool", "") or ""
    agent = kwargs.pop("agent", None)
    outcome = kwargs.pop("outcome", "ok")
    error_type = kwargs.pop("error_type", None)
    error = kwargs.pop("error", None)

    extra = {k: v for k, v in kwargs.items() if v is not None}
    # `elapsed` survives as-is in extra (analyzer may want the integer-second form)
    if elapsed is not None and "elapsed" not in extra:
        extra["elapsed"] = elapsed

    try:
        _audit_core(
            tool=tool,
            event=event_type,
            source="codec-agents",
            outcome=outcome,
            duration_ms=duration_ms,
            agent=agent,
            transport="crew",
            error_type=error_type,
            error=error,
            correlation_id=cid,
            extra=extra or None,
        )
    except Exception as e:
        log.debug("Audit emit failed (event=%s): %s", event_type, e)

# Captures the last Google Docs URL created — fallback if Writer forgets to echo it
_last_gdoc_url: Optional[str] = None

# Google Docs rate-limit / dedup state
_gdoc_lock = threading.Lock()
_gdoc_created: Dict[str, float] = {}   # title_hash → timestamp
_GDOC_COOLDOWN_SEC = 60                 # minimum seconds between docs with same title


# ═══════════════════════════════════════════════════════════════
# TOOL
# ═══════════════════════════════════════════════════════════════

@dataclass
class Tool:
    name: str
    description: str
    fn: Callable

    def run(self, input_str: str) -> str:
        try:
            result = self.fn(input_str)
            text = str(result) if result else "No output."
            if len(text) > 10000:
                text = text[:10000] + f"\n\n[TRUNCATED: output was {len(text)} chars, showing first 10000]"
            return text
        except Exception as e:
            return f"Tool error ({self.name}): {e}"


# ═══════════════════════════════════════════════════════════════
# BUILT-IN TOOLS
# ═══════════════════════════════════════════════════════════════

def _web_search(query: str) -> str:
    """Search via DuckDuckGo (free, no key) or Serper if configured in ~/.codec/config.json."""
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from codec_search import search, format_results
    results = search(query.strip(), max_results=10)
    return format_results(results, max_snippets=10)


def _web_fetch(url: str) -> str:
    try:
        # Fix #7 (H1) + re-audit N3: SSRF guard BEFORE the request AND on every
        # redirect hop. The fetched text is returned to the agent/LLM, so a read
        # of an internal/metadata host is an exfil path; _sync_http defaults to
        # follow_redirects=True, which would reach an internal target via a 302
        # the guard never saw — so we follow redirects manually here.
        import codec_ssrf
        from urllib.parse import urljoin
        cur = url.strip()
        try:
            for _ in range(6):  # initial request + up to 5 redirects
                codec_ssrf.validate_url(cur)
                r = _sync_http.get(cur, follow_redirects=False)
                if r.is_redirect and r.headers.get("location"):
                    cur = urljoin(cur, r.headers["location"])
                    continue
                break
            else:
                return "Fetch error: blocked URL (too many redirects)"
        except codec_ssrf.SSRFError as e:
            return f"Fetch error: blocked URL ({e})"
        if r.status_code in (401, 403):
            return f"Blocked by site (HTTP {r.status_code}). Site requires JavaScript or blocks automated access."
        if r.status_code >= 400:
            return f"HTTP error {r.status_code} fetching {url}"
        text = r.text
        text = re.sub(r'<script[^>]*>[\s\S]*?</script>', '', text)
        text = re.sub(r'<style[^>]*>[\s\S]*?</style>', '', text)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        if len(text) > 10000:
            return text[:10000] + f"\n\n[TRUNCATED: page was {len(text)} chars, showing first 10000]"
        return text
    except Exception as e:
        return f"Fetch error: {e}"


def _file_read(path: str) -> str:
    path = path.strip()
    if path.startswith("~/"):
        path = os.path.expanduser(path)
    elif not path.startswith("/"):
        path = os.path.join(os.path.expanduser("~/codec-workspace"), path)
    # Resolve symlinks and .. to prevent traversal
    path = os.path.realpath(path)
    home = os.path.realpath(os.path.expanduser("~"))
    if not path.startswith(home):
        return "Error: cannot read files outside home directory."
    try:
        with open(path, "r", errors="ignore") as f:
            content = f.read()
        if len(content) > 10000:
            return content[:10000] + f"\n\n[TRUNCATED: file was {len(content)} chars, showing first 10000]"
        return content
    except Exception as e:
        return f"File read error: {e}"


def _file_write(input_str: str) -> str:
    path = ""
    content = ""
    for line in input_str.split("\n"):
        if line.lower().startswith("path:"):
            path = line.split(":", 1)[1].strip()
        elif line.lower().startswith("content:"):
            content = input_str.split("content:", 1)[1].strip()
            break
    if not path:
        lines = input_str.strip().split("\n", 1)
        path = lines[0].strip()
        content = lines[1] if len(lines) > 1 else ""
    workspace = os.path.expanduser("~/codec-workspace")
    os.makedirs(workspace, exist_ok=True)
    if not path.startswith("/"):
        path = os.path.join(workspace, path)
    # Resolve symlinks and .. to prevent traversal
    path = os.path.realpath(path)
    home = os.path.realpath(os.path.expanduser("~"))
    if not path.startswith(home):
        return "Error: cannot write outside home directory."
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        return f"Written {len(content)} chars to {path}"
    except Exception as e:
        return f"File write error: {e}"


def _google_docs_create(input_str: str) -> str:
    """Create a richly styled Google Doc — reuses codec_gdocs.create_google_doc().
    Rate-limited: blocks duplicate titles within 60 seconds."""
    global _last_gdoc_url
    title = "CODEC Report"
    content = input_str
    if "title:" in input_str.lower():
        for line in input_str.split("\n"):
            if line.lower().startswith("title:"):
                title = line.split(":", 1)[1].strip()
            elif line.lower().startswith("content:"):
                content = input_str.split("content:", 1)[1].strip()
                break

    # Dedup: reject same title within cooldown period
    title_hash = hashlib.sha256(title.encode()).hexdigest()[:16]
    with _gdoc_lock:
        now = time.time()
        last_created = _gdoc_created.get(title_hash, 0)
        if now - last_created < _GDOC_COOLDOWN_SEC:
            remaining = int(_GDOC_COOLDOWN_SEC - (now - last_created))
            return (f"Rate-limited: a Google Doc titled '{title}' was created {int(now - last_created)}s ago. "
                    f"Wait {remaining}s or use a different title. Last URL: {_last_gdoc_url or 'unknown'}")

        try:
            import sys as _sys
            _dash = os.path.dirname(os.path.abspath(__file__))
            if _dash not in _sys.path:
                _sys.path.insert(0, _dash)
            from codec_gdocs import create_google_doc
            doc_url = create_google_doc(title, content)
            if doc_url:
                _last_gdoc_url = doc_url
                _gdoc_created[title_hash] = now
                return f"Google Doc created: {doc_url}"
            return "Google Docs error: doc creation returned None"
        except Exception as e:
            return f"Google Docs error: {e}"


def _shell_execute(cmd: str) -> str:
    import subprocess
    cmd = cmd.strip()
    from codec_config import is_dangerous
    if is_dangerous(cmd):
        _audit("shell_blocked", cmd=cmd[:200])
        return "BLOCKED: dangerous command pattern detected. Command not executed."
    # Print command for transparency before execution
    log.info(f"[shell_execute] Running: {cmd[:200]}")
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                           timeout=30, cwd=os.path.expanduser("~"))
        out = r.stdout
        if len(out) > 5000:
            out = out[:5000] + f"\n[TRUNCATED: stdout was {len(r.stdout)} chars]"
        if r.stderr:
            stderr = r.stderr
            if len(stderr) > 2000:
                stderr = stderr[:2000] + f"\n[TRUNCATED: stderr was {len(r.stderr)} chars]"
            out += "\nSTDERR: " + stderr
        return out or "(no output)"
    except subprocess.TimeoutExpired:
        return "Command timed out (30s)"
    except Exception as e:
        return f"Shell error: {e}"


BUILTIN_TOOLS = [
    Tool("web_search",        "Search Google for any query. Input: search query string.",             _web_search),
    Tool("web_fetch",         "Fetch and read a web page. Input: URL string.",                        _web_fetch),
    Tool("file_read",         "Read a file from disk. Input: file path.",                             _file_read),
    Tool("file_write",        "Write a file. Input: 'path: /path\\ncontent: text'",                  _file_write),
    Tool("google_docs_create","Create a Google Doc. Input: 'title: Title\\ncontent: body text'",     _google_docs_create),
    Tool("shell_execute",     "Run a shell command. Dangerous commands are blocked. Input: cmd",      _shell_execute),
]


# ═══════════════════════════════════════════════════════════════
# SKILL LOADER (lazy via SkillRegistry)
# ═══════════════════════════════════════════════════════════════

from codec_skill_registry import SkillRegistry

_agents_registry = SkillRegistry(SKILLS_DIR)


def _make_lazy_fn(registry: "SkillRegistry", skill_name: str):
    """Return a callable that lazy-loads the skill module on first call."""
    def _lazy_run(input_str: str) -> str:
        mod = registry.load(skill_name)
        if mod is None or not hasattr(mod, "run"):
            return f"Skill '{skill_name}' could not be loaded."
        return mod.run(input_str)
    return _lazy_run


def load_skill_tools() -> List[Tool]:
    """Scan skills and return Tool objects with lazy-loaded run functions.

    Only metadata is parsed at startup (via AST); the actual module
    import happens on first invocation of each tool.
    """
    _agents_registry.scan()
    tools = []
    for name in _agents_registry.names():
        desc = _agents_registry.get_description(name)
        tools.append(Tool(
            name=name,
            description=desc,
            fn=_make_lazy_fn(_agents_registry, name),
        ))
    print(f"[Agents] Registered {len(tools)} skill tools (lazy)")
    return tools


def get_all_tools() -> List[Tool]:
    return BUILTIN_TOOLS + load_skill_tools()


# ═══════════════════════════════════════════════════════════════
# AGENT
# ═══════════════════════════════════════════════════════════════

@dataclass
class Agent:
    name: str
    role: str
    tools: List[Tool] = field(default_factory=list)
    max_tool_calls: int = 5
    thinking: bool = False      # Keep off by default — adds latency; crews can override
    verbose: bool = True

    # Phase 1 Step 3 §2.2 stuck-detection ring buffer.
    # Per-agent: each Agent instance tracks its own (tool_name, args_hash)
    # window of last M=5 calls. When the same key appears N=3 times,
    # _handle_stuck() fires (warn first, escalate at N+2 = 5).
    # Defaults loaded from ~/.codec/config.json: stuck.{repeat_threshold,
    # window, escalation_action} on first Agent.run call. Cached as
    # instance attrs to avoid re-reading config every loop iteration.
    _recent_calls: List[tuple] = field(default_factory=list, repr=False)
    _stuck_warned_keys: set = field(default_factory=set, repr=False)
    _stuck_escalated_keys: set = field(default_factory=set, repr=False)

    # ── A-7 (PR-3D-a): ReAct-loop helpers extracted from run() ──────────────
    @staticmethod
    def _parse_action(text: str) -> tuple:
        """Pure parse of the ReAct text protocol. Returns (tool, final_text):
          tool       = (name, input) if a well-formed TOOL:/INPUT: block exists, else None
          final_text = text after the LAST 'FINAL:' (stripped) if present, else None
        Both may be set; run() applies TOOL-before-FINAL precedence with its own
        tool-budget state (unchanged)."""
        m = re.search(r'TOOL:\s*(\S+)\s*\nINPUT:\s*([\s\S]*?)(?=\nTOOL:|\nFINAL:|$)', text)
        tool = (m.group(1).strip(), m.group(2).strip()) if m else None
        final_text = text.rsplit("FINAL:", 1)[1].strip() if "FINAL:" in text else None
        return tool, final_text

    @staticmethod
    def _validate_tool_call(tool_name: str, tool_input: str):
        """Return a rejection message to feed back to the LLM if the tool call is
        malformed, else None. Pure — the caller does the logging + message append."""
        if not tool_name:
            return "Empty tool name rejected. Try again or use FINAL:."
        if len(tool_name) > _MAX_TOOL_NAME_LEN:
            return "Tool name too long (max 100 chars). Try again or use FINAL:."
        if not _VALID_TOOL_NAME_RE.match(tool_name):
            return (f"Tool name '{tool_name[:60]}' contains invalid characters. "
                    f"Only alphanumeric, underscore, hyphen, and dot are allowed. "
                    f"Try again or use FINAL:.")
        if len(tool_input) > _MAX_TOOL_INPUT_LEN:
            return (f"Tool input too long ({len(tool_input)} chars, max "
                    f"{_MAX_TOOL_INPUT_LEN}). Try again or use FINAL:.")
        return None

    async def _execute_tool_with_hooks(self, tool, tool_name: str, tool_input: str) -> str:
        """Run `tool` through run_with_hooks in a worker thread, propagating
        contextvars (incl. _correlation_id_var) so audits fired inside the tool
        inherit the agent/crew cid (asyncio doesn't copy them automatically).
        Applies the Step-2 veto contract. Returns the tool result string. Stuck
        detection is applied by the caller AFTER the tool_result audit, to keep
        that audit's result_len reporting the pre-stuck length."""
        loop = asyncio.get_event_loop()
        _agent_cid = _correlation_id_var.get() or _new_correlation_id()
        _agent_name = self.name
        _tool_name_local = tool_name
        _tool_input_local = tool_input
        _real_tool = tool

        def _run_tool_with_hooks():
            def _inner(t, _c):
                return _real_tool.run(t)
            return run_with_hooks(
                tool_name=_tool_name_local,
                task=_tool_input_local,
                context="",
                transport="crew",
                agent=_agent_name,
                correlation_id=_agent_cid,
                invoke=_inner,
            )

        ctx = contextvars.copy_context()
        result = await loop.run_in_executor(None, ctx.run, _run_tool_with_hooks)
        if isinstance(result, HookVeto):
            result = (f"Tool '{tool_name}' was vetoed by plugin "
                      f"'{result.plugin_name}': {result.reason}")
        return result

    async def run(self, task: str, context: str = "", callback: Optional[Callable] = None) -> str:
        # Inherit correlation_id from the surrounding Crew if there is one;
        # otherwise (e.g. run_custom_agent — solo agent path) generate our own.
        # We don't reset the token: the asyncio.Task that owns this context goes
        # away after the request, and any nested tool calls inside this same
        # agent run should see the same cid (that's the whole point).
        if _correlation_id_var.get() is None:
            _correlation_id_var.set(_new_correlation_id())
        self._gdoc_url = None  # Capture real URL from google_docs_create tool
        tool_desc = "\n".join(f"  - {t.name}: {t.description}" for t in self.tools) or "  (no tools)"

        system = f"""{self.role}

You have access to these tools:
{tool_desc}

To use a tool, respond EXACTLY in this format (nothing else on those lines):
TOOL: tool_name
INPUT: the input for the tool

To give your final answer, respond EXACTLY in this format:
FINAL: your complete answer here

Rules:
- Use tools to gather information you need.
- You may use up to {self.max_tool_calls} tool calls total.
- Think step by step before choosing a tool.
- After each tool result, decide: need more info → another TOOL, or ready → FINAL.
- ALWAYS end with FINAL: when you have enough information."""

        messages = [{"role": "system", "content": system}]
        if context:
            messages.append({"role": "user", "content": f"Context from previous step:\n{context}"})
        messages.append({"role": "user", "content": f"Your task:\n{task}"})

        tool_calls_made = 0
        last_response = ""

        import codec_llm
        for _ in range(self.max_tool_calls + 3):
            # A-12 (PR-3E-async): codec_llm.acall (async, raise_on_error) replaces
            # the inline _async_http.post + parse. Queue (MEDIUM) + the reused
            # client stay here; the except keeps the "LLM error" early-exit.
            await llm_queue.acquire(Priority.MEDIUM)
            try:
                response = await codec_llm.acall(
                    messages, base_url=_qwen_base(), model=_qwen_model(),
                    max_tokens=4000, temperature=0.7, enable_thinking=self.thinking,
                    http=_async_http, raise_on_error=True,
                )
            except Exception as e:
                return f"LLM error: {e}"
            finally:
                await llm_queue.release(Priority.MEDIUM)

            # Strip thinking tags (codec_llm already strips; kept harmless).
            response = re.sub(r'<think>[\s\S]*?</think>', '', response).strip()
            last_response = response

            if self.verbose:
                print(f"[{self.name}] {response[:200]}…")

            # ── Parse the ReAct protocol (A-7: extracted to _parse_action) ──
            # TOOL is checked before FINAL so a response with both doesn't loop.
            parsed_tool, final = self._parse_action(response)

            # FINAL answer (final = text after the LAST 'FINAL:'; skips quoted prompt).
            if final is not None and not (parsed_tool and tool_calls_made < self.max_tool_calls):
                # Guard: if agent has google_docs_create but never called it, reject the FINAL
                has_docs_tool = any(t.name == "google_docs_create" for t in self.tools)
                called_docs = any("google_docs_create" in str(m_msg.get("content", "")) for m_msg in messages if m_msg["role"] == "user" and "Tool result from" in str(m_msg.get("content", "")))
                if has_docs_tool and not called_docs and tool_calls_made == 0:
                    messages.append({"role": "assistant", "content": response})
                    messages.append({"role": "user", "content": (
                        "REJECTED: You must use the google_docs_create tool BEFORE giving a FINAL answer. "
                        "Do NOT invent URLs. Call the tool now with your content."
                    )})
                    continue
                # Guard: replace any fabricated Google Docs URL with the real one
                if self._gdoc_url and "docs.google.com" in final:
                    final = re.sub(
                        r'https://docs\.google\.com/document/d/[A-Za-z0-9_-]+(?:/edit)?(?:\?[^\s)]*)?',
                        self._gdoc_url,
                        final
                    )
                    log.info(f"[{self.name}] Replaced fabricated URL with real: {self._gdoc_url}")
                elif self._gdoc_url and "docs.google.com" not in final:
                    # LLM didn't even include a URL — append it
                    final = f"{final}\n{self._gdoc_url}"
                    log.info(f"[{self.name}] Appended real URL to FINAL: {self._gdoc_url}")
                if callback:
                    await _safe_cb(callback, {"agent": self.name, "status": "complete", "preview": final[:200]})
                return final

            # TOOL call (parsed_tool from _parse_action above)
            if parsed_tool and tool_calls_made < self.max_tool_calls:
                tool_name, tool_input = parsed_tool

                # ── Input validation (A-7: extracted to _validate_tool_call) ──
                rejection = self._validate_tool_call(tool_name, tool_input)
                if rejection:
                    log.warning("Rejected malformed tool call: %s", tool_name[:120])
                    messages.append({"role": "assistant", "content": response})
                    messages.append({"role": "user", "content": rejection})
                    continue

                tool = next((t for t in self.tools if t.name == tool_name), None)

                if tool:
                    if callback:
                        await _safe_cb(callback, {
                            "agent": self.name, "status": "tool_call",
                            "tool": tool_name, "input": tool_input[:100]
                        })
                    if self.verbose:
                        print(f"[{self.name}] → {tool_name}({tool_input[:80]}…)")

                    _audit("tool_call", agent=self.name, tool=tool_name,
                           input=tool_input[:200])
                    # A-7: executor + run_with_hooks + Step-2 veto extracted to
                    # _execute_tool_with_hooks (propagates contextvars/cid into the
                    # worker thread; veto string becomes the tool result per §4.4).
                    result = await self._execute_tool_with_hooks(tool, tool_name, tool_input)
                    tool_calls_made += 1
                    _audit("tool_result", agent=self.name, tool=tool_name,
                           result_len=len(result))

                    # Phase 1 Step 3 §2.2 — stuck detection.
                    # Run in the executor so the worker thread can call
                    # ask_user.ask() synchronously without blocking the
                    # event loop on escalation. The helper returns a
                    # (possibly modified) result string with a warning
                    # banner injected, OR an escalation answer string if
                    # the user told the agent how to proceed.
                    if _stuck_enabled():
                        stuck_ctx = contextvars.copy_context()
                        result = await asyncio.get_event_loop().run_in_executor(
                            None, stuck_ctx.run,
                            self._handle_stuck_post_tool, tool_name,
                            tool_input, result)

                    # Capture real Google Docs URL from tool result
                    if tool_name == "google_docs_create" and "docs.google.com" in result:
                        url_match = re.search(r'https://docs\.google\.com/document/d/[A-Za-z0-9_-]+/edit', result)
                        if url_match:
                            self._gdoc_url = url_match.group(0)
                            log.info(f"[{self.name}] Captured real GDoc URL: {self._gdoc_url}")

                    if self.verbose:
                        print(f"[{self.name}] ← {result[:150]}…")

                    messages.append({"role": "assistant", "content": response})
                    messages.append({
                        "role": "user",
                        "content": (
                            f"Tool result from {tool_name}:\n{result}\n\n"
                            f"Continue. Use another TOOL or respond with FINAL: "
                            f"({self.max_tool_calls - tool_calls_made} tool calls remaining)."
                        )
                    })
                else:
                    messages.append({"role": "assistant", "content": response})
                    messages.append({
                        "role": "user",
                        "content": f"Tool '{tool_name}' not found. Available: {', '.join(t.name for t in self.tools)}. Try again or use FINAL:."
                    })
            else:
                # No TOOL/FINAL — treat as final
                if callback:
                    await _safe_cb(callback, {"agent": self.name, "status": "complete", "preview": response[:200]})
                return response

        return last_response

    # ── Phase 1 Step 3 §2.2 — stuck detection ──────────────────────────
    def _handle_stuck_post_tool(self, tool_name: str, tool_input: str,
                                 result: str) -> str:
        """Called from Agent.run after each tool result. Records the call
        in the per-agent ring buffer, detects N=3 / N+2=5 repeats, emits
        stuck_warning / stuck_escalated audit events, and either injects
        a soft warning into the result OR invokes ask_user for explicit
        user direction.

        Runs in a worker thread (via run_in_executor wrapping in
        Agent.run) so that ask_user.ask()'s threading.Event.wait()
        doesn't block the asyncio event loop.

        Returns the (possibly modified) result string the agent's ReAct
        loop will see as the tool result.
        """
        try:
            window, threshold, escalation_action = _load_stuck_config()
            args_hash = hashlib.sha1(
                (tool_input or "").encode("utf-8", errors="replace")
            ).hexdigest()[:8]
            key = (tool_name, args_hash)
            self._recent_calls.append(key)
            if len(self._recent_calls) > window:
                self._recent_calls = self._recent_calls[-window:]
            repeat_count = self._recent_calls.count(key)
            cid = _correlation_id_var.get()

            if repeat_count >= threshold + 2 and key not in self._stuck_escalated_keys:
                # Escalation: invoke ask_user (synchronously — we're in
                # a worker thread). Per §2.3.
                self._stuck_escalated_keys.add(key)
                action = escalation_action
                from codec_audit import log_event as _le
                try:
                    _le(
                        "stuck_escalated", "codec-agents",
                        f"Agent {self.name} stuck calling {tool_name}",
                        extra={"tool": tool_name,
                               "repeat_count": repeat_count,
                               "agent": self.name,
                               "action": action},
                        outcome="warning", level="warning",
                        tool=tool_name,
                        correlation_id=cid,
                    )
                except Exception as e:
                    log.warning("[stuck] escalation audit failed: %s", e)

                if action == "abort":
                    raise RuntimeError(
                        f"Stuck-abort: agent '{self.name}' called {tool_name} "
                        f"{repeat_count} times with the same args.")
                if action == "warn_only":
                    return result + (
                        f"\n\n[STUCK ESCALATED] Agent has called {tool_name} "
                        f"{repeat_count} times with the same args; warn_only "
                        f"mode — proceed with caution.")
                # Default: ask_user
                try:
                    from codec_ask_user import ask
                    user_directive = ask(
                        question=(
                            f"Agent '{self.name}' has called {tool_name} "
                            f"{repeat_count} times with the same args and keeps "
                            f"getting the same result. How should I proceed?"
                        ),
                        options=["Try a different approach", "Abandon the task",
                                 "Continue anyway"],
                        agent=self.name,
                        asked_from="crew",
                    )
                except Exception as e:
                    log.warning("[stuck] ask_user invoke failed: %s", e)
                    user_directive = "(ask_user failed — agent should self-recover)"
                return result + (
                    f"\n\n[STUCK — user said]: {user_directive}\n"
                    f"Adjust your strategy based on this directive.")

            if repeat_count >= threshold and key not in self._stuck_warned_keys:
                # Soft warning: inject a banner into the result. The LLM
                # will see this and (hopefully) try a different tool.
                self._stuck_warned_keys.add(key)
                from codec_audit import log_event as _le
                try:
                    _le(
                        "stuck_warning", "codec-agents",
                        f"Agent {self.name} repeating {tool_name}",
                        extra={"tool": tool_name,
                               "repeat_count": repeat_count,
                               "agent": self.name},
                        outcome="warning", level="warning",
                        tool=tool_name,
                        correlation_id=cid,
                    )
                except Exception as e:
                    log.warning("[stuck] warning audit failed: %s", e)
                return result + (
                    f"\n\n⚠ [STUCK WARNING] You've called {tool_name} "
                    f"{repeat_count} times with the same args. Try a "
                    f"different tool, different inputs, or wrap up with "
                    f"FINAL: — repeating won't help.")
            return result
        except Exception as e:
            log.warning("[stuck] handler failed (non-fatal): %s", e)
            return result


def _stuck_enabled() -> bool:
    """Read STUCK_DETECTION_ENABLED env var. Default true."""
    val = (os.environ.get("STUCK_DETECTION_ENABLED") or "true").strip().lower()
    return val not in ("false", "0", "no", "off")


def _load_stuck_config() -> tuple:
    """Load (window, threshold, escalation_action) from
    ~/.codec/config.json: stuck.{window, repeat_threshold,
    escalation_action}. Defaults: window=5, threshold=3, action=ask_user.
    Read each call so config edits take effect on PM2 restart."""
    try:
        import json as _json
        with open(os.path.expanduser("~/.codec/config.json")) as f:
            cfg = _json.load(f).get("stuck", {})
    except Exception:
        cfg = {}
    window = cfg.get("window")
    if not isinstance(window, int) or window < 2:
        window = 5
    threshold = cfg.get("repeat_threshold")
    if not isinstance(threshold, int) or threshold < 2:
        threshold = 3
    action = cfg.get("escalation_action")
    if action not in ("ask_user", "abort", "warn_only"):
        action = "ask_user"
    return window, threshold, action


async def _safe_cb(callback, data):
    """Call callback whether sync or async."""
    try:
        result = callback(data)
        if asyncio.iscoroutine(result):
            await result
    except Exception as e:
        print(f"[Agents] Callback error: {e}")


# ═══════════════════════════════════════════════════════════════
# CREW
# ═══════════════════════════════════════════════════════════════

@dataclass
class Crew:
    agents: List[Agent]
    tasks: List[str]
    mode: str = "sequential"    # "sequential" | "parallel"
    max_steps: int = 8
    allowed_tools: Optional[List[str]] = None  # Tool name allowlist; None = no restriction

    def __post_init__(self):
        """Enforce tool scoping: strip any agent tool not in the crew allowlist."""
        if self.allowed_tools is not None:
            allowed = set(self.allowed_tools)
            for agent in self.agents:
                before = len(agent.tools)
                agent.tools = [t for t in agent.tools if t.name in allowed]
                if agent.tools != agent.tools or before != len(agent.tools):
                    stripped = before - len(agent.tools)
                    if stripped:
                        print(f"[Crew] Scoped {agent.name}: removed {stripped} tool(s) outside allowlist")

    async def run(self, callback: Optional[Callable] = None) -> str:
        # One correlation_id per crew run. All nested agent_start / agent_finish /
        # tool_call / tool_result entries inherit this ID via the contextvar.
        cid_token = _correlation_id_var.set(_new_correlation_id())
        start = time.time()
        try:
            agent_names = [a.name for a in self.agents]
            _audit("crew_start", agents=agent_names, mode=self.mode,
                   allowed_tools=self.allowed_tools)
            if callback:
                await _safe_cb(callback, {"status": "started", "agents": len(self.agents), "tasks": len(self.tasks)})

            if self.mode == "sequential":
                context = ""
                results = []
                pairs = list(zip(self.agents, self.tasks))[:self.max_steps]
                for i, (agent, task) in enumerate(pairs):
                    if callback:
                        await _safe_cb(callback, {
                            "status": "agent_start", "agent": agent.name,
                            "task_num": i + 1, "total": len(pairs)
                        })
                    _audit("agent_start", agent=agent.name,
                           task_num=i + 1, total=len(pairs))
                    a_t0 = time.time()
                    try:
                        result = await agent.run(task, context=context, callback=callback)
                    except Exception as a_err:
                        _audit("agent_finish", agent=agent.name,
                               duration_ms=(time.time() - a_t0) * 1000.0,
                               outcome="error",
                               error_type=type(a_err).__name__,
                               error=str(a_err)[:500])
                        raise
                    _audit("agent_finish", agent=agent.name,
                           duration_ms=(time.time() - a_t0) * 1000.0,
                           result_len=len(result) if isinstance(result, str) else None)
                    results.append(result)
                    context = result

                final = results[-1] if results else "No results."

            elif self.mode == "parallel":
                # LS-3 / SR-2: enforce max_steps cap in parallel mode to match
                # sequential. Without this, Crew(mode="parallel", agents=[N])
                # spawned N concurrent agent.run coroutines unbounded.
                pairs = list(zip(self.agents, self.tasks))[:self.max_steps]
                coros = [a.run(t, callback=callback) for a, t in pairs]
                results = await asyncio.gather(*coros)
                final = "\n\n---\n\n".join(results)
            else:
                final = f"Unknown crew mode: {self.mode}"

            elapsed = int(time.time() - start)
            _audit("crew_complete", mode=self.mode, elapsed=elapsed,
                   duration_ms=(time.time() - start) * 1000.0,
                   result_len=len(final))
            if callback:
                await _safe_cb(callback, {"status": "complete", "elapsed": elapsed})
            return final
        except Exception as crew_err:
            _audit("crew_error", mode=self.mode,
                   duration_ms=(time.time() - start) * 1000.0,
                   outcome="error",
                   error_type=type(crew_err).__name__,
                   error=str(crew_err)[:500])
            raise
        finally:
            _correlation_id_var.reset(cid_token)


# ═══════════════════════════════════════════════════════════════
# MEMORY
# ═══════════════════════════════════════════════════════════════

def save_to_memory(session_name: str, task: str, result: str):
    try:
        import sys as _sys
        _dash = os.path.dirname(os.path.abspath(__file__))
        if _dash not in _sys.path:
            _sys.path.insert(0, _dash)
        from codec_memory import CodecMemory
        mem = CodecMemory()
        sid = f"agents_{session_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        task_text = task[:2000]
        result_text = result[:2000]
        if len(task) > 2000:
            task_text += f" [TRUNCATED from {len(task)} chars]"
        if len(result) > 2000:
            result_text += f" [TRUNCATED from {len(result)} chars]"
        mem.save(sid, "user",      f"[AGENT TASK] {task_text}")
        mem.save(sid, "assistant", f"[AGENT RESULT] {result_text}")
        print(f"[Agents] Saved to memory: {sid}")
    except Exception as e:
        print(f"[Agents] Memory save error: {e}")


# ═══════════════════════════════════════════════════════════════
# PRE-BUILT CREWS
# ═══════════════════════════════════════════════════════════════

async def _elevate_query(raw_topic: str) -> dict:
    """Refine a raw user query into an optimized research brief.

    Returns dict with:
      - refined_topic: clear one-line research question
      - search_queries: list of 4-6 diverse search queries
      - scope: what to include / exclude
      - angles: different perspectives to cover
    """
    today_str = datetime.now().strftime("%A, %B %d, %Y")
    current_year = datetime.now().year
    system = (
        f"You are a research query optimizer. Today's date is {today_str}. "
        f"Current year is {current_year}. The user will give you a rough, "
        "informal request. Your job is to interpret their TRUE INTENT — not "
        "the literal words — and produce a structured research brief.\n\n"
        "RULES:\n"
        "- Fix typos, grammar, and vague language\n"
        "- Identify what they ACTUALLY want to learn (not literal keyword interpretation)\n"
        "- Generate 4-6 diverse search queries that cover different angles\n"
        f"- Search queries MUST target the current year ({current_year}) or near-term content. "
        "Never generate queries about past years unless the user explicitly asks for historical analysis\n"
        "- If the user uses slang or ambiguous terms, interpret them in context\n"
        "- Never interpret casual words (like 'handful', 'bunch', 'couple') as topic keywords\n"
        "- Expand abbreviations and clarify jargon\n\n"
        "Respond in EXACTLY this format (no extra text):\n"
        "TOPIC: <one clear sentence describing the research goal>\n"
        "SCOPE: <what to include and what to exclude, 1-2 sentences>\n"
        "ANGLES: <3-4 different perspectives to investigate, comma-separated>\n"
        "QUERIES:\n"
        "1. <first search query>\n"
        "2. <second search query>\n"
        "3. <third search query>\n"
        "4. <fourth search query>\n"
        "5. <fifth search query (optional)>\n"
        "6. <sixth search query (optional)>"
    )
    try:
        # A-12 (PR-3E-async): codec_llm.acall (async non-stream; never-raise → ""
        # on failure → the parse below falls back to defaults, matching the
        # original except). Not queue-wrapped (the original wasn't either).
        import codec_llm
        text = await codec_llm.acall(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": f"Research request: {raw_topic}"},
            ],
            base_url=_qwen_base(), model=_qwen_model(),
            max_tokens=800, temperature=0.3, http=_async_http,
        )

        # Parse structured response
        result = {"refined_topic": raw_topic, "search_queries": [], "scope": "", "angles": ""}
        for line in text.split("\n"):
            line = line.strip()
            if line.upper().startswith("TOPIC:"):
                result["refined_topic"] = line.split(":", 1)[1].strip()
            elif line.upper().startswith("SCOPE:"):
                result["scope"] = line.split(":", 1)[1].strip()
            elif line.upper().startswith("ANGLES:"):
                result["angles"] = line.split(":", 1)[1].strip()
            elif re.match(r'^\d+\.\s', line):
                q = re.sub(r'^\d+\.\s*', '', line).strip()
                if q:
                    result["search_queries"].append(q)

        log.info(f"[QueryElevation] '{raw_topic[:60]}' → '{result['refined_topic'][:60]}'")
        if result["search_queries"]:
            log.info(f"[QueryElevation] {len(result['search_queries'])} search queries generated")
        return result
    except Exception as e:
        log.warning(f"[QueryElevation] Failed, using raw topic: {e}")
        return {"refined_topic": raw_topic, "search_queries": [], "scope": "", "angles": ""}


# C6 / SR-41: 12 crew builders moved to codec_crews.py. Imported here so
# CREW_REGISTRY (below) keeps working without changes — and so any
# external caller doing `from codec_agents import deep_research_crew`
# (etc.) keeps working.
from codec_crews import (  # noqa: F401 — re-exports for CREW_REGISTRY + back-compat
    deep_research_crew,
    daily_briefing_crew,
    trip_planner_crew,
    competitor_analysis_crew,
    email_handler_crew,
    social_media_crew,
    code_review_crew,
    data_analyst_crew,
    content_writer_crew,
    meeting_summarizer_crew,
    invoice_generator_crew,
    project_manager_crew,
)



CREW_REGISTRY = {
    "deep_research":       {"builder": deep_research_crew,      "description": "Comprehensive web research → Google Docs report",   "args": ["topic"]},
    "daily_briefing":      {"builder": daily_briefing_crew,     "description": "Morning briefing: calendar, weather, news",          "args": []},
    "trip_planner":        {"builder": trip_planner_crew,       "description": "Plan a trip: research + itinerary → Google Docs",    "args": ["destination", "dates"]},
    "competitor_analysis": {"builder": competitor_analysis_crew,"description": "Competitive analysis: web research → report",        "args": ["topic"]},
    "email_handler":       {"builder": email_handler_crew,      "description": "Read, categorize, and draft email replies",          "args": []},
    "social_media":        {"builder": social_media_crew,       "description": "Create platform-specific social media posts",        "args": ["topic"]},
    "code_review":         {"builder": code_review_crew,        "description": "Review code for bugs, security, quality",            "args": ["code"]},
    "data_analysis":       {"builder": data_analyst_crew,       "description": "Gather and analyze data on any topic",               "args": ["topic"]},
    "content_writer":      {"builder": content_writer_crew,     "description": "Write blog posts, articles, newsletters with research → Google Docs",  "args": ["topic"], "optional_args": {"content_type": "blog post", "audience": "general"}},
    "meeting_summarizer":  {"builder": meeting_summarizer_crew, "description": "Summarize meeting notes — actions, decisions, follow-ups → Google Docs + Calendar", "args": ["meeting_input"]},
    "invoice_generator":   {"builder": invoice_generator_crew,  "description": "Generate professional invoices from natural language → Google Docs",    "args": ["invoice_details"]},
    "project_manager":     {"builder": project_manager_crew,    "description": "Project status report from Calendar, Gmail, Drive, Tasks → Google Docs", "args": ["project"]},
}

AVAILABLE_CREWS = CREW_REGISTRY


# ═══════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════

async def run_crew(crew_name: str, callback=None, **kwargs) -> dict:
    if crew_name not in CREW_REGISTRY:
        return {
            "status": "error",
            "error": f"Unknown crew: {crew_name}. Available: {list(CREW_REGISTRY.keys())}"
        }
    reg = CREW_REGISTRY[crew_name]
    # Reset global doc URL to prevent leaks between crew runs
    global _last_gdoc_url
    _last_gdoc_url = None
    start = time.time()

    # ── Query Elevation: refine raw user input for research crews ──
    if crew_name in ("deep_research", "competitor_analysis") and kwargs.get("topic"):
        try:
            if callback:
                await _safe_cb(callback, {"agent": "QueryElevation", "type": "status",
                                          "message": "Refining your research query..."})
            elevated = await _elevate_query(kwargs["topic"])
            kwargs["_elevated"] = elevated
            if callback:
                await _safe_cb(callback, {"agent": "QueryElevation", "type": "status",
                                          "message": f"Research focus: {elevated.get('refined_topic', '')[:100]}"})
        except Exception as e:
            log.warning(f"Query elevation failed, proceeding with raw topic: {e}")

    try:
        crew   = reg["builder"](**kwargs)
        result = await crew.run(callback=callback)
        elapsed = int(time.time() - start)

        save_to_memory(crew_name, f"{crew_name}: {json.dumps(kwargs)}", result[:2000])
        return {"status": "complete", "result": result, "elapsed_seconds": elapsed, "crew": crew_name}
    except Exception as e:
        import traceback; traceback.print_exc()
        return {"status": "error", "error": str(e), "elapsed_seconds": int(time.time() - start)}


def list_crews() -> List[dict]:
    return [
        {"name": n, "description": r["description"], "args": r["args"]}
        for n, r in CREW_REGISTRY.items()
    ]


# ═══════════════════════════════════════════════════════════════
# CUSTOM AGENT RUNNER
# ═══════════════════════════════════════════════════════════════

async def run_custom_agent(
    name: str,
    role: str,
    tools: List[str],
    max_iterations: int = 8,
    task: str = "",
    callback=None,
) -> dict:
    """
    Run a single ad-hoc agent built from the chat UI.
    tools: list of tool names to give the agent.
    """
    max_iterations = min(max_iterations, 25)
    start = time.time()
    all_tools   = get_all_tools()
    tool_map    = {t.name: t for t in all_tools}
    sel_tools   = [tool_map[n] for n in tools if n in tool_map]

    agent = Agent(
        name        = name or "Custom",
        role        = role or "You are a helpful AI assistant. Complete the user's task.",
        tools       = sel_tools,
        max_tool_calls = max(1, max_iterations),
    )

    async def _cb(update):
        if callback:
            await _safe_cb(callback, update)

    try:
        result  = await agent.run(task, callback=_cb)
        elapsed = int(time.time() - start)
        save_to_memory(f"custom_{name}", task, result[:2000])
        return {"status": "complete", "result": result, "elapsed_seconds": elapsed}
    except Exception as e:
        import traceback; traceback.print_exc()
        return {"status": "error", "error": str(e), "elapsed_seconds": int(time.time() - start)}
