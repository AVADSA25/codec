#!/usr/bin/env python3
"""Model-pack downloader for the Sovereign AI Workstation (W5-5, E-8).

Fetches the ML models declared in packaging/macos/models.json into
~/.codec/models/ (never inside the .app). Tiered: `bundled` models work offline
on first launch; `on_demand` models download with explicit consent + a size
warning on first use.

The planning/consent core is pure stdlib and unit-tested. The actual download
lazy-imports `huggingface_hub` and uses `snapshot_download`, which gives
resumable + commit/etag-verified transfers for free.

See docs/W5-5-MODEL-FETCH-DESIGN.md. Closes E-8 (mechanism).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

DEFAULT_MANIFEST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models.json")
DEFAULT_DEST = "~/.codec/models"


def load_manifest(path: str = DEFAULT_MANIFEST) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    models = data.get("models", data) if isinstance(data, dict) else data
    if not isinstance(models, list):
        raise ValueError("models.json must contain a 'models' list")
    return models


def select(models: list[dict], tier: str) -> list[dict]:
    if tier == "all":
        return list(models)
    if tier not in ("bundled", "on_demand"):
        raise ValueError(f"unknown tier: {tier}")
    return [m for m in models if m.get("tier") == tier]


def total_gb(models: list[dict]) -> float:
    return round(sum(float(m.get("approx_gb", 0)) for m in models), 1)


def model_dest(dest_root: str, m: dict) -> str:
    return os.path.join(os.path.expanduser(dest_root), m["name"])


def consent_text(models: list[dict], dest_root: str) -> str:
    lines = [f"The following {len(models)} model(s) will be downloaded to {dest_root}:"]
    for m in models:
        lines.append(f"  - {m['name']:<16} {m['kind']:<7} {m['repo']}@{m.get('revision','main')}  (~{m['approx_gb']} GB)")
    lines.append(f"Total: ~{total_gb(models)} GB")
    return "\n".join(lines)


def download(m: dict, dest_root: str) -> str:
    """Download one model via huggingface_hub (lazy import). Resumable + verified."""
    try:
        from huggingface_hub import snapshot_download
    except ImportError as e:  # pragma: no cover - exercised only on real runs
        raise SystemExit(
            "huggingface_hub is required to download models. Install it (it ships "
            "with mlx-lm) or `pip install huggingface_hub`."
        ) from e
    dest = model_dest(dest_root, m)
    os.makedirs(dest, exist_ok=True)
    snapshot_download(repo_id=m["repo"], revision=m.get("revision", "main"), local_dir=dest)
    return dest


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Download CODEC model packs.")
    ap.add_argument("--tier", default="bundled", choices=["bundled", "on_demand", "all"])
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST)
    ap.add_argument("--dest", default=None, help="install root (default: manifest dest_default or ~/.codec/models)")
    ap.add_argument("--dry-run", action="store_true", help="print the plan; download nothing")
    ap.add_argument("--yes", action="store_true", help="consent to download (required for a real run)")
    args = ap.parse_args(argv)

    with open(args.manifest, encoding="utf-8") as fh:
        raw = json.load(fh)
    dest_root = args.dest or (raw.get("dest_default") if isinstance(raw, dict) else None) or DEFAULT_DEST
    models = select(load_manifest(args.manifest), args.tier)

    if not models:
        print(f"no models in tier '{args.tier}'.")
        return 0

    print(consent_text(models, dest_root))

    if args.dry_run:
        print("[dry-run] nothing downloaded.")
        return 0
    if not args.yes:
        print("\nRefusing to download without consent. Re-run with --yes (or --dry-run to preview).")
        return 1

    for m in models:
        print(f"==> downloading {m['name']} ({m['repo']}) ...")
        dest = download(m, dest_root)
        print(f"    done: {dest}")
    print(f"==> {len(models)} model(s) ready under {os.path.expanduser(dest_root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
