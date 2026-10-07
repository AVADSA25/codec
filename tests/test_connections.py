"""Connections (UI phase 3, P3.9; docs/P3.9-DESIGN.md).

Settings > Connectors: Google Workspace status and the Mac-only Reconnect, the AI
apps signed in to CODEC's MCP server with a per-app Revoke applied by the MCP
server itself, the Telegram and iMessage bridges with a test message, and the
dashboard's macOS permissions. Nothing here touches ~/.codec, Google, Telegram or
Messages: tmp paths and fakes.
"""
from __future__ import annotations

import asyncio
import json
import os
import runpy
import secrets
import shutil
import stat
import subprocess
import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent
HOME = (REPO / "codec_dashboard.html").read_text(encoding="utf-8")


@pytest.fixture
def cn(tmp_path, monkeypatch):
    import codec_audit
    import routes.connections as c
    for name, file in (("GOOGLE_TOKEN", "google_token.json"), ("GOOGLE_CREDS", "google_credentials.json"),
                       ("MCP_CLIENTS", "mcp_clients.json"), ("MCP_REVOKE", "mcp_revoke.json"),
                       ("CONFIG_PATH", "config.json"), ("MESSAGES_DB", "chat.db")):
        monkeypatch.setattr(c, name, str(tmp_path / file))
    monkeypatch.setattr(c, "_ACCOUNT", {"key": None, "value": None, "at": 0.0})
    monkeypatch.setattr(c, "_REAUTH", {"proc": None, "started": 0.0})
    audits = []
    monkeypatch.setattr(codec_audit, "log_event", lambda *a, **k: audits.append((a, k)))
    app = FastAPI()
    app.include_router(c.router)
    return SimpleNamespace(client=TestClient(app), c=c, tmp=tmp_path, audits=audits)


def _events(audits):
    return [a[0] for a, _ in audits]


# ── Google Workspace ──────────────────────────────────────────────────────────

def _token(cn, scopes, refresh="SECRET-REFRESH-1"):
    Path(cn.c.GOOGLE_TOKEN).write_text(json.dumps({
        "token": "SECRET-ACCESS", "refresh_token": refresh, "client_secret": "SECRET-CLIENT",
        "client_id": "x.apps.googleusercontent.com", "scopes": scopes, "expiry": "2026-10-06T12:00:00Z"}))


def test_google_status_lists_scopes_and_never_a_token(cn, monkeypatch):
    import codec_google_auth as ga
    calls, cleared = [], []

    class Profile:
        def users(self):
            return self

        def getProfile(self, userId):
            calls.append(userId)
            return SimpleNamespace(execute=lambda: {"emailAddress": "owner@example.com"})

    monkeypatch.setattr(ga, "build_service", lambda api, version: Profile())
    monkeypatch.setattr(ga, "invalidate_cache", lambda: cleared.append(1))
    assert cn.client.get("/api/connections/google").json()["connected"] is False, "no token file: not connected"
    _token(cn, ga.ALL_SCOPES[:-1])
    r = cn.client.get("/api/connections/google")
    d = r.json()
    assert "SECRET" not in r.text and d["connected"] is True and d["account"] == "owner@example.com"
    assert [s["name"] for s in d["scopes"]] == ["Gmail", "Calendar", "Drive", "Docs", "Sheets", "Slides", "Tasks"]
    assert d["missing"] == ["Tasks"] and d["scopes"][-1]["ok"] is False and d["expires"] and d["updated"]
    assert d["reconnect"] == {"allowed": True, "running": False, "client_file": False}
    cn.client.get("/api/connections/google")
    assert calls == ["me"], "the account is asked once an hour per token, not on every look"
    _token(cn, ga.ALL_SCOPES, refresh="SECRET-REFRESH-2")  # a reconnect writes a new token
    d = cn.client.get("/api/connections/google").json()
    assert d["missing"] == [] and len(calls) == 2 and cleared, "a new token: asked again, the old one dropped"
    remote = cn.client.get("/api/connections/google", headers={"cf-connecting-ip": "203.0.113.9"}).json()
    assert remote["reconnect"]["allowed"] is False


