"""CODEC Skill: Web Search via DuckDuckGo (or Serper if key configured)"""
SKILL_NAME = "web_search"
SKILL_DESCRIPTION = "Search the web and return a quick answer"
SKILL_TRIGGERS = ["search for", "search the web", "google search", "look up", "who is", "who won", "latest news"]


def run(task, app="", ctx=""):
    import sys, os
    sys.path.insert(0, os.path.expanduser("~/codec-repo"))

    query = task.lower()
    for remove in ["search for", "google", "look up", "can you", "please", "search"]:
        query = query.replace(remove, "")
    query = query.strip().strip("?").strip()
    if not query:
        return None

    # Try DuckDuckGo instant answer first (fast, no extra deps)
    try:
        import requests
        r = requests.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            if data.get("AbstractText"):
                return data["AbstractText"][:400]
            if data.get("Answer"):
                return str(data["Answer"])[:400]
            if data.get("RelatedTopics"):
                topics = [
                    t.get("Text", "")
                    for t in data["RelatedTopics"][:3]
                    if isinstance(t, dict) and t.get("Text")
                ]
                if topics:
                    return " | ".join(t[:150] for t in topics)
    except Exception:
        pass

    # Fallback: full codec_search (Serper if key configured, DDG HTML scrape otherwise)
    try:
        from codec_search import search, format_results
        results = search(query, max_results=5)
        if results:
            return format_results(results, max_snippets=3)
    except Exception:
        pass

    # No instant answer found — let Q-Agent handle it with web fetch
    return None
