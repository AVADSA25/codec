"""Web Push for approvals, questions, briefing and agent results (UI phase 3, P3.13;
docs/P3.13-DESIGN.md).

The push carries no content: callers pass a kind, the payload is a fixed line
from codec_push.KINDS, encrypted per RFC 8291 and signed per RFC 8292. Only
known push services are posted to. The Keychain and ~/.codec are never
touched here: the fixture swaps in a dict for the Keychain and tmp paths.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent
FCM = "https://fcm.googleapis.com/fcm/send/"
IPHONE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _point(pub) -> bytes:
    return pub.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def _device(base: str = FCM):
    """A browser subscription with its private key, like PushSubscription.toJSON()."""
    key = ec.generate_private_key(ec.SECP256R1())
    auth = os.urandom(16)
    sub = {"endpoint": base + secrets.token_urlsafe(16),
           "keys": {"p256dh": _b64u(_point(key.public_key())), "auth": _b64u(auth)}}
    return sub, key, auth


def _decrypt(body: bytes, ua_key, auth: bytes) -> bytes:
    """RFC 8291 receiver side, written independently with cryptography's HKDF."""
    salt, idlen = body[:16], body[20]
    as_public, record = body[21:21 + idlen], body[21 + idlen:]
    ua_public = _point(ua_key.public_key())
    shared = ua_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public))
    ikm = HKDF(hashes.SHA256(), 32, auth, b"WebPush: info\x00" + ua_public + as_public).derive(shared)
    cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
    padded = AESGCM(cek).decrypt(nonce, record, None).rstrip(b"\x00")
    assert padded.endswith(b"\x02"), "last-record delimiter"
    return padded[:-1]


@pytest.fixture
def push(tmp_path, monkeypatch):
    import codec_audit
    import codec_push
    ns = SimpleNamespace(mod=codec_push, keychain={}, events=[], posts=[], status={})
    monkeypatch.setattr(codec_push, "STATE_PATH", tmp_path / "push.json")
    monkeypatch.setattr(codec_push, "_keychain_get", lambda: ns.keychain.get("vapid"))

    def keychain_set(value):
        ns.keychain["vapid"] = value
        return True

    def fake_post(endpoint, data, headers):
        ns.posts.append((endpoint, data, headers))
        return ns.status.get(endpoint, 201)

    monkeypatch.setattr(codec_push, "_keychain_set", keychain_set)
    monkeypatch.setattr(codec_push, "_post", fake_post)
    monkeypatch.setattr(codec_push, "_KEY_CACHE", {"key": None, "at": 0.0})
    monkeypatch.setattr(codec_push, "_LAST_SENT", {})
    monkeypatch.setattr(codec_audit, "log_event", lambda *a, **kw: ns.events.append((a, kw)))
    monkeypatch.delenv("PUSH_ENABLED", raising=False)
    return ns


def test_encryption_matches_the_rfc8291_example(push):
    p = push.mod
    server = ec.derive_private_key(int.from_bytes(_unb64u("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"),
                                   ec.SECP256R1())
    body = p.encrypt(b"When I grow up, I want to be a watermelon",
                     "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
                     "BTBZMqHH6r4Tts7J_aSIgg", salt=_unb64u("DGv6ra1nlYgDCS1FRnbzlw"), server_key=server)
    assert _b64u(body) == (
        "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3j"
        "l7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")


def test_a_random_device_decrypts_what_codec_sends(push):
    sub, key, auth = _device()
    body = push.mod.encrypt(b'{"kind":"test"}', sub["keys"]["p256dh"], sub["keys"]["auth"])
    assert _decrypt(body, key, auth) == b'{"kind":"test"}'


def test_vapid_header_verifies_with_the_public_key(push):
    p = push.mod
    public = p.ensure_key()
    header = p.vapid_header("https://web.push.apple.com/QGt-some-token")
    token, k = header.removeprefix("vapid t=").split(", k=")
    assert k == public
    head, claims, sig = token.split(".")
    assert json.loads(_unb64u(head)) == {"typ": "JWT", "alg": "ES256"}
    c = json.loads(_unb64u(claims))
    assert c["aud"] == "https://web.push.apple.com"
    assert time.time() < c["exp"] <= time.time() + 24 * 3600
    assert c["sub"].startswith(("https://", "mailto:"))
    raw = _unb64u(sig)
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), _unb64u(public))
    pub.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
               f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))


