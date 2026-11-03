#!/usr/bin/env python3
"""List old secrets left at rest, by name only (P-088, docs/SECRETS-AT-REST-DESIGN.md).

    python3 tools/list_old_secrets.py             # text
    python3 tools/list_old_secrets.py --json      # the same report as JSON
    python3 tools/list_old_secrets.py --commands  # print (never run) the deletes for the test leftovers

Two read-only lists for the owner to look at before he says yes to a clean-up:

1. Keychain items whose service starts with ai.avadigital.codec, read with `security dump-keychain`
   without -d, so macOS hands over item attributes and no values. Test leftovers (service
   ai.avadigital.codec._test_*), items never to delete (audit_hmac_secret and internal_token, every
   account), and a count of the other CODEC items.
2. Old copies of config.json in ~/.codec and ~/.codec/backups (every file whose name starts with
   "config", except config.json itself and its .lock / .tmp) that hold an old unsalted PIN hash
   (auth_pin_hash of 64 hex digits; the live one is argon2id) or a value of JWT shape.

Prints item, account, file and setting names only: never a value. Writes, deletes and changes
nothing. Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from typing import Any, Optional

PREFIX = "ai.avadigital.codec"
TEST_PREFIX = PREFIX + "._test_"
NEVER_DELETE = (PREFIX + ".audit_hmac_secret", PREFIX + ".internal_token")
SECURITY_BIN = "/usr/bin/security"
CODEC_DIR = os.path.expanduser("~/.codec")
MAX_FILE_BYTES = 5 * 1024 * 1024
DUMP_TIMEOUT = 120

_ATTR = re.compile(r'^\s*"(svce|acct)"<blob>=(.*)$')
_HEX_ATTR = re.compile(r"^0x([0-9A-Fa-f]+)")
_OLD_PIN_HASH = re.compile(r"^[0-9a-fA-F]{64}$")
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
_RAW_OLD_PIN_HASH = re.compile(r'"auth_pin_hash"\s*:\s*"[0-9a-fA-F]{64}"')
_PLAIN_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")

OLD_PIN_HASH = "old PIN hash"
JWT = "JWT"


# ── Keychain ──────────────────────────────────────────────────────────────────

def _attr_value(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith('"') and raw.endswith('"') and len(raw) >= 2:
        return raw[1:-1]
    m = _HEX_ATTR.match(raw)
    if m and len(m.group(1)) % 2 == 0:
        return bytes.fromhex(m.group(1)).decode("utf-8", "replace")
    return ""  # <NULL> or a shape this parser does not know


def parse_dump(text: str) -> list:
    """[{service, account}] of the generic-password items under ai.avadigital.codec."""
    items, cur = [], None

    def _close():
        if cur and cur.get("class") == "genp" and cur.get("service", "").startswith(PREFIX):
            items.append({"service": cur["service"], "account": cur.get("account", "")})

    for line in text.splitlines():
        if line.startswith("keychain:"):
            _close()
            cur = {}
            continue
        if cur is None:
            continue
        if line.startswith("class:"):
            cur["class"] = line.split(":", 1)[1].strip().strip('"')
            continue
        m = _ATTR.match(line)
        if m:
            cur["service" if m.group(1) == "svce" else "account"] = _attr_value(m.group(2))
    _close()
    return sorted(items, key=lambda i: (i["service"], i["account"]))


def keychain_items(security_bin: Optional[str] = None) -> Optional[list]:
    """The CODEC items in the Keychain, names only, or None when `security` cannot be run here.
    Only `dump-keychain` is run, never with -d (which would read every value)."""
    prog = security_bin or SECURITY_BIN
    if not os.path.exists(prog):
        return None
    try:
        r = subprocess.run([prog, "dump-keychain"], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=DUMP_TIMEOUT, errors="replace")
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return parse_dump(r.stdout)


def sort_items(items: list) -> dict:
    groups = {"test": [], "never_delete": [], "other": []}
    for i in items:
        if i["service"].startswith(TEST_PREFIX):
            groups["test"].append(i)
        elif i["service"] in NEVER_DELETE:
            groups["never_delete"].append(i)
        else:
            groups["other"].append(i)
    return groups


def delete_commands(items: list) -> list:
    """The owner's delete lines for test leftovers. Raises on anything else."""
    lines = []
    for i in items:
        if not i["service"].startswith(TEST_PREFIX) or i["service"] in NEVER_DELETE:
            raise ValueError("refusing a delete line for an item that is not a test leftover")
        lines.append(f"security delete-generic-password -s {shlex.quote(i['service'])} "
                     f"-a {shlex.quote(i['account'])}")
    return lines


# ── Old copies of config.json ─────────────────────────────────────────────────

def backup_files(codec_dir: str) -> list:
    """Every config copy in codec_dir and codec_dir/backups, as names relative to codec_dir."""
    out = []
    for sub in ("", "backups"):
        folder = os.path.join(codec_dir, sub) if sub else codec_dir
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for n in names:
            if not n.startswith("config") or n == "config.json" or n.endswith((".lock", ".tmp")):
                continue
            full = os.path.join(folder, n)
            if os.path.islink(full) or not os.path.isfile(full):
                continue
            out.append(os.path.join(sub, n) if sub else n)
    return sorted(out)


