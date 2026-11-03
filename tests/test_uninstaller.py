"""Tests for PR-5E (Audit E / E-14, W5-12) — the macOS uninstaller.

Safety-critical: the script deletes things, so every test runs against a
throwaway ``--home`` temp dir (never the real $HOME) and Keychain ops are gated
to real-$HOME runs. We verify dry-run preserves everything, --yes removes the
app/agents/logs but keeps user data, and --yes --purge-data removes data too.

Reference: docs/W5-12-UNINSTALLER-DESIGN.md, docs/audits/PHASE-1-APPLE-APP.md (E-14).
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
UNINSTALL = REPO / "packaging" / "macos" / "uninstall_codec.sh"
KC_PREFIX = "ai.avadigital.codec."


def _fixtures(tmp_path: Path) -> tuple[Path, Path]:
    """Build a fake $HOME (with .codec, logs, a LaunchAgent) + a fake .app."""
    home = tmp_path / "home"
    (home / ".codec").mkdir(parents=True)
    (home / ".codec" / "memory.db").write_text("data")
    (home / "Library" / "Logs" / "CODEC").mkdir(parents=True)
    (home / "Library" / "Logs" / "CODEC" / "launch.log").write_text("log")
    la = home / "Library" / "LaunchAgents"
    la.mkdir(parents=True)
    (la / "ai.avadigital.codec.codec-dashboard.plist").write_text("<plist/>")
    app = tmp_path / "Applications" / "Sovereign AI Workstation.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_text("<plist/>")
    return home, app


def _run(home: Path, app: Path, *flags: str):
    return subprocess.run(
        ["bash", str(UNINSTALL), "--home", str(home), "--app", str(app), *flags],
        capture_output=True, text=True, timeout=60,
    )


def test_script_present_executable_and_guarded():
    assert UNINSTALL.exists(), "E-14: packaging/macos/uninstall_codec.sh must exist"
    assert os.access(UNINSTALL, os.X_OK), "must be executable"
    t = UNINSTALL.read_text()
    assert t.splitlines()[0].startswith("#!"), "needs a shebang"
    # Safety: a guarded remove helper, and no bare `rm -rf "$HOME"`.
    assert "safe_rm" in t, "must route deletes through a guarded safe_rm helper"
    assert 'rm -rf "$HOME"' not in t and "rm -rf $HOME" not in t, "must never rm -rf bare $HOME"
    # Must teach the user about the un-revokable TCC residue.
    low = t.lower()
    assert "privacy" in low or "tcc" in low or "accessibility" in low, "must document TCC residue"


def test_dry_run_is_default_and_deletes_nothing(tmp_path):
    home, app = _fixtures(tmp_path)
    r = _run(home, app, "--dry-run")
    assert r.returncode == 0, f"dry-run failed: {r.stderr}\n{r.stdout}"
    # Everything still present.
    assert (home / ".codec" / "memory.db").exists()
    assert (home / "Library" / "Logs" / "CODEC" / "launch.log").exists()
    assert (home / "Library" / "LaunchAgents" / "ai.avadigital.codec.codec-dashboard.plist").exists()
    assert (app / "Contents" / "Info.plist").exists()
    # And it tells the user what it would remove.
    assert ".codec" in r.stdout and "LaunchAgents" in r.stdout


def test_no_yes_also_means_dry_run(tmp_path):
    # Running with neither --dry-run nor --yes must NOT delete (safe default).
    home, app = _fixtures(tmp_path)
    r = _run(home, app)
    assert r.returncode == 0
    assert (home / ".codec" / "memory.db").exists(), "default run must not delete"
    assert (app / "Contents" / "Info.plist").exists()


def test_yes_removes_app_agents_logs_but_keeps_user_data(tmp_path):
    home, app = _fixtures(tmp_path)
    r = _run(home, app, "--yes")
    assert r.returncode == 0, f"{r.stderr}\n{r.stdout}"
    assert not app.exists(), "--yes must remove the .app"
    assert not (home / "Library" / "LaunchAgents" / "ai.avadigital.codec.codec-dashboard.plist").exists()
    assert not (home / "Library" / "Logs" / "CODEC").exists()
    # User data preserved without --purge-data.
    assert (home / ".codec" / "memory.db").exists(), "--yes alone must KEEP ~/.codec"


def test_purge_data_removes_user_data(tmp_path):
    home, app = _fixtures(tmp_path)
    r = _run(home, app, "--yes", "--purge-data")
    assert r.returncode == 0, f"{r.stderr}\n{r.stdout}"
    assert not (home / ".codec").exists(), "--yes --purge-data must remove ~/.codec"


# ── P-019: the uninstaller removes what the code creates ─────────────────────
# Every name below is read from the code, so a new Keychain item or data path
# the uninstaller does not know about fails here (docs/PRIVACY-TRUTH-DESIGN.md).

def _code_keychain_names() -> set:
    """git grep of every Keychain name the code uses: KEY_* in codec_keychain.py,
    KEYCHAIN_KEY / KEY_SLOT anywhere, a full ai.avadigital.codec.<name> service
    constant, and any literal keychain_get/set/delete("<name>") call."""
    greps = [
        ["-E", r'^KEY_[A-Z_]+ = "[a-z0-9_]+"', "--", "codec_keychain.py"],
        ["-E", r'^(KEYCHAIN_KEY|KEY_SLOT) = "[a-z0-9_]+"|= "ai\.avadigital\.codec\.[a-z0-9_]+"'
               r'|keychain_(get|set|delete)\("[a-z0-9_]+"', "--", "*.py", ":!tests"],
    ]
    names = set()
    for args in greps:
        r = subprocess.run(["git", "grep", "-h", "-o", *args], cwd=REPO, capture_output=True, text=True)
        if r.returncode not in (0, 1):
            pytest.skip(f"git grep unavailable: {r.stderr.strip()[:120]}")
        for match in r.stdout.split():
            m = re.search(r'"([a-z0-9_.]+)"', match)
            if m:
                names.add(m.group(1).removeprefix(KC_PREFIX))
    assert len(names) >= 10, f"git grep found too few Keychain names: {sorted(names)}"
    return names


def _src(rel: str) -> str:
    return (REPO / rel).read_text()


def _bundle_id() -> str:
    m = re.search(r"<key>CFBundleIdentifier</key>\s*<string>([^<]+)</string>",
                  _src("packaging/macos/Info.plist"))
    assert m, "Info.plist has no CFBundleIdentifier"
    return m.group(1)


def _user_data_names() -> list:
    """Basenames of the ~/.codec items the brief names, each read from its code constant."""
    def path_const(rel, name):
        m = re.search(rf'^{name}\s*=\s*os\.path\.expanduser\("~/\.codec/([^"]+)"\)', _src(rel), re.M)
        assert m, f"{rel} no longer defines {name} under ~/.codec"
        return m.group(1)

    names = [
        path_const("codec_config.py", "_NEW_DB"),                 # memory.db
        path_const("skills/clipboard.py", "DB"),                  # clipboard.db
        path_const("codec_google_auth.py", "TOKEN_PATH"),         # google_token.json
        path_const("codec_config.py", "CONFIG_PATH"),             # config.json
    ]
    for rel, pattern in (("codec_notes.py", r'Path\.home\(\) / "\.codec" / "(notes)"'),
                         ("codec_agent_plan.py", r'_AGENTS_DIR = _CODEC_DIR / "(agents)"'),
                         ("codec_backup.py", r'def backup_dir\(\) -> Path:\s*return home\(\) / "(backups)"')):
        m = re.search(pattern, _src(rel))
        assert m, f"{rel}: data folder constant moved"
        names.append(m.group(1))
    return names


def _sandbox(tmp_path: Path) -> tuple[Path, Path]:
    home, app = _fixtures(tmp_path)
    for name in _user_data_names():
        p = home / ".codec" / name
        if "." in name:
            p.write_text("x")
        else:
            p.mkdir(parents=True, exist_ok=True)
    logs = home / ".pm2" / "logs"
    logs.mkdir(parents=True)
    (logs / "codec-dashboard-out.log").write_text("log")
    (logs / "whisper-stt-out.log").write_text("log")
    (logs / "other-app-out.log").write_text("log")
    for d in ("Caches", "WebKit", "HTTPStorages"):
        (home / "Library" / d / _bundle_id()).mkdir(parents=True)
    return home, app


def test_keychain_list_equals_the_code():
    m = re.search(r'^KC_SERVICES="([^"]*)"$', UNINSTALL.read_text(), re.M)
    assert m, "KC_SERVICES not found in uninstall_codec.sh"
    listed = m.group(1).split()
    assert len(listed) == len(set(listed)), "KC_SERVICES lists a name twice"
    code = _code_keychain_names()
    assert set(listed) == code, (f"uninstaller and code disagree: only in the script {sorted(set(listed) - code)}, "
                                 f"only in the code {sorted(code - set(listed))}")
    assert f'KC_PREFIX="{KC_PREFIX.rstrip(".")}"' in UNINSTALL.read_text()


def test_dry_run_lists_every_keychain_name_without_calling_security(tmp_path):
    home, app = _sandbox(tmp_path)
    r = _run(home, app, "--dry-run")
    assert r.returncode == 0, f"{r.stderr}\n{r.stdout}"
    printed = set(re.findall(r"keychain item: ai\.avadigital\.codec\.([a-z0-9_]+)", r.stdout))
    assert printed == _code_keychain_names()
    assert "plus any other ai.avadigital.codec.* item" in r.stdout


def test_dry_run_itemises_user_data_logs_and_caches_and_deletes_nothing(tmp_path):
    home, app = _sandbox(tmp_path)
    before = sorted(str(p) for p in tmp_path.rglob("*"))
    r = _run(home, app, "--dry-run")
    assert r.returncode == 0, f"{r.stderr}\n{r.stdout}"
    assert sorted(str(p) for p in tmp_path.rglob("*")) == before, "a dry run changed the sandbox"
    for name in _user_data_names():
        assert re.search(rf"^\s+{re.escape(name)}\s+present\s", r.stdout, re.M), f"dry run does not list {name}"
    assert f"would remove: {home}/.pm2/logs/codec-dashboard-out.log" in r.stdout
    assert f"would remove: {home}/.pm2/logs/whisper-stt-out.log" in r.stdout
    assert "other-app-out.log" not in r.stdout, "only CODEC's PM2 logs may be listed"
    for d in ("Caches", "WebKit", "HTTPStorages"):
        assert f"would remove: {home}/Library/{d}/{_bundle_id()}" in r.stdout


def test_yes_removes_pm2_logs_and_caches_but_keeps_user_data(tmp_path):
    home, app = _sandbox(tmp_path)
    r = _run(home, app, "--yes")
    assert r.returncode == 0, f"{r.stderr}\n{r.stdout}"
    assert not (home / ".pm2" / "logs" / "codec-dashboard-out.log").exists()
    assert not (home / ".pm2" / "logs" / "whisper-stt-out.log").exists()
    assert (home / ".pm2" / "logs" / "other-app-out.log").exists(), "another app's PM2 log was removed"
    for d in ("Caches", "WebKit", "HTTPStorages"):
        assert not (home / "Library" / d / _bundle_id()).exists()
    for name in _user_data_names():
        assert (home / ".codec" / name).exists(), f"--yes alone must keep ~/.codec/{name}"


def test_pm2_log_names_cover_every_app_in_the_ecosystem_file():
    m = re.search(r'^PM2_LOG_NAMES="([^"]*)"$', UNINSTALL.read_text(), re.M)
    assert m, "PM2_LOG_NAMES not found in uninstall_codec.sh"
    prefixes = m.group(1).split()
    apps = re.findall(r'^\s+name: "([^"]+)"', _src("ecosystem.config.js"), re.M)
    assert len(apps) >= 10, f"ecosystem.config.js app names not found: {apps}"
    for app in apps:
        assert any(app == p or app.startswith(p + "-") for p in prefixes), \
            f"the uninstaller leaves the PM2 logs of {app} behind"


def test_keychain_scan_reads_no_secret_and_deletes_every_item_of_a_service():
    t = UNINSTALL.read_text()
    calls = [ln for ln in t.splitlines() if '"$SECURITY"' in ln and not ln.lstrip().startswith("#")]
    assert any("dump-keychain" in ln for ln in calls), "real runs must find owner-named key slots too"
    for ln in calls:
        assert not re.search(r"dump-keychain\s+-\w*d|find-generic-password|\s-w\b|\s-g\b", ln), \
            f"the uninstaller must never read a secret value: {ln.strip()}"
    assert re.search(r'while .*"\$SECURITY" delete-generic-password -s "\$svc"', t), \
        "one delete removes one item; mcp_tokens holds several, so the delete must loop"
    # A locked keychain or a refused delete must not read as "(absent)": a real
    # --yes run scans again and names what is left.
    after = t[t.index("kc_delete_service \"$svc\""):]
    assert re.search(r'left="\$\(kc_scan', after) and "STILL PRESENT" in after
