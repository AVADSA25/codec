"""Local-first defaults (owner decision D-37, docs/LOCAL-FIRST-DEFAULTS-DESIGN.md).

1. Chat searches the web only with the "Search the web" toggle (force_search),
   sends a short query, marks the reply and writes one lengths-only audit line.
2. A Vibe request (web:false) fetches no link and sends no search.
3. Vision stays local unless vision_provider is set.
4. Mail triage and text assist use the local model while chat is on a cloud model.
5. A new Google Doc is private unless share=True; a failed run removes its doc.

The modules changed here are loaded from this checkout by file path: some skills
put ~/codec-repo first on sys.path, and a plain import could then load the live
copy. No test reaches the network: search, link fetch, Gemini, the LLM and the
Google APIs are all replaced.
"""
from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
import re
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_LOADED: dict = {}


def _load(rel: str):
    """This checkout's copy of a module, loaded once by file path."""
    if rel not in _LOADED:
        name = "_d37_" + re.sub(r"\W", "_", rel)
        spec = importlib.util.spec_from_file_location(name, REPO / rel)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _LOADED[rel] = mod
    return _LOADED[rel]


# The 14 substring triggers that used to search on their own (routes/chat.py before D-37).
OLD_TRIGGERS = ["search for", "search the web", "google", "look up", "find out",
                "what is the latest", "current news", "recent", "today's", "right now",
                "who won", "stock price", "weather in", "news about"]


@pytest.fixture
def web(monkeypatch):
    """Replace the search, the link fetch, the audit and memory recall; record calls."""
    chat = _load("routes/chat.py")
    import codec_memory
    import codec_search
    import routes.qchat as qchat
    import routes.vibe as vibe
    rec = types.SimpleNamespace(chat=chat, searched=[], fetched=[], audit=[])

    def fake_search(query, max_results=5):
        rec.searched.append(query)
        return [{"title": "Result", "url": "https://result.example", "snippet": "s"}]

    class _NoMemory:
        def get_context(self, text, n=8):
            return ""

        def search_recent(self, days=3, limit=5):
            return []

    class _NoRows:
        def execute(self, *a, **k):
            return self

        def fetchall(self):
            return []

    monkeypatch.setattr(codec_search, "search", fake_search)
    monkeypatch.setattr(codec_search, "format_results", lambda r, max_snippets=5: "RESULTS")
    monkeypatch.setattr(chat, "_fetch_url_content",
                        lambda url, max_chars=8000: rec.fetched.append(url) or "PAGE TEXT")
    monkeypatch.setattr(chat, "log_event", lambda *a, **k: rec.audit.append((a, k)))
    monkeypatch.setattr(codec_memory, "CodecMemory", _NoMemory)
    monkeypatch.setattr(qchat, "qchat_db", lambda: _NoRows())
    monkeypatch.setattr(vibe, "vibe_db", lambda: _NoRows())
    return rec


# ── 1. Web search only with the toggle ──────────────────────────────────────

@pytest.mark.parametrize("trigger", OLD_TRIGGERS)
def test_search_trigger_words_alone_send_nothing(web, trigger):
    meta: dict = {}
    msgs = [{"role": "user", "content": f"between us, {trigger} my landlord dispute with Mr Example"}]
    assert web.chat._enrich_messages(msgs, {}, meta=meta) == msgs
    assert web.searched == [] and "web" not in meta and web.audit == []


