"""`system` skill: the Data volume and a real memory line (P-097).

On macOS "/" is the sealed system snapshot (about 2% used on every Mac), so the skill
reads the Data volume; and it gives a memory line with numbers, or none. Every command
output here is a fixture: no real disk, memory or uptime is read.
"""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

_SKILL = Path(__file__).resolve().parents[1] / "skills" / "system_info.py"
_spec = importlib.util.spec_from_file_location("system_info_p097", _SKILL)
si = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(si)

DF_DATA = ("Filesystem 1024-blocks       Used  Available Capacity  Mounted on\n"
           "/dev/disk3s5 1948455240 1185317936 728843312    62%    /System/Volumes/Data\n")
DF_ROOT = ("Filesystem 1024-blocks     Used Available Capacity Mounted on\n"
           "/dev/sda1      61255492 30627746  30627746      50% /\n")
VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                                    67267.
Pages active:                                1529199.
Pages inactive:                              1410426.
Pages speculative:                            118110.
Pages wired down:                             237591.
Pages purgeable:                                3878.
"Translation faults":                     2588515092.
File-backed pages:                            735206.
Anonymous pages:                             2322529.
Pages stored in compressor:                  2610784.
Pages occupied by compressor:                 776836.
"""
MEMSIZE = "68719476736\n"   # 64 GB


def test_disk_line_reports_used_and_total_of_the_data_volume():
    assert si._disk_line(DF_DATA, True) == "Disk (Data volume): 1.2 TB used of 2.0 TB (62% full)"
    assert si._disk_line(DF_ROOT, False) == "Disk: 31.4 GB used of 62.7 GB (50% full)"
    assert si._disk_line("", True) is None
    assert si._disk_line("df: /nope: No such file or directory\n", True) is None


def test_memory_line_has_real_numbers_or_is_absent():
    # (2322529 - 3878 + 237591 + 776836) pages of 16 KiB = 50.9 GB of 2**30 bytes
    assert si._memory_line(VM_STAT, MEMSIZE) == "Memory: 50.9 GB used of 64.0 GB"
    older = VM_STAT.replace("Anonymous pages:", "Other pages:")
    # active pages instead: (1529199 + 237591 + 776836) * 16384 = 38.8 GB
    assert si._memory_line(older, MEMSIZE) == "Memory: 38.8 GB used of 64.0 GB"
    assert si._memory_line("", MEMSIZE) is None
    assert si._memory_line(VM_STAT, "") is None
    assert si._memory_line(VM_STAT.replace("Pages wired down", "Pages gone"), MEMSIZE) is None


@pytest.fixture
def fake_cmds(monkeypatch, tmp_path):
    calls = []
    outputs = {"uptime": " 9:00  up 2 days,  3:01, 2 users, load averages: 1 1 1\n",
               "vm_stat": VM_STAT, "sysctl -n hw.memsize": MEMSIZE, "df": DF_DATA,
               "bash": "{ 1.00 1.10 1.20 }\n"}

    def run(cmd, **kw):
        calls.append(cmd)
        key = " ".join(cmd) if cmd[0] == "sysctl" else cmd[0]
        if key not in outputs:
            raise FileNotFoundError(cmd[0])
        return SimpleNamespace(stdout=outputs[key], returncode=0)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(si, "_DATA_VOLUME", str(tmp_path))   # a folder that exists
    return SimpleNamespace(calls=calls, outputs=outputs, data=str(tmp_path))


def test_run_reads_the_data_volume_and_prints_memory(fake_cmds):
    out = si.run("system info")
    assert "Disk (Data volume): 1.2 TB used of 2.0 TB (62% full)" in out
    assert "Memory: 50.9 GB used of 64.0 GB" in out
    assert "Memory stats available" not in out
    assert ["df", "-P", "-k", fake_cmds.data] in fake_cmds.calls


def test_run_without_vm_stat_has_no_memory_line(fake_cmds, monkeypatch, tmp_path):
    del fake_cmds.outputs["vm_stat"]           # Linux: no vm_stat
    fake_cmds.outputs["df"] = DF_ROOT
    monkeypatch.setattr(si, "_DATA_VOLUME", str(tmp_path / "missing"))
    out = si.run("system info")
    assert "Memory" not in out
    assert "Disk: 31.4 GB used of 62.7 GB (50% full)" in out
    assert ["df", "-P", "-k", "/"] in fake_cmds.calls
