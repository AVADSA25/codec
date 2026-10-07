"""Skills switched off from the Skills page (UI phase 3, P3.6; docs/P3.6-DESIGN.md).

``~/.codec/config.json:skills_off`` lists the skills the owner turned off. It is a
deny list, so a skill added later is on until someone turns it off. (The older
``skills`` allow list, written by ``/skills enable|disable`` before P3.6, was never
read; honouring it now would silently turn off every skill added since, so it is
ignored.)

Every path that runs a skill asks ``is_off`` first: codec_dispatch (check_skill
skips, run_skill refuses), the MCP tools, the voice call's matcher, crew tools and
the chat's '@' list. The file is read again when it changes, so a switch reaches
every process without a restart.
"""
from __future__ import annotations

import json
import os
import threading

import codec_jsonstore

CONFIG_PATH = os.path.expanduser("~/.codec/config.json")
_LOCK = threading.Lock()
_CACHE = {"stamp": None, "off": frozenset()}


def off_set() -> frozenset:
    """The names switched off, read again when config.json changes."""
    path = CONFIG_PATH
    try:
        st = os.stat(path)
        stamp = (path, st.st_mtime_ns, st.st_size)
    except OSError:
        return frozenset()
    with _LOCK:
        if stamp != _CACHE["stamp"]:
            try:
                with open(path, encoding="utf-8") as f:
                    cfg = json.load(f)
            except (OSError, ValueError):
                cfg = {}
            names = cfg.get("skills_off") if isinstance(cfg, dict) else None
            _CACHE["off"] = frozenset(str(n) for n in names) if isinstance(names, list) else frozenset()
            _CACHE["stamp"] = stamp
        return _CACHE["off"]


def is_off(name: str) -> bool:
    return bool(name) and name in off_set()


def off_message(name: str) -> str:
    return f"The skill '{name}' is turned off. Turn it back on in Skills (Home > Skills)."


def set_on(name: str, on: bool) -> bool:
    """Switch a skill on or off in config.json (atomic, under the config lock). Returns whether it is on."""
    path = CONFIG_PATH
    with codec_jsonstore.file_lock(path):
        try:
            with open(path, encoding="utf-8") as f:
                cfg = json.load(f)
        except (OSError, ValueError):
            cfg = {}
        if not isinstance(cfg, dict):
            cfg = {}
        off = [str(n) for n in (cfg.get("skills_off") or []) if isinstance(n, str)]
        off = [n for n in off if n != name]
        if not on:
            off.append(name)
        cfg["skills_off"] = sorted(set(off))
        codec_jsonstore.atomic_write_json(path, cfg)
    with _LOCK:
        _CACHE["stamp"] = None  # read again on the next check
    return on