def test_the_key_is_made_once_and_kept(push):
    p = push.mod
    first = p.ensure_key()
    stored = push.keychain["vapid"]
    assert len(_unb64u(stored)) == 32 and len(_unb64u(first)) == 65
    p._KEY_CACHE.update(key=None, at=0.0)
    assert p.ensure_key() == first and push.keychain["vapid"] == stored


@pytest.mark.parametrize("endpoint,ok", [
    (FCM + "abc", True),
    ("https://updates.push.services.mozilla.com/wpush/v2/abc", True),
    ("https://web.push.apple.com/QGt-abc", True),
    ("https://wns2-par02p.notify.windows.com/w/?token=abc", True),
    ("http://fcm.googleapis.com/fcm/send/abc", False),
    ("https://fcm.googleapis.com.evil.example/x", False),
    ("https://evil.example/fcm.googleapis.com", False),
    ("https://push.apple.com.evil.example/x", False),
    ("https://127.0.0.1/x", False),
    ("https://user:pw@fcm.googleapis.com/x", False),
    ("https://fcm.googleapis.com:8443/x", False),
])
def test_only_known_push_services_over_https(push, endpoint, ok):
    assert push.mod.endpoint_allowed(endpoint) is ok


def test_devices_are_validated_labelled_and_stored_owner_only(push):
    p = push.mod
    sub, _, _ = _device()
    with pytest.raises(ValueError):
        p.add_device({**sub, "endpoint": "https://evil.example/x"})
    with pytest.raises(ValueError):
        p.add_device({**sub, "keys": {"p256dh": _b64u(b"\x04" + b"\x01" * 64), "auth": sub["keys"]["auth"]}})
    dev_id = p.add_device(sub, IPHONE_UA)
    p.add_device(sub, IPHONE_UA)
    state = p.read_state()
    assert len(state["devices"]) == 1 and state["devices"][0]["label"] == "iPhone Safari"
    assert p.public_devices()[0]["id"] == dev_id and "endpoint" not in p.public_devices()[0]
    assert (p.STATE_PATH.stat().st_mode & 0o777) == 0o600
    for _ in range(12):
        p.add_device(_device()[0])
    assert len(p.read_state()["devices"]) == p.MAX_DEVICES


def test_the_encrypted_body_is_exactly_the_fixed_line(push):
    p = push.mod
    p.ensure_key()
    sub, key, auth = _device()
    p.add_device(sub)
    for kind, (_switch, title, body, url, ttl, urgency) in p.KINDS.items():
        push.posts.clear()
        p.send(kind)
        (endpoint, data, headers), = push.posts
        assert json.loads(_decrypt(data, key, auth)) == {"kind": kind, "title": title, "body": body, "url": url}
        assert headers["Content-Encoding"] == "aes128gcm" and headers["TTL"] == str(ttl)
        assert headers["Urgency"] == urgency and headers["Topic"] == "codec-" + kind.replace("_", "-")
        assert headers["Authorization"].startswith("vapid t=")
    assert p.send("anything_else")["devices"] == 0 and len(push.posts) == 1


def test_switches_kill_switch_and_no_devices_send_nothing(push, monkeypatch):
    p = push.mod
    p.ensure_key()
    assert p.send("approval")["devices"] == 0          # no device yet
    p.add_device(_device()[0])
    p.set_types({"approvals": False, "bogus": True})
    assert p.read_state()["types"] == {"approvals": False, "questions": True, "briefing": True, "agents": True}
    assert p.send("approval")["devices"] == 0 and not push.posts
    assert p.send("test")["ok"] == 1                   # the test ignores the switches
    monkeypatch.setenv("PUSH_ENABLED", "false")
    assert p.send("question")["devices"] == 0 and len(push.posts) == 1


