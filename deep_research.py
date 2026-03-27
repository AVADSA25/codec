"""
CODEC Deep Research — CrewAI + Serper + Qwen + Google Docs
"""
import os, json, time
from datetime import datetime
from crewai import Agent, Task, Crew, Process, LLM
from crewai_tools import SerperDevTool
import litellm

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
SERPER_API_KEY = "5bfdf8c7aed2128f1535bcdd0e2164f46e08b5c1"
os.environ["SERPER_API_KEY"] = SERPER_API_KEY

# Qwen 3.5 35B — thinking mode OFF to avoid mlx-lm system-message ordering constraint
# CrewAI injects system messages mid-conversation; mlx-lm rejects this with enable_thinking=True
llm = LLM(
    model="openai/mlx-community/Qwen3.5-35B-A3B-4bit",
    base_url="http://localhost:8081/v1",
    api_key="not-needed",
    temperature=0.7,
    max_tokens=16000,
)

# ── TOOLS ──
search_tool = SerperDevTool(n_results=10)

# ── AGENTS ──
def build_crew(topic: str):
    researcher = Agent(
        role="Senior Research Analyst",
        goal=f"Find comprehensive, accurate, up-to-date information about: {topic}",
        backstory="You are an elite researcher who finds primary sources, cross-references facts, and identifies key insights. You search broadly first, then dive deep into the most relevant sources.",
        tools=[search_tool],
        llm=llm,
        verbose=True,
        max_iter=10,
        allow_delegation=False
    )

    writer = Agent(
        role="Research Report Writer",
        goal=f"Synthesize research findings into a comprehensive, well-structured report about: {topic}",
        backstory="You are a professional report writer. You organize information logically with clear sections, cite sources, highlight key findings, and write in a clear professional style. Reports should be 2000-5000 words.",
        llm=llm,
        verbose=True,
        allow_delegation=False
    )

    research_task = Task(
        description=f"""Research the following topic thoroughly: {topic}

        1. Search for the topic broadly first (3-5 searches with different angles)
        2. Identify the top 10-15 most relevant and authoritative sources
        3. Extract key facts, statistics, expert opinions, and recent developments
        4. Note any controversies or conflicting information
        5. Compile all findings with source URLs""",
        expected_output="Comprehensive research notes with facts, statistics, quotes, and source URLs organized by subtopic",
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
        - 2000-5000 words
        - Professional tone
        - Include statistics and data where available
        - Cite sources inline (e.g., [1], [2])
        - Use markdown formatting (headers, bold, lists)""",
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


def create_google_doc(title: str, content: str) -> str:
    """Create a Google Doc with the research report and return the URL"""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    TOKEN_PATH = os.path.expanduser("~/.codec/google_token.json")

    try:
        creds = Credentials.from_authorized_user_file(TOKEN_PATH)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(TOKEN_PATH, "w") as f:
                f.write(creds.to_json())

        docs_service = build("docs", "v1", credentials=creds)

        # Create empty doc
        doc = docs_service.documents().create(body={"title": title}).execute()
        doc_id = doc["documentId"]
        doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"

        # Insert content (Google Docs API uses insertText requests)
        requests_body = [{
            "insertText": {
                "location": {"index": 1},
                "text": content
            }
        }]
        docs_service.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": requests_body}
        ).execute()

        return doc_url
    except Exception as e:
        print(f"[DEEP] Google Docs error: {e}")
        return None


def run_deep_research(topic: str, callback=None):
    """Main entry point — runs research and creates Google Doc"""
    start = time.time()

    if callback:
        callback({"status": "searching", "message": f"Researching: {topic}..."})

    crew = build_crew(topic)
    result = crew.kickoff()

    report_text = str(result)
    elapsed = int(time.time() - start)

    if callback:
        callback({"status": "writing", "message": "Creating Google Doc..."})

    # Create Google Doc
    title = f"CODEC Research: {topic[:80]} — {datetime.now().strftime('%Y-%m-%d')}"
    doc_url = create_google_doc(title, report_text)

    if doc_url:
        return {
            "status": "complete",
            "doc_url": doc_url,
            "title": title,
            "elapsed_seconds": elapsed,
            "report_preview": report_text[:500] + "..."
        }
    else:
        # Fallback: return report in chat if Google Docs fails
        return {
            "status": "complete_no_doc",
            "report": report_text,
            "elapsed_seconds": elapsed
        }
