"""
CODEC Agents — Local multi-agent framework
Replaces CrewAI with ~300 lines. Zero external dependencies.
Uses CODEC skills as tools + Qwen 3.5 35B with thinking mode.
"""
import asyncio
import importlib.util
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import httpx

# ── CONFIG ──
CONFIG_PATH = os.path.expanduser("~/.codec/config.json")
SKILLS_DIR  = os.path.expanduser("~/.codec/skills")
DB_PATH     = os.path.expanduser("~/.q_memory.db")

def _cfg():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except Exception:
        return {}

def _qwen_url():
    c = _cfg()
    return c.get("llm_base_url", "http://localhost:8081/v1").rstrip("/") + "/chat/completions"

def _qwen_model():
    return _cfg().get("llm_model", "mlx-community/Qwen3.5-35B-A3B-4bit")

SERPER_API_KEY = _cfg().get("serper_api_key", os.environ.get("SERPER_API_KEY", ""))

# Captures the last Google Docs URL created — fallback if Writer forgets to echo it
_last_gdoc_url: Optional[str] = None


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
            return str(result)[:10000] if result else "No output."
        except Exception as e:
            return f"Tool error ({self.name}): {e}"


# ═══════════════════════════════════════════════════════════════
# BUILT-IN TOOLS
# ═══════════════════════════════════════════════════════════════

def _web_search(query: str) -> str:
    """Search via DuckDuckGo (free, no key) or Serper if configured in ~/.codec/config.json."""
    import sys as _sys
    _sys.path.insert(0, os.path.expanduser("~/codec-repo"))
    from codec_search import search, format_results
    results = search(query.strip(), max_results=10)
    return format_results(results, max_snippets=10)


def _web_fetch(url: str) -> str:
    import httpx as _hx
    try:
        r = _hx.get(url.strip(), timeout=30, follow_redirects=True)
        text = r.text
        text = re.sub(r'<script[^>]*>[\s\S]*?</script>', '', text)
        text = re.sub(r'<style[^>]*>[\s\S]*?</style>', '', text)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text[:10000]
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
            return f.read()[:10000]
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
    """Create a richly styled Google Doc — reuses codec_gdocs.create_google_doc()."""
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
    try:
        import sys as _sys
        _dash = os.path.dirname(os.path.abspath(__file__))
        if _dash not in _sys.path:
            _sys.path.insert(0, _dash)
        from codec_gdocs import create_google_doc
        doc_url = create_google_doc(title, content)
        if doc_url:
            _last_gdoc_url = doc_url
            return f"Google Doc created: {doc_url}"
        return "Google Docs error: doc creation returned None"
    except Exception as e:
        return f"Google Docs error: {e}"


def _shell_execute(cmd: str) -> str:
    import subprocess
    cmd = cmd.strip()
    from codec_config import DANGEROUS_PATTERNS
    for b in DANGEROUS_PATTERNS:
        if b in cmd.lower():
            return f"BLOCKED: dangerous command ({b})"
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                           timeout=30, cwd=os.path.expanduser("~"))
        out = r.stdout[:5000]
        if r.stderr:
            out += "\nSTDERR: " + r.stderr[:2000]
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
# SKILL LOADER
# ═══════════════════════════════════════════════════════════════

