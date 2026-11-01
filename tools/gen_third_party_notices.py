#!/usr/bin/env python3
"""Make THIRD-PARTY-NOTICES.md for the buyer build (P-056, docs/RELEASE-NOTICES-DESIGN.md).

The Python part comes from the exact wheels the app installs: every package in
packaging/macos/requirements-lock.txt, its version, its licence (read from the wheel's
METADATA) and the licence files the wheel ships. Models come from
packaging/macos/model_licences.json (read from each model card at the pinned revision);
vendored web assets from static/vendor/SOURCES.md and the licence files beside them.

  # get the wheels (hash-checked, nothing installed):
  python3 -m pip download --require-hashes --only-binary=:all: --no-deps \\
      --python-version 3.12 --implementation cp --abi cp312 --abi abi3 --abi none \\
      --platform macosx_14_0_arm64 --platform macosx_11_0_arm64 --platform macosx_11_0_universal2 \\
      --platform macosx_10_13_universal2 --platform any -d WHEELS -r packaging/macos/requirements-lock.txt
  python3 tools/gen_third_party_notices.py --wheels WHEELS            # writes THIRD-PARTY-NOTICES.md
  python3 tools/gen_third_party_notices.py --wheels WHEELS --check    # exit 1 if the file is stale

The output has no date and a fixed order, so the same inputs always give the same bytes.
A package whose licence cannot be read, or that ships no licence file and has no entry in
SAME_TEXT_AS, stops the run: add the entry by hand after reading the project.
"""
from __future__ import annotations

import argparse
import difflib
import email
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOCK = REPO / "packaging" / "macos" / "requirements-lock.txt"
MODELS = REPO / "packaging" / "macos" / "models.json"
MODEL_LICENCES = REPO / "packaging" / "macos" / "model_licences.json"
RUNTIME = REPO / "packaging" / "macos" / "python-runtime.json"
SOURCES = REPO / "static" / "vendor" / "SOURCES.md"
GPL3 = REPO / "packaging" / "macos" / "licences" / "GPL-3.0.txt"
OUT = REPO / "THIRD-PARTY-NOTICES.md"

# The licence words packages put in METADATA, as one short SPDX-style name each.
CLASSIFIERS = {
    "MIT License": "MIT", "BSD License": "BSD", "Apache Software License": "Apache-2.0",
    "ISC License (ISCL)": "ISC", "Python Software Foundation License": "PSF-2.0",
    "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0", "The Unlicense (Unlicense)": "Unlicense",
    "GNU Lesser General Public License v3 (LGPLv3)": "LGPL-3.0",
}
SYNONYMS = {"Apache 2.0": "Apache-2.0", "Apache 2.0 License": "Apache-2.0", "LGPLv3": "LGPL-3.0",
            "3-Clause BSD License": "BSD-3-Clause", "BSD 3-Clause License": "BSD-3-Clause",
            "ISC License": "ISC", "MIT License": "MIT"}
# Set by hand where METADATA holds the licence text, or a vague word, instead of a name.
# Each entry says what was read.
OVERRIDES = {
    "tiktoken": ("MIT", "METADATA holds the whole licence text; the shipped LICENSE file is the MIT licence"),
    "scipy": ("BSD-3-Clause", "METADATA holds a copyright line; the shipped LICENSE.txt is BSD-3-Clause plus bundled-library notices"),
    "python-dateutil": ("BSD-3-Clause OR Apache-2.0", "METADATA says 'Dual License'; the shipped LICENSE says BSD-3-Clause and Apache-2.0"),
    "qrcode": ("BSD-3-Clause", "METADATA says BSD and 'Other/Proprietary'; the shipped LICENSE is the BSD-3-Clause text"),
}
# Packages whose wheel ships no licence file: the text of the same project family, or the
# standard text of the licence METADATA names. The copyright line is then not the project's own.
SAME_TEXT_AS = {
    "fastmcp-slim": ("fastmcp", "same project; its wheel ships no licence file"),
    "pyobjc-core": ("pyobjc-framework-cocoa", "same project (PyObjC); its wheel ships no licence file"),
    "pyobjc-framework-applicationservices": ("pyobjc-framework-cocoa", "same project (PyObjC); its wheel ships no licence file"),
    "pyobjc-framework-avfoundation": ("pyobjc-framework-cocoa", "same project (PyObjC); its wheel ships no licence file"),
    "pyobjc-framework-coreaudio": ("pyobjc-framework-cocoa", "same project (PyObjC); its wheel ships no licence file"),
    "pyobjc-framework-coremedia": ("pyobjc-framework-cocoa", "same project (PyObjC); its wheel ships no licence file"),
    "sentencepiece": ("fastmcp", "standard Apache-2.0 text; its wheel ships no licence file"),
    "tokenizers": ("fastmcp", "standard Apache-2.0 text; its wheel ships no licence file"),
    "mlx-whisper": ("mlx", "MIT text of the mlx project from the same ml-explore organisation; its wheel ships no licence file"),
    "dnspython": ("@ISC", "standard ISC text; its wheel ships no licence file"),
}
ISC_TEXT = """ISC License

Permission to use, copy, modify, and/or distribute this software for any purpose with or
without fee is hereby granted, provided that the above copyright notice and this permission
notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH REGARD TO THIS
SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL
THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT,
NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE
OF THIS SOFTWARE."""

