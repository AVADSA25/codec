"""P-088: the read-only listing of old secrets at rest (tools/list_old_secrets.py).

Design: docs/SECRETS-AT-REST-DESIGN.md. Every test uses a fake `security` program and config
copies under tmp_path; none reads the owner's Keychain or ~/.codec, and none sets HOME.
"""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("list_old_secrets_under_test", REPO / "tools" / "list_old_secrets.py")
los = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(los)

OLD_HASH = "a" * 40 + "0123456789abcdef01234567"   # 64 hex digits, the pre-argon2id form
JWT_VALUE = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ0ZXN0LWxpY2VuY2UifQ.c2lnbmF0dXJlLXRlc3Qtb25seQ"


def _item(svce: str, acct: str, cls: str = '"genp"') -> str:
    return (f'keychain: "/Users/someone/Library/Keychains/login.keychain-db"\n'
            f"version: 512\nclass: {cls}\nattributes:\n"
            f'    0x00000007 <blob>="{svce}"\n'
            f'    "acct"<blob>={acct}\n'
            f'    "cdat"<timedate>=0x32303236313130333132303030305A00  "20261103120000Z\\000"\n'
            f'    "gena"<blob>=<NULL>\n'
            f'    "svce"<blob>="{svce}"\n')


DUMP = "".join([
    _item("ai.avadigital.codec._test_4242_test_x0.audit_hmac_secret", '"someone"'),
    _item("ai.avadigital.codec._test_rem_77_test_y1.audit_hmac_secret", "0x736F6D656F6E65  \"someone\""),
    _item("ai.avadigital.codec.audit_hmac_secret", '"someone"'),
    _item("ai.avadigital.codec.audit_hmac_secret", '"codec"'),
    _item("ai.avadigital.codec.internal_token", '"someone"'),
    _item("ai.avadigital.codec.serper_api_key", '"someone"'),
    _item("ai.avadigital.codec._test_inet", '"someone"', cls='"inet"'),   # not a generic password
    _item("com.example.other-app", '"someone@example.com"'),
])


@pytest.fixture
def fake_security(tmp_path):
    """A stand-in for /usr/bin/security that prints DUMP for dump-keychain and logs its argv."""
    folder = tmp_path / "bin"
    folder.mkdir()
    (folder / "dump.txt").write_text(DUMP)
    fake = folder / "security"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "here = os.path.dirname(os.path.abspath(__file__))\n"
        "with open(os.path.join(here, 'argv.log'), 'a') as f:\n"
        "    f.write(' '.join(sys.argv[1:]) + '\\n')\n"
        "if sys.argv[1:] != ['dump-keychain']:\n"
        "    sys.exit(2)\n"
        "sys.stdout.write(open(os.path.join(here, 'dump.txt')).read())\n")
    os.chmod(fake, stat.S_IRWXU)
    return fake


@pytest.fixture
def codec_dir(tmp_path):
    d = tmp_path / "codec"
    (d / "backups").mkdir(parents=True)
    (d / "config.json").write_text(json.dumps({"ava": {"license_key": JWT_VALUE}}))  # live: never matched
    (d / "config.json.lock").write_text("")
    (d / "config.json.bak-old").write_text(json.dumps(
        {"auth_pin_hash": OLD_HASH, "ava": {"license_key": JWT_VALUE, "enabled": True}}))
    (d / "config.json.bak-argon").write_text(json.dumps(
        {"auth_pin_hash": "$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$aGFzaA", "llm_model": "x"}))
    (d / "backups" / "config-pre-swap.json").write_text(json.dumps({"license_token": JWT_VALUE}))
    (d / "config.json.bak-broken").write_text('{"auth_pin_hash": "' + OLD_HASH + '", ')
    (d / "notes.json").write_text(json.dumps({"auth_pin_hash": OLD_HASH}))  # not a config copy
    return d


