"""CODEC Crews — 12 pre-built multi-agent workflows.

C6 / SR-41: extracted from codec_agents.py. Each builder takes **kwargs
and returns a configured Crew with agents, tasks, and an allowed_tools
allowlist. The CREW_REGISTRY itself stays in codec_agents so the public
`run_crew(name, ...)` entrypoint and the registry shape don't move.

This file is ~700 LOC of declarative wiring. Splitting it from
codec_agents lets the runtime engine (Crew/Agent class + dispatch +
correlation_id plumbing) be read independently from "the list of
crews available out of the box".

Future refactor: per-crew files at crews/{name}.py — each builder is
self-contained.
"""
from __future__ import annotations

import logging
from datetime import datetime

# Runtime types and the tool registry come from codec_agents.
from codec_agents import Agent, Crew, Tool, get_all_tools  # noqa: F401

log = logging.getLogger("codec_crews")

# Some crews need direct access to specific tool functions — they all
# resolve via get_all_tools() filter-by-name, so no further imports
# required.


def deep_research_crew(**kwargs) -> Crew:
    all_tools = get_all_tools()
    search_tools = [t for t in all_tools if t.name in ("web_search", "web_fetch")]
    write_tools  = [t for t in all_tools if t.name in ("google_docs_create",)]
    topic = kwargs.get("topic", "the given topic")
    # Query elevation results (injected by run_crew before building)
    elevated = kwargs.get("_elevated", {})
    refined_topic = elevated.get("refined_topic", topic)
    search_queries = elevated.get("search_queries", [])
    scope = elevated.get("scope", "")
    angles = elevated.get("angles", "")

    # Date grounding: the local LLM (Qwen 3.6) has a 2024 knowledge cutoff
    # and defaults to that period when asked about "current" content. The
    # weekly AI report was returning "March 2024 AI landscape" because of
    # this. Inject today's date into every agent role so the LLM is
    # anchored to the real present.
    today_str = datetime.now().strftime("%A, %B %d, %Y")
    current_year = datetime.now().year

    # Build enhanced research brief for the Researcher agent
    research_brief = f"Research thoroughly: {refined_topic}\n"
    research_brief += f"Today is {today_str}. Current year is {current_year}. All findings MUST cover the current week / month / year — NOT historical content from previous years unless explicitly part of the topic.\n"
    if scope:
        research_brief += f"Scope: {scope}\n"
    if angles:
        research_brief += f"Cover these angles: {angles}\n"
    if search_queries:
        research_brief += "Suggested search queries (use these as starting points, adapt as needed):\n"
        for i, q in enumerate(search_queries, 1):
            research_brief += f"  {i}. {q}\n"
    research_brief += "Fetch the most relevant source pages and extract key details, stats, and examples."

    researcher = Agent(
        name="Researcher",
        role=(
            f"You are an elite research analyst. Today is {today_str}. The current year is {current_year}. "
            "Find comprehensive, accurate, up-to-date information from the CURRENT period — not from previous years. "
            "You have been given a refined research brief with suggested search queries. "
            "Use the suggested queries as starting points but adapt them based on what you find. "
            "Search broadly (4-6 queries), then fetch the most relevant sources. "
            f"When constructing search queries, include '{current_year}' or recent date markers to bias results toward current content. "
            "Extract key facts, statistics, expert opinions, and recent developments. "
            "If a source is older than 6 months, note its date explicitly so the Writer can flag it. "
            "Focus on the INTENT of the research, not just literal keywords."
        ),
        tools=search_tools, max_tool_calls=8,
    )
    writer = Agent(
        name="Writer",
        role=(
            f"You are a professional report writer. Today is {today_str}. The current year is {current_year}. "
            "Synthesize research into a comprehensive well-structured report: "
            "Executive Summary, Key Findings, Analysis, Conclusion, Sources. "
            f"Frame the report as a snapshot of the CURRENT ({current_year}) AI/industry landscape. "
            "Do NOT describe past years as if they were current. If your training data perceives a different "
            f"'current' year, override that — today's actual date is {today_str}. "
            "Write 2000-5000 words in markdown. Cite sources inline with their publication dates.\n"
            "CRITICAL: You MUST use the google_docs_create tool to save your report. "
            "Do NOT fabricate or invent a Google Docs URL. The tool will return the real URL. "
            "NEVER output a FINAL response until you have called google_docs_create and received the actual URL back.\n"
            "Your FINAL response format MUST be:\n"
            "1. First line: the exact Google Docs URL returned by the tool\n"
            "2. Then a blank line\n"
            "3. Then a 3-5 sentence summary of the key findings from your report"
        ),
        tools=write_tools, max_tool_calls=2,
    )
    return Crew(
        agents=[researcher, writer],
        tasks=[
            research_brief,
            f"Write a comprehensive report about: {refined_topic}\n"
            f"Use research context provided. Save to Google Docs with title: "
            f"'CODEC Research: {refined_topic[:80]} — {datetime.now().strftime('%Y-%m-%d')}'\n"
            f"After saving, your FINAL response MUST begin with the Google Docs URL on its own line."
        ],
        allowed_tools=["web_search", "web_fetch", "google_docs_create"],
    )


