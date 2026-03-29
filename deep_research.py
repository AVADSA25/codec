"""
CODEC Deep Research — own Agent framework + DuckDuckGo + Qwen + Google Docs
"""
import os, re, sys, time
from datetime import datetime
from crewai import Agent, Task, Crew, Process, LLM
import litellm
import requests as rq

sys.path.insert(0, os.path.expanduser("~/codec-repo"))

litellm.drop_params = True

# mlx-lm enforces: system messages MUST be at position 0.
# CrewAI injects system messages mid-conversation during tool use — patch
# litellm.completion to merge all system messages to the front before every call.
_orig_completion = litellm.completion

def _completion_fixed(**kwargs):
    msgs = kwargs.get("messages", [])
    if msgs:
        sys_msgs = [m for m in msgs if m.get("role") == "system"]
        other_msgs = [m for m in msgs if m.get("role") != "system"]
        if sys_msgs and (len(sys_msgs) > 1 or msgs[0].get("role") != "system"):
            combined = "\n\n".join(m["content"] for m in sys_msgs)
            kwargs["messages"] = [{"role": "system", "content": combined}] + other_msgs
    # Force thinking mode OFF — Qwen3.5 defaults to thinking ON which bloats
    # responses with <think> tags and causes empty/None responses for CrewAI
    extra_body = kwargs.get("extra_body") or {}
    extra_body.setdefault("chat_template_kwargs", {})["enable_thinking"] = False
    kwargs["extra_body"] = extra_body
    return _orig_completion(**kwargs)

litellm.completion = _completion_fixed

# ── CONFIG ──
PEXELS_API_KEY = "uMkQte71lNmkAfylWcfFY5k8SuUPEPqsZoVEcEJ4kaPIpNf8qzROxJNi"

llm = LLM(
    model="openai/mlx-community/Qwen3.5-35B-A3B-4bit",
    base_url="http://localhost:8081/v1",
    api_key="not-needed",
    temperature=0.7,
    max_tokens=32000,
)

# ── TOOLS ──
from crewai.tools import BaseTool

class _DDGSearchTool(BaseTool):
    name: str = "web_search"
    description: str = (
        "Search the web for any topic. "
        "Uses DuckDuckGo (free, no API key). "
        "Input: a plain search query string. "
        "Returns titles, snippets, and URLs."
    )

    def _run(self, query: str) -> str:
        from codec_search import search, format_results
        results = search(query.strip(), max_results=10)
        return format_results(results, max_snippets=10)

search_tool = _DDGSearchTool()


# ── AGENTS ──
def build_crew(topic: str):
    researcher = Agent(
        role="Senior Research Analyst",
        goal=f"Find comprehensive, accurate, up-to-date information about: {topic}",
        backstory="You are an elite researcher who finds primary sources, cross-references facts, and identifies key insights. You search broadly first, then dive deep into the most relevant sources.",
        tools=[search_tool],
        llm=llm,
        verbose=True,
        max_iter=20,
        allow_delegation=False
    )

    writer = Agent(
        role="Research Report Writer",
        goal=f"Synthesize research findings into a comprehensive, well-structured report about: {topic}",
        backstory="You are a professional report writer. You organize information logically with clear sections, cite sources, highlight key findings, and write in a clear professional style. Reports should be comprehensive and thorough — aim for 8000-12000 words across 10-15 well-developed sections.",
        llm=llm,
        verbose=True,
        allow_delegation=False
    )

    research_task = Task(
        description=f"""Research the following topic thoroughly: {topic}

        1. Search for the topic broadly first (5-8 searches with different angles)
        2. Identify the top 15-20 most relevant and authoritative sources
        3. Extract key facts, statistics, expert opinions, and recent developments
        4. Dive deep into each major subtopic with dedicated follow-up searches
        5. Note any controversies, competing viewpoints, or conflicting data
        6. Compile all findings with source URLs — the more detail, the better""",
        expected_output="Extensive research notes with facts, statistics, quotes, source URLs, and deep subtopic coverage — enough material for a 10,000-word report",
        agent=researcher
    )

    writing_task = Task(
        description=f"""Write a comprehensive research report about: {topic}

        Structure:
        1. Executive Summary (2-3 paragraphs)
        2. Introduction & Background
        3. Key Findings (multiple sections as needed)
        4. Analysis & Implications
        5. Conclusion
        6. Sources (numbered list with URLs)

        Requirements:
        - 8000-12000 words minimum — this is a full long-form report, not a summary
        - Each section should have 3-6 paragraphs of substantive content
        - Professional tone
        - Include statistics, data, and specific numbers where available
        - Cite sources inline (e.g., [1], [2])
        - Use markdown formatting (headers, bold, lists)
        - Do NOT truncate or cut sections short — complete every section fully""",
        expected_output="A complete, well-structured research report in markdown format with citations",
        agent=writer
    )

    crew = Crew(
        agents=[researcher, writer],
        tasks=[research_task, writing_task],
        process=Process.sequential,
        verbose=True
    )

    return crew


