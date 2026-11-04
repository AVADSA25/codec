#!/usr/bin/env python3
"""The built app carries the Cookbook and the Touch ID helper, and no stdio MCP service (P-012).

Checks a built .app (docs/APP-CONTENTS-DESIGN.md):

  1. Resources/app/codec_cookbook/ is there, and the bundle's own Python imports
     codec_cookbook (every module), codec_compare and skills/cookbook_scan.py. It runs
     with -B and PYTHONDONTWRITEBYTECODE from a temp folder, so no .pyc lands in the
     signed app and no repo file shadows the bundle's.
  2. Resources/app/codec_auth/codec_auth (routes/_shared.AUTH_BINARY) is an executable
     Mach-O file. --touchid also runs `codec_auth --check`, which must print its JSON.
  3. Resources/services.json has no codec-mcp (stdio server; restarts for ever as a
     LaunchAgent) and no codec-watchdog, and still has codec-mcp-http.
  4. --signed: `codesign --verify --strict` passes on the helper, and after the import
     probe `codesign --verify --deep --strict` still passes on the whole app.

Prints APP-CONTENTS-PASS on success; exits 1 and lists every problem otherwise. Stdlib only.

  python3 packaging/macos/verify/app_contents.py [--app APP] [--python PATH] [--signed] [--touchid]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
DEFAULT_APP = os.path.join(REPO, "dist", "Sovereign AI Workstation.app")

COOKBOOK_MODULES = ["catalog", "args", "probe", "fit", "download", "serve", "offer"]
NOT_SHIPPED = ["codec-mcp", "codec-watchdog"]
MUST_SHIP = ["codec-mcp-http"]
MACHO_MAGICS = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"}

IMPORT_PROBE = r"""
import importlib, importlib.util, json, sys
app = sys.argv[1]
sys.path[:0] = [app, app + "/skills"]
bad = {}
for name in json.loads(sys.argv[2]):
    try:
        mod = importlib.import_module(name)
        if not getattr(mod, "__file__", "").startswith(app):
            bad[name] = "imported from outside the app: " + str(getattr(mod, "__file__", None))
    except BaseException as e:
        bad[name] = f"{type(e).__name__}: {e}"[:200]
try:
    spec = importlib.util.spec_from_file_location("cookbook_scan", app + "/skills/cookbook_scan.py")
    skill = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(skill)
    if not callable(getattr(skill, "run", None)):
        bad["skills/cookbook_scan.py"] = "no run()"
except BaseException as e:
    bad["skills/cookbook_scan.py"] = f"{type(e).__name__}: {e}"[:200]
print(json.dumps(bad))
"""


def check_cookbook(app_dir, python):
    problems = []
    pkg = os.path.join(app_dir, "codec_cookbook")
    for f in ["__init__.py", "catalog.json"] + [m + ".py" for m in COOKBOOK_MODULES]:
        if not os.path.isfile(os.path.join(pkg, f)):
            problems.append(f"Resources/app/codec_cookbook/{f} is missing")
    if problems:
        return problems
    if not python:
        return ["no Python to import with: build with --with-python, or pass --python"]
    names = ["codec_cookbook"] + [f"codec_cookbook.{m}" for m in COOKBOOK_MODULES] + ["codec_compare"]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    env.pop("PYTHONPATH", None)
    with tempfile.TemporaryDirectory() as tmp:
        try:
            r = subprocess.run([python, "-B", "-c", IMPORT_PROBE, app_dir, json.dumps(names)],
                               capture_output=True, text=True, env=env, cwd=tmp, timeout=300)
        except (OSError, subprocess.SubprocessError) as e:
            return [f"could not run {python}: {e}"]
    if r.returncode != 0 or not r.stdout.strip():
        return [f"the import probe failed: {r.stderr.strip()[-300:]}"]
    bad = json.loads(r.stdout.strip().splitlines()[-1])
    return [f"{python} cannot import {name}: {why}" for name, why in bad.items()]


def is_macho(path):
    try:
        with open(path, "rb") as f:
            return f.read(4) in MACHO_MAGICS
    except OSError:
        return False


def check_helper(app_dir, touchid):
    helper = os.path.join(app_dir, "codec_auth", "codec_auth")
    if not os.path.isfile(helper):
        return ["Resources/app/codec_auth/codec_auth is missing (Touch ID reports unavailable)"]
    problems = []
    if not is_macho(helper):
        problems.append("Resources/app/codec_auth/codec_auth is not a Mach-O file")
    if not os.access(helper, os.X_OK):
        problems.append("Resources/app/codec_auth/codec_auth is not executable")
    if touchid and not problems:
        try:
            r = subprocess.run([helper, "--check"], capture_output=True, text=True, timeout=10)
            data = json.loads(r.stdout)
            if r.returncode != 0 or not isinstance(data.get("available"), bool):
                problems.append(f"codec_auth --check gave no availability answer: {r.stdout[:200]}")
            else:
                print(f"codec_auth --check: available={data['available']} method={data.get('method')}")
        except (OSError, subprocess.SubprocessError, ValueError, AttributeError) as e:
            problems.append(f"codec_auth --check did not run: {e}")
    return problems


def check_services(resources):
    path = os.path.join(resources, "services.json")
    try:
        with open(path, encoding="utf8") as f:
            names = [a.get("name") for a in json.load(f)]
    except (OSError, ValueError, AttributeError) as e:
        return [f"Resources/services.json is unreadable: {e}"]
    problems = [f"services.json ships the dev-only service {n}" for n in NOT_SHIPPED if n in names]
    problems += [f"services.json is missing {n}" for n in MUST_SHIP if n not in names]
    return problems


def codesign_verify(args):
    if not shutil.which("codesign"):
        return "codesign is not available on this machine"
    r = subprocess.run(["codesign", "--verify", *args], capture_output=True, text=True, timeout=600)
    return None if r.returncode == 0 else (r.stderr.strip() or r.stdout.strip())[-300:]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--app", default=DEFAULT_APP, help="the built .app")
    ap.add_argument("--python", help="interpreter for the import probe "
                                     "(default: the bundle's Resources/python/bin/python3)")
    ap.add_argument("--signed", action="store_true", help="also check the code signatures")
    ap.add_argument("--touchid", action="store_true", help="also run codec_auth --check")
    a = ap.parse_args(argv)

    a.app = os.path.abspath(a.app)   # the probe runs from a temp folder
    resources = os.path.join(a.app, "Contents", "Resources")
    app_dir = os.path.join(resources, "app")
    if not os.path.isdir(app_dir):
        print(f"APP-CONTENTS FAILED: {app_dir} does not exist (build the app first)", file=sys.stderr)
        return 1
    python = a.python
    if not python:
        bundled = os.path.join(resources, "python", "bin", "python3")
        python = bundled if os.path.exists(bundled) else None

    problems = check_cookbook(app_dir, python) + check_helper(app_dir, a.touchid) + check_services(resources)
    if a.signed:
        why = codesign_verify(["--strict", os.path.join(app_dir, "codec_auth", "codec_auth")])
        if why:
            problems.append(f"codesign --verify --strict fails on the Touch ID helper: {why}")
        why = codesign_verify(["--deep", "--strict", a.app])
        if why:
            problems.append(f"codesign --verify --deep --strict fails on the app: {why}")
    if problems:
        print("APP-CONTENTS FAILED:\n - " + "\n - ".join(problems), file=sys.stderr)
        return 1
    print(f"checked {a.app} (imports with {python})")
    print("APP-CONTENTS-PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