def daily_briefing_crew(**kwargs) -> Crew:
    all_tools = get_all_tools()
    scout_tools = [t for t in all_tools if t.name in (
        "google_calendar", "weather", "web_search", "google_tasks", "google_keep"
    )]
    write_tools = [t for t in all_tools if t.name in ("google_docs_create",)]
    scout = Agent(
        name="Scout",
        role=(
            "You are the user's daily briefing researcher. Your job is to gather comprehensive data. "
            "Check ALL of these sources — do not skip any:\n"
            "1. Google Calendar — get today's full schedule\n"
            "2. Google Tasks — list all pending/overdue items\n"
            "3. Google Keep — any recent notes or reminders\n"
            "4. Weather — current conditions AND forecast\n"
            "5. Web search — search 'top news today', 'stock market today', 'S&P 500', 'tech news today'\n"
            "Be EXHAUSTIVE. Include exact event times, task names with details, temperatures, "
            "specific stock prices, headline details with sources. The more data the better."
        ),
        tools=scout_tools, max_tool_calls=8,
    )
    writer = Agent(
        name="Briefing Writer",
        role=(
            "You are a professional report writer at CODEC. Synthesize all gathered data into a "
            "comprehensive, well-structured daily briefing report. Write 1500-3000 words in markdown.\n\n"
            "Required sections with ## headings:\n"
            "1. **Executive Summary** — 3-4 sentence overview of the day ahead\n"
            "2. **Calendar & Schedule** — all events with times, prep notes, conflicts\n"
            "3. **Pending Tasks** — categorized list with priorities and deadlines\n"
            "4. **Weather Forecast** — current + outlook, activity recommendations\n"
            "5. **Market Overview** — major indices, notable movers, key economic data\n"
            "6. **Top News Headlines** — 5-8 headlines with brief analysis\n"
            "7. **Key Takeaways & Priorities** — actionable items for today\n\n"
            "Write professionally. Use bullet points, bold for emphasis. "
            "Cite news sources inline. Make it comprehensive and insightful.\n\n"
            "CRITICAL: You MUST use the google_docs_create tool to save your report. "
            "Do NOT fabricate or invent a Google Docs URL. The tool will return the real URL. "
            "NEVER output a FINAL response until you have called google_docs_create and received the actual URL back.\n"
            "Your FINAL response format MUST be:\n"
            "1. First line: the exact Google Docs URL returned by the tool\n"
            "2. Then a blank line\n"
            "3. Then a 3-5 sentence summary of today's key priorities and highlights"
        ),
        tools=write_tools, max_tool_calls=2,
    )
    today = datetime.now().strftime("%A, %B %d, %Y")
    return Crew(
        agents=[scout, writer],
        tasks=[
            "Gather ALL daily briefing data — use every tool available:\n"
            "1. Check Google Calendar for today's events\n"
            "2. Check Google Tasks for pending items\n"
            "3. Check Google Keep for recent notes\n"
            "4. Get current weather and forecast\n"
            "5. Search 'top news today' AND 'stock market today S&P 500 Dow Jones'\n"
            "Be thorough — search at least 3 different queries for news/markets.",
            f"Write a comprehensive Daily Briefing report (1500-3000 words) using ALL gathered data.\n"
            f"Save to Google Docs with title: 'CODEC: Daily Briefing — {today}'\n"
            f"After saving, your FINAL response MUST begin with the Google Docs URL on its own line."
        ],
        allowed_tools=["google_calendar", "weather", "web_search", "google_tasks", "google_keep", "google_docs_create"],
    )


