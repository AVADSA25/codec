"""The buyer app's macOS floor (D-34): one number in Info.plist, the same everywhere, and a checker
that finds files needing a newer macOS (packaging/macos/verify/macho_minos.py)."""
from __future__ import annotations

import importlib.util
import plistlib
import re
import struct
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "packaging" / "macos"

_spec = importlib.util.spec_from_file_location("macho_minos", PKG / "verify" / "macho_minos.py")
mm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mm)


def _floor() -> str:
    return plistlib.loads((PKG / "Info.plist").read_bytes())["LSMinimumSystemVersion"]


# ── the floor is written the same everywhere ──────────────────────────────────


def test_floor_is_14_and_info_plist_is_the_source():
    assert _floor() == "14.0"  # mlx publishes no wheel below macOS 14


def test_readme_docs_and_lock_state_the_same_floor():
    major = _floor().split(".")[0]
    readme = (REPO / "README.md").read_text()
    req = re.search(r"^- macOS (\d+)", readme, re.M)
    assert req and req.group(1) == major, "README requirements line must name the floor"
    assert re.search(rf"Minimum macOS: {major} ", (REPO / "docs" / "APPLE-DISTRIBUTION.md").read_text())
    assert re.search(rf"macOS {major}\+", (PKG / "requirements-lock.txt").read_text().split("\n\n")[0])


def test_build_scripts_use_the_floor_not_the_build_macs_os():
    build = (PKG / "build_app.sh").read_text()
    assert "LSMinimumSystemVersion" in build and "-target" in build
    assert not re.search(r"apple-macos1[0-3]\.", build), "no hard-coded old target"
    assert "macho_minos.py" in build, "the bundle must be checked before signing"
    bundle = (PKG / "bundle_python.sh").read_text()
    assert '--platform "macosx_${FLOOR_MAJOR}_0_${PIP_ARCH}"' in bundle and "--target" in bundle
    assert "--require-hashes" in bundle and "--only-binary=:all:" in bundle and "--no-deps" in bundle


# ── the Mach-O reader ─────────────────────────────────────────────────────────


def _thin(minos: tuple[int, int, int], kind="build", platform=1, ncmds_extra=()):
    """A minimal 64-bit arm64 Mach-O header with one version command."""
    packed = (minos[0] << 16) | (minos[1] << 8) | minos[2]
    if kind == "build":
        cmd = struct.pack("<IIIIII", mm.LC_BUILD_VERSION, 24, platform, packed, packed, 0)
    else:
        cmd = struct.pack("<IIII", mm.LC_VERSION_MIN_MACOSX, 16, packed, packed)
    cmds = b"".join(ncmds_extra) + cmd
    header = struct.pack("<IiiIIIII", mm.MH_MAGIC_64, 0x0100000C, 0, 6, len(ncmds_extra) + 1, len(cmds), 0, 0)
    return header + cmds


def _fat(*thin_images: bytes):
    out = struct.pack(">II", mm.FAT_MAGIC, len(thin_images))
    offset = 4096
    body = b""
    for img in thin_images:
        out += struct.pack(">iiIII", 0x0100000C, 0, offset + len(body), len(img), 12)
        body += img + b"\0" * (4096 - len(img) % 4096)
    return out.ljust(offset, b"\0") + body


def test_reads_build_version_and_old_version_min(tmp_path):
    (tmp_path / "a.dylib").write_bytes(_thin((26, 2, 0)))
    (tmp_path / "b.so").write_bytes(_thin((10, 9, 0), kind="min"))
    assert mm.macho_minos(tmp_path / "a.dylib") == (26, 2, 0)
    assert mm.macho_minos(tmp_path / "b.so") == (10, 9, 0)


def test_universal_file_reports_its_highest_image(tmp_path):
    (tmp_path / "u").write_bytes(_fat(_thin((11, 0, 0)), _thin((14, 0, 0)), _thin((26, 0, 0))))
    assert mm.macho_minos(tmp_path / "u") == (26, 0, 0)


def test_ignores_other_platforms_text_and_java_class_files(tmp_path):
    (tmp_path / "ios").write_bytes(_thin((17, 0, 0), platform=2))
    (tmp_path / "x.py").write_text("print('hi')\n")
    (tmp_path / "Foo.class").write_bytes(b"\xca\xfe\xba\xbe\x00\x00\x00\x34" + b"\0" * 64)
    assert mm.macho_minos(tmp_path / "ios") == ()
    assert mm.macho_minos(tmp_path / "x.py") is None
    assert mm.macho_minos(tmp_path / "Foo.class") is None


def test_checker_passes_at_the_floor_and_fails_above_it(tmp_path, capsys):
    (tmp_path / "ok.so").write_bytes(_thin((14, 0, 0)))
    (tmp_path / "old.dylib").write_bytes(_thin((11, 0, 0)))
    assert mm.main(["--app", str(tmp_path), "--floor", "14.0"]) == 0
    assert "MACHO-MINOS-PASS (2 Mach-O files, highest minos 14.0" in capsys.readouterr().out
    (tmp_path / "libmlx.dylib").write_bytes(_thin((26, 2, 0)))
    assert mm.main(["--app", str(tmp_path), "--floor", "14.0"]) == 1
    err = capsys.readouterr().err
    assert "1 of 3 Mach-O files" in err and "26.2  libmlx.dylib" in err


def test_checker_uses_info_plist_when_no_floor_given(tmp_path, capsys):
    (tmp_path / "ok.so").write_bytes(_thin((14, 0, 0)))
    assert mm.main(["--app", str(tmp_path)]) == 0
    (tmp_path / "new.so").write_bytes(_thin((15, 0, 0)))
    assert mm.main(["--app", str(tmp_path)]) == 1


def test_checker_fails_on_an_empty_folder_and_a_missing_one(tmp_path):
    assert mm.main(["--app", str(tmp_path)]) == 1  # nothing to check is not a pass
    assert mm.main(["--app", str(tmp_path / "nope")]) == 1


@pytest.mark.skipif(sys.platform != "darwin", reason="needs a real Mach-O file")
def test_reads_a_real_system_binary():
    v = mm.macho_minos(Path("/usr/bin/true"))
    assert v is not None  # a Mach-O file; the version itself depends on the Mac
