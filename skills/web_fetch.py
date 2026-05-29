"""CODEC Skill: Web Fetch"""
SKILL_NAME = "web_fetch"
SKILL_DESCRIPTION = "fetches and returns the content of a specified URL"
SKILL_TRIGGERS = ["fetch", "get url"]
SKILL_MCP_EXPOSE = True

import re
import requests

def run(task: str, context: str = "") -> str:
    try:
        m = re.search(r"https?://\S+", (task or "") + " " + (context or ""))
        if not m:
            return "web_fetch failed: no http(s) URL found in task"
        url = m.group(0).rstrip(").,;'\"")

        # Fix #7 (H1): SSRF guard BEFORE the request. Blocks fetches of internal
        # / loopback / link-local / cloud-metadata addresses — the fetched body
        # flows back into the chat/LLM transcript, so a read is an exfil path.
        import codec_ssrf
        try:
            codec_ssrf.validate_url(url)
        except codec_ssrf.SSRFError as e:
            return f"web_fetch failed: blocked URL ({e})"

        response = requests.get(url, timeout=10)
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