def test_search_toggle_sends_a_short_query_marks_the_reply_and_audits_lengths(web):
    private = "My bank account is 12345678 and my doctor said the scan looked worrying."
    text = ("Search the web for the recent opening hours of the Lisbon city library and the "
            "nearest metro station to it\n" + private)
    meta: dict = {}
    out = web.chat._enrich_messages([{"role": "user", "content": text}], {}, force_search=True, meta=meta)
    assert len(web.searched) == 1
    query = web.searched[0]
    assert 0 < len(query.split()) <= 12 and query.startswith("the recent opening hours")
    assert "bank" not in query and "Search the web" not in query, "the lead-in and the pasted lines stay"
    assert "RESULTS" in out[0]["content"] and meta["web"] == {"searched": True, "results": 1}
    assert len(web.audit) == 1
    (event, source, _message), kw = web.audit[0]
    assert event == "web_search_sent" and source == "codec-dashboard"
    extra = kw["extra"]
    assert set(extra) == {"query_words", "query_chars", "message_chars", "results", "temporary"}
    assert all(isinstance(v, (int, bool)) for v in extra.values()), "lengths only, never text"
    assert extra["query_words"] == len(query.split()) and extra["message_chars"] == len(text)


def test_search_query_is_at_most_twelve_words():
    chat = _load("routes/chat.py")
    long = " ".join(f"word{i}" for i in range(40))
    assert chat._derive_search_query(long).split() == [f"word{i}" for i in range(12)]
    assert chat._derive_search_query("Hey CODEC, can you look up who won the 2026 final?") == "who won the 2026 final"
    assert chat._derive_search_query("https://only.a/link") == ""


def test_search_marker_reaches_the_reply_on_both_paths():
    src = (REPO / "routes" / "chat.py").read_text(encoding="utf-8")
    assert "yield f\"data: {json.dumps({'web': _web_used})}\\n\\n\"" in src and 'out["web"] = _web_used' in src
    page = (REPO / "codec_chat.html").read_text(encoding="utf-8")
    assert page.count("if(j.web){_lastWeb=j.web}") == 2, "send and regenerate"
    assert "s.textContent = 'Searched the web';" in page
    assert "if(md&&_lastWeb)renderWebUsed(md);" in page and "if(md2&&_lastWeb)renderWebUsed(md2);" in page
    assert page.count("force_search:webSearchEnabled") == 2, "the existing toggle, no new control"


# ── 2. Vibe: web:false ──────────────────────────────────────────────────────

class _Req:
    def __init__(self, body):
        self._body = body
        self.query_params = {}

    async def json(self):
        return self._body


def _post_chat(web, monkeypatch, tmp_path, body):
    """Run POST /api/chat (non-stream) with the model and the prompt builder replaced."""
    import codec_llm
    import codec_models
    import codec_usage
    chat = web.chat
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"llm_base_url": "http://localhost:8083/v1"}))
    monkeypatch.setattr(chat, "CONFIG_PATH", str(cfg))
    monkeypatch.setattr(chat, "_try_skill", lambda text, temporary=False: (None, None))
    monkeypatch.setattr(chat, "_build_chat_system_prompt", lambda *a, **k: "SYSTEM")
    monkeypatch.setattr(codec_models, "ensure_llm_available", lambda *a, **k: None)
    monkeypatch.setattr(codec_usage, "record_reply", lambda *a, **k: None)
    monkeypatch.setattr(codec_llm, "call", lambda messages, **k: "Here is the widget.")
    return asyncio.run(chat.chat_completion(_Req(body)))


def _vibe_body(**extra):
    files = "\n".join(f"const line{i} = 'recent news';" for i in range(120))
    user = ("Add a recent news widget that reads https://example.org/feed.json\n\n[PROJECT FILES]\n"
            "Open file: app.js\nAll files: app.js\n```javascript file:app.js\n" + files + "\n```")
    return {"messages": [{"role": "system", "content": "You are CODEC Vibe, a coding assistant."},
                         {"role": "user", "content": user}],
            "thinking": False, "stream": False, "workdir": "", **extra}


def test_vibe_body_with_web_false_fetches_and_searches_nothing(web, monkeypatch, tmp_path):
    out = _post_chat(web, monkeypatch, tmp_path, _vibe_body(web=False))
    assert out["response"] == "Here is the widget." and "web" not in out
    assert web.fetched == [] and web.searched == []
    msgs = _vibe_body()["messages"]
    web.chat._enrich_messages(msgs, {}, force_search=True, web=False)
    assert web.fetched == [] and web.searched == [], "web:false wins over the toggle"
    _post_chat(web, monkeypatch, tmp_path, _vibe_body())
    assert web.fetched == ["https://example.org/feed.json"], "control: without web:false the link is read"