def test_keychain_listing_groups_names_and_never_asks_for_values(fake_security):
    items = los.keychain_items(str(fake_security))
    g = los.sort_items(items)
    assert [i["service"] for i in g["test"]] == [
        "ai.avadigital.codec._test_4242_test_x0.audit_hmac_secret",
        "ai.avadigital.codec._test_rem_77_test_y1.audit_hmac_secret"]
    assert g["test"][1]["account"] == "someone"  # hex-encoded attribute decoded
    assert [(i["service"], i["account"]) for i in g["never_delete"]] == [
        ("ai.avadigital.codec.audit_hmac_secret", "codec"),
        ("ai.avadigital.codec.audit_hmac_secret", "someone"),
        ("ai.avadigital.codec.internal_token", "someone")]
    assert [i["service"] for i in g["other"]] == ["ai.avadigital.codec.serper_api_key"]
    assert (fake_security.parent / "argv.log").read_text() == "dump-keychain\n"


def test_keychain_unavailable_is_reported_not_guessed(tmp_path):
    assert los.keychain_items(str(tmp_path / "no-security-here")) is None
    report = los.build_report(str(tmp_path), str(tmp_path / "no-security-here"))
    assert report["keychain"] == {"available": False}
    assert "not read" in los.render(report)


def test_delete_lines_only_for_test_leftovers(fake_security):
    g = los.sort_items(los.keychain_items(str(fake_security)))
    lines = los.delete_commands(g["test"])
    assert lines == [
        "security delete-generic-password -s ai.avadigital.codec._test_4242_test_x0.audit_hmac_secret -a someone",
        "security delete-generic-password -s ai.avadigital.codec._test_rem_77_test_y1.audit_hmac_secret -a someone"]
    for protected in g["never_delete"] + g["other"]:
        with pytest.raises(ValueError):
            los.delete_commands([protected])


def test_config_copies_found_by_setting_name(codec_dir):
    res = los.scan_backups(str(codec_dir))
    assert res["checked"] == 4  # the live config.json, its .lock and notes.json are not copies
    hits = {h["file"]: h for h in res["with_old_secrets"]}
    assert set(hits) == {"config.json.bak-old", "config.json.bak-broken", os.path.join("backups", "config-pre-swap.json")}
    assert hits["config.json.bak-old"]["old_pin_hash"] == ["auth_pin_hash"]
    assert hits["config.json.bak-old"]["jwt"] == ["ava.license_key"]
    assert hits[os.path.join("backups", "config-pre-swap.json")] == {
        "file": os.path.join("backups", "config-pre-swap.json"), "json": True,
        "old_pin_hash": [], "jwt": ["license_token"]}
    assert hits["config.json.bak-broken"]["json"] is False
    assert hits["config.json.bak-broken"]["old_pin_hash"] == ["(not JSON)"]


def test_output_names_only_never_a_value(fake_security, codec_dir, monkeypatch, capsys):
    monkeypatch.setattr(los, "SECURITY_BIN", str(fake_security))
    monkeypatch.setattr(los, "CODEC_DIR", str(codec_dir))
    for argv in ([], ["--json"], ["--commands"]):
        assert los.main(argv) == 0
        out = capsys.readouterr()
        text = out.out + out.err
        for value in (OLD_HASH, JWT_VALUE, "eyJ", "argon2id$", "com.example.other-app", "someone@example.com"):
            assert value not in text
    los.main([])
    text = capsys.readouterr().out
    assert "Test leftovers" in text and ": 2" in text
    assert "(another account for the same name)" in text
    assert "3 hold an old PIN hash or a JWT" in text
    report = json.loads((los.main(["--json"]), capsys.readouterr().out)[1])
    assert len(report["keychain"]["test"]) == 2 and report["keychain"]["other_count"] == 1


def test_tool_writes_nothing(fake_security, codec_dir, monkeypatch, capsys):
    def snapshot():
        return {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in codec_dir.rglob("*") if p.is_file()}
    before = snapshot()
    monkeypatch.setattr(los, "SECURITY_BIN", str(fake_security))
    monkeypatch.setattr(los, "CODEC_DIR", str(codec_dir))
    los.main([])
    capsys.readouterr()
    assert snapshot() == before
