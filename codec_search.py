"""CODEC Search — DuckDuckGo (free, no API key) or Serper (better results, needs key)"""
import httpx
import logging
import os
import re
import time
import threading

log = logging.getLogger(__name__)

# Kept for back-compat with any external importer; the serper key is now read
# via codec_config.get_serper_api_key() (PR-2B-2, Keychain-aware).
CONFIG_PATH = os.path.expanduser("~/.codec/config.json")

# --- TTL cache for search results ---
_cache: dict[str, tuple[float, list]] = {}  # key -> (timestamp, results)
_cache_lock = threading.Lock()
_CACHE_TTL = 300  # 5 minutes
_CACHE_MAX = 100
_ERROR_TITLE = "Search error"  # the row search_ddg / search_serper return on failure


class SerperStatusError(Exception):
    """Serper answered with a status other than 200 (429 = out of credit)."""


def _cache_get(key: str) -> list | None:
    with _cache_lock:
        entry = _cache.get(key)
        if entry and (time.monotonic() - entry[0]) < _CACHE_TTL:
            return entry[1]
        _cache.pop(key, None)
        return None


def _cache_put(key: str, value: list) -> None:
    with _cache_lock:
        # Evict expired entries if at capacity
        if len(_cache) >= _CACHE_MAX:
            now = time.monotonic()
            expired = [k for k, (ts, _) in _cache.items() if now - ts >= _CACHE_TTL]
            for k in expired:
                del _cache[k]
            # If still at capacity, drop oldest
            if len(_cache) >= _CACHE_MAX:
                oldest_key = min(_cache, key=lambda k: _cache[k][0])
                del _cache[oldest_key]
        _cache[key] = (time.monotonic(), value)


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
    except Exception:
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
        return [{"title": _ERROR_TITLE, "link": "", "snippet": str(e)}]


def search_serper(query: str, api_key: str, max_results: int = 10, *,
                  raise_errors: bool = False) -> list:
    """Search via Serper.dev — better results, needs API key ($10 for 100k queries).
    A network error or a status other than 200 (429 = out of credit) gives the error
    row, or raises with raise_errors=True so search() can fall back (P-097)."""
    try:
        r = httpx.post(
            "https://google.serper.dev/search",
            json={"q": query, "num": max_results},
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            timeout=10,
        )
        if r.status_code != 200:
            raise SerperStatusError(f"Serper answered HTTP {r.status_code}")
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
        if raise_errors:
            raise
        return [{"title": _ERROR_TITLE, "link": "", "snippet": str(e)}]


def _cacheable(results: list) -> bool:
    """P-097: an empty list or an error row is an outage, not an answer."""
    return bool(results) and not any(
        isinstance(r, dict) and r.get("title") == _ERROR_TITLE for r in results)


def search(query: str, max_results: int = 10) -> list:
    """Auto-select: use Serper if API key configured, otherwise DuckDuckGo.
    If Serper raises or answers other than 200, DuckDuckGo answers instead (P-097).
    Real results are cached for 5 minutes keyed on (query, max_results); an error
    row or an empty list is never cached."""
    cache_key = f"{query}||{max_results}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    results = None
    try:
        # PR-2B-2 (D-15): Keychain-aware getter (cfg→Keychain migration + env).
        from codec_config import get_serper_api_key
        serper_key = (get_serper_api_key() or "").strip()
        if serper_key:
            results = search_serper(query, serper_key, max_results, raise_errors=True)
    except Exception as e:
        # The HTTP status or the error type only, never the query.
        log.warning("Serper search failed (%s); using DuckDuckGo",
                    e if isinstance(e, SerperStatusError) else type(e).__name__)
        results = None
    if results is None:
        results = search_ddg(query, max_results)
    if _cacheable(results):
        _cache_put(cache_key, results)
    return results


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
