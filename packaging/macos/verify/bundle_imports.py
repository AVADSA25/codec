#!/usr/bin/env python3
"""Every package the services import is in the buyer lock, and imports in the bundle.

Two checks, one scan (audit P-011, D-35):

  1. static  (any machine): follow the imports of each service in the fleet definition
     through the repo's own modules, collect the third-party ones, and require each to
     be in packaging/macos/requirements-lock.txt, or in optional_imports.json with a
     reason, or imported only inside a try/except ImportError block (the code already
     copes without it).
  2. runtime (--python PATH): import each of those modules with the bundle's own
     interpreter. -B and PYTHONDONTWRITEBYTECODE keep it from writing .pyc files into
     the signed .app and breaking the signature.

Prints BUNDLE-IMPORTS-PASS on success, exits 1 and lists every problem otherwise.
Stdlib only.

  python3 packaging/macos/verify/bundle_imports.py [--python PATH] [--services FILE]
"""
import argparse
import ast
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(PKG))
LOCK = os.path.join(PKG, "requirements-lock.txt")
OPTIONAL = os.path.join(PKG, "optional_imports.json")

# Import name -> distribution name where the two differ. A name not here is its own
# distribution with "_" read as "-" (fastapi, mlx_vlm -> mlx-vlm).
IMPORT_TO_DIST = {
    "PIL": "pillow",
    "AppKit": "pyobjc-framework-cocoa",
    "Foundation": "pyobjc-framework-cocoa",
    "Quartz": "pyobjc-framework-quartz",
    "AVFoundation": "pyobjc-framework-avfoundation",
    "CoreMedia": "pyobjc-framework-coremedia",
    "google": "google-auth",
    "googleapiclient": "google-api-python-client",
    "google_auth_oauthlib": "google-auth-oauthlib",
    "argon2": "argon2-cffi",
    "key_value": "py-key-value-aio",
    "yaml": "pyyaml",
    "cv2": "opencv-python",
}

# Scripts the README tells the owner to run by hand; they ship in the bundle too.
HELPER_SCRIPTS = ["request_mic.py", "setup_hue.py"]

# Services started by a shell script or a module that the entry-file scan cannot see.
EXTRA_MODULES = {
    "qwen3.6": ["mlx_vlm"],            # scripts/start_model_server.sh runs mlx_vlm.server
    "kokoro-82m": ["mlx_audio"],       # python3 -m mlx_audio.server
}


def local_names():
    # Skills import their siblings by bare name (the skills folder is on sys.path).
    names = {f[:-3] for f in os.listdir(os.path.join(REPO, "skills")) if f.endswith(".py")}
    names -= set(sys.stdlib_module_names)
    for entry in os.listdir(REPO):
        if entry.endswith(".py"):
            names.add(entry[:-3])
        elif os.path.isdir(os.path.join(REPO, entry)) and \
                os.path.exists(os.path.join(REPO, entry, "__init__.py")):
            names.add(entry)
    return names


def fleet(services_file=None):
    """The services as plain dicts (name, script, args)."""
    if services_file:
        return json.load(open(services_file))
    out = subprocess.run(
        ["node", "-e", 'console.log(JSON.stringify(require(process.argv[1]).apps))',
         os.path.join(REPO, "ecosystem.config.js")],
        capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def entry_files(app):
    """Repo-relative .py files a service starts."""
    text = f"{app.get('script', '')} {app.get('args', '')}"
    files = re.findall(r"[\w./-]+\.py\b", text)
    m = re.search(r"-m\s+uvicorn\s+([\w.]+):", text)
    if m:
        files.append(m.group(1).replace(".", "/") + ".py")
    return [f for f in files if os.path.exists(os.path.join(REPO, f))]


def _guarded(node, parents):
    """True when the import sits in a try body whose handlers catch ImportError."""
    cur = node
    while cur in parents:
        par = parents[cur]
        if isinstance(par, ast.Try) and cur in par.body:
            for h in par.handlers:
                t = h.type
                names = [n.id for n in ast.walk(t) if isinstance(n, ast.Name)] if t else ["Exception"]
                if any(n in ("ImportError", "ModuleNotFoundError", "Exception", "BaseException")
                       for n in names):
                    return True
        cur = par
    return False


def scan(entries, local, stdlib):
    """-> {module: {"files": set, "guarded_only": bool}} for third-party imports."""
    seen, queue, found = set(), list(entries), {}
    while queue:
        rel = queue.pop()
        if rel in seen:
            continue
        seen.add(rel)
        path = os.path.join(REPO, rel)
        try:
            tree = ast.parse(open(path, encoding="utf8").read())
        except (OSError, SyntaxError):
            continue
        parents = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
        for n in ast.walk(tree):
            mods = []
            if isinstance(n, ast.Import):
                mods = [a.name for a in n.names]
            elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
                mods = [n.module]
            for mod in mods:
                top = mod.split(".")[0]
                if top in stdlib:
                    continue
                if top in local:
                    for cand in (mod.replace(".", "/") + ".py", mod.replace(".", "/") + "/__init__.py",
                                 top + ".py", top + "/__init__.py", "skills/" + top + ".py"):
                        if os.path.exists(os.path.join(REPO, cand)):
                            queue.append(cand)
                    continue
                rec = found.setdefault(mod, {"files": set(), "guarded_only": True})
                rec["files"].add(rel)
                if not _guarded(n, parents):
                    rec["guarded_only"] = False
    return found


def lock_packages():
    pkgs = set()
    for line in open(LOCK, encoding="utf8"):
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)==", line)
        if m:
            pkgs.add(re.sub(r"[-_.]+", "-", m.group(1)).lower())
    return pkgs


