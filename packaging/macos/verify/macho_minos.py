#!/usr/bin/env python3
"""Fail when any Mach-O file under a folder needs a newer macOS than the app's floor (D-34, P-055).

Every compiled file (.so, .dylib, executables) records the oldest macOS it runs on, its
`minos`. A wheel built for macOS 26 carries minos 26.x and cannot load on macOS 14, which is
how the first buyer bundle ended up needing macOS 26.2 while Info.plist promised 13. This
walks a folder, reads the version out of every Mach-O header (thin or universal), and exits 1
if any is above the floor. No `otool` needed: it reads the file headers itself.

    macho_minos.py --app "dist/Sovereign AI Workstation.app"
    macho_minos.py --app <venv site-packages> --floor 14.0

The floor defaults to LSMinimumSystemVersion in packaging/macos/Info.plist, so the app, the
README and this check cannot drift apart (tests/test_macos_floor.py pins the three).
Prints MACHO-MINOS-PASS on success.
"""
from __future__ import annotations

import argparse
import os
import plistlib
import struct
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]

MH_MAGIC, MH_MAGIC_64 = 0xFEEDFACE, 0xFEEDFACF          # thin, little-endian on disk
FAT_MAGIC, FAT_MAGIC_64 = 0xCAFEBABE, 0xCAFEBABF        # universal, big-endian on disk
LC_VERSION_MIN_MACOSX, LC_BUILD_VERSION = 0x24, 0x32
PLATFORM_MACOS = 1
MAX_ARCHS = 8  # a Java class file also starts with CAFEBABE; its "arch count" is a version number


def parse_version(text: str) -> tuple[int, int, int]:
    parts = [int(p) for p in text.strip().split(".")] + [0, 0]
    return parts[0], parts[1], parts[2]


def decode(packed: int) -> tuple[int, int, int]:
    return packed >> 16, (packed >> 8) & 0xFF, packed & 0xFF


def _thin_minos(f, offset: int):
    """The macOS minimum of one thin image starting at `offset`, or None if it records none."""
    f.seek(offset)
    head = f.read(32)
    if len(head) < 28:
        return None
    magic = struct.unpack("<I", head[:4])[0]
    if magic not in (MH_MAGIC, MH_MAGIC_64):
        return None
    ncmds, sizeofcmds = struct.unpack("<II", head[16:24])
    f.seek(offset + (32 if magic == MH_MAGIC_64 else 28))
    cmds = f.read(min(sizeofcmds, 4 * 1024 * 1024))
    pos, found = 0, None
    for _ in range(ncmds):
        if pos + 8 > len(cmds):
            break
        cmd, size = struct.unpack("<II", cmds[pos:pos + 8])
        if size < 8:
            break
        if cmd == LC_BUILD_VERSION and pos + 16 <= len(cmds):
            platform, minos = struct.unpack("<II", cmds[pos + 8:pos + 16])
            if platform == PLATFORM_MACOS:
                found = decode(minos) if found is None else min(found, decode(minos))
        elif cmd == LC_VERSION_MIN_MACOSX and pos + 12 <= len(cmds):
            found = decode(struct.unpack("<I", cmds[pos + 8:pos + 12])[0]) if found is None else found
        pos += size
    return found


def macho_minos(path: Path):
    """None if `path` is not a Mach-O file; else the highest macOS minimum over its images,
    as (major, minor, patch), or () when no image records one."""
    try:
        with open(path, "rb") as f:
            first = f.read(4)
            if len(first) < 4:
                return None
            be = struct.unpack(">I", first)[0]
            le = struct.unpack("<I", first)[0]
            if le in (MH_MAGIC, MH_MAGIC_64):
                offsets = [0]
            elif be in (FAT_MAGIC, FAT_MAGIC_64):
                n = struct.unpack(">I", f.read(4))[0]
                if not 1 <= n <= MAX_ARCHS:
                    return None
                wide = be == FAT_MAGIC_64
                size = 32 if wide else 20
                offsets = []
                for _ in range(n):
                    entry = f.read(size)
                    if len(entry) < size:
                        return None
                    offsets.append(struct.unpack(">Q", entry[8:16])[0] if wide else struct.unpack(">I", entry[8:12])[0])
            else:
                return None
            versions = [v for v in (_thin_minos(f, o) for o in offsets) if v]
            return max(versions) if versions else ()
    except OSError:
        return None


def scan(root: Path):
    """Yield (path, minos) for every Mach-O file under `root`; symlinks are not followed."""
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            p = Path(dirpath) / name
            if p.is_symlink() or not p.is_file():
                continue
            v = macho_minos(p)
            if v is not None:
                yield p, v


def floor_from_info_plist() -> str:
    with open(PKG / "Info.plist", "rb") as f:
        return plistlib.load(f)["LSMinimumSystemVersion"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--app", required=True, help="the .app, or any folder, to walk")
    ap.add_argument("--floor", help="highest macOS minimum allowed (default: Info.plist LSMinimumSystemVersion)")
    args = ap.parse_args(argv)

    root = Path(args.app)
    if not root.is_dir():
        print(f"MACHO-MINOS-FAIL: not a folder: {root}", file=sys.stderr)
        return 1
    floor_text = args.floor or floor_from_info_plist()
    floor = parse_version(floor_text)

    total, highest, too_new = 0, (), []
    for path, v in scan(root):
        total += 1
        if v:
            highest = max(highest, v)
            if v > floor:
                too_new.append((path, v))
    if total == 0:
        print(f"MACHO-MINOS-FAIL: no Mach-O file under {root}", file=sys.stderr)
        return 1
    if too_new:
        print(f"MACHO-MINOS-FAIL: {len(too_new)} of {total} Mach-O files need macOS above {floor_text}:", file=sys.stderr)
        for path, v in sorted(too_new, key=lambda t: (-t[1][0], -t[1][1], str(t[0])))[:40]:
            print(f"  {'.'.join(map(str, v[:2]))}  {path.relative_to(root)}", file=sys.stderr)
        if len(too_new) > 40:
            print(f"  ... and {len(too_new) - 40} more", file=sys.stderr)
        return 1
    top = ".".join(map(str, highest[:2])) if highest else "none recorded"
    print(f"MACHO-MINOS-PASS ({total} Mach-O files, highest minos {top}, floor {floor_text})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