def test_gone_devices_are_removed_and_results_recorded(push):
    p = push.mod
    p.ensure_key()
    good, gone, bad = _device(), _device("https://web.push.apple.com/"), _device()
    for d in (good, gone, bad):
        p.add_device(d[0])
    push.status[gone[0]["endpoint"]] = 410
    push.status[bad[0]["endpoint"]] = 500
    assert p.send("agent_done") == {"devices": 3, "ok": 1, "failed": 1, "removed": 1}
    devices = {d["endpoint"]: d for d in p.read_state()["devices"]}
    assert gone[0]["endpoint"] not in devices
    assert devices[good[0]["endpoint"]]["last_ok"] and devices[bad[0]["endpoint"]]["last_error"] == "HTTP 500"
    logged = json.dumps([e for e in push.events], default=str)
    assert "push_sent" in logged and "push_subscribed" in logged
    for d in (good, gone, bad):
        assert d[0]["endpoint"] not in logged and d[0]["keys"]["auth"] not in logged


def test_notify_runs_in_the_background_throttles_and_never_raises(push, monkeypatch):
    p = push.mod
    sent = []

    class Now:  # runs the thread's target at once
        def __init__(self, target, args=(), **kw):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(p, "threading", SimpleNamespace(Thread=Now))
    monkeypatch.setattr(p, "send", lambda kind: sent.append(kind))
    p.notify("question")
    p.notify("question")
    p.notify("approval")
    p.notify("not_a_kind")
    assert sent == ["question", "approval"]

    def boom(kind):
        raise RuntimeError("push service down")

    monkeypatch.setattr(p, "send", boom)
    p.notify("agent_done")  # must not raise


def test_ask_user_pushes_an_approval_or_a_question(push, tmp_path, monkeypatch):
    import codec_ask_user
    monkeypatch.setattr(codec_ask_user, "PENDING_QUESTIONS_PATH", tmp_path / "pending_questions.json")
    monkeypatch.setattr(codec_ask_user, "NOTIFICATIONS_PATH", tmp_path / "notifications.json")
    monkeypatch.setattr(codec_ask_user, "CONFIG_PATH", tmp_path / "config.json")
    kinds = []
    monkeypatch.setattr(push.mod, "notify", kinds.append)
    codec_ask_user.ask("Delete the draft folder?", destructive=True, timeout=1)
    codec_ask_user.ask("Which city for the weather?", timeout=1)
    assert kinds == ["approval", "question"]