def dist_for(mod):
    top = mod.split(".")[0]
    return re.sub(r"[-_.]+", "-", IMPORT_TO_DIST.get(top, top)).lower()


def required_modules(services_file=None):
    """-> (modules the bundle must import, problems)."""
    local, stdlib = local_names(), set(sys.stdlib_module_names)
    optional = json.load(open(OPTIONAL))
    lock = lock_packages()
    entries, problems = [], []
    extra = []
    for app in fleet(services_file):
        files = entry_files(app)
        if not files and app["name"] not in EXTRA_MODULES:
            problems.append(f"service {app['name']}: no entry .py file found")
        entries += files
        extra += EXTRA_MODULES.get(app["name"], [])
    # Skills load on demand from any service, so every skill counts as an entry.
    entries += [h for h in HELPER_SCRIPTS if os.path.exists(os.path.join(REPO, h))]
    entries += [f"skills/{f}" for f in sorted(os.listdir(os.path.join(REPO, "skills")))
                if f.endswith(".py") and not f.startswith("_")]
    found = scan(entries, local, stdlib)
    for m in extra:
        found.setdefault(m, {"files": {"(launcher)"}, "guarded_only": False})
    need = {}
    for mod, rec in sorted(found.items()):
        top = mod.split(".")[0]
        if top in optional or mod in optional:
            continue
        if rec["guarded_only"]:
            continue
        if dist_for(mod) not in lock:
            problems.append(f"{mod} (from {sorted(rec['files'])[0]}) is not in the buyer lock: "
                            f"add {dist_for(mod)} to requirements.txt, or list {top} in "
                            f"optional_imports.json with the reason")
            continue
        need[mod] = rec
    return sorted(need), problems


RUNTIME_PROBE = r"""
import importlib, json, sys
bad = {}
for m in json.loads(sys.argv[1]):
    try:
        importlib.import_module(m)
    except BaseException as e:
        bad[m] = f"{type(e).__name__}: {e}"[:200]
print(json.dumps(bad))
"""


def runtime_failures(python, modules):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:   # not the repo: local files must not shadow
        r = subprocess.run([python, "-B", "-c", RUNTIME_PROBE, json.dumps(modules)],
                           capture_output=True, text=True, env=env, cwd=tmp, timeout=600)
    if r.returncode != 0:
        return {"(probe)": r.stderr.strip()[-300:]}
    return json.loads(r.stdout.strip().splitlines()[-1])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--python", help="the bundle's interpreter; omit for the static check only")
    ap.add_argument("--services", help="Resources/services.json; default: read ecosystem.config.js")
    a = ap.parse_args()
    modules, problems = required_modules(a.services)
    if a.python:
        for mod, why in runtime_failures(a.python, modules).items():
            problems.append(f"the bundled python cannot import {mod}: {why}")
    if problems:
        print("BUNDLE-IMPORTS FAILED:\n - " + "\n - ".join(problems), file=sys.stderr)
        return 1
    print(f"checked {len(modules)} modules" + (" in the bundle's python" if a.python else " against the lock"))
    print("BUNDLE-IMPORTS-PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
