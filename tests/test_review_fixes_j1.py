"""J1 — regression tests for the post-refactor review findings.

Covers the real bugs surfaced by the code-review / security-review sweep after
the route-extraction series:

  1. SSRF guard on _fetch_url_content (chat URL auto-fetch — the injection vector)
  2. UnboundLocalError on POST /api/chat with {"tools": false}
  3. _enrich_messages repo_dir is two-levels-up (codec_search lives at repo root)
  4. _shutdown_services no longer NameErrors on the moved _qchat_conn/_vibe_conn
  5. /api/run_code rejects unsupported languages instead of running them as python
"""
from __future__ import annotations

import asyncio
import inspect
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


# ── 1. SSRF guard ──────────────────────────────────────────────────────────
class TestSSRFGuard:
    @pytest.mark.parametrize("url", [
        "http://127.0.0.1:8083/v1/chat/completions",   # local LLM
        "http://localhost/admin",
        "http://169.254.169.254/latest/meta-data/",     # cloud metadata
        "http://192.168.1.10/x",                        # private LAN
        "http://10.0.0.5/x",                            # private
        "http://[::1]/x",                               # ipv6 loopback
        "ftp://example.com/x",                          # non-http scheme
        "file:///etc/passwd",                           # file scheme
        "http://0.0.0.0/x",                             # unspecified
    ])
    def test_blocks_internal_and_non_http(self, url):
        import routes.chat as c
        assert c._url_host_is_public(url) is False, f"{url} should be blocked"

    def test_allows_public_numeric(self):
        import routes.chat as c
        # 1.1.1.1 is a public, routable address — no DNS needed.
        assert c._url_host_is_public("https://1.1.1.1/") is True

    def test_fetch_returns_empty_for_blocked_host(self):
        """_fetch_url_content must short-circuit to '' for a non-public host —
        no httpx call is made (we'd get a connection, not a block, otherwise)."""
        import routes.chat as c
        assert c._fetch_url_content("http://127.0.0.1:8083/secret") == ""


# ── 2. tools:false must not UnboundLocalError ──────────────────────────────
def test_chat_bindings_hoisted_before_use_tools_gate():
    import routes.chat as c
    src = inspect.getsource(c.chat_completion)
    i_lut = src.index('last_user_text = ""')
    i_ha = src.index("has_attachment = False")
    i_gate = src.index("if use_tools:")
    assert i_lut < i_gate, "last_user_text must be bound before the use_tools gate"
    assert i_ha < i_gate, "has_attachment must be bound before the use_tools gate"


# ── 3. _enrich_messages repo_dir resolves to repo ROOT (two dirnames) ──────
def test_enrich_messages_repo_dir_is_two_levels_up():
    src = (_REPO / "routes" / "chat.py").read_text()
    # the codec_search sys.path insert must climb to the repo root, not routes/
    assert "_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))" in src


# ── 4. shutdown handler runs clean (no NameError on moved singletons) ──────
def test_shutdown_services_no_nameerror():
    import codec_dashboard as cd
    # Must complete without raising NameError for _qchat_conn / _vibe_conn.
    asyncio.run(cd._shutdown_services())


def test_dashboard_no_dead_global_singletons():
    src = (_REPO / "codec_dashboard.py").read_text()
    assert "global _qchat_conn, _vibe_conn" not in src, (
        "dead `global _qchat_conn, _vibe_conn` should be gone — they live in "
        "routes/qchat.py + routes/vibe.py now"
    )


# ── 5. /api/run_code rejects unsupported languages ─────────────────────────
class _FakeReq:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


def test_run_code_rejects_unsupported_language():
    import routes.vibe_exec as ve
    resp = asyncio.run(ve.run_code(_FakeReq({"code": "SELECT 1;", "language": "sql"})))
    # JSONResponse with 400 — sql isn't runnable, must not fall through to python3.13
    assert getattr(resp, "status_code", None) == 400


def test_run_code_still_accepts_python():
    import routes.vibe_exec as ve
    # empty-code guard returns 400 too, but a real python snippet must NOT be
    # rejected as "unsupported language" — assert the body differs.
    resp = asyncio.run(ve.run_code(_FakeReq({"code": "", "language": "python"})))
    # empty code → 400 "No code", NOT "Unsupported language"
    import json as _json
    body = _json.loads(bytes(resp.body).decode())
    assert "Unsupported language" not in body.get("error", "")
