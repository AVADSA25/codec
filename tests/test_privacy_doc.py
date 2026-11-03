"""PRIVACY.md is the one privacy page, and every claim it makes about hosts,
logs, Keychain names and deletion equals the code.

PR-6F (Audit F / F-2) added the page and pinned the EU questions (GDPR, AI Act,
contact, processors). P-019 (audit 25 Oct 2026) made root PRIVACY.md the only
real file (docs/PRIVACY.md points to it) and tied each claim to the constant
that proves it, so changing either side without the other fails here.

Reference: docs/PRIVACY-TRUTH-DESIGN.md.
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "PRIVACY.md"
KC_PREFIX = "ai.avadigital.codec."


def _doc() -> str:
    """PRIVACY.md with whitespace collapsed, so a claim can wrap lines."""
    return " ".join(DOC.read_text().split())


def _section(title: str) -> str:
    text = DOC.read_text()
    start = text.index(title)
    nxt = text.find("\n## ", start + len(title))
    return " ".join(text[start:nxt if nxt != -1 else None].split())


def _src(rel: str) -> str:
    return (REPO / rel).read_text()


def _const(rel: str, name: str):
    """A module-level literal (str, number, dict, tuple, frozenset({...}))."""
    for node in ast.parse(_src(rel)).body:
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
        else:
            continue
        if name in targets:
            value = node.value
            if isinstance(value, ast.Call) and value.args:   # frozenset({...})
                value = value.args[0]
            return ast.literal_eval(value)
    raise AssertionError(f"{rel} no longer defines {name}")


def _code_keychain_names() -> set:
    """Every Keychain name the code uses, by git grep: KEY_* in codec_keychain.py,
    KEYCHAIN_KEY / KEY_SLOT anywhere, a full ai.avadigital.codec.<name> service
    constant, and any literal keychain_get/set/delete("<name>") call."""
    greps = [
        ["-E", r'^KEY_[A-Z_]+ = "[a-z0-9_]+"', "--", "codec_keychain.py"],
        ["-E", r'^(KEYCHAIN_KEY|KEY_SLOT) = "[a-z0-9_]+"|= "ai\.avadigital\.codec\.[a-z0-9_]+"'
               r'|keychain_(get|set|delete)\("[a-z0-9_]+"', "--", "*.py", ":!tests"],
    ]
    names = set()
    for args in greps:
        r = subprocess.run(["git", "grep", "-h", "-o", *args], cwd=REPO,
                           capture_output=True, text=True)
        if r.returncode not in (0, 1):
            pytest.skip(f"git grep unavailable: {r.stderr.strip()[:120]}")
        for match in r.stdout.split():
            m = re.search(r'"([a-z0-9_.]+)"', match)
            if m:
                names.add(m.group(1).removeprefix(KC_PREFIX))
    assert len(names) >= 10, f"git grep found too few Keychain names: {sorted(names)}"
    return names


# ── one file, the EU questions (PR-6F) ───────────────────────────────────────

def test_privacy_doc_present_and_complete():
    assert DOC.exists(), "F-2: PRIVACY.md must exist at the repo root"
    t = _doc()
    assert "local-first" in t.lower(), "must state the local-first default"
    assert "GDPR" in t, "must cover GDPR (paid tier, EU market)"
    assert "AI Act" in t, "must cover the EU AI Act transparency obligation"
    assert "privacy@avadigital.ai" in t, "must give a data-subject contact"


def test_privacy_doc_discloses_cloud_flows():
    t = _doc()
    for processor in ("Anthropic", "Cloudflare", "Google", "Telegram", "Google Gemini", "GitHub"):
        assert processor in t, f"PRIVACY.md must disclose the {processor} data flow"
    assert "leaves your Mac" in t, "must have an explicit 'what leaves the machine' section"


def test_docs_privacy_is_a_pointer_to_the_root_file():
    pointer = (REPO / "docs" / "PRIVACY.md").read_text()
    assert "(../PRIVACY.md)" in pointer, "docs/PRIVACY.md must link the root PRIVACY.md"
    assert len(pointer.splitlines()) <= 6, "docs/PRIVACY.md must stay a short pointer, not a second page"
    assert "|---" not in pointer and "GDPR (" not in pointer, "a second privacy text crept back"


# ── hosts ────────────────────────────────────────────────────────────────────

def test_update_check_host_and_interval_match_the_code():
    t = _section("### 2.1")
    assert _const("codec_update.py", "DEFAULT_FEED_URL") in t
    m = re.search(r"setInterval\(checkForUpdate,\s*([\d*\s]+)\)", _src("codec_dashboard.html"))
    assert m, "the dashboard no longer re-checks for updates on a timer"
    ms = 1
    for part in m.group(1).split("*"):
        ms *= int(part)
    assert f"every {ms // 3_600_000} hours" in t
    assert "sparkle_feed_url" in t and '"sparkle_feed_url"' in _src("codec_update.py")
    assert 'expanduser("~/Downloads")' in _src("codec_update.py") and "`~/Downloads`" in t


def test_licence_key_fetch_matches_the_code():
    src = _src("codec_license.py")
    base = re.search(r'cfg\.get\("license_base_url"\) or "([^"]+)"', src)
    assert base and 'base.rstrip("/") + "/public-key"' in src
    t = _section("### 2.1")
    assert base.group(1) + "/public-key" in t
    assert _const("codec_license.py", "_PUBKEY_CACHE_TTL") == 3600.0
    assert "at most once an hour per CODEC process once the key has been fetched" in t
    # A failed fetch is not cached in memory, so the next licence check (the
    # state is cached _LICENSE_STATE_CACHE_TTL seconds) asks again.
    assert "_PUBKEY_CACHE_VALUE = pem" in src and _const("codec_license.py", "_LICENSE_STATE_CACHE_TTL") == 60.0
    assert "at most once a minute while the fetch keeps failing" in t
    fetch = ast.get_source_segment(src, next(
        n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "_fetch_pubkey"))
    assert "_license_token" not in fetch and "Authorization" not in fetch, "the key fetch now sends the token"
    assert "is not sent" in t


def test_gemini_only_with_vision_provider():
    for rel in ("codec_vision.py", "codec.py", "codec_voice.py"):
        assert 'get("vision_provider", "local")' in _src(rel), f"{rel}: vision no longer defaults to local"
    vision = _src("codec_vision.py")
    assert 'provider == "gemini" and gem_key' in vision
    host = re.search(r"https://([a-z.]+)/v1beta/models/", vision).group(1)
    t = _doc()
    assert f"Google Gemini** (`{host}`) only when `config.json` has `\"vision_provider\": \"gemini\"`" in t


def test_web_search_claims_match_the_code():
    t = _doc()
    chat = _src("routes/chat.py")
    assert "(web and force_search and not urls)" in chat, "chat search is no longer gated on the toggle"
    assert f"at most {_const('routes/chat.py', 'SEARCH_QUERY_MAX_WORDS')} words" in t
    assert ">Search the web<" in _src("codec_chat.html") and 'the "Search the web" toggle is on' in t
    for host in sorted(set(re.findall(r'"https://([a-z.]+)/', _src("codec_search.py")))):
        assert f"`{host}`" in t, f"PRIVACY.md does not name the search host {host}"
    triggers = _const("skills/web_search.py", "SKILL_TRIGGERS")
    for phrase in ("search for", "look up", "who is"):
        assert phrase in triggers and f'"{phrase}"' in t


def test_model_provider_defaults_match_the_code():
    t = _doc()
    local = re.search(r'QWEN_BASE_URL\s+= cfg\.get\("llm_base_url", "([^"]+)"\)', _src("codec_config.py"))
    assert local and f"The local model (`{local.group(1)}`) by default" in t
    proxy = _const("codec_setup.py", "AVA_PROXY_URL").removeprefix("https://")
    assert f"(`{proxy}`)" in t and f"(`{_const('codec_setup.py', 'AVA_DEFAULT_MODEL')}` by default)" in t
    for rel in ("codec_email_triage.py", "codec_textassist.py", "codec_briefing.py",
                "codec_agent_plan.py", "codec_agent_runner.py"):
        assert "codec_cloud_models.local_only(" in _src(rel), f"{rel} no longer stays on the local model"
    assert "Mail triage, text assist, the morning briefing and Project-mode agents always stay local" in t


def test_push_hosts_match_the_code():
    t = _doc()
    for host in _const("codec_push.py", "_PUSH_HOSTS"):
        assert f"`{host}`" in t
    for suffix in _const("codec_push.py", "_PUSH_SUFFIXES"):
        assert f"`*{suffix}`" in t


def test_lookup_skills_alerts_and_marketplace_hosts_are_named():
    t = _doc()
    for host in sorted(set(re.findall(r'"https://([a-z.]+)/', _src("skills/bitcoin_price.py")))):
        assert f"`{host}`" in t, f"PRIVACY.md does not name the bitcoin price host {host}"
    registry = re.match(r"https://([a-z.]+)/", _const("codec_marketplace.py", "REGISTRY_URL")).group(1)
    assert f"`{registry}`" in t
    alerts = _src("codec_alerts.py")
    assert 'alerts_cfg.get("email"' in alerts and 'alerts_cfg.get("slack"' in alerts
    assert "**Alerts by e-mail or Slack**" in t and "`alerts.email` or `alerts.slack`" in t
    assert 's.login(cfg["from"], cfg["password"])' in alerts and "the e-mail alert password" in t
    assert 'atype == "email_check"' in _src("codec_heartbeat.py") and "A mail check: a Gmail request" in t


def test_staying_offline_names_the_web_search_skill():
    """The skill fires on a phrase, toggle or not, so 'staying offline' must say so."""
    assert "web_search" in _const("routes/chat.py", "CHAT_SKILL_ALLOWLIST")
    triggers = _const("skills/web_search.py", "SKILL_TRIGGERS")
    offline = _doc()[_doc().index("**Staying offline.**"):]
    offline = offline[:offline.index("## 6.")]
    assert "`web_search` skill" in offline and "switch that skill off on the Skills page" in offline
    for phrase in ("who is", "look up"):
        assert phrase in triggers and f'"{phrase}"' in offline


def test_every_host_named_is_called_by_the_code():
    """A host on the page that the code never calls is a stale or invented claim."""
    hosts = set(re.findall(r"`(?:https?://)?\*?\.?([a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|ai|in|org|dev|io))[/`:]",
                           DOC.read_text()))
    assert len(hosts) >= 15, f"host scan found too few hosts: {sorted(hosts)}"
    code = "".join(p.read_text(errors="ignore") for p in REPO.glob("*.py"))
    code += "".join(p.read_text(errors="ignore") for p in (REPO / "skills").glob("*.py"))
    code += "".join(p.read_text(errors="ignore") for p in (REPO / "routes").glob("*.py"))
    for host in sorted(hosts):
        if host == "myaccount.google.com":       # where the owner revokes access, not a call
            continue
        assert host in code, f"PRIVACY.md names {host}, which no CODEC code calls"


def test_false_sentences_are_gone():
    t = _doc()
    for gone in ("every outbound call is either to a host", "records every one of these calls",
                 "Tokens stored in Keychain, never on disk", "service prefix `codec.*`",
                 "trusted_skill_manifest.json", "localhost:1234", "Twilio", "50MB",
                 "Application Support/Sovereign AI Workstation"):
        assert gone not in t, f"PRIVACY.md still says {gone!r}, which the code contradicts"


# ── Keychain names, logs, retention, deletion ────────────────────────────────

def test_keychain_names_equal_the_code():
    assert _const("codec_keychain.py", "_SERVICE_PREFIX") + "." == KC_PREFIX
    listed = set(re.findall(r"`ai\.avadigital\.codec\.([a-z0-9_]+)`", _section("### Secrets in the macOS Keychain")))
    code = _code_keychain_names()
    assert listed == code, (f"Keychain table and code disagree: only on the page {sorted(listed - code)}, "
                            f"only in the code {sorted(code - listed)}")


def test_pm2_log_names_match_the_ecosystem_file():
    names = re.findall(r'^\s+name: "([^"]+)"', _src("ecosystem.config.js"), re.M)
    assert len(names) >= 10, f"ecosystem.config.js app names not found: {names}"
    first = _section("## 1. What CODEC stores")
    assert "`~/.pm2/logs/codec-*`" in first
    for name in names:
        if name != "codec" and not name.startswith("codec-"):
            assert f"`{name}-*`" in first or f"`~/.pm2/logs/{name}-*`" in first, \
                f"PRIVACY.md does not name the PM2 log of {name}"


def test_google_token_is_named_as_a_file_on_disk():
    token = os.path.basename(_const("codec_google_auth.py", "TOKEN_PATH").replace("~", ""))
    # P-082: written by atomic replace, which makes the file 0600 (codec_jsonstore).
    assert "codec_jsonstore.atomic_write_json(TOKEN_PATH" in _src("codec_google_auth.py")
    assert f"`~/.codec/{token}`" in _doc() and "On disk (0600), not in the Keychain" in _doc()


def test_audit_claims_match_the_code():
    t = _section("## 3. What the audit log records")
    audit = _src("codec_audit.py")
    assert '"""Rotate audit.log daily.' in audit and "if mtime_day >= today:" in audit
    assert "A new file starts each day (UTC)" in t
    assert f"older than {_const('codec_audit.py', '_RETAIN_DAYS')} days are deleted" in t
    groups = re.search(r"var _auditAllCategories = \[([^\]]*)\]", _src("codec_dashboard.html"))
    assert groups and f"into {len(re.findall(r'[a-z]+', groups.group(1)))} filter groups" in t
    assert f"`{_const('codec_keychain.py', 'KEY_AUDIT_HMAC_SECRET')}`" in t and "HMAC-SHA256" in t
    assert 'log_event("web_search_sent"' in _src("routes/chat.py") and "`web_search_sent`" in t
    for rel in ("codec_update.py", "codec_license.py", "codec_llm.py"):
        src = _src(rel)
        assert "audit(" not in src and "log_event(" not in src, f"{rel} now writes audit lines; update §3"
    assert "Model calls, the update check and the licence key fetch have no line of their own" in t