def test_reconnect_runs_only_on_the_mac_and_once(cn, monkeypatch):
    started = []

    class Proc:
        def __init__(self, args, **kw):
            started.append((args, kw))
            self.code, self.killed = None, False

        def poll(self):
            return self.code

        def terminate(self):
            self.killed = True

    monkeypatch.setattr(cn.c.subprocess, "Popen", Proc)
    c = cn.client
    r = c.post("/api/connections/google/reconnect", headers={"cf-connecting-ip": "203.0.113.9"})
    assert r.status_code == 403 and "only on the Mac" in r.json()["error"]
    assert c.post("/api/connections/google/reconnect").status_code == 409, "no Google client file"
    Path(cn.c.GOOGLE_CREDS).write_text("{}")
    assert c.post("/api/connections/google/reconnect").json() == {"started": True}
    args, kw = started[0]
    assert args[-1].endswith("reauth_google.py") and kw["stdin"] == subprocess.DEVNULL and kw["start_new_session"]
    assert c.post("/api/connections/google/reconnect").status_code == 409, "one sign-in at a time"
    assert c.get("/api/connections/google").json()["reconnect"]["running"] is True
    proc = cn.c._REAUTH["proc"]
    cn.c._REAUTH["started"] = time.time() - cn.c.REAUTH_MAX_SECONDS - 5
    assert cn.c._reauth_running() is False and proc.killed, "an abandoned sign-in stops after 10 minutes"
    assert "google_reconnect_started" in _events(cn.audits)


def test_reauth_keeps_the_old_token_until_the_new_one_is_saved(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".codec").mkdir(parents=True)
    token = home / ".codec" / "google_token.json"
    token.write_text('{"token": "old"}')
    monkeypatch.setenv("HOME", str(home))
    outcome = {}

    class Flow:
        @classmethod
        def from_client_secrets_file(cls, path, scopes):
            return cls()

        def run_local_server(self, port, timeout_seconds):
            outcome["timeout"] = timeout_seconds
            if outcome.get("fail"):
                raise RuntimeError("sign-in abandoned")
            return SimpleNamespace(to_json=lambda: '{"token": "new"}')

    fake = types.ModuleType("google_auth_oauthlib.flow")
    fake.InstalledAppFlow = Flow
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib", types.ModuleType("google_auth_oauthlib"))
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib.flow", fake)
    outcome["fail"] = True
    with pytest.raises(RuntimeError):
        runpy.run_path(str(REPO / "reauth_google.py"))
    assert token.read_text() == '{"token": "old"}' and not (home / ".codec" / "google_token.json.backup").exists()
    outcome["fail"] = False
    runpy.run_path(str(REPO / "reauth_google.py"))
    assert token.read_text() == '{"token": "new"}' and stat.S_IMODE(token.stat().st_mode) == 0o600
    assert (home / ".codec" / "google_token.json.backup").read_text() == '{"token": "old"}'
    assert not (home / ".codec" / "google_token.json.new").exists() and outcome["timeout"] == 600


# ── AI apps signed in to CODEC's MCP server ───────────────────────────────────

@pytest.fixture
def mcp(tmp_path, monkeypatch, cn):
    import codec_keychain as kc
    import codec_oauth_provider as cop
    monkeypatch.setattr(kc, "is_keychain_available", lambda: False)
    monkeypatch.setattr(kc, "_FALLBACK_KEY_PATH", tmp_path / "kc_secret.key")
    monkeypatch.setattr(kc, "_FALLBACK_STORE_PATH", tmp_path / "kc_secrets.enc.json")
    revoked = []
    monkeypatch.setattr(cop, "_oauth_log_event", lambda e, *a, **k: revoked.append((e, k.get("client_id"), k.get("extra"))))
    return SimpleNamespace(cop=cop, revoked=revoked, state=tmp_path / "oauth_state.json")


def _new_provider(m):
    from mcp.server.auth.settings import ClientRegistrationOptions
    return m.cop.PersistentOAuthProvider(base_url="https://test.example.com", state_path=m.state,
                                         client_registration_options=ClientRegistrationOptions(enabled=True))


def _client(cid, name, uri):
    from mcp.shared.auth import OAuthClientInformationFull
    from pydantic import AnyUrl
    return OAuthClientInformationFull(client_id=cid, client_secret="CLIENT-SECRET-" + cid, client_name=name,
                                      redirect_uris=[AnyUrl(uri)], grant_types=["authorization_code", "refresh_token"],
                                      response_types=["code"], scope="read", client_id_issued_at=1759000000)