def trip_planner_crew(**kwargs) -> Crew:
    all_tools = get_all_tools()
    destination = kwargs.get("destination", "the destination")
    dates = kwargs.get("dates", "")
    research_tools = [t for t in all_tools if t.name in ("web_search", "web_fetch")]
    plan_tools     = [t for t in all_tools if t.name in ("google_docs_create", "google_calendar")]

    researcher = Agent(
        name="Travel Researcher",
        role=(
            "Research travel destinations thoroughly. Find flights, hotels, attractions, restaurants, "
            "local tips, safety info, and hidden gems. Compare prices across sources."
        ),
        tools=research_tools, max_tool_calls=8,
    )
    planner = Agent(
        name="Trip Planner",
        role=(
            "Create a detailed day-by-day itinerary. Organize into morning/afternoon/evening. "
            "Include estimated costs and travel times. Save to Google Docs. "
            "Add key travel dates (departure, return) to Google Calendar."
        ),
        tools=plan_tools, max_tool_calls=3,
    )
    return Crew(
        agents=[researcher, planner],
        tasks=[
            f"Research a trip to {destination} {dates}. "
            f"Find: best flights, top hotels (mid-range), must-see attractions, restaurants, transport.",
            f"Create a day-by-day itinerary for {destination} {dates}. "
            f"Save to Google Docs: 'Trip Plan: {destination} — {datetime.now().strftime('%Y-%m-%d')}'"
        ],
        allowed_tools=["web_search", "web_fetch", "google_docs_create", "google_calendar"],
    )


def competitor_analysis_crew(**kwargs) -> Crew:
    all_tools = get_all_tools()
    topic = kwargs.get("topic", "the market")
    web_tools   = [t for t in all_tools if t.name in ("web_search", "web_fetch")]
    write_tools = [t for t in all_tools if t.name in ("google_docs_create",)]

    scout = Agent(
        name="Web Scout",
        role=(
            "Research competitors and market landscape thoroughly. Find products, pricing, "
            "market position, recent news, reviews, funding, and team size. "
            "Search each competitor individually for depth."
        ),
        tools=web_tools, max_tool_calls=8,
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
        allowed_tools=["web_search", "web_fetch", "google_docs_create"],
    )


def email_handler_crew(**kwargs) -> Crew:
    all_tools = get_all_tools()
    gmail_tools = [t for t in all_tools if t.name in ("google_gmail",)]

    reader = Agent(
        name="Email Reader",
        role=(
            "Read unread emails from the inbox. Categorize each as URGENT, NORMAL, LOW, or SPAM. "
            "For each: sender, subject, category, 1-line summary.\n"
            "IMPORTANT: When using the google_gmail tool, your input MUST contain the word 'unread' "
            "to fetch unread emails. Example input: 'check unread emails'"
        ),
        tools=gmail_tools, max_tool_calls=3,
    )
    responder = Agent(
        name="Email Responder",
        role=(
            "Draft brief professional replies for urgent and normal emails. "
            "Tone: direct, confident, clear. Keep replies short — 2-4 sentences max.\n"
            "If there are no emails to reply to, say so clearly."
        ),
        tools=gmail_tools, max_tool_calls=3,
    )
    return Crew(
        agents=[reader, responder],
        tasks=[
            "Use the google_gmail tool with input 'check unread emails' to fetch all unread emails. "
            "Categorize each by urgency. List them all with sender, subject, and summary.",
            "Draft replies for urgent and normal emails. Summarize actions for the rest.",
        ],
        allowed_tools=["google_gmail"],
    )


