"""Tests for codec_update — Sparkle-compatible update checker.

Verifies appcast parsing, semver comparison, and (critically) Ed25519 signature
verification using a real throwaway key — a tampered download must be rejected.
"""
import base64
import importlib

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


@pytest.fixture
def upd():
    import codec_update
    return importlib.reload(codec_update)


@pytest.fixture
def keypair():
    priv = Ed25519PrivateKey.generate()
    pub_b64 = base64.b64encode(priv.public_key().public_bytes_raw()).decode()
    return priv, pub_b64


def _appcast(version: str, url: str, sig: str, length: int) -> str:
    return f'''<?xml version="1.0"?>
<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle" version="2.0">
  <channel>
    <item>
      <title>Version {version}</title>
      <sparkle:shortVersionString>{version}</sparkle:shortVersionString>
      <sparkle:version>1</sparkle:version>
      <enclosure url="{url}" sparkle:edSignature="{sig}" length="{length}"
                 type="application/octet-stream"/>
    </item>
  </channel>
</rss>'''


# ── version comparison ───────────────────────────────────────────────────────────

def test_is_newer(upd):
    assert upd.is_newer("3.2.0", "3.1.0")
    assert upd.is_newer("3.1.1", "3.1.0")
    assert not upd.is_newer("3.1.0", "3.1.0")
    assert not upd.is_newer("3.0.9", "3.1.0")
    assert upd.is_newer("3.10.0", "3.9.0")     # numeric, not lexical

def test_semver_tolerates_prefix_and_partial(upd):
    assert upd._semver_tuple("v3.1") == (3, 1, 0)
    assert upd._semver_tuple("3.1.0-beta") == (3, 1, 0)


# ── appcast parsing ──────────────────────────────────────────────────────────────

def test_parse_appcast(upd):
    xml = _appcast("3.2.0", "https://x/cdc-3.2.0.dmg", "SIGAAA", 123)
    items = upd.parse_appcast(xml)
    assert len(items) == 1
    it = items[0]
    assert it.version == "3.2.0" and it.url.endswith("3.2.0.dmg")
    assert it.ed_signature == "SIGAAA" and it.length == 123

def test_parse_appcast_sorts_newest_first(upd):
    xml = '''<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle"><channel>
      <item><sparkle:shortVersionString>3.1.0</sparkle:shortVersionString>
        <enclosure url="u/3.1.0.dmg" sparkle:edSignature="a" length="1"/></item>
      <item><sparkle:shortVersionString>3.3.0</sparkle:shortVersionString>
        <enclosure url="u/3.3.0.dmg" sparkle:edSignature="b" length="2"/></item>
    </channel></rss>'''
    items = upd.parse_appcast(xml)
    assert items[0].version == "3.3.0"


# ── Ed25519 verification (the security-critical path) ────────────────────────────

def test_verify_accepts_valid_signature(upd, keypair):
    priv, pub_b64 = keypair
    data = b"the real CODEC dmg bytes"
    sig = base64.b64encode(priv.sign(data)).decode()
    assert upd.verify_ed25519(data, sig, pub_b64) is True

def test_verify_rejects_tampered_data(upd, keypair):
    priv, pub_b64 = keypair
    sig = base64.b64encode(priv.sign(b"original")).decode()
    assert upd.verify_ed25519(b"TAMPERED", sig, pub_b64) is False

def test_verify_rejects_wrong_key(upd, keypair):
    priv, _ = keypair
    other_pub = base64.b64encode(
        Ed25519PrivateKey.generate().public_key().public_bytes_raw()).decode()
    sig = base64.b64encode(priv.sign(b"data")).decode()
    assert upd.verify_ed25519(b"data", sig, other_pub) is False

def test_verify_rejects_garbage(upd, keypair):
    _, pub_b64 = keypair
    assert upd.verify_ed25519(b"data", "not-base64-sig!!", pub_b64) is False


