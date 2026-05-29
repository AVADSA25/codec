"""CODEC Skill: Web Fetch"""
SKILL_NAME = "web_fetch"
SKILL_DESCRIPTION = "fetches and returns the content of a specified URL"
SKILL_TRIGGERS = ["fetch", "get url"]
SKILL_MCP_EXPOSE = True

import re
from urllib.parse import urljoin

import requests


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
        if "text" in content_type or "json" in content_type:
            return response.text
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