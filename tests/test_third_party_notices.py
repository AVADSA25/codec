"""P-056: THIRD-PARTY-NOTICES.md covers what the buyer app contains.

Stdlib only, reads the committed file next to the lock, models.json and SOURCES.md.
That the file equals what the generator makes from the locked wheels needs the wheels
(tools/gen_third_party_notices.py --wheels DIR --check); this checks the file's content.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TEXT = (REPO / "THIRD-PARTY-NOTICES.md").read_text(encoding="utf-8")
LOCK = (REPO / "packaging" / "macos" / "requirements-lock.txt").read_text(encoding="utf-8")


def _canon(n: str) -> str:
    return re.sub(r"[-_.]+", "-", n).lower()


def _section(title: str) -> str:
    m = re.search(rf"^## {re.escape(title)}.*?\n(.*?)(?=^## )", TEXT, re.M | re.S)
    assert m, f"no section {title!r}"
    return m.group(1)


def _rows(section: str) -> list[list[str]]:
    lines = [l for l in section.splitlines() if l.startswith("|") and not re.fullmatch(r"[|\-\s]+", l)]
    return [[c.strip() for c in l.strip().strip("|").split("|")] for l in lines[1:]]  # skip the header row


def _packages() -> list[list[str]]:
    return _rows(_section("Python packages"))


def test_every_locked_package_is_listed_with_its_version_and_a_licence():
    lock = {(_canon(m.group(1)), m.group(2)) for m in re.finditer(r"^([A-Za-z0-9][A-Za-z0-9_.\-]*)==([^\s\\]+)", LOCK, re.M)}
    listed = {(_canon(r[0]), r[1]) for r in _packages()}
    assert lock and listed == lock, f"missing {sorted(lock - listed)[:5]}, extra {sorted(listed - lock)[:5]}"
    assert all(r[2] for r in _packages()), "a package row has no licence"


def test_no_gpl_or_agpl_package_and_the_lgpl_ones_are_named():
    bad = [r[0] for r in _packages() if re.search(r"(?<!L)GPL", r[2])]
    assert not bad, f"GPL/AGPL licence in the package table: {bad}"
    lic = {_canon(r[0]): r[2] for r in _packages()}
    assert lic["pynput"] == "LGPL-3.0" and lic["zeroconf"].startswith("LGPL-2.1")
    lgpl = _section("Libraries under the LGPL")
    assert "libsndfile" in lgpl and "LGPL-2.1" in lgpl and "pynput" in lgpl and "zeroconf" in lgpl


def test_every_text_a_row_names_is_in_the_file_once():
    heads = re.findall(r"^### (T\d+)$", TEXT, re.M)
    assert len(heads) == len(set(heads))
    used = {t for r in _packages() for t in re.findall(r"T\d+", r[3])}
    assert used and used <= set(heads), f"rows name texts that are not in the file: {sorted(used - set(heads))[:5]}"


def test_every_model_has_a_licence_read_at_its_pinned_revision():
    pins = {m["name"]: m for m in json.loads((REPO / "packaging/macos/models.json").read_text())["models"]}
    cards = json.loads((REPO / "packaging/macos/model_licences.json").read_text())["models"]
    assert set(cards) == set(pins), "model_licences.json and models.json list different models"
    models = {r[0]: r for r in _rows(_section("Models"))}
    for name, pin in pins.items():
        assert cards[name]["revision"] == pin["revision"], f"{name}: the pin moved; read the new model card"
        assert name in models and pin["revision"][:12] in models[name][2]
        # The app is sold: a model with no licence read, or one that bars commercial use, cannot ship.
        assert not re.search(r"unverified|non-?commercial|research", cards[name]["licence"], re.I), name


def test_every_vendored_web_asset_is_covered():
    sources = (REPO / "static" / "vendor" / "SOURCES.md").read_text(encoding="utf-8")
    web = _section("Web assets served by the dashboard")
    pkgs = {m.group(1).strip() for m in re.finditer(r"^\| `[^`]+` \| ([^|(]+?)(?: \(`[^|]*)? \| [^|]+ \| `[0-9a-f]{64}` \|$", sources, re.M)}
    assert pkgs, "SOURCES.md rows not read"
    for p in pkgs:
        assert p.lower() in web.lower(), f"{p} is vendored but not in the notices"
    assert "monaco-editor" in web
    for r in _rows(web):
        assert (REPO / r[4].strip("`")).is_file(), f"licence file {r[4]} is missing"