def social_media_crew(**kwargs) -> Crew:
    all_tools = get_all_tools()
    topic = kwargs.get("topic", "the given topic")
    search_tools = [t for t in all_tools if t.name in ("web_search", "web_fetch")]
    write_tools  = [t for t in all_tools if t.name in ("google_docs_create",)]

    # Inject CODEC product context when topic mentions CODEC
    codec_ctx = ""
    if "codec" in topic.lower():
        codec_ctx = (
            "\n\nIMPORTANT CONTEXT: CODEC is an open-source intelligent command layer for macOS "
            "— a voice-controlled AI workstation with 50+ skills, 10+ multi-agent crews, local LLMs, "
            "and Google Workspace integration. It is NOT a video codec. "
            "Website: opencodec.org. Built by AVA Digital."
        )

    trend_scout = Agent(
        name="Trend Scout",
        role=(
            "You are a social media trend analyst. Research trending topics, hashtags, "
            "and viral content. Find what's popular right now on Twitter, LinkedIn, and Instagram. "
            "Identify key angles, hashtags, and audience interests." + codec_ctx
        ),
        tools=search_tools, max_tool_calls=8,
    )
    content_creator = Agent(
        name="Content Creator",
        role=(
            "You are an expert social media copywriter. Write platform-specific posts: "
            "Twitter (max 280 chars, punchy, with hashtags), "
            "LinkedIn (professional tone, 150-300 words, insight-driven), "
            "Instagram (visual description + engaging caption + hashtags).\n"
            "CRITICAL: You MUST use the google_docs_create tool to save your posts. "
            "Do NOT fabricate a Google Docs URL. The tool returns the real URL.\n"
            "Your FINAL response format MUST be:\n"
            "1. First line: the exact Google Docs URL returned by the tool\n"
            "2. Then the 3 posts" + codec_ctx
        ),
        tools=write_tools, max_tool_calls=2,
    )
    return Crew(
        agents=[trend_scout, content_creator],
        tasks=[
            f"Research trending content about: {topic}{codec_ctx}\n"
            f"Find trending hashtags, popular angles, viral formats, and audience interests.",
            f"Write 3 platform-specific posts (Twitter, LinkedIn, Instagram) about: {topic}. "
            "Save all to a Google Doc with title: "
            "'Social Media Posts: " + topic[:60] + " — " + datetime.now().strftime('%Y-%m-%d') + "'\n"
            "After saving, your FINAL response MUST begin with the Google Docs URL on its own line."
        ],
        allowed_tools=["web_search", "web_fetch", "google_docs_create"],
    )


def code_review_crew(**kwargs) -> Crew:
    all_tools = get_all_tools()
    code = kwargs.get("code", "")
    read_tools     = [t for t in all_tools if t.name in ("file_read",)]
    [t for t in all_tools if t.name in ("file_read", "web_search")]
    [t for t in all_tools if t.name in ("file_read", "file_write")]

    # Truncate code for prompt injection into all tasks
    code_snippet = code[:3000]

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
        tools=read_tools, max_tool_calls=4,
    )
    clean_coder = Agent(
        name="Clean Coder",
        role=(
            "You are a software architect focused on code quality. Suggest improvements for: "
            "readability, naming conventions, function decomposition, DRY principles, "
            "design patterns, documentation, and maintainability. "
            "Provide concrete refactoring suggestions. Do NOT write files — this is a review only."
        ),
        tools=read_tools, max_tool_calls=3,
    )
    return Crew(
        agents=[bug_hunter, security_auditor, clean_coder],
        tasks=[
            f"Review this code for bugs, logic errors, and edge cases:\n{code_snippet}",
            f"Review this code for security vulnerabilities:\n{code_snippet}\n\n"
            f"Also consider the bug findings from the previous reviewer.",
            f"Review this code for readability and maintainability:\n{code_snippet}\n\n"
            f"Also consider the bug and security findings from the previous reviewers.",
        ],
        allowed_tools=["file_read"],
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
        tools=gather_tools, max_tool_calls=8,
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
            "Analyze the data and write an insights report. Save to Google Docs with title: "
            "'Data Analysis: " + topic[:60] + " — " + datetime.now().strftime('%Y-%m-%d') + "'"
        ],
        allowed_tools=["web_search", "web_fetch", "google_sheets", "google_docs_create"],
    )