# ── check_for_update gating ──────────────────────────────────────────────────────

def test_check_returns_none_when_not_newer(upd, monkeypatch):
    monkeypatch.setattr(upd, "_current_version", lambda: "9.9.9")
    xml = _appcast("3.2.0", "https://x/a.dmg", "sig", 1)
    monkeypatch.setattr(upd, "parse_appcast", upd.parse_appcast)  # keep real parser
    # feed a fake appcast via a stubbed urlopen
    class _R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return xml.encode()
    monkeypatch.setattr(upd.urllib.request, "urlopen", lambda *a, **k: _R())
    assert upd.check_for_update("http://feed") is None

def test_check_returns_info_when_newer(upd, monkeypatch):
    monkeypatch.setattr(upd, "_current_version", lambda: "3.1.0")
    xml = _appcast("3.2.0", "https://x/a.dmg", "sig", 1)
    class _R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return xml.encode()
    monkeypatch.setattr(upd.urllib.request, "urlopen", lambda *a, **k: _R())
    info = upd.check_for_update("http://feed")
    assert info is not None and info.version == "3.2.0"


# ── download_and_verify: streamed, checked over an mmap, state copied first (P-027) ─
# A fixture appcast points at a fixture DMG (random bytes signed with the throwaway key) through a file:// URL.
# dest_dir, the state folder and the snapshot folder are all in tmp_path: never ~/Downloads or ~/.codec.

import mmap
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MB = 1 << 20