def load_skill_tools() -> List[Tool]:
    tools = []
    if not os.path.isdir(SKILLS_DIR):
        return tools
    for fname in sorted(os.listdir(SKILLS_DIR)):
        if not fname.endswith(".py"):
            continue
        try:
            path = os.path.join(SKILLS_DIR, fname)
            spec = importlib.util.spec_from_file_location(fname[:-3], path)
            mod  = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if hasattr(mod, "run") and hasattr(mod, "SKILL_DESCRIPTION"):
                tools.append(Tool(
                    name=fname[:-3],
                    description=getattr(mod, "SKILL_DESCRIPTION", fname),
                    fn=mod.run,
                ))
        except Exception as e:
            print(f"[Agents] Skill load error {fname}: {e}")
    print(f"[Agents] Loaded {len(tools)} skill tools")
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

    async def run(self, task: str, context: str = "", callback: Optional[Callable] = None) -> str:
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

        async with httpx.AsyncClient(timeout=180) as client:
            for _ in range(self.max_tool_calls + 3):
                payload = {
                    "model": _qwen_model(),
                    "messages": messages,
                    "max_tokens": 4000,
                    "temperature": 0.7,
                    "chat_template_kwargs": {"enable_thinking": self.thinking},
                }
                try:
                    r = await client.post(_qwen_url(), json=payload,
                                          headers={"Content-Type": "application/json"})
                    data = r.json()
                    response = data["choices"][0]["message"]["content"].strip()
                except Exception as e:
                    return f"LLM error: {e}"

                # Strip thinking tags
                response = re.sub(r'<think>[\s\S]*?</think>', '', response).strip()
                last_response = response

                if self.verbose:
                    print(f"[{self.name}] {response[:200]}…")

                # FINAL answer — rsplit gets the LAST occurrence (skips quoted prompt text)
                if "FINAL:" in response:
                    final = response.rsplit("FINAL:", 1)[1].strip()
                    if callback:
                        await _safe_cb(callback, {"agent": self.name, "status": "complete", "preview": final[:200]})
                    return final

                # TOOL call
                m = re.search(r'TOOL:\s*(\S+)\s*\nINPUT:\s*([\s\S]*?)(?=\nTOOL:|\nFINAL:|$)', response)
                if m and tool_calls_made < self.max_tool_calls:
                    tool_name  = m.group(1).strip()
                    tool_input = m.group(2).strip()
                    tool = next((t for t in self.tools if t.name == tool_name), None)

                    if tool:
                        if callback:
                            await _safe_cb(callback, {
                                "agent": self.name, "status": "tool_call",
                                "tool": tool_name, "input": tool_input[:100]
                            })
                        if self.verbose:
                            print(f"[{self.name}] → {tool_name}({tool_input[:80]}…)")

                        loop = asyncio.get_event_loop()
                        result = await loop.run_in_executor(None, tool.run, tool_input)
                        tool_calls_made += 1

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

    async def run(self, callback: Optional[Callable] = None) -> str:
        start = time.time()
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
                result = await agent.run(task, context=context, callback=callback)
                results.append(result)
                context = result

            final = results[-1] if results else "No results."

        elif self.mode == "parallel":
            coros = [a.run(t, callback=callback) for a, t in zip(self.agents, self.tasks)]
            results = await asyncio.gather(*coros)
            final = "\n\n---\n\n".join(results)
        else:
            final = f"Unknown crew mode: {self.mode}"

        elapsed = int(time.time() - start)
        if callback:
            await _safe_cb(callback, {"status": "complete", "elapsed": elapsed})
        return final


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
        mem.save(sid, "user",      f"[AGENT TASK] {task[:2000]}")
        mem.save(sid, "assistant", f"[AGENT RESULT] {result[:2000]}")
        print(f"[Agents] Saved to memory: {sid}")
    except Exception as e:
        print(f"[Agents] Memory save error: {e}")


# ═══════════════════════════════════════════════════════════════
# PRE-BUILT CREWS
# ═══════════════════════════════════════════════════════════════