# ── PEXELS IMAGES ──────────────────────────────────────────────────────────────
_GENERIC_HEADING_WORDS = {
    "executive", "summary", "introduction", "background", "conclusion",
    "sources", "references", "key", "findings", "analysis", "implications",
    "discussion", "results", "overview", "appendix", "abstract",
}

# ~1 section of body text in a standard Google Doc (~700 words)
_CHARS_PER_IMAGE = 3_500
_MAX_IMAGES      = 7   # hero + up to 6 section images


def _pexels_fetch_one(query: str, page_offset: int = 0) -> str | None:
    """Fetch a single Pexels landscape photo URL for a query."""
    try:
        resp = rq.get(
            "https://api.pexels.com/v1/search",
            params={"query": query, "per_page": 5, "page": page_offset + 1,
                    "orientation": "landscape"},
            headers={"Authorization": PEXELS_API_KEY},
            timeout=10,
        )
        photos = resp.json().get("photos", [])
        if photos:
            return photos[0]["src"]["large2x"]
    except Exception as e:
        print(f"[PEXELS] Error for query '{query}': {e}")
    return None


def _heading_keywords(heading: str) -> str:
    """Extract meaningful keywords from a heading string."""
    cleaned = re.sub(r"^\d+[\.\d]*\s+", "", heading)
    words = [w for w in cleaned.split()
             if w.lower() not in _GENERIC_HEADING_WORDS and len(w) > 3]
    return " ".join(words[:2])


def _section_body_text(positions: list, after_idx: int, max_chars: int = 600) -> str:
    """Return body text immediately following after_idx (until the next h2)."""
    collecting, parts, total = False, [], 0
    for p in positions:
        if p["start"] >= after_idx and not collecting:
            collecting = True
        if collecting and p["type"] == "h2" and p["start"] > after_idx:
            break
        if collecting and p["type"] == "body":
            parts.append(p["text"])
            total += len(p["text"])
            if total >= max_chars:
                break
    return " ".join(parts)


def _smart_query(topic_base: str, positions: list, insert_idx: int) -> str:
    """
    Derive a contextual Pexels search query for the section at insert_idx.
    Detects data/stats → analytics, hardware → chips, privacy → security, etc.
    """
    # Nearest heading at or before insert_idx
    heading = ""
    for p in positions:
        if p["type"] in ("h2", "h3") and p["start"] <= insert_idx:
            heading = p["text"]

    body   = _section_body_text(positions, insert_idx)
    ctx    = (heading + " " + body).lower()
    kw     = _heading_keywords(heading)

    if any(w in ctx for w in ["statistic", "percent", "%", "billion", "million",
                               "market", "adoption", "growth", "forecast", "survey",
                               "gartner", "idc", "report"]):
        return f"{topic_base} data analytics business"
    if any(w in ctx for w in ["hardware", "chip", "processor", "npu", "gpu",
                               "silicon", "device", "laptop", "memory"]):
        return f"{topic_base} computer hardware chip"
    if any(w in ctx for w in ["privacy", "security", "encryption", "gdpr",
                               "compliance", "breach", "zero-data", "exfiltration"]):
        return f"{topic_base} cybersecurity data protection"
    if any(w in ctx for w in ["global", "worldwide", "international", "enterprise",
                               "fortune 500", "deployment", "adoption"]):
        return f"{topic_base} global enterprise technology"
    if any(w in ctx for w in ["energy", "power", "environment", "carbon",
                               "sustainable", "green", "watt"]):
        return f"{topic_base} sustainable energy environment"
    if any(w in ctx for w in ["medical", "health", "drug", "clinical", "pharma",
                               "patient", "diagnosis"]):
        return f"{topic_base} medical health technology"
    if any(w in ctx for w in ["legal", "regulatory", "law", "regulation", "act",
                               "executive order", "liability"]):
        return f"{topic_base} law regulation compliance"
    if any(w in ctx for w in ["future", "forecast", "predict", "trend", "next",
                               "horizon", "emerging", "revolution"]):
        return f"{topic_base} future innovation technology"
    if kw:
        return f"{topic_base} {kw}"
    return f"{topic_base} technology"


