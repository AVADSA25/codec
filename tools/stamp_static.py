#!/usr/bin/env python3
"""Stamp /static links in the dashboard pages with a content version.

The dashboard pages are served no-cache, but /static files are cached for an
hour. Without a version in the URL, a browser can pair a freshly deployed page
with an hour-old stylesheet. Each link to a stamped file gets ?v=<first 8 hex
of the file's sha256>, so a changed file gets a new URL.

    python3 tools/stamp_static.py          # rewrite the pages
    python3 tools/stamp_static.py --check  # exit 1 if a page is stale (CI)
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STAMPED = ("codec.css", "codec-md.js")


def versions() -> dict:
    return {name: hashlib.sha256((REPO / "static" / name).read_bytes()).hexdigest()[:8]
            for name in STAMPED}


def stamp(text: str, vers: dict) -> str:
    for name, v in vers.items():
        text = re.sub(r'((?:href|src)="/static/' + re.escape(name) + r')(?:\?v=[0-9a-f]*)?"',
                      r'\g<1>?v=' + v + '"', text)
    return text


def main(check: bool) -> int:
    vers = versions()
    stale = []
    for page in sorted(REPO.glob("codec_*.html")):
        old = page.read_text()
        new = stamp(old, vers)
        if new != old:
            stale.append(page.name)
            if not check:
                page.write_text(new)
    if check and stale:
        print("stale /static versions in: " + ", ".join(stale) +
              " (run: python3 tools/stamp_static.py)")
        return 1
    print(("ok: " if check else "stamped: ") + ", ".join(f"{k}?v={v}" for k, v in vers.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main("--check" in sys.argv[1:]))