def content_writer_crew(**kwargs) -> Crew:
    """Content Writer crew — research + write + publish to Google Docs."""
    all_tools = get_all_tools()
    topic = kwargs.get("topic", "the given topic")
    content_type = kwargs.get("content_type", "blog post")
    audience = kwargs.get("audience", "general")
    research_tools = [t for t in all_tools if t.name in ("web_search", "web_fetch")]
    write_tools = [t for t in all_tools if t.name in ("google_docs_create",)]

    researcher = Agent(
        name="Content Researcher",
        role=(
            f"You are a content research specialist. Your job is to research the topic "
            f"'{topic}' thoroughly to provide the writer with factual, current, and "
            f"engaging material. Find statistics, expert quotes, real examples, trending "
            f"angles, and competitor content on this topic. Focus on what would resonate "
            f"with a {audience} audience. Search at least 3 different angles."
        ),
        tools=research_tools, max_tool_calls=8,
    )
    writer = Agent(
        name="Content Writer",
        role=(
            f"You are an expert content writer. Write a {content_type} about '{topic}' "
            f"for a {audience} audience. Use the research provided as context.\n\n"
            f"Writing guidelines:\n"
            f"- Hook the reader in the first sentence\n"
            f"- Use short paragraphs (2-3 sentences max)\n"
            f"- Include subheadings every 200-300 words\n"
            f"- Weave in statistics and examples from the research\n"
            f"- End with a clear call to action or takeaway\n"
            f"- SEO: naturally include the main topic keyword 3-5 times\n"
            f"- Tone: professional but conversational, not robotic\n"
            f"- Length: 1500-2500 words for blog posts, 800-1200 for LinkedIn\n\n"
            "Save the final piece to Google Docs with title: "
            f"'{content_type.title()}: {topic[:60]} — {datetime.now().strftime('%Y-%m-%d')}'\n"
            f"IMPORTANT: Your FINAL response MUST include the exact Google Docs URL returned by the tool."
        ),
        tools=write_tools, max_tool_calls=2,
    )
    return Crew(
        agents=[researcher, writer],
        tasks=[
            f"Research the topic '{topic}' for a {content_type}. Target audience: {audience}. "
            f"Find current statistics, expert opinions, real-world examples, trending angles, "
            f"and what competitors have written about this. Provide organized research notes.",
            f"Write a compelling {content_type} about '{topic}' using the research provided. "
            f"Save to Google Docs when complete.",
        ],
        allowed_tools=["web_search", "web_fetch", "google_docs_create"],
    )


def meeting_summarizer_crew(**kwargs) -> Crew:
    """Meeting Summarizer crew — parse notes + extract actions + save structured summary."""
    all_tools = get_all_tools()
    meeting_input = kwargs.get("meeting_input", "")

    # Auto-pull from CODEC Voice memory if user says "summarize the call"
    if len(meeting_input) < 100 and any(
        w in meeting_input.lower() for w in ["call", "last", "voice", "previous", "recent"]
    ):
        try:
            from codec_memory import CodecMemory
            mem = CodecMemory()
            rows = mem.search("voice", limit=30)
            if rows:
                transcript = "\n".join(
                    f"{r.get('role','?')}: {r.get('content','')}"
                    for r in reversed(rows)
                    if r.get("session_id", "").startswith("voice_")
                )
                if transcript:
                    meeting_input = f"[CODEC Voice Call Transcript]\n{transcript}"
        except Exception as e:
            log.warning("Voice transcript retrieval failed: %s", e)

    read_tools = [t for t in all_tools if t.name in ("file_read",)]
    write_tools = [t for t in all_tools if t.name in ("google_docs_create", "google_calendar")]

    parser = Agent(
        name="Meeting Parser",
        role=(
            "You are a meeting analysis specialist. Your job is to take raw meeting notes, "
            "transcripts, or audio transcriptions and extract structured information.\n\n"
            "Extract the following:\n"
            "1. ATTENDEES — who was present (names, roles if mentioned)\n"
            "2. KEY TOPICS — main subjects discussed (3-7 bullet points)\n"
            "3. DECISIONS MADE — any decisions that were finalized\n"
            "4. ACTION ITEMS — specific tasks assigned, with WHO is responsible and DEADLINE if mentioned\n"
            "5. OPEN QUESTIONS — unresolved issues that need follow-up\n"
            "6. NEXT MEETING — date/time if scheduled\n\n"
            "If the input is a file path, read it first. "
            "Be precise. Don't invent information that wasn't in the notes."
        ),
        tools=read_tools, max_tool_calls=3,
    )
    formatter = Agent(
        name="Summary Writer",
        role=(
            "You are a professional meeting documentation writer. Take the parsed meeting "
            "data and create a clean, structured meeting summary document.\n\n"
            "Format:\n"
            "MEETING SUMMARY\n"
            "Date: [date]\n"
            "Attendees: [names]\n\n"
            "OVERVIEW\n"
            "[2-3 sentence executive summary]\n\n"
            "KEY DISCUSSION POINTS\n"
            "[Numbered list with brief descriptions]\n\n"
            "DECISIONS\n"
            "[Numbered list]\n\n"
            "ACTION ITEMS\n"
            "[Table: Action | Owner | Deadline | Status]\n\n"
            "OPEN QUESTIONS\n"
            "[Numbered list]\n\n"
            "NEXT STEPS\n"
            "[What happens next, next meeting date]\n\n"
            "Save to Google Docs with title: "
            f"'Meeting Summary — {datetime.now().strftime('%Y-%m-%d')}'\n"
            "If action items have deadlines, add them to Google Calendar.\n"
            "IMPORTANT: Your FINAL response MUST include the exact Google Docs URL returned by the tool."
        ),
        tools=write_tools, max_tool_calls=3,
    )
    return Crew(
        agents=[parser, formatter],
        tasks=[
            f"Parse and extract structured information from these meeting notes:\n\n{meeting_input[:8000]}",
            "Create a formatted meeting summary document from the parsed data. "
            "Save to Google Docs. Add any action items with deadlines to Google Calendar.",
        ],
        allowed_tools=["file_read", "google_docs_create", "google_calendar"],
    )