def test_retention_defaults_match_the_code():
    d = _const("codec_backup.py", "RETENTION_DEFAULTS")
    t = _section("## 5. Deleting your data")
    assert f"chats ({d['chats_days']} days by default)" in t
    assert f"clipboard history ({d['clipboard_days']} days) and webcam photos ({d['photos_days']} days)" in t
    first = _section("## 1. What CODEC stores")
    assert f"Chats are deleted after {d['chats_days']} days by default" in first


def test_deletion_paths_match_the_code():
    t = _section("## 5. Deleting your data")
    paths = {
        os.path.basename(_const("codec_config.py", "_NEW_DB")): "`memory.db`",
        os.path.basename(_const("skills/clipboard.py", "DB")): "`clipboard.db`",
        os.path.basename(_const("routes/qchat.py", "QCHAT_DB")): "`qchat.db`",
        os.path.basename(_const("routes/vibe.py", "VIBE_DB")): "`vibe.db`",
        os.path.basename(_const("codec_google_auth.py", "TOKEN_PATH")): "`google_token.json`",
        os.path.basename(_const("codec_config.py", "CONFIG_PATH")): "`config.json`",
    }
    for code_name, shown in paths.items():
        assert shown == f"`{code_name}`" and shown in t, f"deletion steps miss {code_name}"
    assert 'Path.home() / ".codec" / "notes"' in _src("codec_notes.py") and "`notes/`" in t
    assert '_AGENTS_DIR = _CODEC_DIR / "agents"' in _src("codec_agent_plan.py") and "`agents/`" in t
    assert 'return home() / "backups"' in _src("codec_backup.py") and "`backups/`" in t
    assert 'folder = home() / "photos"' in _src("codec_backup.py") and "`photos/`" in t
    assert f"`security delete-generic-password -s {KC_PREFIX}<name>`" in t
    assert "packaging/macos/uninstall_codec.sh" in t and (REPO / "packaging/macos/uninstall_codec.sh").exists()