def _segment(key: Any) -> str:
    k = str(key)
    return k if _PLAIN_KEY.match(k) and not _JWT.search(k) else "*"


def old_secret_fields(obj: Any, path: str = "") -> list:
    """[(setting name, kind)] for an old PIN hash or a JWT-shaped value. Names only."""
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            here = (path + "." if path else "") + _segment(k)
            if _JWT.search(str(k)):
                found.append((path or "*", JWT))
            if isinstance(v, str):
                if k == "auth_pin_hash" and _OLD_PIN_HASH.match(v.strip()):
                    found.append((here, OLD_PIN_HASH))
                if _JWT.search(v):
                    found.append((here, JWT))
            else:
                found += old_secret_fields(v, here)
    elif isinstance(obj, list):
        for v in obj:
            if isinstance(v, str):
                if _JWT.search(v):
                    found.append((path + "[]", JWT))
            else:
                found += old_secret_fields(v, path + "[]")
    return found


def scan_file(path: str) -> Optional[dict]:
    """{"json": bool, "fields": [(name, kind)]}, or None when the file cannot be read."""
    try:
        if os.path.getsize(path) > MAX_FILE_BYTES:
            return None
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return None
    try:
        return {"json": True, "fields": old_secret_fields(json.loads(text))}
    except ValueError:
        fields = []
        if _RAW_OLD_PIN_HASH.search(text):
            fields.append(("(not JSON)", OLD_PIN_HASH))
        if _JWT.search(text):
            fields.append(("(not JSON)", JWT))
        return {"json": False, "fields": fields}


def scan_backups(codec_dir: str) -> dict:
    files = backup_files(codec_dir)
    hits, unreadable = [], []
    for name in files:
        res = scan_file(os.path.join(codec_dir, name))
        if res is None:
            unreadable.append(name)
            continue
        if res["fields"]:
            fields = sorted(set(res["fields"]))
            hits.append({"file": name, "json": res["json"],
                         "old_pin_hash": [f for f, k in fields if k == OLD_PIN_HASH],
                         "jwt": [f for f, k in fields if k == JWT]})
    return {"folder": codec_dir, "checked": len(files), "unreadable": unreadable, "with_old_secrets": hits}


# ── Report ────────────────────────────────────────────────────────────────────

def build_report(codec_dir: Optional[str] = None, security_bin: Optional[str] = None) -> dict:
    items = keychain_items(security_bin)
    if items is None:
        keychain = {"available": False}
    else:
        g = sort_items(items)
        keychain = {"available": True, "test": g["test"], "never_delete": g["never_delete"],
                    "other_count": len(g["other"])}
    return {"keychain": keychain, "config_backups": scan_backups(codec_dir or CODEC_DIR)}


def render(report: dict) -> str:
    out = ["Keychain items under ai.avadigital.codec (names only, no values)"]
    kc = report["keychain"]
    if not kc.get("available"):
        out.append("  not read: the security program is not here or did not answer")
    else:
        out.append(f"  Test leftovers ({TEST_PREFIX}*), safe to delete after your word: {len(kc['test'])}")
        out += [f"    {i['service']}   account: {i['account']}" for i in kc["test"]]
        out.append(f"  Never delete (live audit secret and internal token): {len(kc['never_delete'])}")
        seen = {}
        for i in kc["never_delete"]:
            seen[i["service"]] = seen.get(i["service"], 0) + 1
            note = "   (another account for the same name)" if seen[i["service"]] > 1 else ""
            out.append(f"    {i['service']}   account: {i['account']}{note}")
        out.append(f"  Other CODEC items, kept and not part of this clean-up: {kc['other_count']}")
    cb = report["config_backups"]
    out.append("")
    out.append("Old copies of config.json (names only, no values)")
    out.append(f"  {cb['checked']} file(s) checked in {cb['folder']} and its backups folder; "
               f"{len(cb['with_old_secrets'])} hold an old PIN hash or a JWT")
    for h in cb["with_old_secrets"]:
        parts = []
        if h["old_pin_hash"]:
            parts.append(f"{OLD_PIN_HASH}: {', '.join(h['old_pin_hash'])}")
        if h["jwt"]:
            parts.append(f"{JWT}: {', '.join(h['jwt'])}")
        out.append(f"    {h['file']}   {'; '.join(parts)}")
    if cb["unreadable"]:
        out.append(f"  Not read (too big or not readable): {', '.join(cb['unreadable'])}")
    return "\n".join(out)


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="List old secrets left at rest, by name only. Changes nothing.")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--json", action="store_true", help="print the report as JSON")
    mode.add_argument("--commands", action="store_true",
                      help="print (not run) the delete lines for the test Keychain leftovers")
    args = ap.parse_args(argv)
    report = build_report()
    if args.json:
        print(json.dumps(report, indent=2))
    elif args.commands:
        kc = report["keychain"]
        if not kc.get("available"):
            print("Keychain not read: the security program is not here or did not answer.", file=sys.stderr)
            return 1
        for line in delete_commands(kc["test"]):
            print(line)
    else:
        print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
