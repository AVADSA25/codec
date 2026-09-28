"""CODEC Alerting — dispatch alerts via Telegram, Email, Slack when components fail.

Configure in ~/.codec/config.json:
  "alerts": {
    "telegram": {"chat_id": "..."},   # bot token: the CODEC bot's Keychain slot
    "email": {"enabled": false, "smtp_host": "smtp.gmail.com", "smtp_port": 587,
              "from": "codec@you.com", "to": "you@you.com", "password": "app-password"},
    "slack": {"enabled": false, "webhook_url": "https://hooks.slack.com/..."},
    "extra_services": {
      "AVA Gateway": "http://127.0.0.1:4000/health/liveliness",
      "Postgres": "tcp://127.0.0.1:5433"
    }
  }

`extra_services` (2026-07 log review) extends the built-in CODEC probe set
with user infrastructure. Values are `http(s)://` URLs (any HTTP response
counts as up) or `tcp://host:port` (a successful connect counts as up).
Extra services get the same consecutive-failure alerting + recovery
notifications as built-ins but are NEVER auto-restarted — monitoring is
strictly read-only for them.

Remote alerts (27 Sep 2026 audit, item 6): with `alerts.telegram.chat_id` set,
alerts reach the owner's phone through the CODEC Telegram bot. The token comes
from the Keychain (`get_telegram_bot_token()`); a plaintext
`alerts.telegram.bot_token` is still honoured for older configs. Outbound only:
the bot's `telegram.allowed_chat_ids` stays empty, so it accepts no commands.
`alert_once` / `alert_resolved` send an ongoing problem once, repeat it at most
every 6 h, and send one message when it clears. Messages carry service names
and states only, never user data.
"""
import json
import logging
import os
import smtplib
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from email.mime.text import MIMEText
from typing import Optional

log = logging.getLogger("codec_alerts")

CONFIG_PATH = os.path.expanduser("~/.codec/config.json")
ALERT_STATE_PATH = os.path.expanduser("~/.codec/alert_state.json")
# Which problems have alerted and when; shared by every process that runs the
# heartbeat (codec-heartbeat and the dashboard), so writes go through a lock.
ALERTS_SENT_PATH = os.path.expanduser("~/.codec/alerts_sent.json")
REPEAT_EVERY_S = 6 * 3600


def _load_config() -> dict:
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _load_state() -> dict:
    try:
        with open(ALERT_STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state: dict):
    # Fix #9 Phase 1: atomic write (tmp+fsync+replace) so a crash mid-write
    # can't truncate the alert state.
    import codec_jsonstore
    codec_jsonstore.atomic_write_json(ALERT_STATE_PATH, state)


# ── Alert Channels ──────────────────────────────────────────────────────