def _grant(m, p, cid, issued):
    """A token pair as exchange_authorization_code makes it, issued at `issued`."""
    from mcp.server.auth.provider import AccessToken, RefreshToken
    at, rt = f"codec_at_{secrets.token_hex(32)}", f"codec_rt_{secrets.token_hex(32)}"  # random, like the real ones
    p.access_tokens[at] = AccessToken(token=at, client_id=cid, scopes=["read"], expires_at=int(issued + m.cop.ACCESS_TOKEN_TTL))
    p.refresh_tokens[rt] = RefreshToken(token=rt, client_id=cid, scopes=["read"], expires_at=int(issued + m.cop.REFRESH_TOKEN_TTL))
    p._access_to_refresh_map[at] = rt
    p._refresh_to_access_map[rt] = at
    p._save()
    return at, rt


def test_the_provider_mirror_has_names_and_ids_but_no_secret(mcp, monkeypatch):
    p = _new_provider(mcp)
    asyncio.run(p.register_client(_client("alpha", "Claude", "https://claude.ai/api/mcp/auth_callback")))
    now = time.time()
    at, rt = _grant(mcp, p, "alpha", now - 60)
    path = mcp.state.parent / "mcp_clients.json"
    text = path.read_text()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert at not in text and rt not in text and "CLIENT-SECRET" not in text
    a = json.loads(text)["apps"]["alpha"]
    assert a["name"] == "Claude" and a["host"] == "claude.ai" and a["registered"] == 1759000000
    assert sorted(a["tokens"]) == sorted([at[-8:], rt[-8:]]) and a["last_used"] is None
    t0 = time.time()
    assert asyncio.run(p.load_access_token(at)) is not None
    first = json.loads(path.read_text())["apps"]["alpha"]["last_used"]
    assert first and first >= t0
    monkeypatch.setattr(mcp.cop.time, "time", lambda: t0 + 60)
    asyncio.run(p.load_access_token(at))
    assert json.loads(path.read_text())["apps"]["alpha"]["last_used"] == first, "written at most every 5 minutes"
    monkeypatch.setattr(mcp.cop.time, "time", lambda: t0 + 400)
    asyncio.run(p.load_access_token(at))
    assert json.loads(path.read_text())["apps"]["alpha"]["last_used"] == t0 + 400
    assert _new_provider(mcp)._last_used["alpha"] == t0 + 400, "last use survives a restart"


def test_revoke_signs_out_one_app_and_keeps_the_others_and_a_later_reconnect(mcp, cn, monkeypatch):
    monkeypatch.setattr(cn.c, "MCP_CLIENTS", str(mcp.state.parent / "mcp_clients.json"))
    monkeypatch.setattr(cn.c, "MCP_REVOKE", str(mcp.state.parent / "mcp_revoke.json"))
    p = _new_provider(mcp)
    asyncio.run(p.register_client(_client("alpha", "Claude", "https://claude.ai/cb")))
    asyncio.run(p.register_client(_client("beta", "Other app", "https://other.example/cb")))
    now = time.time()
    a_at, a_rt = _grant(mcp, p, "alpha", now - 120)
    b_at, b_rt = _grant(mcp, p, "beta", now - 120)
    c = cn.client
    listed = {a["client_id"]: a for a in c.get("/api/connections/apps").json()["apps"]}
    assert listed["alpha"]["live"] and listed["beta"]["live"] and listed["alpha"]["name"] == "Claude"
    assert c.post("/api/connections/apps/revoke", json={"client_id": "nope"}).status_code == 404
    assert c.post("/api/connections/apps/revoke", json={"client_id": "alpha"}).json() == {"requested": True}
    req = json.loads((mcp.state.parent / "mcp_revoke.json").read_text())["alpha"]
    assert sorted(req["tokens"]) == sorted([a_at[-8:], a_rt[-8:]]) and stat.S_IMODE(
        (mcp.state.parent / "mcp_revoke.json").stat().st_mode) == 0o600
    assert c.get("/api/connections/apps").json()["apps"][-1]["revoking"] is True, "asked, not applied yet"
    assert asyncio.run(p.load_access_token(a_at)) is None, "the next request of that app is refused"
    assert a_rt not in p.refresh_tokens
    assert asyncio.run(p.load_access_token(b_at)) is not None and b_rt in p.refresh_tokens, "the other app stays"
    assert ("mcp_client_revoked", "alpha", {"tokens_removed": 2}) in mcp.revoked
    assert "mcp_client_revoke_requested" in _events(cn.audits)
    listed = {a["client_id"]: a for a in c.get("/api/connections/apps").json()["apps"]}
    assert listed["alpha"]["live"] is False and listed["alpha"]["revoking"] is False and listed["beta"]["live"]
    # The app signs in again (owner PIN), after the request: the file stays and must not undo that.
    n_at, n_rt = _grant(mcp, p, "alpha", req["at"] + 5)
    p2 = _new_provider(mcp)  # a restart applies the file again
    assert n_at in p2.access_tokens and n_rt in p2.refresh_tokens and a_at not in p2.access_tokens
    assert b_at in p2.access_tokens