LICENCE_FILE = re.compile(r"^(LICEN[CS]E|COPYING|NOTICE|COPYRIGHT)([-_.].*)?$", re.I)
SKIP_EXT = (".so", ".dylib", ".yml", ".pyc", ".h", ".c", ".cpp", ".py", ".pyi", ".typed", ".json")
FENCE = "~" * 8


def canon(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def read_lock() -> list[tuple[str, str]]:
    rows = []
    for line in LOCK.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9_.\-]*)==([^\s\\]+)", line)
        if m:
            rows.append((m.group(1), m.group(2)))
    return rows


def find_wheels(wheel_dir: Path, lock: list[tuple[str, str]]) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for p in wheel_dir.glob("*.whl"):
        parts = p.name[:-4].split("-")
        found[canon(parts[0]) + "==" + parts[1]] = p
    out = {}
    for name, ver in lock:
        key = canon(name) + "==" + ver
        if key not in found:
            sys.exit(f"missing wheel for {name}=={ver} in {wheel_dir}")
        out[canon(name)] = found[key]
    return out


def _text(b: bytes) -> str | None:
    if b"\0" in b:
        return None
    return b.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n").strip("\n")


def licence_files(z: zipfile.ZipFile) -> list[tuple[str, str]]:
    out = []
    for n in sorted(z.namelist()):
        if n.endswith("/") or ".dSYM/" in n or n.lower().endswith(SKIP_EXT):
            continue
        base = n.rsplit("/", 1)[-1]
        inside_licensing = "/licensing/" in "/" + n
        if not (LICENCE_FILE.match(base) or inside_licensing):
            continue
        t = _text(z.read(n))
        if t:
            out.append((n, t))
    return out


def resolve_licence(name: str, meta) -> tuple[str, str]:
    """-> (licence, note). note is '' unless set by hand."""
    if canon(name) in OVERRIDES:
        lic, why = OVERRIDES[canon(name)]
        return lic, why
    expr = (meta.get("License-Expression") or "").strip()
    if expr:
        return expr, ""
    field = (meta.get("License") or "").strip()
    if field and "\n" not in field and len(field) <= 60:
        return SYNONYMS.get(field, field), ""
    classes = [CLASSIFIERS.get(c.split("::")[-1].strip(), c.split("::")[-1].strip())
               for c in meta.get_all("Classifier", []) if c.startswith("License")]
    if classes:
        return " / ".join(dict.fromkeys(classes)), ""
    sys.exit(f"cannot read a licence for {name}: add it to OVERRIDES after reading the project")


def project_url(meta) -> str:
    urls = {}
    for v in meta.get_all("Project-URL", []):
        k, _, u = v.partition(",")
        urls[k.strip().lower()] = u.strip()
    for k in ("source", "repository", "source code", "homepage", "home"):
        if urls.get(k):
            return urls[k]
    return (meta.get("Home-page") or next(iter(urls.values()), "")).strip()


def read_packages(wheels: dict[str, Path]) -> list[dict]:
    pkgs = []
    for key in sorted(wheels):
        z = zipfile.ZipFile(wheels[key])
        meta_name = next(n for n in z.namelist() if n.endswith(".dist-info/METADATA"))
        meta = email.message_from_bytes(z.read(meta_name))
        lic, note = resolve_licence(meta["Name"], meta)
        pkgs.append({"key": key, "name": meta["Name"], "version": meta["Version"], "licence": lic,
                     "note": note, "url": project_url(meta), "files": licence_files(z), "same_as": None})
    return pkgs