def _find_image_positions(positions: list) -> tuple:
    """
    Return (hero_idx, [additional_idxs...]) for image insertion.
    Rule: hero after h1, then one image per ~3 pages (_CHARS_PER_IMAGE chars).
    """
    h1_list  = [p for p in positions if p["type"] == "h1"]
    hero_idx = h1_list[0]["end"] if h1_list else 2

    additional   = []
    next_thresh  = _CHARS_PER_IMAGE
    running      = 0

    for p in positions:
        running += p["end"] - p["start"]
        if running >= next_thresh and p["type"] in ("h2", "h3"):
            if not additional or p["start"] != additional[-1]:
                additional.append(p["start"])
                next_thresh += _CHARS_PER_IMAGE
                if len(additional) >= _MAX_IMAGES - 1:
                    break

    return hero_idx, additional


# ── GOOGLE DOCS ────────────────────────────────────────────────────────────────
def _parse_markdown(content: str) -> list:
    """
    Parse a markdown string into a list of blocks:
    {'text': str, 'type': 'h1'|'h2'|'h3'|'body'|'empty'}
    Inline markdown (bold, italic, links, code) is stripped to plain text.
    """
    blocks = []
    for line in content.split("\n"):
        if line.startswith("# "):
            blocks.append({"text": line[2:].strip(), "type": "h1"})
        elif line.startswith("## "):
            blocks.append({"text": line[3:].strip(), "type": "h2"})
        elif line.startswith("### "):
            blocks.append({"text": line[4:].strip(), "type": "h3"})
        elif line.strip() in ("---", "***", "___"):
            blocks.append({"text": "", "type": "empty"})
        elif line.strip() == "":
            blocks.append({"text": "", "type": "empty"})
        else:
            text = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
            text = re.sub(r"\*(.+?)\*", r"\1", text)
            text = re.sub(r"`(.+?)`", r"\1", text)
            text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
            blocks.append({"text": text, "type": "body"})
    return blocks