def test_a_revoke_written_while_the_server_was_down_applies_at_start(mcp):
    p = _new_provider(mcp)
    asyncio.run(p.register_client(_client("alpha", "Claude", "https://claude.ai/cb")))
    at, rt = _grant(mcp, p, "alpha", time.time() - 300)
    (mcp.state.parent / "mcp_revoke.json").write_text(json.dumps({"alpha": {"at": time.time(), "tokens": []}}))
    p2 = _new_provider(mcp)
    assert at not in p2.access_tokens and rt not in p2.refresh_tokens, "issued before the request: removed"
    assert json.loads((mcp.state.parent / "mcp_clients.json").read_text())["apps"]["alpha"]["tokens"] == []


def test_the_tools_summary_follows_the_security_lists(cn):
    import codec_config
    t = cn.c.tools_summary()
    assert t["exposed"] > 10
    assert set(t["asks"]) <= set(codec_config._HTTP_CONSENT_REQUIRED) and not set(t["asks"]) & set(t["blocked"])
    assert "terminal" in t["blocked"] and "standing_rules" in t["blocked"]
    assert cn.client.get("/api/connections/apps").json()["reported"] is False, "no mirror yet: the page says so"


# ── Bridges ───────────────────────────────────────────────────────────────────

def _bridge_config(cn):
    Path(cn.c.CONFIG_PATH).write_text(json.dumps({
        "telegram": {"allowed_chat_ids": [111]}, "alerts": {"telegram": {"chat_id": "999"}},
        "imessage": {"enabled": True, "allowed_senders": ["+34600111222", "not a handle!"]}}))


def test_bridge_status_without_numbers_or_tokens(cn, monkeypatch):
    _bridge_config(cn)
    monkeypatch.setattr(cn.c, "pm2_status", lambda: {"codec-telegram": "online", "codec-imessage": "stopped"})
    monkeypatch.setattr(cn.c, "_telegram_token", lambda cfg: "SECRET-BOT")
    r = cn.client.get("/api/connections/bridges")
    d = r.json()
    assert d["telegram"] == {"token": True, "running": "online", "allowed": 1, "alerts_chat": True, "can_test": True}
    im = d["imessage"]
    assert im["running"] == "stopped" and im["enabled"] is True and im["allowed"] == 2 and im["can_test"] is True
    assert im["recipients"] == ["+34" + "•" * 7 + "22"], "only valid handles, masked"
    assert "SECRET" not in r.text and "600111" not in r.text


def test_the_test_message_goes_only_to_configured_recipients(cn, monkeypatch):
    _bridge_config(cn)
    sent = []
    monkeypatch.setattr(cn.c, "_telegram_token", lambda cfg: "tok")
    monkeypatch.setattr(cn.c, "send_telegram", lambda token, chat, text: sent.append(("tg", chat, text)) or True)
    monkeypatch.setattr(cn.c, "send_imessage", lambda handle, text: sent.append(("im", handle, text)) or True)
    c = cn.client
    assert c.get("/api/connections/bridges/test").status_code == 405, "only a click (POST) sends"
    assert c.post("/api/connections/bridges/test", json={"bridge": "telegram"}).json() == {"sent": True}
    assert c.post("/api/connections/bridges/test", json={"bridge": "imessage", "to": 0}).json() == {"sent": True}
    assert sent == [("tg", "999", cn.c.TEST_LINE), ("im", "+34600111222", cn.c.TEST_LINE)], "owner chat first"
    for bad in ({"bridge": "imessage", "to": 5}, {"bridge": "imessage", "to": "+15550000000"},
                {"bridge": "imessage", "to": True}, {"bridge": "sms"}):
        assert c.post("/api/connections/bridges/test", json=bad).status_code == 400, bad
    assert len(sent) == 2
    monkeypatch.setattr(cn.c, "send_telegram", lambda *a: False)
    r = c.post("/api/connections/bridges/test", json={"bridge": "telegram"})
    assert r.status_code == 502 and r.json()["sent"] is False
    assert _events(cn.audits).count("bridge_test_sent") == 3 and "600111" not in repr(cn.audits)


