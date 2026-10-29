#!/usr/bin/env python3
"""Build static/vendor/monaco from the monaco-editor npm tarball (D-24).

The Vibe page (codec_vibe.html) loads Monaco's AMD build from
/static/vendor/monaco/vs, so nothing comes from a CDN. This tool writes that
folder from the package exactly as npm publishes it, and checks it:

    npm pack monaco-editor@0.57.0                                  # in an empty folder
    python3 tools/vendor_monaco.py monaco-editor-0.57.0.tgz          # rewrite the folder
    python3 tools/vendor_monaco.py --check monaco-editor-0.57.0.tgz  # exit 1 if it differs

Kept: every file under package/min/vs that the editor can load, found by
following the module names and the script and worker URLs from vs/loader.js and
vs/editor/editor.main.js, plus package/LICENSE and package/ThirdPartyNotices.txt.

Left out: the translated messages (nls/lang, the page is in English), the
second copies of the language workers under language/ (nothing loads them), and
the TypeScript language service worker (about 7 MB). The page switches the
TypeScript and JavaScript language features off, so that worker is never
started; JavaScript and TypeScript keep their colours.

One file is changed: editor.main.css carries the codicon icon font as a data:
URL, which the dashboard CSP (font-src 'self') refuses. The font is written,
byte for byte, to vs/editor/codicon.ttf and the CSS points at it instead.
Every other file is the published file unchanged.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import posixpath
import re
import shutil
import sys
import tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "static" / "vendor" / "monaco"
MAX_BYTES = 8_000_000
ENTRIES = ("loader.js", "editor/editor.main.js")
LEFT_OUT = re.compile(r"^assets/ts\.worker-[\w-]+\.js$")
NOTICES = ("LICENSE", "ThirdPartyNotices.txt")
# A module name or relative URL in quotes: "./x", "../x", "vs/x", "vs/x!" (loader plugin).
REF = re.compile(r"""["'`]((?:\.\.?/)+[\w.\-/]+|vs/[\w.\-/]+)!?["'`]""")
FONT = re.compile(rb"url\(data:font/ttf;base64,([A-Za-z0-9+/=]+)\)")


def read_package(tgz: Path) -> tuple[dict, dict]:
    """The tarball's files under package/min/vs, and the other package files we use."""
    vs, other = {}, {}
    with tarfile.open(tgz, "r:gz") as tar:
        for m in tar.getmembers():
            name = m.name
            if not m.isfile() or not name.startswith("package/"):
                continue
            rel = name[len("package/"):]
            if rel.startswith("/") or ".." in rel.split("/"):
                raise SystemExit(f"refusing a strange path in the tarball: {name}")
            if rel.startswith("min/vs/"):
                vs[rel[len("min/vs/"):]] = tar.extractfile(m).read()
            elif rel in NOTICES or rel == "package.json":
                other[rel] = tar.extractfile(m).read()
    return vs, other


def reachable(vs: dict) -> set:
    """Every file under min/vs that the loader and editor.main can reach."""
    seen, todo = set(), list(ENTRIES)
    while todo:
        rel = todo.pop()
        if rel in seen or rel not in vs:
            continue
        seen.add(rel)
        if not rel.endswith(".js"):
            continue
        for ref in REF.findall(vs[rel].decode("utf-8", "replace")):
            if ref.startswith("vs/"):
                target = ref[3:]
            else:
                target = posixpath.normpath(posixpath.join(posixpath.dirname(rel), ref))
            for cand in (target, target + ".js"):
                if cand in vs:
                    todo.append(cand)
                    break
    if missing := [e for e in ENTRIES if e not in seen]:
        raise SystemExit(f"not a Monaco min/vs build: missing {missing}")
    return seen


def build(tgz: Path) -> tuple[dict, dict]:
    """The folder's expected files ({relative path: bytes}) and facts about it."""
    vs, other = read_package(tgz)
    keep = {r for r in reachable(vs) if not LEFT_OUT.match(r)}
    files = {"vs/" + r: vs[r] for r in sorted(keep)}
    css = files["vs/editor/editor.main.css"]
    fonts = FONT.findall(css)
    if len(fonts) != 1:
        raise SystemExit(f"expected one inline codicon font in editor.main.css, found {len(fonts)}")
    files["vs/editor/codicon.ttf"] = base64.b64decode(fonts[0], validate=True)
    files["vs/editor/editor.main.css"] = FONT.sub(b"url(codicon.ttf)", css)
    for n in NOTICES:
        files[n] = other[n]
    raw = tgz.read_bytes()
    facts = {
        "version": json.loads(other["package.json"])["version"],
        "integrity": "sha512-" + base64.b64encode(hashlib.sha512(raw).digest()).decode(),
        "tarball_sha256": hashlib.sha256(raw).hexdigest(),
        "files": len(files),
        "bytes": sum(len(b) for b in files.values()),
        "left_out": sorted(set(vs) - keep),
    }
    return files, facts


def on_disk() -> dict:
    if not OUT.is_dir():
        return {}
    return {p.relative_to(OUT).as_posix(): p.read_bytes() for p in OUT.rglob("*") if p.is_file()}


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("tarball", type=Path, help="monaco-editor-<version>.tgz from `npm pack`")
    ap.add_argument("--check", action="store_true", help="compare the folder with the tarball, write nothing")
    args = ap.parse_args(argv)
    files, facts = build(args.tarball)
    print(f"monaco-editor {facts['version']}  {facts['integrity']}")
    print(f"tarball sha256 {facts['tarball_sha256']}")
    print(f"{facts['files']} files, {facts['bytes']:,} bytes; left out {len(facts['left_out'])} files")
    if facts["bytes"] > MAX_BYTES:
        print(f"too big: {facts['bytes']:,} bytes is over the {MAX_BYTES:,} limit")
        return 1
    if args.check:
        have = on_disk()
        wrong = sorted(p for p in set(files) | set(have) if files.get(p) != have.get(p))
        for p in wrong[:20]:
            print(("missing: " if p not in have else "extra: " if p not in files else "differs: ") + p)
        if wrong:
            print(f"{len(wrong)} files differ (run: python3 tools/vendor_monaco.py {args.tarball})")
            return 1
        print("monaco vendor check passed")
        return 0
    if OUT.exists():
        shutil.rmtree(OUT)
    for rel, data in files.items():
        path = OUT / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    print(f"wrote {OUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
