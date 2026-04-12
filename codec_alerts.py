"""CODEC Alerting — dispatch alerts via Telegram, Email, Slack when components fail.

Configure in ~/.codec/config.json:
  "alerts": {
    "telegram": {"enabled": true, "bot_token": "...", "chat_id": "..."},
    "email": {"enabled": false, "smtp_host": "smtp.gmail.com", "smtp_port": 587,
              "from": "codec@you.com", "to": "you@you.com", "password": "app-password"},
    "slack": {"enabled": false, "webhook_url": "https://hooks.slack.com/..."}
  }
"""
import json
import logging
import os
import smtplib
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime
from email.mime.text import MIMEText
from typing import Optional

log = logging.getLogger("codec_alerts")

CONFIG_PATH = os.path.expanduser("~/.codec/config.json")
ALERT_STATE_PATH = os.path.expanduser("~/.codec/alert_state.json")


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
    os.makedirs(os.path.dirname(ALERT_STATE_PATH), exist_ok=True)
    with open(ALERT_STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


# ── Alert Channels ──────────────────────────────────────────────────────

def _send_telegram(bot_token: str, chat_id: str, message: str) -> bool:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = json.dumps({"chat_id": chat_id, "text": message, "parse_mode": "HTML"}).encode()
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


def _send_macos_notification(message: str):
    try:
        subprocess.run(
            ["osascript", "-e", f'display notification "{message[:120]}" with title "CODEC Alert" sound name "Glass"'],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass


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
    tg = alerts_cfg.get("telegram", {})
    if tg.get("enabled") and tg.get("bot_token") and tg.get("chat_id"):
        _send_telegram(tg["bot_token"], tg["chat_id"], message)

    # Email
    em = alerts_cfg.get("email", {})
    if em.get("enabled") and em.get("from") and em.get("to"):
        _send_email(em, subject, message)

    # Slack
    sl = alerts_cfg.get("slack", {})
    if sl.get("enabled") and sl.get("webhook_url"):
        _send_slack(sl["webhook_url"], message)

    log.info("Alert dispatched [%s]: %s", level, message[:100])


# ── Service Monitoring ──────────────────────────────────────────────────

_SERVICES = {
    "LLM (Qwen)": "http://localhost:{llm_port}/v1/models",
    "Whisper STT": "http://localhost:{stt_port}/",
    "Kokoro TTS": "http://localhost:{tts_port}/v1/models",
    "Dashboard": "http://localhost:{dashboard_port}/api/health",
    "Vision": "http://localhost:{vision_port}/v1/models",
}


def _check_service(url: str, timeout: int = 5) -> bool:
    try:
        req = urllib.request.Request(url, method="GET")
        urllib.request.urlopen(req, timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True  # 4xx/5xx means service is up
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


# PM2 name mapping for auto-restart
_PM2_NAMES = {
    "LLM (Qwen)": "qwen35b",
    "Whisper STT": "whisper-stt",
    "Kokoro TTS": "kokoro-82m",
    "Dashboard": "codec-dashboard",
    "Vision": "qwen-vision",
}


def check_services_and_alert():
    """Check all services, attempt restart on failure, send alerts.

    Uses consecutive failure counting — alert fires after 2 consecutive failures.
    """
    cfg = _load_config()
    state = _load_state()
    now = datetime.now().isoformat()

    # Resolve ports from config
    ports = {
        "llm_port": cfg.get("llm_base_url", "http://localhost:8081").split(":")[-1].split("/")[0],
        "stt_port": cfg.get("stt_url", "http://localhost:8084").split(":")[-1].split("/")[0],
        "tts_port": cfg.get("tts_url", "http://localhost:8085").split(":")[-1].split("/")[0],
        "dashboard_port": cfg.get("dashboard_port", 8090),
        "vision_port": cfg.get("vision_base_url", "http://localhost:8082").split(":")[-1].split("/")[0],
    }

    failures = state.get("consecutive_failures", {})
    state.get("last_alert", {})

    for name, url_tpl in _SERVICES.items():
        url = url_tpl.format(**ports)
        up = _check_service(url)

        if up:
            prev_fails = failures.get(name, 0)
            if prev_fails >= 2:
                # Recovery — was down, now up
                downtime = state.get(f"down_since_{name}", "unknown")
                send_alert(
                    "recovery",
                    f"CODEC RECOVERED: {name} is back online. Was down since {downtime}.",
                )
            failures[name] = 0
            if f"down_since_{name}" in state:
                del state[f"down_since_{name}"]
        else:
            failures[name] = failures.get(name, 0) + 1

            if failures[name] == 1:
                state[f"down_since_{name}"] = now

            if failures[name] == 1:
                # First failure — try auto-restart (with cooldown to prevent restart loops)
                pm2_name = _PM2_NAMES.get(name)
                last_restart_key = f"last_restart_{name}"
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
                        send_alert("recovery", f"CODEC RECOVERED: {name} auto-restarted successfully.")
                        continue

            if failures[name] >= 2:
                # 2 consecutive failures — alert
                send_alert(
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
            send_alert("critical", f"CODEC ALERT: Disk space critically low — only {free_gb:.1f} GB free!")
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
                    send_alert("warning", f"CODEC WARNING: PM2 exec_cwd is {cwd}, expected {expected_cwd}. Run sync_to_pm2.sh.")
                break
    except Exception:
        pass

    state["consecutive_failures"] = failures
    state["last_check"] = now
    _save_state(state)