def invoice_generator_crew(**kwargs) -> Crew:
    """Invoice Generator crew — parse details + create professional invoice in Google Docs."""
    from codec_config import cfg
    all_tools = get_all_tools()
    invoice_details = kwargs.get("invoice_details", "")
    read_tools = [t for t in all_tools if t.name in ("google_gmail", "google_drive")]
    write_tools = [t for t in all_tools if t.name in ("google_docs_create",)]

    parser = Agent(
        name="Invoice Parser",
        role=(
            "You are an invoice preparation specialist. Your job is to extract and organize "
            "all invoice details from the user's natural language input.\n\n"
            "Extract:\n"
            "1. FROM (sender): Company name, address, email, phone\n"
            "   - Default: " + cfg.get("invoice_from_name", "Your Company") + ", " + cfg.get("invoice_from_email", "your@email.com") + "\n"
            "2. TO (client): Client name, company, address, email\n"
            "3. INVOICE NUMBER: Generate as INV-YYYYMMDD-001 if not specified\n"
            "4. DATE: Today's date if not specified\n"
            "5. DUE DATE: Net 30 from invoice date if not specified\n"
            "6. LINE ITEMS: Description, quantity, unit price, total per line\n"
            "7. SUBTOTAL: Sum of all line items\n"
            "8. TAX: If mentioned (default 0%)\n"
            "9. TOTAL: Subtotal + tax\n"
            "10. PAYMENT DETAILS: " + cfg.get("invoice_payment_info", "PayPal or bank details if mentioned") + "\n"
            "11. NOTES: Any special terms, late payment fees, thank you message\n\n"
            "If any client details are missing, check Google Drive or Gmail for previous "
            "correspondence with this client to fill in their details.\n\n"
            "Output all fields in a clear structured format."
        ),
        tools=read_tools, max_tool_calls=3,
    )
    today_str = datetime.now().strftime("%B %d, %Y")
    today_inv = datetime.now().strftime("%Y%m%d")
    creator = Agent(
        name="Invoice Creator",
        role=(
            "You are a professional invoice document creator. Take the parsed invoice data "
            "and create a clean, professional invoice in Google Docs.\n\n"
            f"IMPORTANT: Today's date is {today_str}. Use this for invoice date unless specified.\n"
            f"Generate invoice number as INV-{today_inv}-001 unless already specified.\n\n"
            "Format the invoice EXACTLY like this (use markdown headings and bold):\n\n"
            "# INVOICE\n\n"
            "**Invoice Number:** INV-XXXXXXXX-001\n"
            "**Invoice Date:** [date]\n"
            "**Due Date:** [due date]\n"
            "**Currency:** [EUR/USD]\n\n"
            "---\n\n"
            "## From\n"
            "**[Company Name]**\n"
            "[Address if available]\n"
            "[Email] | [Phone if available]\n\n"
            "## Bill To\n"
            "**[Client Name]**\n"
            "[Company if applicable]\n"
            "[Address/Country]\n"
            "[Email if available]\n\n"
            "---\n\n"
            "## Services\n\n"
            "| Description | Quantity | Unit Price | Total |\n"
            "|---|---|---|---|\n"
            "| [Service] | [Qty] | [Price] | [Line Total] |\n\n"
            "---\n\n"
            "**Subtotal:** [amount]\n"
            "**Tax (0%):** 0.00\n"
            "## Total Due: [AMOUNT IN BOLD]\n\n"
            "---\n\n"
            "## Payment Information\n"
            "Payment is due by [due date].\n"
            "Please transfer to: [payment details from parser, or 'Contact sender for payment details']\n\n"
            "## Terms & Conditions\n"
            "- Payment due within the specified period\n"
            "- Late payments may incur a 1.5% monthly fee\n"
            "- Questions? Contact [sender email]\n\n"
            "---\n"
            "*Generated by CODEC — AVA Digital*\n\n"
            "Save to Google Docs with title: "
            f"'CODEC: Invoice [number] — [client name] — {datetime.now().strftime('%Y-%m-%d')}'\n"
            "IMPORTANT: Your FINAL response MUST include the exact Google Docs URL returned by the tool."
        ),
        tools=write_tools, max_tool_calls=2,
    )
    return Crew(
        agents=[parser, creator],
        tasks=[
            f"Parse these invoice details and extract all required fields:\n\n{invoice_details}",
            "Create a professional invoice document from the parsed data. Save to Google Docs.",
        ],
        allowed_tools=["google_gmail", "google_drive", "google_docs_create"],
    )


