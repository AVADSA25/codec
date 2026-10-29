"""CODEC browser URL policy: one check before a browser skill opens a URL (P-101).

Owner decision D-45 (29 Oct 2026). The chrome skills open URLs built from
free text in the owner's logged-in Chrome, so a prompt-injected page or
message could steer it to file://, javascript:, a local service or a cloud
metadata address. check_url allows http and https only, then applies the
codec_ssrf rules (P-008: public addresses only, parser-mismatch URLs
refused), with two owner lists in ~/.codec/config.json:browser_policy:

    {"browser_policy": {"allow_private": ["127.0.0.1:8090", "router.local"],
                        "deny": ["example-tracker.com"]}}

allow_private names hosts (optionally host:port) that may be private, such as
the CODEC dashboard; deny names hosts that are always refused (a subdomain of
a denied host is refused too). Every refusal writes one browser_url_blocked
audit line with the reason and the skill, never the URL.
"""
import json
import os
from urllib.parse import urlparse

import codec_ssrf

CONFIG_PATH = os.path.expanduser("~/.codec/config.json")
_SCHEMES = ("http", "https")


def _policy():
    try:
        with open(CONFIG_PATH) as f:
            p = json.load(f).get("browser_policy") or {}
        return p if isinstance(p, dict) else {}
    except Exception:
        return {}


def _hosts(values):
    return {str(v).strip().lower().rstrip(".") for v in (values or []) if str(v).strip()}


def _audit(reason, skill):
    try:
        from codec_audit import log_event
        log_event("browser_url_blocked", source="codec-browser-policy",
                  message=f"{skill or 'browser'} refused a URL: {reason}",
                  level="warning", outcome="error",
                  extra={"reason": reason, "skill": skill or ""})
    except Exception:
        pass


def check_url(url, skill=""):
    """Return (True, "") when a browser skill may open `url`, else
    (False, refusal text for the owner)."""
    try:
        parsed = urlparse((url or "").strip())
        scheme = (parsed.scheme or "").lower()
        host = (parsed.hostname or "").lower().rstrip(".")
        netloc = f"{host}:{parsed.port}" if parsed.port else host
    except ValueError:
        scheme, host, netloc = "", "", ""
    if scheme not in _SCHEMES:
        _audit("scheme", skill)
        return False, "I only open http and https addresses in the browser."
    policy = _policy()
    deny = _hosts(policy.get("deny"))
    if host and any(host == d or host.endswith("." + d) for d in deny):
        _audit("deny_list", skill)
        return False, "That site is on the browser deny list (browser_policy.deny in config.json)."
    allow = _hosts(policy.get("allow_private"))
    if host and (host in allow or netloc in allow):
        return True, ""
    try:
        codec_ssrf.validate_url(url)
    except codec_ssrf.SSRFError:
        _audit("non_public", skill)
        return False, ("I don't open local or private addresses in the browser. "
                       "To allow one, add its host to browser_policy.allow_private "
                       "in ~/.codec/config.json.")
    return True, ""
