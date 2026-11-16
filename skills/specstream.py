"""CODEC Skill: SpecStream - live web search and page extraction via SpecStream."""
SKILL_NAME = "specstream"
SKILL_DESCRIPTION = "Search the live web or extract page text using the SpecStream API"
SKILL_MCP_EXPOSE = True
SKILL_TRIGGERS = [
    "specstream search",
    "specstream extract",
    "search with specstream",
    "extract with specstream",
    "read with specstream",
    "specstream",
]

import re
import requests

_MAX_EXTRACT_CHARS = 8_000
_SEARCH_TIMEOUT = 20
_EXTRACT_TIMEOUT = 35

_TRIGGER_PREFIXES = (
    "search with specstream",
    "extract with specstream",
    "read with specstream",
    "specstream search",
    "specstream extract",
    "specstream read",
    "specstream",
)


def _get_config() -> tuple[str, str, str]:
    """Return (base_url, api_key, speed) from codec_config."""
    import codec_config

    base_url = getattr(codec_config, "SPECSTREAM_BASE_URL", "https://api.specstream.ai")
    cfg = getattr(codec_config, "cfg", None)
    if base_url == "https://api.specstream.ai" and isinstance(cfg, dict):
        override = (cfg.get("specstream_base_url") or "").strip()
        if override:
            base_url = override
    base_url = (base_url or "https://api.specstream.ai").strip().rstrip("/")

    api_key = ""
    if hasattr(codec_config, "get_specstream_api_key"):
        api_key = (codec_config.get_specstream_api_key() or "").strip()

    speed = ""
    if isinstance(cfg, dict):
        speed = (cfg.get("specstream_speed") or "").strip().lower()
    return base_url, api_key, speed


def _emit_audit(
    event: str,
    *,
    outcome: str = "ok",
    error_type: str | None = None,
    message: str = "",
    extra: dict | None = None,
) -> None:
    """Emit a query-free audit event to ~/.codec/audit.log."""
    try:
        from codec_audit import audit

        audit(
            SKILL_NAME,
            event=event,
            source="codec-specstream",
            outcome=outcome,
            level="warning" if outcome != "ok" else "info",
            error_type=error_type,
            message=message,
            extra=extra,
        )
    except Exception:
        pass


def _fence_output(text: str, label: str, context: str) -> str:
    """Wrap external content in an untrusted-text fence unless in agent mode."""
    if isinstance(context, str) and context.strip().lower().startswith("agent:"):
        return text
    try:
        import codec_fence

        return codec_fence.fence(text, label)
    except Exception:
        return text


def _parse_task(task: str) -> tuple[str, list[str] | str]:
    """Return ('extract', urls), ('search', query), or ('empty', '')."""
    raw = (task or "").strip()
    if not raw:
        return ("empty", "")

    low = raw.lower()
    extract_hint = False
    cleaned = raw
    for prefix in _TRIGGER_PREFIXES:
        if low.startswith(prefix):
            extract_hint = "extract" in prefix or "read" in prefix
            cleaned = raw[len(prefix):].strip()
            break

    cleaned = re.sub(r"^(?:for|:|-)\s+", "", cleaned, flags=re.IGNORECASE).strip()
    urls = [u.rstrip(").,;'\"") for u in re.findall(r"https?://[^\s<>\"')\]]+", cleaned)]
    non_url = re.sub(r"https?://[^\s<>\"')\]]+", "", cleaned).strip(" ,;")

    if urls and (extract_hint or not non_url):
        return ("extract", urls[:20])
    if cleaned:
        return ("search", cleaned)
    return ("empty", "")


def _format_error(resp) -> str:
    """Turn a non-200 SpecStream response into a plain status message."""
    code = ""
    msg = ""
    try:
        err = (resp.json() or {}).get("error") or {}
        code = str(err.get("code") or "")
        msg = str(err.get("message") or "")
    except ValueError:
        pass

    if resp.status_code == 401:
        return "SpecStream API key was rejected (401). Check your specstream_api_key."
    if resp.status_code == 402:
        return f"SpecStream balance or monthly limit reached (402): {msg or code}".strip(": ")
    if resp.status_code == 403:
        return f"SpecStream key permission or spend limit refused the call (403): {msg or code}".strip(": ")
    if resp.status_code in (429, 503):
        retry = resp.headers.get("Retry-After")
        suffix = f" Retry after {retry}s." if retry else ""
        return f"SpecStream rate limit or capacity reached ({resp.status_code}).{suffix}"
    detail = msg or code or f"HTTP {resp.status_code}"
    return f"SpecStream request failed ({resp.status_code}): {detail}"


def _format_search(data: dict) -> str:
    """Format SpecStream /v1/web-search data payload into readable text."""
    lines: list[str] = []
    answer = data.get("answer")
    if isinstance(answer, dict) and answer.get("value"):
        lines.append(f"Answer: {answer['value']}")
        if answer.get("page"):
            lines.append(f"Source: {answer['page']}")

    sources = data.get("sources") or []
    for i, item in enumerate(sources[:5], 1):
        if not isinstance(item, dict):
            continue
        title = (item.get("title") or item.get("url") or "Result").strip()
        url = (item.get("url") or "").strip()
        snippet = (item.get("snippet") or "").strip()
        lines.append(f"{i}. {title}")
        if snippet:
            lines.append(f"   {snippet[:300]}")
        if url:
            lines.append(f"   {url}")

    if not sources:
        for i, row in enumerate((data.get("rows") or [])[:5], 1):
            if not isinstance(row, dict):
                continue
            name = (row.get("name") or "Result").strip()
            page = (row.get("page") or "").strip()
            snippet = (row.get("snippet") or "").strip()
            lines.append(f"{i}. {name}")
            if snippet:
                lines.append(f"   {snippet[:300]}")
            if page:
                lines.append(f"   {page}")

    return "\n".join(lines).strip()


