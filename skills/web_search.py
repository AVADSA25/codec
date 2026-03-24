"""CODEC Skill: Web Search via DuckDuckGo"""
SKILL_NAME = "web_search"
SKILL_DESCRIPTION = "Search the web and return a quick answer"
SKILL_TRIGGERS = ["search for", "google", "look up", "search", "what is the latest", "news about", "who won"]

def run(task, app="", ctx=""):
    import requests, re
    # Extract search query
    query = task.lower()
    for remove in ["search for", "google", "look up", "can you", "please", "search"]:
        query = query.replace(remove, "")
    query = query.strip().strip("?").strip()
    if not query:
        return None  # Decline — no query found

    try:
        # DuckDuckGo instant answer API (no key needed)
        r = requests.get("https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
            timeout=10)
        if r.status_code == 200:
            data = r.json()
            # Try abstract first
            if data.get("AbstractText"):
                return data["AbstractText"][:400]
            # Try answer
            if data.get("Answer"):
                return str(data["Answer"])[:400]
            # Try related topics
            if data.get("RelatedTopics"):
                topics = [t.get("Text", "") for t in data["RelatedTopics"][:3] if isinstance(t, dict)]
                if topics:
                    return " | ".join(t[:150] for t in topics if t)
            return f"No instant answer found for '{query}'. Try asking Q directly for a more detailed response."
    except Exception as e:
        return f"Search error: {e}"
