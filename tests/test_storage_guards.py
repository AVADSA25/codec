"""Every PWA surface must reach storage through the guarded shim.

Storage access THROWS when blocked — Safari private mode, a hardened profile,
an iframe whose third-party storage is blocked — it does not return null. An
unguarded read at the top level of a script kills that ENTIRE script: every
statement after it silently never runs and the page looks fine while being
half-dead. That is what happened to cortex, which the dashboard embeds in an
iframe, and it cost a debugging session because the symptom (theme not applying)
pointed nowhere near the cause.

This is a lint, not a behaviour test: it pins the property that no future edit
reintroduces a bare `localStorage.getItem` on a page.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SURFACES = sorted(REPO.glob("codec_*.html"))

# The shim's own bodies are the only place bare storage may appear.
_SHIM_BODIES = re.compile(
    r"function _(?:ls|ss)(?:Get|Set|Del)\(k(?:,v)?\)\{try\{"
    r"(?:return )?(?:local|session)Storage\.(?:get|set|remove)Item\([^)]*\)\}catch\(e\)\{[^}]*\}\}"
)
_BARE_ACCESS = re.compile(r"(?:local|session)Storage\.(?:get|set|remove)Item\s*\(")
_ACCESSORS = ("_lsGet", "_lsSet", "_lsDel", "_ssGet", "_ssSet", "_ssDel")


def _strip_shim(text: str) -> str:
    return _SHIM_BODIES.sub("", text)


def test_surfaces_are_discovered():
    """Guard the guard: a bad glob would make every test below vacuously pass."""
    assert len(SURFACES) >= 7, f"expected the PWA surfaces, found {SURFACES}"


@pytest.mark.parametrize("path", SURFACES, ids=lambda p: p.name)
def test_no_bare_storage_access(path: Path):
    leftovers = _BARE_ACCESS.findall(_strip_shim(path.read_text()))
    assert not leftovers, (
        f"{path.name} touches storage directly {len(leftovers)}x — use "
        f"_lsGet/_lsSet/_lsDel/_ssGet/_ssSet/_ssDel, which cannot throw"
    )


@pytest.mark.parametrize("path", SURFACES, ids=lambda p: p.name)
def test_shim_is_defined_before_first_use(path: Path):
    """Function declarations hoist only within their own script block, so the
    shim has to be defined in an EARLIER block than any caller."""
    text = path.read_text()
    if "function _lsGet(" not in text:
        pytest.skip(f"{path.name} does not touch storage")
    checked = 0
    for accessor in _ACCESSORS:
        definition = text.find(f"function {accessor}(")
        if definition < 0:
            continue
        calls = [m.start() for m in re.finditer(rf"(?<!function ){accessor}\(", text)]
        if not calls:
            continue  # shim exports the whole set; not every page uses every one
        checked += 1
        assert definition < min(calls), (
            f"{path.name}: {accessor} is called before it is defined"
        )
    assert checked, f"{path.name} defines the shim but never calls it"


@pytest.mark.parametrize("path", SURFACES, ids=lambda p: p.name)
def test_reads_have_a_fallback_and_writes_do_not(path: Path):
    """The read shims must return null on failure, not undefined — call sites
    do `_lsGet(k)||''` and `=== 'true'`, which undefined would also satisfy,
    but an explicit null keeps the contract identical to the real API."""
    text = path.read_text()
    if "function _lsGet(" not in text:
        pytest.skip(f"{path.name} does not touch storage")
    for getter in ("_lsGet", "_ssGet"):
        body = text[text.index(f"function {getter}("):]
        assert "return null" in body[:160], f"{path.name}: {getter} must fall back to null"


def test_suite_does_not_write_to_the_real_audit_log():
    """The suite must never append to ~/.codec/audit.log.

    An unmocked `audit()` used to write there — 315 lines per full run, seeding
    an HMAC-signed forensic log with events that read as real security findings.
    conftest redirects the module constants at import time; this pins it.
    """
    import codec_audit
    real = Path(os.path.expanduser("~/.codec/audit.log"))
    assert Path(codec_audit._AUDIT_LOG) != real, "audit log is NOT isolated"
    assert Path(codec_audit._AUDIT_DIR) != real.parent, "audit dir is NOT isolated"

    # Prove it end to end, not just by inspecting the constants. Look for OUR
    # probe rather than diffing the file: on a live machine the real log is
    # being appended to by codec-observer while this runs, so a whole-content
    # comparison would fail on the daemon's writes, not on ours.
    probe = "test_isolation_probe_a4be465"
    codec_audit.audit(event=probe, source="pytest", outcome="ok",
                      message="must not reach the real log")
    assert probe in Path(codec_audit._AUDIT_LOG).read_text(), \
        "the probe did not reach the isolated log either — check the redirect"
    if real.exists():
        assert probe not in real.read_text(), \
            "audit() reached the operator's real log"