def _format_extract(data: dict) -> str:
    """Format SpecStream /v1/extract data payload into readable text."""
    blocks: list[str] = []
    remaining = _MAX_EXTRACT_CHARS
    for item in data.get("results") or []:
        if not isinstance(item, dict):
            continue
        url = (item.get("url") or "").strip()
        status = (item.get("status") or "unknown").strip()
        title = (item.get("title") or "").strip()
        text = (item.get("text") or "").strip()

        if status == "ok" and text:
            header = f"{title} ({url})" if title else url
            chunk = text[:remaining]
            if len(text) > remaining:
                chunk += f"\n...[truncated, {len(text)} chars total]"
            blocks.append(f"{header}\n{chunk}" if header else chunk)
            remaining = max(0, remaining - len(chunk))
            if remaining == 0:
                break
        else:
            line = f"{url}: {status}" if url else f"status: {status}"
            if text:
                line += f"\n{text[:300]}"
            blocks.append(line)

    return "\n\n".join(blocks).strip()


def _run_search(base_url: str, api_key: str, query: str, speed: str, context: str) -> str:
    params: dict[str, object] = {"q": query[:2000], "num": 5}
    if speed in ("fast", "default"):
        params["speed"] = speed

    resp = requests.get(
        f"{base_url}/v1/web-search",
        params=params,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=_SEARCH_TIMEOUT,
    )
    if resp.status_code != 200:
        _emit_audit(
            "specstream_search",
            outcome="error",
            error_type=f"HTTP_{resp.status_code}",
            message="SpecStream search error",
            extra={"query_chars": len(query), "status_code": resp.status_code},
        )
        return _format_error(resp)

    data = (resp.json() or {}).get("data") or {}
    sources = data.get("sources") or []
    _emit_audit(
        "specstream_search",
        outcome="ok",
        message="SpecStream search completed",
        extra={"query_chars": len(query), "results": len(sources), "status_code": resp.status_code},
    )
    formatted = _format_search(data)
    if not formatted:
        return "SpecStream found no results for that query."
    return _fence_output(formatted, "SpecStream web search results", context)


def _run_extract(base_url: str, api_key: str, urls: list[str], context: str) -> str:
    resp = requests.post(
        f"{base_url}/v1/extract",
        json={"urls": urls, "format": "markdown"},
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=_EXTRACT_TIMEOUT,
    )
    if resp.status_code != 200:
        _emit_audit(
            "specstream_extract",
            outcome="error",
            error_type=f"HTTP_{resp.status_code}",
            message="SpecStream extract error",
            extra={"url_count": len(urls), "status_code": resp.status_code},
        )
        return _format_error(resp)

    data = (resp.json() or {}).get("data") or {}
    results = data.get("results") or []
    _emit_audit(
        "specstream_extract",
        outcome="ok",
        message="SpecStream extract completed",
        extra={"url_count": len(urls), "results": len(results), "status_code": resp.status_code},
    )
    formatted = _format_extract(data)
    if not formatted:
        return "SpecStream returned no content for the requested URL(s)."
    return _fence_output(formatted, "SpecStream extracted page content", context)


def run(task: str, app: str = "", ctx: str = "") -> str:
    """Execute a SpecStream web search or page extract task."""
    mode, payload = _parse_task(task)
    if mode == "empty":
        return "Specify a search query or URL for SpecStream, e.g. 'specstream search <query>' or 'specstream extract <url>'."

    base_url, api_key, speed = _get_config()
    if not (base_url.startswith("https://") or base_url.startswith("http://")):
        return "SpecStream error: specstream_base_url in codec_config must start with http:// or https://."

    if not api_key:
        return (
            "SpecStream API key is not configured. Set SPECSTREAM_API_KEY or store it "
            "in Keychain as ai.avadigital.codec.specstream_api_key (https://specstream.ai/workspace)."
        )

    context = app if isinstance(app, str) and app.strip().lower().startswith("agent:") else ctx
    try:
        if mode == "extract":
            return _run_extract(base_url, api_key, payload, context)
        return _run_search(base_url, api_key, str(payload), speed, context)
    except requests.exceptions.Timeout:
        _emit_audit(f"specstream_{mode}", outcome="timeout", error_type="Timeout")
        return "SpecStream request timed out."
    except requests.exceptions.ConnectionError:
        _emit_audit(f"specstream_{mode}", outcome="error", error_type="ConnectionError")
        return "SpecStream connection failed."
    except (requests.exceptions.RequestException, ValueError) as e:
        _emit_audit(f"specstream_{mode}", outcome="error", error_type=type(e).__name__)
        return f"SpecStream request failed: {e}"