class Texts:
    """Licence texts, each stored once under an id (T001...), in order of first use."""

    def __init__(self):
        self.by_hash: dict[str, str] = {}
        self.items: list[dict] = []

    def add(self, text: str, used_by: str, label: str) -> str:
        h = hashlib.sha256(text.encode()).hexdigest()
        if h not in self.by_hash:
            tid = f"T{len(self.items) + 1:03d}"
            self.by_hash[h] = tid
            self.items.append({"id": tid, "text": text, "used_by": [], "labels": []})
        item = self.items[int(self.by_hash[h][1:]) - 1]
        if used_by not in item["used_by"]:
            item["used_by"].append(used_by)
        if label not in item["labels"]:
            item["labels"].append(label)
        return self.by_hash[h]


def web_assets() -> list[dict]:
    """Group the rows of static/vendor/SOURCES.md by package; add Monaco."""
    groups: dict[str, dict] = {}
    for line in SOURCES.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\| `([^`]+)` \| (.+?) \| (.+?) \| `[0-9a-f]{64}` \|$", line)
        if not m:
            continue
        pkg = re.sub(r" \(`[^)]*`.*?\)$", "", m.group(2)).strip()
        g = groups.setdefault(pkg, {"package": pkg, "licence": m.group(3).strip(), "files": 0})
        g["files"] += 1
    mon = re.search(r"\| Package \| `monaco-editor` ([0-9.]+) .*?, (\w[\w-]*), Microsoft \|", SOURCES.read_text(encoding="utf-8"))
    if not mon:
        sys.exit("SOURCES.md: no Monaco package row")
    groups["monaco-editor " + mon.group(1)] = {"package": "monaco-editor " + mon.group(1), "licence": mon.group(2), "files": 115}
    return list(groups.values())


WEB_TEXTS = {  # package prefix -> (licence file in the repo, used licence)
    "marked": "static/vendor/LICENSE-marked.txt",
    "dompurify": "static/vendor/LICENSE-dompurify.txt",
    "@highlightjs/cdn-assets": "static/vendor/LICENSE-highlightjs.txt",
    "@ibm/plex-sans": "static/fonts/LICENSE-IBM-Plex.txt",
    "@ibm/plex-mono": "static/fonts/LICENSE-IBM-Plex.txt",
    "katex": "static/vendor/katex/LICENSE.txt",
    "mermaid": "static/vendor/LICENSE-mermaid.txt",
    "monaco-editor": "static/vendor/monaco/LICENSE",
}


def _render(lock, wheels_dir: Path | None) -> str:
    wheels = find_wheels(wheels_dir, lock)
    pkgs = read_packages(wheels)
    by_key = {p["key"]: p for p in pkgs}
    texts = Texts()
    std = {"@ISC": ISC_TEXT}
    for p in pkgs:
        p["ids"] = list(dict.fromkeys(texts.add(t, p["name"], n.rsplit("/", 1)[-1]) for n, t in p["files"]))
    for p in pkgs:
        if p["ids"]:
            continue
        src_name, why = SAME_TEXT_AS.get(p["key"], (None, None))
        if not src_name:
            sys.exit(f"{p['name']} ships no licence file: add it to SAME_TEXT_AS after reading the project")
        p["note"] = (p["note"] + "; " if p["note"] else "") + why
        if src_name in std:
            p["ids"] = [texts.add(std[src_name], p["name"], "standard text")]
        else:
            src = by_key[canon(src_name)]
            p["ids"] = list(dict.fromkeys(texts.add(t, p["name"], n.rsplit("/", 1)[-1]) for n, t in src["files"]))
        p["same_as"] = src_name
    # GPL-3: pynput's LGPL-3 text is "supplemented by" it (LGPL-3.0 section 0).
    gpl_id = texts.add(_text(GPL3.read_bytes()), "pynput (LGPL-3.0 is supplemented by the GPL-3.0)", "GPL-3.0")
    web = web_assets()
    for w in web:
        for prefix, rel in WEB_TEXTS.items():
            if w["package"].startswith(prefix + " "):
                w["tid"] = texts.add(_text((REPO / rel).read_bytes()), w["package"], rel.rsplit("/", 1)[-1])
                w["file"] = rel
        if "tid" not in w:
            sys.exit(f"no licence file for web asset {w['package']}")
    models = json.loads(MODEL_LICENCES.read_text(encoding="utf-8"))["models"]
    pins = {m["name"]: m for m in json.loads(MODELS.read_text(encoding="utf-8"))["models"]}
    runtime = json.loads(RUNTIME.read_text(encoding="utf-8"))
    lock_hash = hashlib.sha256(LOCK.read_bytes()).hexdigest()[:16]
    for t in texts.items:
        if FENCE in t["text"]:
            sys.exit(f"{t['id']} contains the fence line {FENCE}")

    o: list[str] = []
    w_ = o.append
    w_("# Third-party notices")
    w_("")
    w_("Sovereign AI Workstation (CODEC) contains and uses the open-source software listed here. "
       "Each project keeps its own copyright and licence. The full licence texts are in the last section. "
       "This file is made by `tools/gen_third_party_notices.py` from the packages the app installs "
       f"(`packaging/macos/requirements-lock.txt`, lock sha256 `{lock_hash}`), so it lists exactly those and no others.")
    w_("")
    w_("## Libraries under the LGPL")
    w_("")
    w_("These libraries are under the GNU Lesser General Public License. Each one is a separate, replaceable file "
       "inside the app, not linked into a single program: you can replace it with your own build of the same "
       "library and the app still starts. The source code of each library is at the address in the package table.")
    w_("")
    lgpl = [p for p in pkgs if "LGPL" in p["licence"]]
    paths = {"pynput": "Contents/Resources/python/lib/python3.12/site-packages/pynput/ (Python source files)",
             "zeroconf": "Contents/Resources/python/lib/python3.12/site-packages/zeroconf/ (Python files and compiled modules built from the project's Cython source)",
             "soundfile": "Contents/Resources/python/lib/python3.12/site-packages/_soundfile_data/libsndfile_arm64.dylib "
                          "(libsndfile, LGPL-2.1, with its own text in `_soundfile_data/COPYING`; the Python wrapper `soundfile` is BSD-3-Clause). "
                          "That one file also contains libmpg123 (LGPL-2.1), libmp3lame (LGPL-2+), libFLAC, libogg, libopus and libvorbis (BSD-3-Clause); "
                          "their copyrights and source addresses are in the soundfile licence notes below"}
    for p in lgpl:
        w_(f"- **{p['name']} {p['version']}** ({p['licence']}): {paths.get(canon(p['name']), 'site-packages')}.")
    if not any(canon(p["name"]) == "soundfile" for p in lgpl):
        sf = by_key["soundfile"]
        w_(f"- **libsndfile** inside **soundfile {sf['version']}** (LGPL-2.1): {paths['soundfile']}.")
    sp = by_key.get("scipy")
    if sp:
        w_(f"- **libquadmath** and the GCC runtime libraries inside **scipy {sp['version']}** (LGPL-2.1-or-later for libquadmath; "
           "GPL-3.0-or-later with the GCC Runtime Library Exception 3.1 for libgfortran and libgcc): "
           "Contents/Resources/python/lib/python3.12/site-packages/scipy/.dylibs/ (separate files; the scipy licence text below names them and the exception).")
    w_("")
    w_("The LGPL-3.0 text of pynput refers to the GNU GPL-3.0 for its terms; that text is included below "
       f"({gpl_id}). Nothing in the app is under the GPL itself.")
    w_("")
    w_("## Notes")
    w_("")
    w_("- Licence names in the package table are the ones each wheel declares in its metadata, written as "
       "one short name. Where the metadata holds only the licence text or a vague word, the name was read "
       "from the shipped licence file and the table says so in its note.")
    w_("- `scipy` ships the GCC runtime libraries (libgfortran, libgcc, libquadmath) as separate files; its licence text carries "
       "their notice and the GCC Runtime Library Exception. The `numpy` wheel carries the same notice text but contains no such "
       "library files in this build. Both packages are BSD-licensed.")
    w_("- `pillow` ships FreeType, which is offered under the FreeType Licence or the GPL-2.0; the app uses it under the FreeType Licence. "
       "Portions of this software are copyright The FreeType Project (www.freetype.org). All rights reserved.")
    w_("- `certifi` is MPL-2.0 (file-level copyleft): it is used unmodified, and its source is at the address in the table.")
    w_("- `python-dateutil` is offered under BSD-3-Clause or Apache-2.0; `cryptography` under Apache-2.0 or BSD-3-Clause; "
       "`packaging` under Apache-2.0 or BSD-2-Clause. DOMPurify is offered under MPL-2.0 or Apache-2.0; the app uses it under Apache-2.0.")
    w_("- The app downloads its models on first run to `~/.codec/models`; they are not inside the app. Each model's "
       "own licence file comes with its download.")
    w_("")
    w_(f"## Python packages ({len(pkgs)})")
    w_("")
    w_("| Package | Version | Licence | Texts | Source |")
    w_("|---|---|---|---|---|")
    for p in pkgs:
        mark = "*" if p["same_as"] else ""
        w_(f"| {p['name']} | {p['version']} | {p['licence']} | {', '.join(p['ids'])}{mark} | {p['url']} |")
    w_("")
    marked = [p for p in pkgs if p["note"]]
    if marked:
        w_("Notes on single packages (`*` marks a text taken from a related project or the standard text, because the wheel ships no licence file; "
           "the copyright line in it is then not the package's own):")
        w_("")
        for p in marked:
            w_(f"- {p['name']}: {p['note']}.")
        w_("")
    w_("## Models")
    w_("")
    w_("Licence as stated on the model card at the revision the app downloads (`packaging/macos/models.json`).")
    w_("")
    w_("| Model | Repository | Revision | Licence | Card |")
    w_("|---|---|---|---|---|")
    for name, pin in pins.items():
        m = models.get(name)
        if not m or m["revision"] != pin["revision"]:
            sys.exit(f"model {name}: packaging/macos/model_licences.json is missing or is not for the pinned revision; read the card again")
        w_(f"| {name} | {pin['repo']} | {pin['revision'][:12]} | {m['licence']} | https://huggingface.co/{pin['repo']}/blob/{pin['revision']}/README.md |")
    w_("")
    for name, m in models.items():
        if m.get("note"):
            w_(f"- {name}: {m['note']}")
    w_("")
    w_("## Web assets served by the dashboard")
    w_("")
    w_("Copies kept in `static/` (provenance and file hashes: `static/vendor/SOURCES.md`).")
    w_("")
    w_("| Package | Licence | Files | Text | Licence file |")
    w_("|---|---|---|---|---|")
    for g in web:
        w_(f"| {g['package']} | {g['licence']} | {g['files']} | {g['tid']} | `{g['file']}` |")
    w_("")
    w_("Monaco also ships `static/vendor/monaco/ThirdPartyNotices.txt` with the notices of the projects inside it.")
    w_("")
    w_("## Bundled runtime")
    w_("")
    w_(f"- CPython {runtime['python_version']} (PSF-2.0) from python-build-standalone release {runtime['pbs_release']} "
       "(https://github.com/astral-sh/python-build-standalone). Its licence texts, with those of the libraries built into it, "
       "are in the app at `Contents/Resources/python/lib/python3.12/LICENSE.txt`.")
    w_("")
    w_("## Licence texts")
    w_("")
    w_("Each text appears once; a package's row above names the text it uses.")
    w_("")
    for t in texts.items:
        w_(f"### {t['id']}")
        w_("")
        w_("Used by: " + ", ".join(t["used_by"]) + f" (file: {', '.join(t['labels'])})")
        w_("")
        w_(FENCE)
        w_(t["text"])
        w_(FENCE)
        w_("")
    return "\n".join(o).rstrip("\n") + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--wheels", type=Path, required=True, help="folder with the locked wheels")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--check", action="store_true", help="exit 1 if the file differs from what would be made")
    a = ap.parse_args()
    new = _render(read_lock(), a.wheels)
    if a.check:
        old = a.out.read_text(encoding="utf-8") if a.out.exists() else ""
        if old == new:
            print("NOTICES-UP-TO-DATE")
            return 0
        diff = list(difflib.unified_diff(old.splitlines(), new.splitlines(), "committed", "generated", lineterm="", n=0))
        print("\n".join(diff[:40]))
        print(f"STALE: {len(diff)} diff lines; run without --check to rewrite {a.out.name}")
        return 1
    a.out.write_text(new, encoding="utf-8")
    print(f"wrote {a.out} ({len(new):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