def _send_telegram(bot_token: str, chat_id: str, message: str) -> bool:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    # Plain text: an alert quoting an error with "<" or "&" must not be
    # rejected by Telegram's HTML parser.
    data = json.dumps({"chat_id": chat_id, "text": message[:4000]}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        log.warning("Telegram alert failed: %s", e)
        return False


def _send_email(cfg: dict, subject: str, body: str) -> bool:
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = cfg["from"]
        msg["To"] = cfg["to"]
        with smtplib.SMTP(cfg["smtp_host"], cfg.get("smtp_port", 587)) as s:
            s.starttls()
            s.login(cfg["from"], cfg["password"])
            s.send_message(msg)
        return True
    except Exception as e:
        log.warning("Email alert failed: %s", e)
        return False


def _send_slack(webhook_url: str, message: str) -> bool:
    data = json.dumps({"text": message}).encode()
    req = urllib.request.Request(webhook_url, data=data, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        log.warning("Slack alert failed: %s", e)
        return False


def _applescript_text(text: str) -> str:
    """Quote text for an AppleScript string literal: alert text can quote git
    or Python errors, and a stray double quote must not end the literal."""
    return " ".join(text.split()).replace("\\", "\\\\").replace('"', '\\"')


def _send_macos_notification(message: str):
    try:
        subprocess.run(
            ["osascript", "-e", f'display notification "{_applescript_text(message[:120])}" with title "CODEC Alert" sound name "Glass"'],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass


def _telegram_target(alerts_cfg: dict):
    """(bot_token, chat_id) for owner alerts, or None when not configured."""
    tg = alerts_cfg.get("telegram") or {}
    chat_id = str(tg.get("chat_id") or "").strip()
    if not chat_id or tg.get("enabled") is False:
        return None
    token = tg.get("bot_token") or ""
    if not token:
        try:
            from codec_config import get_telegram_bot_token
            token = get_telegram_bot_token() or ""
        except Exception:
            token = ""
    return (token, chat_id) if token else None


def send_alert(level: str, message: str, subject: Optional[str] = None):
    """Dispatch alert to all configured channels.

    level: "critical", "warning", "info", "recovery"
    message: alert body text
    subject: optional email subject (defaults to "CODEC Alert: {level}")
    """
    cfg = _load_config()
    alerts_cfg = cfg.get("alerts", {})
    subject = subject or f"CODEC Alert: {level.upper()}"

    # Always send macOS notification
    _send_macos_notification(message)

    # Telegram
    target = _telegram_target(alerts_cfg)
    if target:
        _send_telegram(target[0], target[1], message)

    # Email
    em = alerts_cfg.get("email", {})
    if em.get("enabled") and em.get("from") and em.get("to"):
        _send_email(em, subject, message)

    # Slack
    sl = alerts_cfg.get("slack", {})
    if sl.get("enabled") and sl.get("webhook_url"):
        _send_slack(sl["webhook_url"], message)

    log.info("Alert dispatched [%s]: %s", level, message[:100])


def alert_once(key: str, level: str, message: str, *, repeat_s: int = REPEAT_EVERY_S,
               now: Optional[float] = None) -> bool:
    """Alert about an ongoing problem, at most once every `repeat_s` (6 h).
    `key` names the problem (e.g. "down:Dashboard"). Returns True if sent."""
    import codec_jsonstore
    now = time.time() if now is None else now
    decided = {"send": False}

    def _mutate(state):
        state = state if isinstance(state, dict) else {}
        problems = state.setdefault("problems", {})
        entry = problems.get(key) or {}
        if now - float(entry.get("last_sent", 0)) >= repeat_s:
            problems[key] = {"since": entry.get("since", now), "last_sent": now}
            decided["send"] = True
        return state

    codec_jsonstore.read_modify_write(ALERTS_SENT_PATH, _mutate)
    if decided["send"]:
        send_alert(level, message)
    return decided["send"]


def alert_resolved(key: str, message: str) -> bool:
    """If `key` alerted before, send one recovery message and forget it."""
    import codec_jsonstore
    decided = {"send": False}

    def _mutate(state):
        state = state if isinstance(state, dict) else {}
        decided["send"] = state.setdefault("problems", {}).pop(key, None) is not None
        return state

    codec_jsonstore.read_modify_write(ALERTS_SENT_PATH, _mutate)
    if decided["send"]:
        send_alert("recovery", message)
    return decided["send"]


def open_problems() -> list:
    """Keys of the problems that have alerted and not cleared yet."""
    try:
        with open(ALERTS_SENT_PATH) as f:
            return sorted((json.load(f).get("problems") or {}).keys())
    except Exception:
        return []


def _is_local_url(url: str) -> bool:
    host = (urllib.parse.urlparse(url or "").hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1")


# ── Service Monitoring ──────────────────────────────────────────────────

# Both run on the local model server; skipped while chat uses a cloud model.
_LOCAL_MODEL_SERVICES = ("LLM (Qwen)", "Vision")

_SERVICES = {
    "LLM (Qwen)": "http://localhost:{llm_port}/v1/models",
    "Whisper STT": "http://localhost:{stt_port}/",
    "Kokoro TTS": "http://localhost:{tts_port}/v1/models",
    "Dashboard": "http://localhost:{dashboard_port}/api/health",
    "Vision": "http://localhost:{vision_port}/v1/models",
}


# Gateway errors: a proxy or tunnel (Cloudflare) answered because the service
# behind it did not. From a remote URL they mean the service is down.
_GATEWAY_DOWN = {502, 503, 504} | set(range(520, 531))


def _check_service(url: str, timeout: int = 5) -> bool:
    """Probe one service. `http(s)://` → GET (any HTTP response = up, except
    a gateway error from a remote URL); `tcp://host:port` → bare connect
    (for non-HTTP services like Postgres)."""
    if url.startswith("tcp://"):
        try:
            hostport = url[len("tcp://"):].rstrip("/")
            host, _, port = hostport.rpartition(":")
            with socket.create_connection((host or "127.0.0.1", int(port)),
                                          timeout=timeout):
                return True
        except Exception:
            return False
    try:
        # Cloudflare's browser check answers 403 (error 1010) to urllib's
        # default "Python-urllib" agent without asking the service at all.
        req = urllib.request.Request(url, method="GET", headers={"User-Agent": "CODEC-alerts/1.0"})
        urllib.request.urlopen(req, timeout=timeout)
        return True
    except urllib.error.HTTPError as e:
        # 4xx/5xx means the service is up, unless a gateway answered for it.
        return _is_local_url(url) or e.code not in _GATEWAY_DOWN
    except Exception:
        return False


def _is_listening(url: str, timeout: int = 2) -> bool:
    """Is something still accepting TCP connections on this URL's port?

    Distinguishes BUSY from DEAD. A single-threaded model server doing a large
    prefill (an 11k-token prompt at ~800 tok/s blocks for ~14s) cannot answer an
    HTTP probe inside `_check_service`'s 5s timeout — but its socket is still
    bound and accepting. A crashed process is not.

    Without this, one slow probe made the heartbeat auto-restart the model
    MID-GENERATION, which killed the user's in-flight chat request: the reply
    came back as reasoning-with-no-answer. Long prompts were self-destructing.
    """
    try:
        parsed = urllib.parse.urlparse(url if "//" in url else "//" + url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def _try_restart(service_pm2_name: str) -> bool:
    """Attempt to restart a service via PM2. Returns True if restart command succeeded."""
    try:
        env = os.environ.copy()
        env["PATH"] = "/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:" + env.get("PATH", "")
        subprocess.run(
            ["/opt/homebrew/bin/pm2", "restart", service_pm2_name],
            capture_output=True, timeout=15, env=env,
        )
        time.sleep(5)  # Wait for service to come up
        return True
    except Exception:
        return False


# PM2 name mapping for auto-restart. Only built-in CODEC services appear
# here — extra_services entries are intentionally absent (never restarted).
# 2026-07 log review: the old names "qwen35b" / "qwen-vision" didn't match
# any live PM2 process (the real one is "qwen3.6"), so LLM/Vision
# auto-restart had been silently no-op'ing.
_PM2_NAMES = {
    "LLM (Qwen)": "qwen3.6",
    "Whisper STT": "whisper-stt",
    "Kokoro TTS": "kokoro-82m",
    "Dashboard": "codec-dashboard",
    "Vision": "qwen3.6",
}


def check_services_and_alert():
    """Check all services, attempt restart on failure, send alerts.

    Uses consecutive failure counting — alert fires after 2 consecutive failures.
    """
    cfg = _load_config()
    state = _load_state()
    now = datetime.now().isoformat()

    # On an automatic cloud fallback (codec_models.ensure_llm_available) the
    # local server is down by accident, not on purpose: keep probing it at its
    # own address, and keep restarting a crashed one, so CODEC can switch back.
    auto_fallback = isinstance(cfg.get("llm_auto_fallback_active"), dict)
    llm_url = cfg.get("llm_base_url", "http://localhost:8083")
    if auto_fallback:
        llm_url = (cfg.get("llm_local_restore") or {}).get("llm_base_url") or "http://localhost:8083/v1"

    # Resolve ports from config
    ports = {
        "llm_port": llm_url.split(":")[-1].split("/")[0],
        "stt_port": cfg.get("stt_url", "http://localhost:8084").split(":")[-1].split("/")[0],
        "tts_port": cfg.get("tts_url", "http://localhost:8085").split(":")[-1].split("/")[0],
        "dashboard_port": cfg.get("dashboard_port", 8090),
        "vision_port": cfg.get("vision_base_url", "http://localhost:8083").split(":")[-1].split("/")[0],
    }

    failures = state.get("consecutive_failures", {})
    state.get("last_alert", {})

    # Built-ins (port-templated) + user extras (literal URLs, read-only).
    all_services = {name: url_tpl.format(**ports)
                    for name, url_tpl in _SERVICES.items()}
    extras = cfg.get("alerts", {}).get("extra_services", {}) or {}
    for name, url in extras.items():
        all_services.setdefault(str(name), str(url))

    # While CODEC answers from a cloud model picked by hand, the local model
    # server (chat + vision on one process) is off on purpose: do not probe or
    # alert on it.
    if not auto_fallback and not _is_local_url(cfg.get("llm_base_url", "http://localhost:8083")):
        for name in _LOCAL_MODEL_SERVICES:
            if all_services.pop(name, None) is not None:
                failures[name] = 0
                alert_resolved(f"down:{name}", f"CODEC: {name} is no longer checked while chat uses the cloud model.")

    # Dedupe probes by resolved URL — LLM and Vision usually share one
    # qwen process on :8083; probing it twice double-counted every blip.
    _probe_cache: dict = {}

    for name, url in all_services.items():
        if url not in _probe_cache:
            _probe_cache[url] = _check_service(url)
        up = _probe_cache[url]

        if up:
            prev_fails = failures.get(name, 0)
            if prev_fails >= 2:
                # Recovery — was down, now up (one message, only if it alerted)
                downtime = state.get(f"down_since_{name}", "unknown")
                alert_resolved(
                    f"down:{name}",
                    f"CODEC RECOVERED: {name} is back online. Was down since {downtime}.",
                )
            failures[name] = 0
            if f"down_since_{name}" in state:
                del state[f"down_since_{name}"]
        else:
            failures[name] = failures.get(name, 0) + 1

            if failures[name] == 1:
                state[f"down_since_{name}"] = now

            # BUSY IS NOT DOWN. If the port still accepts connections, the
            # process is alive and merely too busy to answer inside the probe
            # timeout — the normal state for a model server mid-prefill on a
            # long prompt. Restarting it there killed the user's in-flight
            # request and returned an empty answer. Never restart on that;
            # a genuinely crashed process stops listening and still recovers.
            if _is_listening(url):
                failures[name] = 0
                if f"down_since_{name}" in state:
                    del state[f"down_since_{name}"]
                log.info(
                    "%s slow to answer but still listening — treating as busy, "
                    "not restarting", name,
                )
                continue

            if failures[name] == 1:
                # First failure — try auto-restart (with cooldown to prevent
                # restart loops). extra_services never appear in _PM2_NAMES,
                # so they can never be restarted from here. Cooldown is keyed
                # by PM2 process name (not display name) so two entries
                # sharing one process (LLM + Vision → qwen3.6) can't
                # double-restart it in a single pass.
                pm2_name = _PM2_NAMES.get(name)
                last_restart_key = f"last_restart_{pm2_name}"
                last_restart = state.get(last_restart_key, "")
                cooldown_ok = True
                if last_restart:
                    try:
                        elapsed = (datetime.fromisoformat(now) - datetime.fromisoformat(last_restart)).total_seconds()
                        if elapsed < 300:  # 5-minute cooldown between restarts
                            cooldown_ok = False
                            log.info("Skipping auto-restart for %s — last restart was %ds ago (cooldown 300s)", name, int(elapsed))
                    except Exception:
                        pass
                if pm2_name and cooldown_ok:
                    log.info("Auto-restarting %s (%s)...", name, pm2_name)
                    state[last_restart_key] = now
                    _try_restart(pm2_name)
                    time.sleep(15)  # Vision model needs ~15s to load
                    # Re-check after restart
                    if _check_service(url):
                        failures[name] = 0
                        _probe_cache[url] = True  # later names on this URL see the recovery
                        log.info("%s auto-restarted successfully", name)
                        alert_resolved(f"down:{name}", f"CODEC RECOVERED: {name} auto-restarted successfully.")
                        continue

            if failures[name] >= 2:
                # 2 consecutive failures — alert (then at most every 6 h)
                alert_once(
                    f"down:{name}",
                    "critical",
                    f"CODEC ALERT: {name} is not responding.\n"
                    f"Down since: {state.get(f'down_since_{name}', 'unknown')}\n"
                    f"Auto-restart attempted. Manual intervention needed.",
                )

    # Disk space check
    try:
        import shutil
        usage = shutil.disk_usage("/")
        free_gb = usage.free / (1024 ** 3)
        if free_gb < 0.5:
            alert_once("disk_low", "critical", f"CODEC ALERT: Disk space critically low — only {free_gb:.1f} GB free!")
        else:
            alert_resolved("disk_low", f"CODEC RECOVERED: disk space is back to {free_gb:.1f} GB free.")
    except Exception:
        pass

    # PM2 exec_cwd check
    expected_cwd = os.path.expanduser("~/codec-repo")
    try:
        env = os.environ.copy()
        env["PATH"] = "/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:" + env.get("PATH", "")
        out = subprocess.check_output(
            ["/opt/homebrew/bin/pm2", "show", "codec", "--no-color"],
            stderr=subprocess.STDOUT, timeout=10, env=env,
        ).decode()
        for line in out.splitlines():
            if "exec cwd" in line.lower():
                cwd = line.split("│")[-2].strip() if "│" in line else line.split()[-1]
                if cwd != expected_cwd:
                    alert_once("pm2_cwd", "warning", f"CODEC WARNING: PM2 exec_cwd is {cwd}, expected {expected_cwd}. Run sync_to_pm2.sh.")
                break
    except Exception:
        pass

    state["consecutive_failures"] = failures
    state["last_check"] = now
    _save_state(state)