def _dmg(path: Path, size: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        left = size
        while left:
            n = min(left, MB)
            f.write(os.urandom(n))
            left -= n
    return path


def _sign(priv, path: Path) -> str:
    with open(path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as data:
        return base64.b64encode(priv.sign(data)).decode()


def _feed_item(upd, dmg: Path, sig: str, length: int):
    return upd.parse_appcast(_appcast("3.9.0", dmg.as_uri(), sig, length))[0]


def _state(home: Path) -> Path:
    home.mkdir(parents=True)
    con = sqlite3.connect(str(home / "memory.db"))
    con.execute("CREATE TABLE t (x TEXT)")
    con.execute("INSERT INTO t VALUES ('kept')")
    con.commit()
    con.close()
    (home / "config.json").write_text("{}")
    return home


def _left(folder: Path) -> list:
    return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


def test_download_streams_verifies_and_snapshots_before_handing_over(upd, keypair, tmp_path, monkeypatch):
    priv, pub = keypair
    dmg = _dmg(tmp_path / "feed" / "CODEC-3.9.0.dmg", 3 * MB + 123)
    info = _feed_item(upd, dmg, _sign(priv, dmg), dmg.stat().st_size)
    reads = []
    real = upd.urllib.request.urlopen

    class Spy:
        def __init__(self, r): self.r = r
        def __enter__(self): return self
        def __exit__(self, *a): self.r.close()
        def read(self, n=-1):
            reads.append(n)
            return self.r.read(n)
    monkeypatch.setattr(upd.urllib.request, "urlopen", lambda url, timeout=None: Spy(real(url, timeout=timeout)))
    dl, snaps = tmp_path / "dl", tmp_path / "snaps"
    out = upd.download_and_verify(info, dest_dir=dl, cfg={"sparkle_public_ed_key": pub},
                                  state_dir=_state(tmp_path / "codec"), snapshot_dir=snaps)
    assert out == dl / "CODEC-3.9.0.dmg" and _left(dl) == ["CODEC-3.9.0.dmg"]  # no .partial left
    assert out.read_bytes() == dmg.read_bytes()
    assert reads and all(n == upd._CHUNK for n in reads)  # never one read of the whole file
    (snap,) = snaps.iterdir()
    assert sorted(p.name for p in snap.iterdir()) == ["manifest.json", "memory.db", "state.tar.gz"]


def test_download_with_a_bad_signature_leaves_no_file(upd, keypair, tmp_path):
    priv, pub = keypair
    dmg = _dmg(tmp_path / "feed" / "CODEC-3.9.0.dmg", MB)
    bad_sig = base64.b64encode(priv.sign(b"another file")).decode()
    info = _feed_item(upd, dmg, bad_sig, dmg.stat().st_size)
    dl, snaps = tmp_path / "dl", tmp_path / "snaps"
    with pytest.raises(ValueError, match="signature verification FAILED"):
        upd.download_and_verify(info, dest_dir=dl, cfg={"sparkle_public_ed_key": pub},
                                state_dir=_state(tmp_path / "codec"), snapshot_dir=snaps)
    assert _left(dl) == [] and _left(snaps) == []  # nothing at the final path, no .partial, no snapshot


def test_download_with_a_wrong_length_leaves_no_file(upd, keypair, tmp_path):
    priv, pub = keypair
    dmg = _dmg(tmp_path / "feed" / "CODEC-3.9.0.dmg", 2 * MB)
    sig, size = _sign(priv, dmg), dmg.stat().st_size
    dl = tmp_path / "dl"
    for length, message in ((size + 1, "length mismatch: got"), (size - 1, "more than the expected")):
        with pytest.raises(ValueError, match=message):
            upd.download_and_verify(_feed_item(upd, dmg, sig, length), dest_dir=dl,
                                    cfg={"sparkle_public_ed_key": pub},
                                    state_dir=_state(tmp_path / f"codec{length}"), snapshot_dir=tmp_path / "s")
        assert _left(dl) == []


def test_download_with_a_failed_snapshot_keeps_no_dmg(upd, keypair, tmp_path, monkeypatch):
    import codec_preupdate
    priv, pub = keypair
    dmg = _dmg(tmp_path / "feed" / "CODEC-3.9.0.dmg", MB)
    info = _feed_item(upd, dmg, _sign(priv, dmg), dmg.stat().st_size)
    seen = {}

    def failed(src, dest, *, label=""):
        seen.update(src=src, dest=dest, label=label)
        return {"ok": False, "error": "memory.db: error: disk full", "path": None}
    monkeypatch.setattr(codec_preupdate, "snapshot", failed)
    dl = tmp_path / "dl"
    with pytest.raises(RuntimeError, match="could not save a copy of CODEC's data"):
        upd.download_and_verify(info, dest_dir=dl, cfg={"sparkle_public_ed_key": pub},
                                state_dir=tmp_path / "codec", snapshot_dir=tmp_path / "snaps")
    assert _left(dl) == []
    assert seen["src"] == tmp_path / "codec" and seen["label"].endswith("-> 3.9.0")


def test_download_without_a_signature_is_refused_before_downloading(upd, tmp_path, monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("downloaded an unsigned update")
    monkeypatch.setattr(upd.urllib.request, "urlopen", no_network)
    info = upd.UpdateInfo(version="3.9.0", url="https://x/CODEC-3.9.0.dmg", ed_signature="", length=10)
    with pytest.raises(ValueError, match="no Ed25519 signature"):
        upd.download_and_verify(info, dest_dir=tmp_path / "dl", state_dir=tmp_path / "c",
                                snapshot_dir=tmp_path / "s")
    assert _left(tmp_path / "dl") == []


def test_download_removes_only_old_partial_files(upd, keypair, tmp_path):
    priv, pub = keypair
    dmg = _dmg(tmp_path / "feed" / "CODEC-3.9.0.dmg", MB)
    dl = tmp_path / "dl"
    dl.mkdir()
    old, fresh = dl / ".CODEC-3.9.0.dmg.aaaa.partial", dl / ".CODEC-3.9.0.dmg.bbbb.partial"
    other = dl / ".other.dmg.cccc.partial"
    for p in (old, fresh, other):
        p.write_bytes(b"x")
    os.utime(old, (1, 1))
    os.utime(other, (1, 1))
    upd.download_and_verify(_feed_item(upd, dmg, _sign(priv, dmg), dmg.stat().st_size), dest_dir=dl,
                            cfg={"sparkle_public_ed_key": pub}, state_dir=tmp_path / "none",
                            snapshot_dir=tmp_path / "s")
    assert _left(dl) == [".CODEC-3.9.0.dmg.bbbb.partial", ".other.dmg.cccc.partial", "CODEC-3.9.0.dmg"]


def test_a_wildcard_in_the_url_name_removes_only_its_own_partials(upd, tmp_path):
    # The name comes from the appcast URL: "*" must not turn the clean-up into a sweep of other .partial files.
    dl = tmp_path / "dl"
    dl.mkdir()
    other, own = dl / ".other.dmg.cccc.partial", dl / ".CODEC-*.dmg.aaaa.partial"
    for p in (other, own):
        p.write_bytes(b"x")
        os.utime(p, (1, 1))
    upd._remove_stale_partials(dl, "*")
    assert other.exists()
    upd._remove_stale_partials(dl, "CODEC-*.dmg")
    assert _left(dl) == [".other.dmg.cccc.partial"]


_CHILD = r'''
import base64, sys
sys.path.insert(0, sys.argv[1])
import codec_update as u
mode, dmg, sig, pub, out = sys.argv[2:7]
if mode == "whole":   # the old way: the whole file in memory (the control)
    with open(dmg, "rb") as f:
        data = f.read()
    print("verified" if u.verify_ed25519(data, sig, pub) else "bad")
else:
    from pathlib import Path
    size = Path(dmg).stat().st_size
    info = u.UpdateInfo(version="9.9.9", url=Path(dmg).as_uri(), ed_signature=sig, length=size)
    p = u.download_and_verify(info, dest_dir=Path(out) / "dl", cfg={"sparkle_public_ed_key": pub},
                              state_dir=Path(out) / "codec", snapshot_dir=Path(out) / "snaps")
    print("verified" if p.stat().st_size == size else "bad")
'''


def _footprint(tmp_path: Path, mode: str, dmg: Path, sig: str, pub: str) -> int:
    script = tmp_path / "child" / "measure.py"
    script.parent.mkdir(exist_ok=True)
    script.write_text(_CHILD)
    out = tmp_path / f"out-{mode}"
    out.mkdir(exist_ok=True)
    r = subprocess.run(["/usr/bin/time", "-l", sys.executable, str(script), str(REPO), mode, str(dmg), sig, pub,
                        str(out)], capture_output=True, text=True, timeout=300, cwd=str(tmp_path))
    assert r.returncode == 0 and r.stdout.strip() == "verified", r.stdout + r.stderr
    m = re.search(r"^\s*(\d+)\s+peak memory footprint", r.stderr, re.M)
    if not m:
        pytest.skip("this /usr/bin/time does not report a peak memory footprint")
    for p in (out / "dl").glob("*.dmg"):
        p.unlink()
    return int(m.group(1))


@pytest.mark.skipif(sys.platform != "darwin" or not os.path.exists("/usr/bin/time"),
                    reason="peak memory footprint comes from macOS /usr/bin/time -l")
def test_dmg_download_peak_memory_footprint_stays_under_100_mb(upd, keypair, tmp_path):
    priv, pub = keypair
    dmg = _dmg(tmp_path / "feed" / "CODEC-9.9.9.dmg", 150 * MB)
    sig = _sign(priv, dmg)
    try:
        _state(tmp_path / "out-stream" / "codec")
        stream = _footprint(tmp_path, "stream", dmg, sig, pub)
        whole = _footprint(tmp_path, "whole", dmg, sig, pub)
    finally:
        dmg.unlink()
    print(f"\npeak memory footprint, 150 MB DMG: download_and_verify {stream / MB:.1f} MB, "
          f"whole-file read (control) {whole / MB:.1f} MB")
    assert whole > 100 * MB, "the control should hold the whole file: the measure is not working"
    assert stream < 100 * MB