def test_agent_results_push_but_updates_and_silenced_agents_do_not(push, tmp_path, monkeypatch):
    import codec_agent_messaging as cam
    monkeypatch.setattr(cam, "_CODEC_DIR", tmp_path)
    monkeypatch.setattr(cam, "_AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr(cam, "_NOTIFICATIONS_PATH", tmp_path / "notifications.json")
    kinds = []
    monkeypatch.setattr(push.mod, "notify", kinds.append)
    for t in ("agent_update", "agent_question", "agent_done", "agent_blocked", "agent_aborted"):
        cam.post_message("agent_a", t, "Title", "Body")
    cam.set_silenced("agent_b", True)
    cam.post_message("agent_b", "agent_done", "Title", "Body")
    assert kinds == ["agent_done", "agent_blocked", "agent_stopped"]


def test_routes_list_subscribe_switch_test_and_remove(push):
    import routes.push as rp
    app = FastAPI()
    app.include_router(rp.router)
    c = TestClient(app)
    first = c.get("/api/push").json()
    assert first["public_key"] and first["devices"] == [] and first["switches"] == list(push.mod.SWITCHES)
    sub, _, _ = _device()
    assert c.post("/api/push/subscribe", json={"subscription": {**sub, "endpoint": "https://evil.example/x"}}
                  ).status_code == 400
    r = c.post("/api/push/subscribe", json={"subscription": sub}, headers={"user-agent": IPHONE_UA})
    assert r.status_code == 200 and r.json()["devices"][0]["label"] == "iPhone Safari"
    listing = c.get("/api/push")
    assert sub["endpoint"] not in listing.text and sub["keys"]["p256dh"] not in listing.text
    assert c.post("/api/push/types", json={"agents": False}).json()["types"]["agents"] is False
    t = c.post("/api/push/test").json()
    assert (t["devices"], t["ok"]) == (1, 1) and len(push.posts) == 1
    dev_id = listing.json()["devices"][0]["id"]
    assert c.post("/api/push/unsubscribe", json={"id": dev_id}).json()["removed"] is True
    assert c.get("/api/push").json()["devices"] == []


def test_dashboard_mounts_the_routes_and_the_pages_use_them():
    dash = (REPO / "codec_dashboard.py").read_text(encoding="utf-8")
    assert "app.include_router(push_router)" in dash
    shell = (REPO / "static" / "codec-shell.js").read_text(encoding="utf-8")
    assert "push: { support: pushSupport, subscription: currentSub, on: pushOn, off: pushOff" in shell
    assert "userVisibleOnly: true" in shell and "Notification.requestPermission(" in shell
    assert "codec_csrf=" in shell, "the shell's own POSTs carry the CSRF header on every page"
    assert shell.index("pushResync();") > shell.index("function ready()")
    home = (REPO / "codec_dashboard.html").read_text(encoding="utf-8")
    assert 'id="pushSection"' in home and "loadPush(); }" in home
    import codec_push
    keys = [k for k in codec_push.SWITCHES]
    assert all(f"['{k}'," in home for k in keys) and "Add to Home Screen" in home


_HARNESS = r"""
const vm = require('vm'), fs = require('fs');
const code = fs.readFileSync(process.argv[1], 'utf8');
const handlers = {}, shown = [], opened = [], focused = [], navigated = [];
let windows = [];
const self = {
  location: { origin: 'https://codec.test' }, addEventListener: (t, f) => { handlers[t] = f; },
  skipWaiting: async () => {},
  registration: { showNotification: async (title, opts) => { shown.push({ title, opts }); } },
  clients: { claim: async () => {}, matchAll: async () => windows,
             openWindow: async (u) => { opened.push(u); return null; } },
};
vm.runInNewContext(code, { self, caches: {}, fetch: async () => null, Response, URL, Promise, console });
async function push(data) {
  const waits = [];
  handlers.push({ data: data === undefined ? null : { json: () => (typeof data === 'string' ? JSON.parse(data) : data) },
                  waitUntil: (p) => waits.push(p) });
  await Promise.all(waits);
}
async function click(url) {
  const waits = []; let closed = false;
  handlers.notificationclick({ notification: { data: { url }, close: () => { closed = true; } },
                               waitUntil: (p) => waits.push(p) });
  await Promise.all(waits);
  return closed;
}
(async () => {
  await push({ kind: 'approval', title: 'CODEC needs your approval', body: 'Tap to see what it wants to do.', url: '/' });
  await push(undefined);
  await push({ kind: 'question', title: 'x', body: 'y', url: 'https://evil.example/' });
  await push({ kind: 'question', title: 'x', body: 'y', url: '//evil.example/' });
  await push('not json');
  const out = { shown: shown.map(s => ({ title: s.title, body: s.opts.body, tag: s.opts.tag, url: s.opts.data.url,
                                         icon: s.opts.icon })) };
  windows = [{ url: 'https://codec.test/chat', focus: async function () { focused.push(this.url); return this; },
               navigate: async function (u) { navigated.push(u); return this; } }];
  out.closed = await click('/tasks#reports');
  windows = [];
  await click('/');
  await click('https://evil.example/');
  Object.assign(out, { focused, navigated, opened });
  console.log(JSON.stringify(out));
})();
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_worker_shows_the_line_and_opens_only_codec_pages():
    out = subprocess.run(["node", "-e", _HARNESS, str(REPO / "static" / "sw.js")], capture_output=True,
                         text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    r = json.loads(out.stdout.strip().splitlines()[-1])
    first, empty, foreign, protocol_relative, broken = r["shown"]
    assert first == {"title": "CODEC needs your approval", "body": "Tap to see what it wants to do.",
                     "tag": "codec-approval", "url": "/", "icon": "/static/icons/icon-192.png"}
    fallback = {"title": "CODEC", "body": "Open CODEC to see what is new.", "tag": "codec-update", "url": "/",
                "icon": "/static/icons/icon-192.png"}
    assert empty == fallback and broken == fallback
    assert foreign["url"] == "/" and protocol_relative["url"] == "/"
    assert r["closed"] is True and r["focused"] == ["https://codec.test/chat"]
    assert r["navigated"] == ["/tasks#reports"] and r["opened"] == ["/", "/"]