def project_manager_crew(**kwargs) -> Crew:
    """Project Manager crew — gather status + identify blockers + write status report."""
    all_tools = get_all_tools()
    project = kwargs.get("project", "the project")
    gather_tools = [t for t in all_tools if t.name in (
        "google_calendar", "google_gmail", "google_drive", "google_tasks",
        "google_sheets",
    )]
    report_tools = [t for t in all_tools if t.name in ("google_docs_create",)]

    gatherer = Agent(
        name="Status Gatherer",
        role=(
            f"You are a project management assistant. Your job is to gather the current "
            f"status of the project: '{project}'.\n\n"
            f"Check these sources:\n"
            f"1. Google Calendar — any upcoming meetings, deadlines, or milestones related to this project\n"
            f"2. Google Gmail — recent emails mentioning this project or its stakeholders\n"
            f"3. Google Drive — recent documents related to this project\n"
            f"4. Google Tasks — any pending tasks for this project\n"
            f"5. Google Sheets — any tracking spreadsheets\n\n"
            f"For each source, report:\n"
            f"- What you found (or 'nothing found' if empty)\n"
            f"- Any upcoming deadlines or overdue items\n"
            f"- Any blockers or risks you can identify\n\n"
            f"If the project name is vague, search broadly and report what seems relevant."
        ),
        tools=gather_tools, max_tool_calls=7,
    )
    reporter = Agent(
        name="Project Reporter",
        role=(
            f"You are a project status report writer. Take the gathered information about "
            f"'{project}' and create a professional project status report.\n\n"
            f"Format:\n\n"
            f"PROJECT STATUS REPORT\n"
            f"Project: {project}\n"
            f"Date: {datetime.now().strftime('%Y-%m-%d')}\n"
            f"Status: [GREEN / YELLOW / RED]\n\n"
            f"EXECUTIVE SUMMARY\n"
            f"[2-3 sentences on overall project health]\n\n"
            f"PROGRESS SINCE LAST CHECK\n"
            f"[What's been accomplished — from emails, docs, completed tasks]\n\n"
            f"UPCOMING MILESTONES\n"
            f"[Next 2 weeks — from calendar, tasks, deadlines]\n\n"
            f"BLOCKERS AND RISKS\n"
            f"[Any issues identified — overdue tasks, unanswered emails, missing deliverables]\n\n"
            f"ACTION ITEMS\n"
            f"[Recommended next steps with suggested owners and deadlines]\n\n"
            f"METRICS\n"
            f"[Any quantifiable data — task completion rate, email response times, etc.]\n\n"
            f"Save to Google Docs with title: "
            f"'Project Status: {project[:50]} — {datetime.now().strftime('%Y-%m-%d')}'\n"
            f"IMPORTANT: Your FINAL response MUST include the exact Google Docs URL returned by the tool."
        ),
        tools=report_tools, max_tool_calls=2,
    )
    return Crew(
        agents=[gatherer, reporter],
        tasks=[
            f"Gather the current status of project '{project}' from all available sources: "
            f"Calendar, Gmail, Drive, Tasks, and Sheets.",
            f"Write a comprehensive project status report for '{project}'. "
            f"Include traffic light status (GREEN/YELLOW/RED), blockers, and recommended actions. "
            f"Save to Google Docs.",
        ],
        allowed_tools=[
            "google_calendar", "google_gmail", "google_drive", "google_tasks",
            "google_sheets", "google_docs_create",
        ],
    )


# ═══════════════════════════════════════════════════════════════
# CREW REGISTRY
# ═══════════════════════════════════════════════════════════════