def test_imessage_handle_and_text_are_arguments_not_script(cn, monkeypatch):
    seen = {}
    monkeypatch.setattr(cn.c.subprocess, "run", lambda cmd, **kw: seen.setdefault("cmd", cmd) and SimpleNamespace(returncode=0))
    assert cn.c.send_imessage('+34600111222" & do shell script "x', "hello") is True
    cmd = seen["cmd"]
    scripts = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-e"]
    assert cmd[0] == "osascript" and cmd[-2:] == ['+34600111222" & do shell script "x', "hello"]
    assert not any("+34600111222" in s or "hello" in s for s in scripts)


# ── macOS permissions ─────────────────────────────────────────────────────────

def test_the_permission_list(cn, monkeypatch):
    import routes.skills_page as sp
    monkeypatch.setattr(sp, "screen_recording_ok", lambda: True)
    monkeypatch.setattr(sp, "accessibility_ok", lambda: False)
    monkeypatch.setattr(cn.c, "messages_db_readable", lambda: False)
    d = cn.client.get("/api/connections/permissions").json()
    states = {i["key"]: i["state"] for i in d["items"]}
    assert states == {"screen": "ok", "accessibility": "missing", "full_disk": "missing",
                      "microphone": "unknown", "automation": "unknown"}
    assert all(i["url"].startswith("x-apple.systempreferences:com.apple.preference.security?Privacy_") for i in d["items"])
    assert d["items"][0]["where"] == "System Settings > Privacy & Security > Screen Recording" and "dashboard" in d["note"]


@pytest.mark.skipif(sys.platform != "darwin" or os.geteuid() == 0, reason="macOS file permissions")
def test_full_disk_access_is_read_from_the_messages_database(cn):
    assert cn.c.messages_db_readable() is None, "no Messages database here"
    db = Path(cn.c.MESSAGES_DB)
    db.write_text("x")
    assert cn.c.messages_db_readable() is True
    db.chmod(0)
    try:
        assert cn.c.messages_db_readable() is False
    finally:
        db.chmod(0o600)


# ── The page ──────────────────────────────────────────────────────────────────

def test_the_connectors_tab_has_the_four_sections():
    for sid in ('id="connGoogle"', 'id="connApps"', 'id="connBridges"', 'id="connPerms"'):
        assert sid in HOME, sid
    assert "if (id === 'connector') { renderConnections(); renderConnectors(); }" in HOME
    assert HOME.index('id="connGoogle"') < HOME.index('id="connectorList"'), "above the MCP server list"
    for url in ("'/api/connections/google'", "'/api/connections/google/reconnect'", "'/api/connections/apps'",
                "'/api/connections/apps/revoke'", "'/api/connections/bridges'", "'/api/connections/bridges/test'",
                "'/api/connections/permissions'"):
        assert url in HOME, url
    block = HOME[HOME.index("// ── Connections (P3.9"):HOME.index("// ── Connector (external MCP) tab ──")]
    assert "confirm: 'Revoke', danger: true" in block and "title: 'Send a test message?'" in block
    assert "var link = mac && /^x-apple\\.systempreferences:/.test" in block, "System Settings links only in a Mac browser"
    assert ".conn-sec .mcp-name{text-transform:none}" in HOME, "names as written: iMessage, not IMessage"


def _jsdom_available() -> bool:
    if shutil.which("node") is None:
        return False
    r = subprocess.run(["node", "-e", "require.resolve('jsdom')"], capture_output=True, cwd=REPO,
                       env=os.environ.copy(), timeout=20)
    return r.returncode == 0


