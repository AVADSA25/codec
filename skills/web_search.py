"""CODEC Skill: Web Search via DuckDuckGo"""
SKILL_NAME = "web_search"
SKILL_DESCRIPTION = "Search the web and return a quick answer"
SKILL_TRIGGERS = ["search for", "google", "look up", "who is", "who won", "latest news"]

def run(task, app="", ctx=""):
    import requests
    query = task.lower()
    for remove in ["search for", "google", "look up", "can you", "please", "search"]:
        query = query.replace(remove, "")
    query = query.strip().strip("?").strip()
    if not query: return None
    try:
        r = requests.get("https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
            timeout=10)
        if r.status_code == 200:
            data = r.json()
            if data.get("AbstractText"):
                return data["AbstractText"][:400]
            if data.get("Answer"):
                return str(data["Answer"])[:400]
            if data.get("RelatedTopics"):
                topics = [t.get("Text","") for t in data["RelatedTopics"][:3] if isinstance(t, dict) and t.get("Text")]
                if topics:
                    return " | ".join(t[:150] for t in topics)
            # No instant answer — let Q-Agent handle it with a real search
            return None
    except:
        return None
