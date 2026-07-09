"""CODEC Skill: Web Fetch"""
SKILL_NAME = "web_fetch"
SKILL_DESCRIPTION = "fetches and returns the content of a specified URL"
SKILL_TRIGGERS = ["fetch", "get url"]
SKILL_MCP_EXPOSE = True

import html as _html
import re
from urllib.parse import urljoin

import requests

_MAX_CHARS = 12_000  # keep a fetch from blowing an agent step's context budget


def _html_to_text(raw: str) -> str:
    """Strip an HTML document down to its readable text. Stdlib-only (no bs4
    dependency risk) — good enough to make a page's actual content legible to
    the LLM instead of raw markup, which is unusable for extracting facts.

    2026-07: web_fetch previously returned response.text verbatim for ANY
    HTML page — a Project agent researching AI startup launches re-fetched
    the same page 15+ times because it kept receiving
    '<!DOCTYPE html><html lang="en">...' and could never extract a founder
    name from it, burning its entire step budget (80 steps) in the loop
    before ever finishing checkpoint 1."""
    # Drop non-content blocks entirely — their text isn't page content.
    text = re.sub(r"(?is)<(script|style|noscript|svg|head)\b.*?</\1>", " ", raw)
    text = re.sub(r"(?is)<!--.*?-->", " ", text)
    # Block-level tags become newlines so paragraphs/list items stay separable.
    text = re.sub(r"(?i)<(br|/p|/div|/li|/h[1-6]|/tr)\s*/?>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)          # strip remaining tags
    text = _html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    if len(text) > _MAX_CHARS:
        text = text[:_MAX_CHARS] + f"\n...[truncated, {len(raw)} raw chars]"
    return text


def _get_validating_redirects(url: str, max_redirects: int = 5):
    """SSRF-safe GET (Fix #7 H1 + re-audit N3). Validates the URL, then follows
    redirects MANUALLY, re-validating every hop — requests' default
    auto-redirect would otherwise reach an internal / loopback / cloud-metadata
    target via a 302 the guard never saw. Raises codec_ssrf.SSRFError on any
    blocked hop or too many redirects."""
    import codec_ssrf
    for _ in range(max_redirects + 1):
        codec_ssrf.validate_url(url)
        resp = requests.get(url, timeout=10, allow_redirects=False)
        if (resp.is_redirect or resp.is_permanent_redirect) and resp.headers.get("Location"):
            url = urljoin(url, resp.headers["Location"])
            continue
        return resp
    raise codec_ssrf.SSRFError("too many redirects")


def run(task: str, context: str = "") -> str:
    try:
        m = re.search(r"https?://\S+", (task or "") + " " + (context or ""))
        if not m:
            return "web_fetch failed: no http(s) URL found in task"
        url = m.group(0).rstrip(").,;'\"")

        # Fix #7 (H1) + re-audit N3: SSRF guard BEFORE the request AND on every
        # redirect hop. The fetched body flows back into the chat/LLM transcript,
        # so a read of an internal/metadata host is an exfil path.
        import codec_ssrf
        try:
            response = _get_validating_redirects(url)
        except codec_ssrf.SSRFError as e:
            return f"web_fetch failed: blocked URL ({e})"

        response.raise_for_status()
        
        content_type = response.headers.get("content-type", "")
        if "html" in content_type:
            return _html_to_text(response.text)
        if "text" in content_type or "json" in content_type:
            body = response.text
            return body[:_MAX_CHARS] + (f"\n...[truncated, {len(body)} raw chars]" if len(body) > _MAX_CHARS else "")
        else:
            return f"web_fetch failed: unsupported content type {content_type}"
            
    except requests.exceptions.Timeout:
        return "web_fetch failed: request timed out"
    except requests.exceptions.ConnectionError:
        return "web_fetch failed: connection refused"
    except requests.exceptions.RequestException as e:
        return f"web_fetch failed: {str(e)}"
    except Exception as e:
        return f"web_fetch failed: unexpected error {str(e)}"