"""Connections (UI phase 3, P3.9; docs/P3.9-DESIGN.md).

Settings > Connectors gains four sections: Google Workspace (status and a
Mac-only Reconnect), the AI apps signed in to CODEC's MCP server (with a
per-app Revoke the MCP server applies itself), the Telegram and iMessage
bridges (status, and a test message sent only on click to a recipient the owner
configured), and a best-effort list of the dashboard's macOS permissions.
Nothing here returns a token, a secret or a full phone number.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

router = APIRouter()

CODEC_DIR = os.path.expanduser("~/.codec")
CONFIG_PATH = os.path.join(CODEC_DIR, "config.json")
GOOGLE_TOKEN = os.path.join(CODEC_DIR, "google_token.json")
GOOGLE_CREDS = os.path.join(CODEC_DIR, "google_credentials.json")
MCP_CLIENTS = os.path.join(CODEC_DIR, "mcp_clients.json")   # written by codec-mcp-http
MCP_REVOKE = os.path.join(CODEC_DIR, "mcp_revoke.json")     # read by codec-mcp-http
MESSAGES_DB = os.path.expanduser("~/Library/Messages/chat.db")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAUTH_SCRIPT = os.path.join(REPO, "reauth_google.py")
PM2 = "/opt/homebrew/bin/pm2"
REAUTH_MAX_SECONDS = 600
TEST_LINE = "CODEC test message: this bridge can reach you."

_SCOPE_LABELS = {"gmail.modify": "Gmail", "calendar": "Calendar", "drive": "Drive", "documents": "Docs",
                 "spreadsheets": "Sheets", "presentations": "Slides", "tasks": "Tasks"}
_ACCOUNT = {"key": None, "value": None, "at": 0.0}
_REAUTH = {"proc": None, "started": 0.0}
_REAUTH_LOCK = threading.Lock()
_REVOKE_LOCK = threading.Lock()
_HANDLE_RE = re.compile(r"^[+0-9A-Za-z@._-]{3,80}$")


def _read_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return default
    return d if isinstance(d, type(default)) else default


def _config() -> dict:
    return _read_json(CONFIG_PATH, {})


def _audit(event: str, message: str, **kw) -> None:
    try:
        from codec_audit import log_event
        log_event(event, "codec-dashboard", message, **kw)
    except Exception:
        pass


def _iso(ts) -> str | None:
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


# ── Google Workspace ─────────────────────────────────────────────────────────

def _scope_label(scope: str) -> str:
    short = scope.rsplit("/", 1)[-1]
    return _SCOPE_LABELS.get(short, short)


def google_account(stamp) -> str | None:
    """The signed-in Gmail address, asked from Google at most once an hour per token
    version (the token file's own `account` field is empty). None when Google cannot say."""
    if _ACCOUNT["key"] == stamp and time.time() - _ACCOUNT["at"] < 3600:
        return _ACCOUNT["value"]
    value = None
    try:
        import codec_google_auth
        prof = codec_google_auth.build_service("gmail", "v1").users().getProfile(userId="me").execute()
        value = str(prof.get("emailAddress") or "")[:200] or None
    except Exception:
        value = None
    _ACCOUNT.update(key=stamp, value=value, at=time.time())
    return value


def _reauth_running() -> bool:
    p = _REAUTH["proc"]
    if p is None or p.poll() is not None:
        return False
    if time.time() - _REAUTH["started"] > REAUTH_MAX_SECONDS:  # an abandoned sign-in page
        try:
            p.terminate()
        except Exception:
            pass
        return False
    return True


@router.get("/api/connections/google")
def google_status(request: Request):
    import codec_google_auth as ga
    from routes._shared import _is_remote_request
    out = {"connected": False, "account": None, "scopes": [], "missing": [], "expires": None, "updated": None,
           "reconnect": {"allowed": not _is_remote_request(request), "running": _reauth_running(),
                         "client_file": os.path.exists(GOOGLE_CREDS)}}
    try:
        st = os.stat(GOOGLE_TOKEN)
        with open(GOOGLE_TOKEN, encoding="utf-8") as f:
            tok = json.load(f)
    except (OSError, ValueError):
        return out
    if not isinstance(tok, dict):
        return out
    have = set(tok.get("scopes") or [])
    out["scopes"] = [{"name": _scope_label(s), "ok": s in have} for s in ga.ALL_SCOPES]
    out["missing"] = [_scope_label(s) for s in ga.ALL_SCOPES if s not in have]
    out["connected"] = bool(tok.get("refresh_token"))
    out["expires"] = str(tok.get("expiry") or "")[:40] or None  # the hour-long access token; it renews by itself
    out["updated"] = _iso(st.st_mtime)
    stamp = (st.st_mtime_ns, st.st_size)
    if _ACCOUNT["key"] not in (None, stamp):
        try:
            ga.invalidate_cache()  # a new token (a reconnect): stop using the old one in this process
        except Exception:
            pass
    if out["connected"]:
        out["account"] = google_account(stamp)
    return out


@router.post("/api/connections/google/reconnect")
def google_reconnect(request: Request):
    from routes._shared import _is_remote_request
    if _is_remote_request(request):
        return JSONResponse({"error": "Reconnect works only on the Mac itself: Google's sign-in comes back to the Mac."},
                            status_code=403)
    with _REAUTH_LOCK:
        if _reauth_running():
            return JSONResponse({"error": "A Google sign-in is already open on the Mac."}, status_code=409)
        if not os.path.exists(GOOGLE_CREDS):
            return JSONResponse({"error": "There is no Google client file (~/.codec/google_credentials.json)."},
                                status_code=409)
        _REAUTH["proc"] = subprocess.Popen([sys.executable, REAUTH_SCRIPT], cwd=REPO, stdin=subprocess.DEVNULL,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                           start_new_session=True)
        _REAUTH["started"] = time.time()
    _audit("google_reconnect_started", "Google sign-in opened on the Mac")
    return {"started": True}


# ── AI apps signed in to CODEC's MCP server ──────────────────────────────────

def tools_summary() -> dict:
    """What an app signed in over the web (MCP HTTP) can use: from the registry and the security lists."""
    import codec_config
    from codec_dispatch import registry
    if not registry.names():
        registry.scan()
    try:
        blocked = set(codec_config._mcp_blocked_tools("http", codec_config.load_config()))
    except Exception:
        blocked = set(codec_config._HTTP_BLOCKED) | set(codec_config._HTTP_ONLY_BLOCKED)
    names = list(registry.names())
    exposed = sorted(n for n in names if registry.get_mcp_expose(n) and n not in blocked)
    asks = set(codec_config._HTTP_CONSENT_REQUIRED)
    return {"exposed": len(exposed), "blocked": sorted(n for n in names if n in blocked),
            "asks": sorted(n for n in exposed if n in asks)}


@router.get("/api/connections/apps")
def apps():
    mirror = _read_json(MCP_CLIENTS, {})
    revoked = _read_json(MCP_REVOKE, {})
    out = []
    for cid, a in (mirror.get("apps") or {}).items():
        if not isinstance(a, dict):
            continue
        tokens = {t for t in (a.get("tokens") or []) if isinstance(t, str)}
        req = revoked.get(cid) if isinstance(revoked.get(cid), dict) else {}
        pending = bool(tokens & {t for t in (req.get("tokens") or []) if isinstance(t, str)})
        out.append({"client_id": str(cid), "name": str(a.get("name") or "")[:80], "host": str(a.get("host") or "")[:120],
                    "registered": _iso(a.get("registered")), "last_used": _iso(a.get("last_used")),
                    "live": bool(tokens) and not pending, "revoking": pending, "expires": _iso(a.get("expires"))})
    # Signed-in apps first, the most recently used on top; then the signed-out ones.
    live = sorted((x for x in out if x["live"]), key=lambda x: x["last_used"] or "", reverse=True)
    rest = sorted((x for x in out if not x["live"]), key=lambda x: x["last_used"] or "", reverse=True)
    return {"apps": live + rest, "tools": tools_summary(), "reported": bool(mirror),
            "updated": _iso(mirror.get("updated"))}


@router.post("/api/connections/apps/revoke")
async def revoke_app(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = None
    cid = (body or {}).get("client_id") if isinstance(body, dict) else None
    known = _read_json(MCP_CLIENTS, {}).get("apps") or {}
    if not isinstance(cid, str) or not isinstance(known.get(cid), dict):
        return JSONResponse({"error": "Unknown app."}, status_code=404)
    tokens = [t for t in (known[cid].get("tokens") or []) if isinstance(t, str)][:200]
    import codec_jsonstore
    with _REVOKE_LOCK:
        reqs = _read_json(MCP_REVOKE, {})
        reqs[cid] = {"at": time.time(), "tokens": tokens}
        newest = sorted(reqs.items(), key=lambda kv: (kv[1] or {}).get("at", 0) if isinstance(kv[1], dict) else 0,
                        reverse=True)[:100]
        codec_jsonstore.atomic_write_json(MCP_REVOKE, dict(newest))
    _audit("mcp_client_revoke_requested", "Sign-out of an AI app requested (Connections page)", client_id=cid,
           extra={"tokens": len(tokens)})
    return {"requested": True}


# ── Bridges: Telegram and iMessage ───────────────────────────────────────────

def pm2_status() -> dict:
    """{process name: status}; nothing else from PM2's list (it carries environments)."""
    try:
        r = subprocess.run([PM2, "jlist"], capture_output=True, timeout=5)
        procs = json.loads(r.stdout or b"[]")
    except Exception:
        return {}
    return {p.get("name"): (p.get("pm2_env") or {}).get("status") for p in procs if isinstance(p, dict)}


def _telegram_token(cfg: dict) -> str:
    try:
        from codec_config import get_telegram_bot_token
        return get_telegram_bot_token() or ""
    except Exception:
        return str(((cfg.get("telegram") or {}).get("bot_token")) or "")


def _telegram_chats(cfg: dict) -> list:
    """Where a test may go: the owner alert chat, then the bot's allowed chats. Nothing typed."""
    out = []
    owner = str((((cfg.get("alerts") or {}).get("telegram") or {}).get("chat_id")) or "").strip()
    if owner:
        out.append(owner)
    for c in (cfg.get("telegram") or {}).get("allowed_chat_ids") or []:
        if str(c).strip() and str(c).strip() not in out:
            out.append(str(c).strip())
    return out


def _imessage_handles(cfg: dict) -> list:
    return [str(h).strip() for h in ((cfg.get("imessage") or {}).get("allowed_senders") or [])
            if _HANDLE_RE.match(str(h).strip())]


def _mask(handle: str) -> str:
    h = str(handle)
    return h if len(h) <= 5 else h[:3] + "•" * (len(h) - 5) + h[-2:]


def messages_db_readable():
    """True / False for Full Disk Access (can the dashboard open the Messages database); None off a Mac."""
    if sys.platform != "darwin":
        return None
    try:
        fd = os.open(MESSAGES_DB, os.O_RDONLY)
    except PermissionError:
        return False
    except OSError:
        return None
    os.close(fd)
    return True


@router.get("/api/connections/bridges")
def bridges():
    cfg = _config()
    pm2 = pm2_status()
    tg = cfg.get("telegram") or {}
    im = cfg.get("imessage") or {}
    token = bool(_telegram_token(cfg))
    chats = _telegram_chats(cfg)
    handles = _imessage_handles(cfg)
    return {
        "telegram": {"token": token, "running": pm2.get("codec-telegram"), "allowed": len(tg.get("allowed_chat_ids") or []),
                     "alerts_chat": bool(str((((cfg.get("alerts") or {}).get("telegram") or {}).get("chat_id")) or "").strip()),
                     "can_test": token and bool(chats)},
        "imessage": {"running": pm2.get("codec-imessage"), "enabled": im.get("enabled", True) is not False,
                     "allowed": len(im.get("allowed_senders") or []), "messages_db": messages_db_readable(),
                     "recipients": [_mask(h) for h in handles], "can_test": bool(handles)},
    }


def send_telegram(token: str, chat_id: str, text: str) -> bool:
    import codec_alerts
    return bool(codec_alerts._send_telegram(token, chat_id, text))


def send_imessage(handle: str, text: str) -> bool:
    """Messages through AppleScript; the handle and text go in as arguments, never into the script."""
    script = ['on run argv', 'tell application "Messages"',
              'set targetService to 1st account whose service type = iMessage',
              'send (item 2 of argv) to buddy (item 1 of argv) of targetService', 'end tell', 'end run']
    cmd = ["osascript"]
    for line in script:
        cmd += ["-e", line]
    try:
        r = subprocess.run(cmd + [handle, text], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


@router.post("/api/connections/bridges/test")
async def bridge_test(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = None
    body = body if isinstance(body, dict) else {}
    bridge, cfg = body.get("bridge"), _config()
    if bridge == "telegram":
        token, chats = _telegram_token(cfg), _telegram_chats(cfg)
        if not token or not chats:
            return JSONResponse({"error": "Telegram has no bot token or no chat to send to."}, status_code=409)
        sent = await run_in_threadpool(send_telegram, token, chats[0], TEST_LINE)
    elif bridge == "imessage":
        handles, idx = _imessage_handles(cfg), body.get("to", 0)
        if not handles:
            return JSONResponse({"error": "iMessage has no allowed sender to send to."}, status_code=409)
        if not isinstance(idx, int) or isinstance(idx, bool) or not 0 <= idx < len(handles):
            return JSONResponse({"error": "Pick one of the allowed senders."}, status_code=400)
        sent = await run_in_threadpool(send_imessage, handles[idx], TEST_LINE)
    else:
        return JSONResponse({"error": "Unknown bridge."}, status_code=400)
    _audit("bridge_test_sent", f"Test message through {bridge}", extra={"bridge": bridge, "sent": bool(sent)})
    if not sent:
        return JSONResponse({"sent": False, "error": "It did not send. Check the bridge's settings and logs."},
                            status_code=502)
    return {"sent": True}


# ── macOS permissions (best effort, the dashboard's own) ─────────────────────

_PERMS = [("screen", "Screen Recording", "Privacy_ScreenCapture"),
          ("accessibility", "Accessibility", "Privacy_Accessibility"),
          ("full_disk", "Full Disk Access", "Privacy_AllFiles"),
          ("microphone", "Microphone", "Privacy_Microphone"),
          ("automation", "Automation", "Privacy_Automation")]
PERMISSIONS_NOTE = ("These are the dashboard's own permissions: it runs Chat's skills. The voice call, the wake word "
                    "and the bridges run in their own processes, which have their own.")


@router.get("/api/connections/permissions")
def permissions():
    from routes.skills_page import accessibility_ok, screen_recording_ok
    state = {"screen": screen_recording_ok(), "accessibility": accessibility_ok(), "full_disk": messages_db_readable(),
             "microphone": None, "automation": None}
    items = []
    for key, name, anchor in _PERMS:
        v = state[key]
        items.append({"key": key, "name": name,
                      "state": "ok" if v is True else "missing" if v is False else "unknown",
                      "where": "System Settings > Privacy & Security > " + name,
                      "url": "x-apple.systempreferences:com.apple.preference.security?" + anchor})
    return {"items": items, "note": PERMISSIONS_NOTE}