def create_google_doc(title: str, content: str) -> str:
    """
    Create a styled Google Doc with:
    - h1 centred, 28pt dark
    - h2 15pt CODEC-orange with bottom border
    - h3 13pt slate bold
    - body 11pt, 150% line spacing
    - Up to 3 Pexels images: hero (full-width center), mid (left), late (right)
      each with a distinct search query derived from the nearest section heading
    """
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    TOKEN_PATH = os.path.expanduser("~/.codec/google_token.json")

    # ── colour palette ──
    DARK   = {"red": 0.082, "green": 0.082, "blue": 0.137}   # #151522
    ORANGE = {"red": 0.910, "green": 0.443, "blue": 0.102}   # #E8711A
    SLATE  = {"red": 0.173, "green": 0.243, "blue": 0.314}   # #2C3E50
    GRAY   = {"red": 0.400, "green": 0.400, "blue": 0.400}   # #666666

    try:
        creds = Credentials.from_authorized_user_file(TOKEN_PATH)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(TOKEN_PATH, "w") as f:
                f.write(creds.to_json())

        docs_service = build("docs", "v1", credentials=creds)

        # Create the document
        doc = docs_service.documents().create(body={"title": title}).execute()
        doc_id  = doc["documentId"]
        doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"

        # ── BUILD PLAIN TEXT + POSITION MAP ──────────────────────────
        blocks = _parse_markdown(content)
        full_text = ""
        positions = []   # list of {start, end, type, text}
        idx = 1          # Google Docs indices start at 1

        for block in blocks:
            line = block["text"] + "\n"
            positions.append({
                "start": idx,
                "end":   idx + len(line),
                "type":  block["type"],
                "text":  block["text"],
            })
            full_text += line
            idx += len(line)

        # ── BATCH 1: INSERT TEXT + APPLY STYLES ──────────────────────
        reqs = [{"insertText": {"location": {"index": 1}, "text": full_text}}]

        for p in positions:
            s, e, t = p["start"], p["end"], p["type"]
            te = e - 1   # text end (excluding trailing \n)
            if not p["text"]:
                continue

            if t == "h1":
                reqs += [
                    {"updateParagraphStyle": {
                        "range": {"startIndex": s, "endIndex": e},
                        "paragraphStyle": {
                            "namedStyleType": "HEADING_1",
                            "alignment": "CENTER",
                            "spaceAbove": {"magnitude": 0,  "unit": "PT"},
                            "spaceBelow": {"magnitude": 10, "unit": "PT"},
                        },
                        "fields": "namedStyleType,alignment,spaceAbove,spaceBelow",
                    }},
                    {"updateTextStyle": {
                        "range": {"startIndex": s, "endIndex": te},
                        "textStyle": {
                            "fontSize":        {"magnitude": 28, "unit": "PT"},
                            "bold":            True,
                            "foregroundColor": {"color": {"rgbColor": DARK}},
                        },
                        "fields": "fontSize,bold,foregroundColor",
                    }},
                ]

            elif t == "h2":
                reqs += [
                    {"updateParagraphStyle": {
                        "range": {"startIndex": s, "endIndex": e},
                        "paragraphStyle": {
                            "namedStyleType": "HEADING_2",
                            "spaceAbove": {"magnitude": 22, "unit": "PT"},
                            "spaceBelow": {"magnitude":  5, "unit": "PT"},
                            "borderBottom": {
                                "color":     {"color": {"rgbColor": ORANGE}},
                                "width":     {"magnitude": 1.5, "unit": "PT"},
                                "padding":   {"magnitude": 4,   "unit": "PT"},
                                "dashStyle": "SOLID",
                            },
                        },
                        "fields": "namedStyleType,spaceAbove,spaceBelow,borderBottom",
                    }},
                    {"updateTextStyle": {
                        "range": {"startIndex": s, "endIndex": te},
                        "textStyle": {
                            "fontSize":        {"magnitude": 15, "unit": "PT"},
                            "bold":            True,
                            "foregroundColor": {"color": {"rgbColor": ORANGE}},
                        },
                        "fields": "fontSize,bold,foregroundColor",
                    }},
                ]

            elif t == "h3":
                reqs += [
                    {"updateParagraphStyle": {
                        "range": {"startIndex": s, "endIndex": e},
                        "paragraphStyle": {
                            "spaceAbove": {"magnitude": 14, "unit": "PT"},
                            "spaceBelow": {"magnitude":  4, "unit": "PT"},
                        },
                        "fields": "spaceAbove,spaceBelow",
                    }},
                    {"updateTextStyle": {
                        "range": {"startIndex": s, "endIndex": te},
                        "textStyle": {
                            "fontSize":        {"magnitude": 13, "unit": "PT"},
                            "bold":            True,
                            "italic":          False,
                            "foregroundColor": {"color": {"rgbColor": SLATE}},
                        },
                        "fields": "fontSize,bold,italic,foregroundColor",
                    }},
                ]

            elif t == "body":
                reqs += [
                    {"updateParagraphStyle": {
                        "range": {"startIndex": s, "endIndex": e},
                        "paragraphStyle": {
                            "lineSpacing": 150,
                            "spaceBelow": {"magnitude": 8, "unit": "PT"},
                        },
                        "fields": "lineSpacing,spaceBelow",
                    }},
                    {"updateTextStyle": {
                        "range": {"startIndex": s, "endIndex": te},
                        "textStyle": {
                            "fontSize":        {"magnitude": 11, "unit": "PT"},
                            "foregroundColor": {"color": {"rgbColor": DARK}},
                        },
                        "fields": "fontSize,foregroundColor",
                    }},
                ]

        docs_service.documents().batchUpdate(
            documentId=doc_id, body={"requests": reqs}
        ).execute()

        # ── BATCH 2: INSERT IMAGES ────────────────────────────────────
        # Dynamic: 1 image per ~3 pages, hero always at top
        hero_idx, additional_idxs = _find_image_positions(positions)

        # Build (idx, query) pairs
        topic_base = " ".join(
            title.replace("CODEC Research:", "").replace(":", "")
                 .replace("—", "").split()[:5]
        ).strip()

        img_tasks = [(hero_idx, topic_base)]   # hero gets broad topic query
        for idx in additional_idxs:
            img_tasks.append((idx, _smart_query(topic_base, positions, idx)))

        # Image size/alignment cycle: hero full-width, then alternate left/right
        # (width_pt, height_pt, alignment)
        SPEC_HERO       = (468, 260, "CENTER")
        SPEC_CYCLE      = [
            (310, 174, "START"),   # left,  smaller
            (348, 196, "END"),     # right, medium
            (310, 174, "START"),
            (348, 196, "END"),
            (420, 236, "CENTER"),  # occasional full-width for impact
            (310, 174, "START"),
        ]

        # Fetch images with distinct queries (page_offset avoids identical picks)
        insert_points = []
        for i, (insert_idx, query) in enumerate(img_tasks):
            url = _pexels_fetch_one(query, page_offset=i % 3)
            if not url:
                continue
            w, h, align = SPEC_HERO if i == 0 else SPEC_CYCLE[(i - 1) % len(SPEC_CYCLE)]
            insert_points.append({"idx": insert_idx, "url": url,
                                  "w": w, "h": h, "align": align})

        # Insert BOTTOM → TOP to avoid index shifts
        insert_points.sort(key=lambda x: x["idx"], reverse=True)

        img_reqs = []
        for ip in insert_points:
            n = ip["idx"]
            # 1. Dedicated paragraph for the image
            img_reqs.append({"insertText": {"location": {"index": n}, "text": "\n"}})
            # 2. Place image into that paragraph
            img_reqs.append({"insertInlineImage": {
                "location": {"index": n},
                "uri": ip["url"],
                "objectSize": {
                    "height": {"magnitude": ip["h"], "unit": "PT"},
                    "width":  {"magnitude": ip["w"], "unit": "PT"},
                },
            }})
            # 3. Align the image paragraph
            img_reqs.append({"updateParagraphStyle": {
                "range": {"startIndex": n, "endIndex": n + 2},
                "paragraphStyle": {"alignment": ip["align"]},
                "fields": "alignment",
            }})

        if img_reqs:
            docs_service.documents().batchUpdate(
                documentId=doc_id, body={"requests": img_reqs}
            ).execute()

        return doc_url

    except Exception as e:
        print(f"[DEEP] Google Docs error: {e}")
        import traceback; traceback.print_exc()
        return None


# ── MAIN ENTRY POINT ───────────────────────────────────────────────────────────
def run_deep_research(topic: str, callback=None):
    """Runs research crew, fetches Pexels images, creates styled Google Doc."""
    start = time.time()

    if callback:
        callback({"status": "searching", "message": f"Researching: {topic}..."})

    crew   = build_crew(topic)
    result = crew.kickoff()

    report_text = str(result)
    elapsed     = int(time.time() - start)

    if callback:
        callback({"status": "writing", "message": "Fetching images & creating Google Doc..."})

    title   = f"CODEC Research: {topic[:80]} — {datetime.now().strftime('%Y-%m-%d')}"
    doc_url = create_google_doc(title, report_text)

    if doc_url:
        return {
            "status":         "complete",
            "doc_url":        doc_url,
            "title":          title,
            "elapsed_seconds": elapsed,
            "report_preview": report_text[:500] + "...",
        }
    else:
        return {
            "status":          "complete_no_doc",
            "report":          report_text,
            "elapsed_seconds": elapsed,
        }