def deep_research_crew(**kwargs) -> Crew:
    all_tools = get_all_tools()
    search_tools = [t for t in all_tools if t.name in ("web_search", "web_fetch")]
    write_tools  = [t for t in all_tools if t.name in ("google_docs_create",)]
    topic = kwargs.get("topic", "the given topic")

    researcher = Agent(
        name="Researcher",
        role=(
            "You are an elite research analyst. Find comprehensive, accurate, up-to-date information. "
            "Search broadly first (3-5 queries), then fetch the most relevant sources. "
            "Extract key facts, statistics, expert opinions, and recent developments."
        ),
        tools=search_tools, max_tool_calls=5,
    )
    writer = Agent(
        name="Writer",
        role=(
            "You are a professional report writer. Synthesize research into a comprehensive "
            "well-structured report: Executive Summary, Key Findings, Analysis, Conclusion, Sources. "
            "Write 2000-5000 words in markdown. Cite sources inline. "
            "Save to Google Docs when done. "
            "IMPORTANT: Your FINAL response MUST include the exact Google Docs URL returned by the tool."
        ),
        tools=write_tools, max_tool_calls=2,
    )
    return Crew(
        agents=[researcher, writer],
        tasks=[
            f"Research thoroughly: {topic}\n"
            f"Search at least 3 different angles. Fetch key source pages and extract details.",
            f"Write a comprehensive report about: {topic}\n"
            f"Use research context provided. Save to Google Docs with title: "
            f"'CODEC Research: {topic[:80]} — {datetime.now().strftime('%Y-%m-%d')}'\n"
            f"After saving, your FINAL response MUST begin with the Google Docs URL on its own line."
        ],
    )


def daily_briefing_crew(**kwargs) -> Crew:
    all_tools = get_all_tools()
    scout_tools = [t for t in all_tools if t.name in ("google_calendar", "weather", "web_search")]
    scout = Agent(
        name="Scout",
        role=(
            "You are M's daily briefing assistant. Check today's calendar, current weather, "
            "and top news. Compile a concise 2-minute spoken briefing. "
            "Write as natural speech — no markdown, no bullet points."
        ),
        tools=scout_tools, max_tool_calls=4,
    )
    return Crew(
        agents=[scout],
        tasks=[
            "Compile today's daily briefing for M:\n"
            "1. Calendar — what meetings/events today?\n"
            "2. Weather — what's it like right now?\n"
            "3. News — any major headlines (search 'top news today')?\n"
            "Keep it conversational and brief."
        ],
    )


def trip_planner_crew(**kwargs) -> Crew:
    all_tools = get_all_tools()
    destination = kwargs.get("destination", "the destination")
    dates = kwargs.get("dates", "")
    research_tools = [t for t in all_tools if t.name in ("web_search", "web_fetch")]
    plan_tools     = [t for t in all_tools if t.name in ("google_docs_create", "google_calendar")]

    researcher = Agent(
        name="Travel Researcher",
        role="Research travel destinations. Find flights, hotels, attractions, restaurants, local tips.",
        tools=research_tools, max_tool_calls=5,
    )
    planner = Agent(
        name="Trip Planner",
        role=(
            "Create a detailed day-by-day itinerary. Organize into morning/afternoon/evening. "
            "Include estimated costs and travel times. Save to Google Docs."
        ),
        tools=plan_tools, max_tool_calls=2,
    )
    return Crew(
        agents=[researcher, planner],
        tasks=[
            f"Research a trip to {destination} {dates}. "
            f"Find: best flights, top hotels (mid-range), must-see attractions, restaurants, transport.",
            f"Create a day-by-day itinerary for {destination} {dates}. "
            f"Save to Google Docs: 'Trip Plan: {destination} — {datetime.now().strftime('%Y-%m-%d')}'"
        ],
    )


def competitor_analysis_crew(**kwargs) -> Crew:
    all_tools = get_all_tools()
    topic = kwargs.get("topic", "the market")
    web_tools   = [t for t in all_tools if t.name in ("web_search", "web_fetch")]
    write_tools = [t for t in all_tools if t.name in ("google_docs_create",)]

    scout = Agent(
        name="Web Scout",
        role="Research competitors and market landscape. Find products, pricing, market position, recent news, reviews.",
        tools=web_tools, max_tool_calls=5,
    )
    strategist = Agent(
        name="Strategist",
        role=(
            "Synthesize research into a strategic analysis report. "
            "Include SWOT, competitive positioning, and actionable recommendations. Save to Google Docs."
        ),
        tools=write_tools, max_tool_calls=2,
    )
    return Crew(
        agents=[scout, strategist],
        tasks=[
            f"Research competitors for: {topic}. Find 5+ competitors with products, pricing, strengths, weaknesses.",
            f"Write a strategic competitive analysis. SWOT + recommendations. "
            f"Save to Google Docs: 'Competitor Analysis: {topic[:60]} — {datetime.now().strftime('%Y-%m-%d')}'"
        ],
    )