def test_vibe_page_sends_web_false_and_the_toggle_search_marks_the_reply(web, monkeypatch, tmp_path):
    page = (REPO / "codec_vibe.html").read_text(encoding="utf-8")
    send = page[page.index("var r = await fetch('/api/chat', {"):][:300]
    assert "workdir: _workdir, web: false}" in send
    body = {"messages": [{"role": "user", "content": "opening hours of the city library"}],
            "force_search": True, "stream": False}
    out = _post_chat(web, monkeypatch, tmp_path, body)
    assert out["web"] == {"searched": True, "results": 1} and web.searched == ["opening hours of the city library"]


# ── 3. Vision: local unless vision_provider is set ──────────────────────────

def test_vision_stays_local_with_a_gemini_key_and_no_provider(monkeypatch):
    import codec_config
    import requests
    vision = _load("codec_vision.py")
    posted = []

    class _Resp:
        status_code = 200

        def __init__(self, url):
            self.url = url

        def json(self):
            if "googleapis" in self.url:
                return {"candidates": [{"content": {"parts": [{"text": "gemini"}]}}]}
            return {"choices": [{"message": {"content": "local"}}]}

    monkeypatch.setattr(requests, "post", lambda url, **k: posted.append(url) or _Resp(url))
    monkeypatch.setattr(codec_config, "get_gemini_api_key", lambda: "gemini-key")
    monkeypatch.setattr(codec_config, "cfg", {})
    assert vision.describe_sync("b64", "what is on my screen?") == "local"
    assert posted and not any("googleapis" in u for u in posted), "a Gemini key alone sends nothing to Google"
    monkeypatch.setattr(codec_config, "cfg", {"vision_provider": "gemini"})
    assert vision.describe_sync("b64", "what is on my screen?") == "gemini", "control: the explicit setting still works"


def test_wake_word_and_voice_default_to_local_vision():
    for rel in ("codec.py", "codec_voice.py"):
        src = (REPO / rel).read_text(encoding="utf-8")
        assert '_cfg.get("vision_provider", "local")' in src, rel
        assert '"gemini" if GEMINI_API_KEY' not in src, rel


# ── 4. Mail triage and text assist: the local model ─────────────────────────

LOCAL_URL = "http://localhost:8083/v1"
LOCAL_MODEL = "mlx-community/Qwen3.6-35B-A3B-4bit"
CLOUD_CFG = {
    "llm_base_url": "https://api.cloud-model.example/v1", "llm_model": "cloud-x",
    "extra_models": [{"id": "cloud-x", "label": "Cloud X", "base_url": "https://api.cloud-model.example/v1",
                      "key_slot": "cloud_x_key", "price_in_per_m": 1, "price_out_per_m": 2}],
    "llm_local_restore": {"llm_base_url": LOCAL_URL, "llm_model": LOCAL_MODEL},
}


@pytest.fixture
def llm(monkeypatch):
    import codec_cloud_models
    import codec_llm
    assert codec_cloud_models.active_entry(CLOUD_CFG) is not None, "precondition: a registered cloud entry is active"
    sent = []
    monkeypatch.setattr(codec_llm, "call", lambda messages, **k: sent.append(k) or "[]")
    return sent


def test_triage_classify_uses_the_local_model_while_on_cloud(llm, monkeypatch):
    triage = _load("codec_email_triage.py")
    monkeypatch.setattr(triage, "_load_cfg", lambda: dict(CLOUD_CFG))
    triage.classify([{"sender": "A Example", "subject": "Invoice", "snippet": "please pay"}])
    assert [(k["base_url"], k["model"]) for k in llm] == [(LOCAL_URL, LOCAL_MODEL)]