_JS_HARNESS = r"""
const { JSDOM } = require('jsdom');
const fs = require('fs');
const html = fs.readFileSync(process.argv[1], 'utf8');
const block = html.slice(html.indexOf('// ── Connections (P3.9'), html.indexOf('// ── Connector (external MCP) tab ──'));
const helpers = ['function escHtml(', 'function fmtTime('].map(h => { const i = html.indexOf(h);
  return html.slice(i, html.indexOf('\n}\n', i) > 0 && h === 'function fmtTime(' ? html.indexOf('\n}\n', i) + 3 : html.indexOf('\n', i)); }).join('\n');
const markup = html.slice(html.indexOf('<section class="conn-sec" aria-labelledby="connGoogleH">'), html.indexOf('<div class="mcp-intro">', html.indexOf('id="connPermsH"')));
const dom = new JSDOM('<!doctype html><html><body><div id="panel-connector">' + markup + '</div></body></html>',
  { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
const w = dom.window, calls = [], asks = [];
const now = new Date().toISOString();
const data = {
  '/api/connections/google': { connected: true, account: 'owner@example.com', scopes: [{ name: 'Gmail', ok: true }, { name: 'Tasks', ok: false }],
    missing: ['Tasks'], updated: now, reconnect: { allowed: true, running: false, client_file: true } },
  '/api/connections/apps': { reported: true, apps: [{ client_id: 'alpha', name: 'Claude', host: 'claude.ai', registered: now, last_used: now, live: true, revoking: false }],
    tools: { exposed: 42, blocked: ['terminal'], asks: ['delegate'] } },
  '/api/connections/bridges': { telegram: { token: true, running: 'online', allowed: 1, alerts_chat: true, can_test: true },
    imessage: { running: 'stopped', enabled: true, allowed: 0, messages_db: false, recipients: [], can_test: false } },
  '/api/connections/permissions': { note: 'The dashboard.', items: [{ key: 'screen', name: 'Screen Recording', state: 'missing', where: 'System Settings > Privacy & Security > Screen Recording', url: 'x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture' }] } };
w.fetch = (u, o) => { u = String(u); calls.push([u, (o && o.method) || 'GET', (o && o.body) || '']);
  const d = data[u] || { requested: true, sent: true };
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(d) }); };
w.CodecShell = { ask: o => { asks.push(o.title); return Promise.resolve(true); }, actions() {} };
w.showToast = () => {};
w.eval(helpers + '\nvar currentTab = "connector";\n' + block);
(async () => {
  const d = w.document, out = {};
  w.renderConnections();
  await new Promise(r => setTimeout(r, 100));
  out.google = d.getElementById('connGoogle').textContent;
  out.missing = [...d.querySelectorAll('#connGoogle .conn-scope.miss')].map(e => e.textContent);
  out.apps = d.getElementById('connApps').textContent;
  out.bridges = [...d.querySelectorAll('#connBridges .mcp-card')].map(c => [c.querySelector('.mcp-name').textContent, c.querySelectorAll('button').length]);
  out.perm = d.getElementById('connPerms').textContent;
  out.permLink = !!d.querySelector('#connPerms a[href^="x-apple"]');
  d.querySelector('#connApps [data-revoke="alpha"]').click();
  await new Promise(r => setTimeout(r, 60));
  d.querySelector('#connBridges [data-test="telegram"]').click();
  await new Promise(r => setTimeout(r, 60));
  out.asks = asks;
  out.posts = calls.filter(c => c[1] === 'POST').map(c => c[0] + ' ' + c[2]);
  console.log(JSON.stringify(out)); process.exit(0);
})().catch(e => { console.log('ERR ' + e.message); process.exit(1); });
setTimeout(() => { console.log('TIMEOUT'); process.exit(2); }, 20000);
"""


@pytest.mark.skipif(not _jsdom_available(), reason="node with jsdom not resolvable (set NODE_PATH)")
def test_the_sections_render_and_revoke_asks_first_under_jsdom():
    out = subprocess.run(["node", "-e", _JS_HARNESS, str(REPO / "codec_dashboard.html")], capture_output=True, text=True,
                         cwd=REPO, env=os.environ.copy(), timeout=60)
    line = [ln for ln in out.stdout.splitlines() if ln.startswith("{") or ln.startswith("ERR") or ln == "TIMEOUT"][-1]
    d = json.loads(line)
    assert "Signed in as owner@example.com" in d["google"] and "Reconnect" in d["google"]
    assert d["missing"] == ["Tasks: missing"]
    assert "Claude" in d["apps"] and "from claude.ai" in d["apps"] and "can use 42 of CODEC's tools" in d["apps"]
    assert d["bridges"] == [["Telegram", 1], ["iMessage", 0]]
    assert "Not allowed" in d["perm"] and d["permLink"] is False, "jsdom is not a Mac browser: no System Settings link"
    assert d["asks"] == ["Sign out Claude?", "Send a test message?"]
    assert d["posts"] == ['/api/connections/apps/revoke {"client_id":"alpha"}',
                          '/api/connections/bridges/test {"bridge":"telegram","to":0}']