def email_handler_crew(**kwargs) -> Crew:
    all_tools = get_all_tools()
    gmail_tools = [t for t in all_tools if t.name in ("google_gmail",)]

    reader = Agent(
        name="Email Reader",
        role=(
            "Read unread emails. Categorize each as URGENT, NORMAL, LOW, or SPAM. "
            "For each: sender, subject, category, 1-line summary."
        ),
        tools=gmail_tools, max_tool_calls=3,
    )
    responder = Agent(
        name="Email Responder",
        role=(
            "Draft brief professional replies for urgent emails. "
            "Suggest 1-line action for normal emails. Keep M's voice: direct, confident, clear."
        ),
        tools=gmail_tools, max_tool_calls=3,
    )
    return Crew(
        agents=[reader, responder],
        tasks=[
            "Check unread emails. Categorize each by urgency. List them all.",
            "Draft replies for urgent emails. Summarize actions for the rest.",
        ],
    )


def social_media_crew(**kwargs) -> Crew:
    all_tools = get_all_tools()
    topic = kwargs.get("topic", "the given topic")
    search_tools = [t for t in all_tools if t.name in ("web_search", "web_fetch")]
    write_tools  = [t for t in all_tools if t.name in ("google_docs_create",)]

    trend_scout = Agent(
        name="Trend Scout",
        role=(
            "You are a social media trend analyst. Research trending topics, hashtags, "
            "and viral content. Find what's popular right now on Twitter, LinkedIn, and Instagram. "
            "Identify key angles, hashtags, and audience interests."
        ),
        tools=search_tools, max_tool_calls=5,
    )
    content_creator = Agent(
        name="Content Creator",
        role=(
            "You are an expert social media copywriter. Write platform-specific posts: "
            "Twitter (max 280 chars, punchy, with hashtags), "
            "LinkedIn (professional tone, 150-300 words, insight-driven), "
            "Instagram (visual description + engaging caption + hashtags). "
            "Save all 3 posts to a Google Doc."
        ),
        tools=write_tools, max_tool_calls=2,
    )
    return Crew(
        agents=[trend_scout, content_creator],
        tasks=[
            f"Research trending content about: {topic}\n"
            f"Find trending hashtags, popular angles, viral formats, and audience interests.",
            f"Write 3 platform-specific posts (Twitter, LinkedIn, Instagram) about: {topic}. "
            "Save all to a Google Doc with title: "
            "'Social Media Posts: " + topic[:60] + " — " + datetime.now().strftime('%Y-%m-%d') + "'"
        ],
    )


def code_review_crew(**kwargs) -> Crew:
    all_tools = get_all_tools()
    code = kwargs.get("code", "")
    read_tools     = [t for t in all_tools if t.name in ("file_read",)]
    audit_tools    = [t for t in all_tools if t.name in ("file_read", "web_search")]
    improve_tools  = [t for t in all_tools if t.name in ("file_read", "file_write")]

    bug_hunter = Agent(
        name="Bug Hunter",
        role=(
            "You are an expert software engineer specializing in finding bugs. "
            "Carefully analyze code for logic errors, off-by-one errors, null pointer issues, "
            "incorrect assumptions, race conditions, and edge cases. Be thorough and specific."
        ),
        tools=read_tools, max_tool_calls=3,
    )
    security_auditor = Agent(
        name="Security Auditor",
        role=(
            "You are a security expert. Identify security vulnerabilities including: "
            "injection flaws (SQL, command, XSS), insecure deserialization, authentication issues, "
            "exposed secrets, insecure dependencies, and OWASP Top 10 issues. "
            "Reference CVEs or best practices where relevant."
        ),
        tools=audit_tools, max_tool_calls=4,
    )
    clean_coder = Agent(
        name="Clean Coder",
        role=(
            "You are a software architect focused on code quality. Suggest improvements for: "
            "readability, naming conventions, function decomposition, DRY principles, "
            "design patterns, documentation, and maintainability. "
            "Provide concrete refactoring suggestions."
        ),
        tools=improve_tools, max_tool_calls=3,
    )
    return Crew(
        agents=[bug_hunter, security_auditor, clean_coder],
        tasks=[
            f"Review this code for bugs, logic errors, and edge cases:\n{code[:3000]}",
            f"Check the code for security vulnerabilities",
            f"Suggest improvements for readability and maintainability",
        ],
    )


