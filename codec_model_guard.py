"""Model server memory guard (docs/MODEL-MEMORY-GUARD-DESIGN.md).

codec-dashboard calls tick() once a minute, in a worker thread. It reads the local
model server's footprint and compares it with three lines:

- alert:   memory_alert.footprint_ratio x the model (1.5x, the heartbeat's own line);
- restart: the smaller of restart_ratio x the model (2.5x) and restart_ram_pct of the
           Mac's memory (70%), never below the alert line;
- urgent:  urgent_ram_pct of the Mac's memory (80%), never below the restart line.

Above the alert line it alerts once. Above the restart line twice in a row, or above
the urgent line once, it restarts the server through the model switch's restart path
(codec_models.restart_for_switch) under the switch lock, at most restarts_per_hour
times. A voice call or a Vibe agent run holds a restart for at most max_defer_s; a
server started less than 5 minutes ago holds it below the urgent line. An image job
never holds it. After a restart that did not bring the server back it only alerts,
until the dashboard restarts or the owner turns model_guard.restart off and on.

Figures and reason codes only: no model id, path or text goes to the audit log, an
alert or the state file. Kill switch: MODEL_GUARD_ENABLED=false (nothing is read or
written). config.json:model_guard is read on every tick.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import sys
import time
import urllib.parse

log = logging.getLogger("codec_model_guard")

GB = 1e9
FIRST_TICK_DELAY_S = 60
DEFAULT_INTERVAL_S = 60
WINDOW_S = 3600
VOICE_MARKER = os.path.expanduser("~/.codec/voice_session.json")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}

DEFAULTS = {"enabled": True, "restart": True, "interval_s": 60, "restart_ratio": 2.5,
            "restart_ram_pct": 70, "urgent_ram_pct": 80, "max_defer_s": 120,
            "restarts_per_hour": 2}
BOUNDS = {"interval_s": (30, 300), "restart_ratio": (1.5, 6.0), "restart_ram_pct": (50, 90),
          "urgent_ram_pct": (60, 95), "max_defer_s": (0, 600), "restarts_per_hour": (1, 4)}
_INT_KEYS = {"interval_s", "max_defer_s", "restarts_per_hour"}
_RANK = {None: 0, "alert": 1, "restart": 2, "urgent": 3}
_DEFERRABLE = ("voice_call", "vibe_run")

_lock_fd = None          # held for the life of the process: one guard per Mac
_ram_bytes = None        # hw.memsize, read once
_model_sizes: dict = {}  # model id -> bytes, read once per model
_restart_failed = False  # a restart did not bring the server back: alerts only from here on


def _dir() -> str:
    return os.environ.get("CODEC_MODEL_GUARD_DIR") or os.path.expanduser("~/.codec")


def state_path() -> str:
    return os.path.join(_dir(), "model_guard.json")


def lock_path() -> str:
    return os.path.join(_dir(), "model_guard.lock")


def env_enabled() -> bool:
    return os.environ.get("MODEL_GUARD_ENABLED", "true").strip().lower() not in ("false", "0", "no", "off")


def settings(cfg: dict) -> dict:
    """config.json:model_guard with defaults; a wrong type gives the default, a number
    outside its bounds is clamped. alert_ratio is memory_alert.footprint_ratio."""
    raw = cfg.get("model_guard") if isinstance(cfg.get("model_guard"), dict) else {}
    out = {}
    for key, default in DEFAULTS.items():
        value = raw.get(key, default)
        if isinstance(default, bool):
            out[key] = value if isinstance(value, bool) else default
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            lo, hi = BOUNDS[key]
            value = min(max(value, lo), hi)
            out[key] = int(value) if key in _INT_KEYS else float(value)
        else:
            out[key] = default
    try:
        import codec_heartbeat
        out["alert_ratio"] = codec_heartbeat._memory_limits(cfg)[1]
    except Exception:
        out["alert_ratio"] = 1.5
    return out


def lines(model_b: float, ram_b: float, s: dict) -> dict:
    """The three lines in bytes."""
    alert = s["alert_ratio"] * model_b
    restart = max(min(s["restart_ratio"] * model_b, s["restart_ram_pct"] / 100.0 * ram_b), alert)
    urgent = max(s["urgent_ram_pct"] / 100.0 * ram_b, restart)
    return {"alert": alert, "restart": restart, "urgent": urgent}


def line_for(footprint_b: float, ln: dict):
    for name in ("urgent", "restart", "alert"):
        if footprint_b > ln[name]:
            return name
    return None


# ── Readings (each figure that cannot be read makes the tick do nothing) ─────────────

def _load_config() -> dict:
    try:
        from codec_config_path import config_path
        with open(config_path()) as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def _ram(hb) -> "int | None":
    global _ram_bytes
    if _ram_bytes is None:
        out = hb._mem_cmd([hb._SYSCTL, "-n", "hw.memsize"])
        try:
            _ram_bytes = int((out or "").strip()) or None
        except ValueError:
            _ram_bytes = None
    return _ram_bytes


def _model_size(hb, cm, cfg) -> "int | None":
    try:
        model = cm.local_server_model(cfg)[1]
    except Exception:
        return None
    if model not in _model_sizes:
        size = hb._model_bytes(cfg)
        if not size:
            return None
        _model_sizes[model] = size
    return _model_sizes[model]


def _footprint(hb, procs, cfg) -> "int | None":
    pid = hb._model_server_pid(procs, cfg)
    if not pid:
        return None
    return hb.parse_footprint(hb._mem_cmd([hb._FOOTPRINT, "-p", str(pid), "-f", "bytes", "--noCategories"]))


def read(cfg: dict) -> "dict | None":
    """One reading of the local model server, or None when any needed figure is
    unknown or the server is not listening (a down server is codec_alerts' case)."""
    import codec_heartbeat as hb
    import codec_models as cm
    try:
        base = cm.local_server_model(cfg)[0]
        parsed = urllib.parse.urlparse(base)
        host, port = (parsed.hostname or "localhost").lower(), parsed.port or 8083
    except Exception:
        return None
    if host not in _LOCAL_HOSTS or not cm._is_listening(host, port):
        return None
    procs = hb._pm2_jlist()
    footprint = _footprint(hb, procs, cfg)
    model = _model_size(hb, cm, cfg) if footprint else None
    ram = _ram(hb) if model else None
    if not (footprint and model and ram):
        return None
    swap = hb.parse_swapusage(hb._mem_cmd([hb._SYSCTL, "vm.swapusage"]))
    swap_pct = round(swap[0] * 100.0 / swap[1], 1) if swap and swap[1] > 0 else None
    return {"footprint": footprint, "model": model, "ram": ram, "swap_pct": swap_pct,
            "procs": procs, "app": cm._pm2_process_name(cfg)}


def holds(reading: dict, cfg: dict, now: float) -> list:
    """Reason codes that may hold a restart right now, most important first."""
    out = []
    if os.path.exists(VOICE_MARKER):
        out.append("voice_call")
    vibe = sys.modules.get("codec_vibe_agent")   # not imported means no run on this Mac
    try:
        if vibe is not None and (vibe.live_run() or {}).get("run"):
            out.append("vibe_run")
    except Exception:
        pass
    try:
        import codec_models
        if codec_models.pm2_app_loading(reading.get("procs"), reading.get("app", ""), now):
            out.append("starting")
    except Exception:
        pass
    return out


def cloud_active(cfg: dict) -> bool:
    """True while chat runs on a cloud model: config's base URL is not this Mac, and a
    restart would wait on the wrong port. The guard only alerts then."""
    try:
        host = (urllib.parse.urlparse(str(cfg.get("llm_base_url") or "")).hostname or "").lower()
    except Exception:
        return False
    return bool(host) and host not in _LOCAL_HOSTS


def _restart() -> tuple:
    import codec_models
    return codec_models.restart_for_switch()


def _switch_lock():
    import codec_models
    return codec_models._switch_lock(blocking=False)


# ── Messages, audit, state ───────────────────────────────────────────────────────────

def _gb(b) -> "float | None":
    return round(b / GB, 1) if isinstance(b, (int, float)) else None


def _audit(event: str, level: str, message: str, extra: dict) -> None:
    try:
        from codec_audit import audit
        audit(event=event, source="codec-model-guard", outcome="ok" if level == "info" else "warning",
              level=level, message=message, extra=extra)
    except Exception:
        pass


def _alert(kind: str, key: str, level: str, message: str) -> None:
    try:
        import codec_alerts
        if kind == "once":
            codec_alerts.alert_once(key, level, message)
        elif kind == "resolved":
            codec_alerts.alert_resolved(key, message)
        elif kind == "restarted":
            # The restart message stands in for the recovery message of the alert it ends.
            if not codec_alerts.alert_resolved(key, message):
                codec_alerts.send_alert("warning", message)
    except Exception as e:
        log.warning(f"Model guard alert not sent: {type(e).__name__}")


def _logs_hint(app: str) -> str:
    try:
        import codec_heartbeat
        return codec_heartbeat._logs_hint(app)
    except Exception:
        return f"pm2 logs {app}"


def _load_state() -> dict:
    try:
        with open(state_path()) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        import codec_jsonstore
        os.makedirs(_dir(), exist_ok=True)
        codec_jsonstore.atomic_write_json(state_path(), state)
    except Exception as e:
        log.warning(f"Model guard state not saved: {type(e).__name__}")


def status() -> dict:
    """The last reading for GET /api/status (figures only); {} before the first one."""
    state = _load_state()
    keys = ("checked_at", "footprint_gb", "model_gb", "ram_gb", "swap_pct", "ratio", "line",
            "lines_gb", "above_restart", "restarts_last_hour", "hold", "last_restart")
    return {k: state.get(k) for k in keys if k in state}


def _own_lock() -> bool:
    global _lock_fd
    if _lock_fd is not None:
        return True
    try:
        os.makedirs(_dir(), exist_ok=True)
        fd = os.open(lock_path(), os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return False
    _lock_fd = fd
    return True


# ── The tick ─────────────────────────────────────────────────────────────────────────

def tick(now: "float | None" = None) -> dict:
    """One reading and what follows from it. Returns {"interval_s", "action", ...};
    never raises."""
    try:
        return _tick(time.time() if now is None else now)
    except Exception as e:
        log.warning(f"Model guard tick failed: {type(e).__name__}")
        return {"interval_s": DEFAULT_INTERVAL_S, "action": "error"}


def _tick(now: float) -> dict:
    global _restart_failed
    if not env_enabled():
        return {"interval_s": DEFAULT_INTERVAL_S, "action": "off"}
    cfg = _load_config()
    s = settings(cfg)
    done = {"interval_s": s["interval_s"]}
    if not s["enabled"]:
        return dict(done, action="off")
    if not _own_lock():
        return dict(done, action="other_guard")
    if not s["restart"]:
        _restart_failed = False   # turning restart off and on again re-arms it
    reading = read(cfg)
    if reading is None:
        return dict(done, action="no_reading")

    fp, model, ram = reading["footprint"], reading["model"], reading["ram"]
    ln = lines(model, ram, s)
    line = line_for(fp, ln)
    state = _load_state()
    restarts = [t for t in state.get("restarts") or [] if isinstance(t, (int, float)) and now - t < WINDOW_S]
    ratio = round(fp / model, 2)
    figures = {"footprint_gb": _gb(fp), "model_gb": _gb(model), "ram_gb": _gb(ram),
               "swap_pct": reading["swap_pct"]}
    state.update(figures, checked_at=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
                 ratio=ratio, line=line, lines_gb={k: _gb(v) for k, v in ln.items()},
                 restarts=restarts, restarts_last_hour=len(restarts))

    if line is None:
        if state.get("logged_line"):
            _alert("resolved", "model_memory", "recovery",
                   f"CODEC RECOVERED: the model server holds {fp / GB:.0f} GB again, {ratio:.1f}x its model.")
        state.update(logged_line=None, above_restart=0, hold=None)
        _save_state(state)
        return dict(done, action="ok")

    if _RANK[line] > _RANK[state.get("logged_line")]:
        _audit("model_memory_high", "warning", f"model server footprint above the {line} line",
               dict(figures, ratio=ratio, line=line))
        if not state.get("logged_line"):
            tail = (f"CODEC restarts it at {ln['restart'] / GB:.0f} GB." if s["restart"]
                    else "Automatic restart is off (model_guard.restart).")
            _alert("once", "model_memory", "warning",
                   f"CODEC ALERT: the model server holds {fp / GB:.0f} GB, {ratio:.1f}x its "
                   f"{model / GB:.1f} GB model. This happens when something asks it for another "
                   f"model. {tail}")
        state["logged_line"] = line
    above = int(state.get("above_restart") or 0) + 1 if line in ("restart", "urgent") else 0
    state["above_restart"] = above
    if not (line == "urgent" or above >= 2):
        state["hold"] = None
        _save_state(state)
        return dict(done, action="alert")

    # A restart is wanted. What may stop or hold it:
    urgent = line == "urgent"
    reason = None
    if not s["restart"]:
        reason = "restart_off"
    elif _restart_failed:
        reason = "restart_failed"
    elif cloud_active(cfg):
        reason = "cloud_active"
    elif len(restarts) >= s["restarts_per_hour"]:
        reason = "budget"
    waited = 0.0
    if reason is None and not urgent:
        current = holds(reading, cfg, now)
        hold = state.get("hold") if isinstance(state.get("hold"), dict) else {}
        # max_defer_s counts from the first tick a call or a run held the restart.
        since = hold.get("since") if (hold.get("reason") in _DEFERRABLE
                                      and isinstance(hold.get("since"), (int, float))) else now
        if "starting" in current:
            reason = "starting"
        elif current:
            first = current[0]
            if now - since < s["max_defer_s"]:
                reason = first
            else:
                waited = now - since   # held long enough: restart with the call or run going
    if reason is not None:
        _hold(state, reason, now, figures, fp, len(restarts), reading.get("app", ""))
        _save_state(state)
        return dict(done, action="hold", reason=reason)

    waited_for = (state.get("hold") or {}).get("reason") if isinstance(state.get("hold"), dict) else None
    if waited_for in _DEFERRABLE:
        waited = max(waited, now - float(state["hold"].get("since", now)))
    with _switch_lock() as held:
        if not held:
            _save_state(state)   # a model switch is restarting it already
            return dict(done, action="switch_busy")
        t0 = time.time()
        ok, _detail = _restart()
        seconds = round(time.time() - t0, 1)
    after = None
    if ok:
        try:
            import codec_heartbeat as hb
            after = _footprint(hb, hb._pm2_jlist(), cfg)
        except Exception:
            after = None
    restarts.append(now)
    kind = _supervisor()
    _audit("model_guard_restart", "info" if ok else "warning",
           "model server restarted by the memory guard" if ok else "memory guard restart failed",
           {"result": "ok" if ok else "failed", "supervisor": kind, "before_gb": _gb(fp),
            "after_gb": _gb(after), "seconds": seconds, "waited_s": round(waited)})
    state.update(restarts=restarts, restarts_last_hour=len(restarts), above_restart=0, hold=None,
                 last_restart={"at": state["checked_at"], "result": "ok" if ok else "failed",
                               "before_gb": _gb(fp), "after_gb": _gb(after), "seconds": seconds})
    if ok:
        state["logged_line"] = None
        wait_txt = ""
        if waited_for in _DEFERRABLE and waited > 0:
            what = "the voice call" if waited_for == "voice_call" else "the Vibe agent run"
            wait_txt = f" after waiting {max(1, round(waited / 60))} minutes for {what}"
        after_txt = f"It holds {after / GB:.0f} GB again." if after else "It is starting again."
        _alert("restarted", "model_memory", "warning",
               f"CODEC: restarted the model server at {fp / GB:.0f} GB ({ratio:.1f}x its model)"
               f"{wait_txt}. {after_txt}")
    else:
        _restart_failed = True
        _alert("once", "model_memory_restart_failed", "critical",
               f"CODEC ALERT: CODEC restarted the model server at {fp / GB:.0f} GB and it did not "
               f"come back. No more automatic restarts until the dashboard restarts; its log: "
               f"{_logs_hint(reading.get('app', 'qwen3.6'))}.")
    _save_state(state)
    return dict(done, action="restarted" if ok else "restart_failed")


def _hold(state, reason, now, figures, fp, used, app) -> None:
    """Record a hold; the audit line and (for the budget) the alert go out once per hold."""
    previous = state.get("hold") if isinstance(state.get("hold"), dict) else {}
    if previous.get("reason") != reason:
        keep = previous.get("reason") in _DEFERRABLE and reason in _DEFERRABLE  # call then run: one wait
        state["hold"] = {"reason": reason, "since": previous.get("since", now) if keep else now}
        _audit("model_guard_held", "warning", f"memory guard restart held: {reason}",
               {"reason": reason, "footprint_gb": figures["footprint_gb"]})
        if reason == "budget":
            _alert("once", "model_memory_budget", "critical",
                   f"CODEC ALERT: the model server is at {fp / GB:.0f} GB and CODEC already "
                   f"restarted it {used} times this hour. Not restarting again; its log: "
                   f"{_logs_hint(app)}.")


def _supervisor() -> str:
    try:
        import codec_models
        return codec_models._supervisor_kind()
    except Exception:
        return "pm2"