def test_textassist_call_qwen_uses_the_local_model_while_on_cloud(llm):
    # codec_textassist.py runs on import (it reads the clipboard), so only its
    # two functions are taken from the source and run here.
    src = (REPO / "codec_textassist.py").read_text(encoding="utf-8")
    funcs = [n for n in ast.parse(src).body
             if isinstance(n, ast.FunctionDef) and n.name in ("get_config", "call_qwen")]
    ns: dict = {"re": re}
    exec(compile(ast.Module(body=funcs, type_ignores=[]), "codec_textassist.py", "exec"), ns)
    ns["get_config"] = lambda: dict(CLOUD_CFG)
    ns["call_qwen"]("helo wrld", "proofread")
    assert [(k["base_url"], k["model"]) for k in llm] == [(LOCAL_URL, LOCAL_MODEL)]


# ── 5. Google Docs: private unless share=True ───────────────────────────────

class _Exec:
    def __init__(self, rec, name, kw, result=None, exc=None):
        self.rec, self.name, self.kw, self.result, self.exc = rec, name, kw, result, exc

    def execute(self):
        self.rec.append((self.name, self.kw))
        if self.exc:
            raise self.exc
        return self.result or {}


@pytest.fixture
def gdocs(monkeypatch):
    """codec_gdocs with fake Google services: records every Docs and Drive call."""
    rec = types.SimpleNamespace(calls=[], fail=False)

    def build(service, version, credentials=None):
        if service == "docs":
            docs = types.SimpleNamespace(
                create=lambda body: _Exec(rec.calls, "docs.create", {"body": body}, {"documentId": "doc123"}),
                batchUpdate=lambda documentId, body: _Exec(
                    rec.calls, "docs.batchUpdate", {}, exc=RuntimeError("quota") if rec.fail else None))
            return types.SimpleNamespace(documents=lambda: docs)
        perms = types.SimpleNamespace(create=lambda **kw: _Exec(rec.calls, "drive.permissions.create", kw))
        files = types.SimpleNamespace(update=lambda **kw: _Exec(rec.calls, "drive.files.update", kw))
        return types.SimpleNamespace(permissions=lambda: perms, files=lambda: files)

    discovery = types.ModuleType("googleapiclient.discovery")
    discovery.build = build
    parent = types.ModuleType("googleapiclient")
    parent.discovery = discovery
    auth = types.ModuleType("codec_google_auth")
    auth.get_credentials = lambda: "creds"
    monkeypatch.setitem(sys.modules, "googleapiclient", parent)
    monkeypatch.setitem(sys.modules, "googleapiclient.discovery", discovery)
    monkeypatch.setitem(sys.modules, "codec_google_auth", auth)
    mod = _load("codec_gdocs.py")
    monkeypatch.setattr(mod, "_pexels_fetch_one", lambda *a, **k: None)
    rec.mod = mod
    return rec


def _names(rec):
    return [name for name, _ in rec.calls]


def test_gdoc_is_private_unless_share_is_asked_for(gdocs):
    url = gdocs.mod.create_google_doc("Plan", "# Plan\n\nThe body.")
    assert url == "https://docs.google.com/document/d/doc123/edit"
    assert "drive.permissions.create" not in _names(gdocs), "no 'anyone' permission by default"
    gdocs.calls.clear()
    gdocs.mod.create_google_doc("Plan", "# Plan\n\nThe body.", share=True)
    perms = [kw for name, kw in gdocs.calls if name == "drive.permissions.create"]
    assert perms == [{"fileId": "doc123", "body": {"type": "anyone", "role": "reader"}, "fields": "id"}]


def test_failed_gdoc_run_removes_its_own_doc(gdocs):
    gdocs.fail = True
    assert gdocs.mod.create_google_doc("Plan", "# Plan\n\nThe body.", share=True) is None
    assert ("drive.files.update", {"fileId": "doc123", "body": {"trashed": True}}) in gdocs.calls
    assert "drive.permissions.create" not in _names(gdocs), "an unfinished doc is never shared"