def data_analyst_crew(**kwargs) -> Crew:
    all_tools = get_all_tools()
    topic = kwargs.get("topic", "the given topic")
    tool_map = {t.name: t for t in all_tools}
    gather_tool_names = ["web_search", "web_fetch"]
    if "google_sheets" in tool_map:
        gather_tool_names.append("google_sheets")
    gather_tools = [tool_map[n] for n in gather_tool_names if n in tool_map]
    write_tools  = [t for t in all_tools if t.name in ("google_docs_create",)]

    data_gatherer = Agent(
        name="Data Gatherer",
        role=(
            "You are a data research specialist. Search for quantitative data, statistics, "
            "benchmarks, survey results, and research findings. Find multiple credible sources. "
            "Extract numbers, percentages, trends over time, and comparative data."
        ),
        tools=gather_tools, max_tool_calls=5,
    )
    analyst = Agent(
        name="Analyst",
        role=(
            "You are a data analyst and business intelligence expert. Analyze the data provided, "
            "identify trends, patterns, outliers, and correlations. Create actionable insights "
            "with supporting evidence. Write a structured insights report and save to Google Docs."
        ),
        tools=write_tools, max_tool_calls=2,
    )
    return Crew(
        agents=[data_gatherer, analyst],
        tasks=[
            f"Gather data and statistics about: {topic}\n"
            f"Find key metrics, benchmarks, historical trends, and comparative data from credible sources.",
            f"Analyze the data and write an insights report. Save to Google Docs with title: "
            "'Data Analysis: " + topic[:60] + " — " + datetime.now().strftime('%Y-%m-%d') + "'"
        ],
    )


# ═══════════════════════════════════════════════════════════════
# CREW REGISTRY
# ═══════════════════════════════════════════════════════════════

CREW_REGISTRY = {
    "deep_research":       {"builder": deep_research_crew,      "description": "Comprehensive web research → Google Docs report",   "args": ["topic"]},
    "daily_briefing":      {"builder": daily_briefing_crew,     "description": "Morning briefing: calendar, weather, news",          "args": []},
    "trip_planner":        {"builder": trip_planner_crew,       "description": "Plan a trip: research + itinerary → Google Docs",    "args": ["destination", "dates"]},
    "competitor_analysis": {"builder": competitor_analysis_crew,"description": "Competitive analysis: web research → report",        "args": ["topic"]},
    "email_handler":       {"builder": email_handler_crew,      "description": "Read, categorize, and draft email replies",          "args": []},
    "social_media":        {"builder": social_media_crew,       "description": "Create platform-specific social media posts",        "args": ["topic"]},
    "code_review":         {"builder": code_review_crew,        "description": "Review code for bugs, security, quality",            "args": ["code"]},
    "data_analysis":       {"builder": data_analyst_crew,       "description": "Gather and analyze data on any topic",               "args": ["topic"]},
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
    start = time.time()
    try:
        crew   = reg["builder"](**kwargs)
        result = await crew.run(callback=callback)
        elapsed = int(time.time() - start)

        # If Writer forgot to include the Google Docs URL, inject it from the captured variable
        if "docs.google.com" not in result and _last_gdoc_url:
            result = f"{_last_gdoc_url}\n\n{result}"

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
