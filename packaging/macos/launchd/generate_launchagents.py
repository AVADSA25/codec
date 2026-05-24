#!/usr/bin/env python3
"""Generate macOS launchd LaunchAgent plists from CODEC's PM2 ecosystem (W5-3).

`ecosystem.config.js` stays the single source of truth for the service list; this
derives one `~/Library/LaunchAgents/ai.avadigital.codec.<name>.plist` per service
so the paid `.app` can run the fleet under launchd instead of PM2/Node.

The mapping core (`pm2_app_to_launchd` / `render_plist` / `generate_all`) is pure
stdlib (plistlib + shlex) and unit-tested without node/launchctl. The CLI reads
services from a JSON file (`--from-json`) or by dumping the PM2 ecosystem via node
(`--from-ecosystem`).

See docs/W5-3-LAUNCHD-DESIGN.md. Closes E-7.
"""
from __future__ import annotations

import argparse
import json
import os
import plistlib
import shlex
import subprocess
import sys

LABEL_PREFIX = "ai.avadigital.codec"
DEFAULT_LOG_DIR = "~/Library/Logs/CODEC"


def pm2_app_to_launchd(
    app: dict,
    *,
    interpreter_map: dict[str, str] | None = None,
    default_workdir: str | None = None,
    repo_root: str | None = None,
    log_dir: str = DEFAULT_LOG_DIR,
) -> tuple[str, dict]:
    """Map one PM2 app definition to (launchd Label, plist dict)."""
    name = app["name"]
    label = f"{LABEL_PREFIX}.{name}"

    program = [app["script"], *shlex.split(app.get("args", "") or "")]
    if interpreter_map:
        program = [interpreter_map.get(program[0], program[0]), *program[1:]]

    # Working directory: rewrite the repo-root cwd to the app's bundled workdir
    # (so a service that ran from the dev checkout runs from Resources/app in the
    # bundle); preserve any other absolute cwd (e.g. pilot-runner).
    cwd = app.get("cwd")
    if default_workdir and (cwd is None or (repo_root and cwd == repo_root)):
        cwd = default_workdir

    logs = os.path.expanduser(log_dir)
    plist: dict = {
        "Label": label,
        "ProgramArguments": program,
        "RunAtLoad": True,
        "KeepAlive": bool(app.get("autorestart", False)),
        "StandardOutPath": f"{logs}/{name}.out",
        "StandardErrorPath": f"{logs}/{name}.err",
        "ProcessType": "Background",
    }
    if cwd:
        plist["WorkingDirectory"] = cwd
    env = app.get("env")
    if env:
        plist["EnvironmentVariables"] = dict(env)
    delay_ms = app.get("restart_delay")
    if isinstance(delay_ms, (int, float)) and delay_ms > 0:
        plist["ThrottleInterval"] = max(10, round(delay_ms / 1000))

    return label, plist


def render_plist(plist: dict) -> bytes:
    """Serialize a plist dict to XML plist bytes (launchd format)."""
    return plistlib.dumps(plist, fmt=plistlib.FMT_XML, sort_keys=True)


def generate_all(apps: list[dict], **kw) -> dict[str, bytes]:
    """Return {label: plist_bytes} for every service."""
    out: dict[str, bytes] = {}
    for app in apps:
        label, plist = pm2_app_to_launchd(app, **kw)
        out[label] = render_plist(plist)
    return out


def load_from_json(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict) and "apps" in data:
        data = data["apps"]
    if not isinstance(data, list):
        raise ValueError("services JSON must be a list of app dicts (or {'apps': [...]})")
    return data


def load_from_ecosystem(path: str) -> list[dict]:
    """Dump the PM2 ecosystem's `apps` array to JSON via node."""
    abspath = os.path.abspath(path)
    script = "console.log(JSON.stringify(require(process.argv[1]).apps))"
    try:
        out = subprocess.check_output(["node", "-e", script, abspath], text=True, timeout=60)
    except FileNotFoundError as e:
        raise SystemExit("node is required for --from-ecosystem (not found on PATH)") from e
    except subprocess.CalledProcessError as e:
        raise SystemExit(f"node failed to load {path}: {e}") from e
    return json.loads(out)


def _parse_interpreter_map(pairs: list[str]) -> dict[str, str]:
    m: dict[str, str] = {}
    for p in pairs or []:
        if "=" not in p:
            raise SystemExit(f"--interpreter expects FROM=TO, got: {p}")
        k, v = p.split("=", 1)
        m[k] = v
    return m


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Generate launchd LaunchAgents from the CODEC PM2 ecosystem.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-json", metavar="PATH", help="services JSON (list of PM2 app dicts)")
    src.add_argument("--from-ecosystem", metavar="PATH", help="ecosystem.config.js (dumped via node)")
    ap.add_argument("--out", default=os.path.expanduser("~/Library/LaunchAgents"), help="output dir for plists")
    ap.add_argument("--interpreter", action="append", default=[], metavar="FROM=TO",
                    help="remap an interpreter path (repeatable), e.g. python3=/path/to/bundled/python3")
    ap.add_argument("--workdir", default=None, help="WorkingDirectory for repo-rooted services (the app's Resources/app)")
    ap.add_argument("--log-dir", default=DEFAULT_LOG_DIR, help="dir for per-service stdout/stderr logs")
    ap.add_argument("--dry-run", action="store_true", help="print what would be written; write nothing")
    args = ap.parse_args(argv)

    if args.from_json:
        apps = load_from_json(args.from_json)
        repo_root = None
    else:
        apps = load_from_ecosystem(args.from_ecosystem)
        repo_root = os.path.dirname(os.path.abspath(args.from_ecosystem))

    interp = _parse_interpreter_map(args.interpreter)
    rendered = generate_all(
        apps,
        interpreter_map=interp,
        default_workdir=args.workdir,
        repo_root=repo_root,
        log_dir=args.log_dir,
    )

    if args.dry_run:
        for label in rendered:
            print(f"{label}  ->  {os.path.join(args.out, label + '.plist')}")
        print(f"[dry-run] {len(rendered)} LaunchAgent(s); nothing written")
        return 0

    os.makedirs(args.out, exist_ok=True)
    for label, raw in rendered.items():
        dest = os.path.join(args.out, f"{label}.plist")
        with open(dest, "wb") as fh:
            fh.write(raw)
        print(f"wrote {dest}")
    print(f"{len(rendered)} LaunchAgent(s) written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
