"""CODEC Search — DuckDuckGo (free, no API key) or Serper (better results, needs key)"""
import httpx
import json
import os
import re

CONFIG_PATH = os.path.expanduser("~/.codec/config.json")


def search_ddg(query: str, max_results: int = 10) -> list:
    """Search DuckDuckGo Instant Answers API — free, no API key needed"""
    try:
        r = httpx.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
            follow_redirects=True,
        )
        if r.status_code == 200:
            data = r.json()
            results = []

            # Instant answer
            if data.get("AbstractText"):
                results.append({
                    "title": data.get("Heading", query),
                    "link": data.get("AbstractURL", ""),
                    "snippet": data["AbstractText"][:400],
                })

            # Related topics
            for topic in data.get("RelatedTopics", [])[:max_results - len(results)]:
                if isinstance(topic, dict) and topic.get("Text"):
                    results.append({
                        "title": topic.get("Text", "")[:80],
                        "link": topic.get("FirstURL", ""),
                        "snippet": topic.get("Text", "")[:200],
                    })

            if results:
                return results
    except Exception as e:
        pass

    # Fallback: HTML scrape
    try:
        r = httpx.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
            follow_redirects=True,
        )
        results = []
        links = re.findall(r'<a rel="nofollow" class="result__a" href="(.*?)">(.*?)</a>', r.text)
        snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', r.text, re.DOTALL)
        for i, (href, title) in enumerate(links[:max_results]):
            results.append({
                "title": re.sub(r"<[^>]+>", "", title).strip(),
                "link": href,
                "snippet": re.sub(r"<[^>]+>", "", snippets[i]).strip() if i < len(snippets) else "",
            })
        return results
    except Exception as e:
        return [{"title": "Search error", "link": "", "snippet": str(e)}]


def search_serper(query: str, api_key: str, max_results: int = 10) -> list:
    """Search via Serper.dev — better results, needs API key ($10 for 100k queries)"""
    try:
        r = httpx.post(
            "https://google.serper.dev/search",
            json={"q": query, "num": max_results},
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            timeout=10,
        )
        data = r.json()
        results = []

        # Answer box
        if data.get("answerBox"):
            box = data["answerBox"]
            results.append({
                "title": box.get("title", query),
                "link": box.get("link", ""),
                "snippet": box.get("answer", box.get("snippet", ""))[:400],
            })

        for item in data.get("organic", [])[:max_results - len(results)]:
            results.append({
                "title": item.get("title", ""),
                "link": item.get("link", ""),
                "snippet": item.get("snippet", ""),
            })

        return results
    except Exception as e:
        return [{"title": "Search error", "link": "", "snippet": str(e)}]


def search(query: str, max_results: int = 10) -> list:
    """Auto-select: use Serper if API key configured, otherwise DuckDuckGo"""
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        serper_key = cfg.get("serper_api_key", "").strip()
        if serper_key:
            return search_serper(query, serper_key, max_results)
    except Exception:
        pass
    return search_ddg(query, max_results)


def format_results(results: list, max_snippets: int = 3) -> str:
    """Format search results into a readable string for LLM context"""
    if not results:
        return "No results found."
    lines = []
    for i, r in enumerate(results[:max_snippets], 1):
        title = r.get("title", "")
        snippet = r.get("snippet", "")
        link = r.get("link", "")
        if title or snippet:
            lines.append(f"{i}. {title}")
            if snippet:
                lines.append(f"   {snippet}")
            if link:
                lines.append(f"   {link}")
    return "\n".join(lines)
