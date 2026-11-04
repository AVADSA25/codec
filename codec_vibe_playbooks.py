"""Vibe agent playbooks (P-071, docs/VIBE-PLAYBOOKS-DESIGN.md).

A playbook is a short guide in plain words for one kind of job: a review, an explanation, a clean-up. It is a folder
with one file, PLAYBOOK.md: a small front block (name, description, tools, an optional report kind), then the steps.
Four ship in the repo (vibe_playbooks/); the owner adds his own in ~/.codec/vibe-playbooks/<name>/.

A playbook reaches the agent only after the owner's click at this Mac, stored as the SHA-256 of its file: a file that
changed since is not offered until he approves it again. A playbook can only narrow a run's tools, never add one.
Nothing in a playbook runs; the only substitution is $ARGUMENTS. The agent never writes the folders or the approval
file (codec_vibe_projects.agent_write refuses them, see is_protected).

This module only reads files and the approval file; it writes the approval file and nothing else. The paths are
module constants so tests point them at temp folders.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import codec_vibe_agent_proto as proto

SHIPPED_DIR = Path(__file__).resolve().parent / "vibe_playbooks"
OWNER_DIR = Path(os.path.expanduser("~/.codec/vibe-playbooks"))
APPROVALS_PATH = Path(os.path.expanduser("~/.codec/vibe_playbooks_approved.json"))

FILE_NAME = "PLAYBOOK.md"
SOURCES = ("shipped", "owner")
SHIPPED_ORDER = ("review", "security-review", "simplify", "explain")
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,39}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
KEYS = ("name", "description", "tools", "report")
REQUIRED = ("name", "description", "tools")
REPORTS = ("findings",)
MAX_FILE_BYTES = 64 * 1024
MAX_DESCRIPTION = 200
MAX_FRONT_LINES = 20
MAX_OWNER_FOLDERS = 50
MAX_LISTED = 10                 # names in the prompt
MAX_LIST_CHARS = 1200           # the whole list in the prompt
ARGUMENTS = "$ARGUMENTS"


@dataclass
class Playbook:
    name: str
    source: str
    path: Path
    sha256: str = ""
    size: int = 0
    description: str = ""
    tools: tuple = ()
    report: str = ""
    body: str = ""
    problem: str = ""           # why it cannot be used; "" when it can
    state: str = "new"          # approved | new | changed | invalid
    approved_at: str = ""
    extra: dict = field(default_factory=dict)

    def public(self) -> dict:
        return {"name": self.name, "source": self.source, "description": self.description, "tools": list(self.tools),
                "report": self.report, "sha256": self.sha256, "size": self.size, "state": self.state,
                "problem": self.problem, "approved_at": self.approved_at}


class PlaybookError(Exception):
    """Something the caller can fix. status is an HTTP status code."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# ── reading one file ─────────────────────────────────────────────────────────

def _read_bytes(folder: Path) -> tuple:
    """(bytes, "") of folder/PLAYBOOK.md, or (None, why). Never through a link, only a regular file of at most 64 KB."""
    if os.path.islink(folder) or not folder.is_dir():
        return None, "the folder is a link" if os.path.islink(folder) else "not a folder"
    path = folder / FILE_NAME
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None, f"no {FILE_NAME} in the folder"
    except OSError:
        return None, f"{FILE_NAME} is a link" if os.path.islink(path) else f"{FILE_NAME} cannot be opened"
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode):
            return None, f"{FILE_NAME} is not a file"
        if st.st_size > MAX_FILE_BYTES:
            return None, f"{FILE_NAME} is larger than 64 KB"
        data = f.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        return None, f"{FILE_NAME} is larger than 64 KB"
    return data, ""


def parse(text: str, folder_name: str) -> tuple:
    """(fields, body, problem) of a PLAYBOOK.md text. problem is "" when the playbook can be used."""
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, "", "it does not start with a --- line and the front block"
    end = next((i for i in range(1, min(len(lines), MAX_FRONT_LINES + 2)) if lines[i].strip() == "---"), None)
    if end is None:
        return {}, "", f"the front block has no closing --- line within {MAX_FRONT_LINES} lines"
    fields: dict = {}
    for ln in lines[1:end]:
        if not ln.strip():
            continue
        key, sep, value = ln.partition(":")
        key = key.strip().lower()
        if not sep or key not in KEYS:
            return {}, "", f"unknown line in the front block: {ln.strip()[:60]!r} (the keys are {', '.join(KEYS)})"
        if key in fields:
            return {}, "", f"'{key}' is written twice"
        fields[key] = value.strip()
    missing = [k for k in REQUIRED if not fields.get(k)]
    if missing:
        return {}, "", "missing in the front block: " + ", ".join(missing)
    if fields["name"] != folder_name:
        return {}, "", f"the name '{fields['name'][:40]}' is not the folder's name '{folder_name}'"
    desc = fields["description"]
    if len(desc) > MAX_DESCRIPTION:
        return {}, "", f"the description is longer than {MAX_DESCRIPTION} characters"
    tools = []
    for word in re.split(r"[\s,]+", fields["tools"]):
        if not word:
            continue
        if word not in proto.TOOLS:
            return {}, "", f"'{word[:30]}' is not one of the agent's tools ({', '.join(proto.TOOLS)})"
        if word not in tools:
            tools.append(word)
    if "finish" not in tools:
        tools.append("finish")                          # a run can always end with its report
    report = fields.get("report", "")
    if report and report not in REPORTS:
        return {}, "", f"the report kind '{report[:30]}' is not known (only: {', '.join(REPORTS)})"
    body = "\n".join(lines[end + 1:]).strip()
    if not body:
        return {}, "", "it has no steps after the front block"
    return {"description": desc, "tools": tuple(tools), "report": report}, body, ""


def _load(folder: Path, source: str) -> Playbook:
    pb = Playbook(name=folder.name, source=source, path=folder / FILE_NAME)
    if not NAME_RE.match(folder.name):
        pb.problem, pb.state = "the folder name is not a playbook name (a-z, 0-9 and -, 2 to 40 characters)", "invalid"
        return pb
    data, why = _read_bytes(folder)
    if data is None:
        pb.problem, pb.state = why, "invalid"
        return pb
    pb.sha256, pb.size = hashlib.sha256(data).hexdigest(), len(data)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        pb.problem, pb.state = "it is not UTF-8 text", "invalid"
        return pb
    fields, body, problem = parse(text, folder.name)
    if problem:
        pb.problem, pb.state = problem, "invalid"
        return pb
    pb.description, pb.tools, pb.report, pb.body = fields["description"], fields["tools"], fields["report"], body
    return pb


# ── the approval file ────────────────────────────────────────────────────────

def _key(source: str, name: str) -> str:
    return f"{source}:{name}"


def approvals() -> dict:
    """{"source:name": {"sha256", "at"}} from the approval file; {} when it is missing or unreadable."""
    try:
        with open(APPROVALS_PATH, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return {}
    got = raw.get("approved") if isinstance(raw, dict) else None
    if not isinstance(got, dict):
        return {}
    return {k: v for k, v in got.items()
            if isinstance(k, str) and isinstance(v, dict) and isinstance(v.get("sha256"), str) and SHA_RE.match(v["sha256"])}


def _save(change) -> None:
    import codec_jsonstore

    def mutate(data):
        data = data if isinstance(data, dict) else {}
        got = data.get("approved") if isinstance(data.get("approved"), dict) else {}
        change(got)
        return {"version": 1, "approved": got}
    codec_jsonstore.read_modify_write(APPROVALS_PATH, mutate)


def _audit(event: str, message: str, extra: dict) -> None:
    """The source and the last 8 hex of the hash only: never a playbook's name or text."""
    try:
        from codec_audit import log_event
        log_event(event, "codec-vibe", message, extra=extra)
    except Exception:
        pass


# ── the list ─────────────────────────────────────────────────────────────────

def _folders(root: Path, limit: int) -> list:
    try:
        entries = sorted(p for p in root.iterdir() if not p.name.startswith("."))
    except OSError:
        return []
    return entries[:limit]


def scan() -> list:
    """Every playbook, shipped first (in SHIPPED_ORDER), then the owner's by name, each with its state."""
    shipped = {p.name: _load(p, "shipped") for p in _folders(SHIPPED_DIR, MAX_OWNER_FOLDERS)}
    order = [n for n in SHIPPED_ORDER if n in shipped] + sorted(n for n in shipped if n not in SHIPPED_ORDER)
    out = [shipped[n] for n in order]
    for folder in _folders(OWNER_DIR, MAX_OWNER_FOLDERS):
        pb = _load(folder, "owner")
        if pb.name in shipped and not pb.problem:
            pb.problem, pb.state = "a shipped playbook has this name; rename the folder", "invalid"
        out.append(pb)
    got = approvals()
    for pb in out:
        if pb.problem:
            pb.state = "invalid"
            continue
        a = got.get(_key(pb.source, pb.name))
        if a is None:
            pb.state = "new"
        else:
            pb.approved_at = str(a.get("at") or "")
            pb.state = "approved" if a["sha256"] == pb.sha256 else "changed"
    return out


def find(source: str, name: str) -> Playbook:
    if source not in SOURCES or not isinstance(name, str) or not NAME_RE.match(name):
        raise PlaybookError(404, "No such playbook")
    pb = next((p for p in scan() if p.source == source and p.name == name), None)
    if pb is None:
        raise PlaybookError(404, "No such playbook")
    return pb


def read_text(source: str, name: str) -> dict:
    """The playbook and its whole file as text, for the owner to read before approving it."""
    pb = find(source, name)
    data, _why = _read_bytes(pb.path.parent)
    text = data.decode("utf-8", errors="replace") if data is not None else ""
    return {**pb.public(), "text": text}


def approve(source: str, name: str, sha256) -> dict:
    """The owner's click: store the hash he was shown. 409 when the file changed since, or it cannot be used."""
    pb = find(source, name)
    if pb.problem:
        raise PlaybookError(409, "This playbook cannot be used: " + pb.problem)
    if not isinstance(sha256, str) or sha256 != pb.sha256:
        raise PlaybookError(409, "The file changed since the page showed it. Read it again, then approve.")
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _save(lambda got: got.__setitem__(_key(source, name), {"sha256": pb.sha256, "at": at}))
    _audit("vibe_playbook_approved", "Vibe playbook approved", {"source": source, "sha8": pb.sha256[-8:]})
    return find(source, name).public()


def revoke(source: str, name: str) -> dict:
    pb = find(source, name)
    _save(lambda got: got.pop(_key(source, name), None))
    _audit("vibe_playbook_revoked", "Vibe playbook approval removed", {"source": source, "sha8": pb.sha256[-8:]})
    return find(source, name).public()


def offered() -> dict:
    """{name: Playbook} of the playbooks a run may use now: approved, and the file is the one approved."""
    if not approvals():                                 # nothing approved: no folder is read
        return {}
    return {pb.name: pb for pb in scan() if pb.state == "approved"}


def load(name: str, sha256: "str | None" = None) -> Playbook:
    """The playbook a run asks for, read again now: its bytes must hash to its approval now and, when given, to
    `sha256` (what the run listed at its start). PlaybookError otherwise."""
    pb = offered().get(name) if isinstance(name, str) else None
    if pb is None:
        raise PlaybookError(404, f"There is no approved playbook called '{str(name)[:40]}'.")
    if sha256 is not None and pb.sha256 != sha256:
        raise PlaybookError(409, f"The playbook '{name}' changed after this run started, so it is not loaded. "
                                 "The owner must approve it again.")
    return pb


# ── what the agent sees ──────────────────────────────────────────────────────

def listing(pbs) -> list:
    """The prompt's list lines (proto.playbook_line): at most MAX_LISTED, at most MAX_LIST_CHARS for the whole list."""
    out, used = [], 0
    for pb in pbs:
        line = proto.playbook_line(pb.name, pb.description)
        if len(out) >= MAX_LISTED or used + len(line) + 1 > MAX_LIST_CHARS:
            break
        out.append(line)
        used += len(line) + 1
    return out


def narrow(allowed, tools) -> tuple:
    """The run's tools cut to the playbook's list. finish, the todo block and the playbook tool stay; nothing is
    ever added."""
    keep = set(tools) | {"finish", proto.TODO, proto.PLAYBOOK}
    return tuple(t for t in allowed if t in keep)


def body_lines(pb: Playbook, arguments: str) -> list:
    """The steps with $ARGUMENTS replaced (the only substitution), as lines."""
    return pb.body.replace(ARGUMENTS, arguments).split("\n")


def is_protected(target) -> bool:
    """True when `target` (a path, existing or not) is inside a playbook folder or is the approval file. The agent's
    file tools refuse such a path (codec_vibe_projects.agent_write, agent_delete)."""
    real = os.path.realpath(str(target))
    for root in (OWNER_DIR, SHIPPED_DIR):
        r = os.path.realpath(str(root))
        if real == r or real.startswith(r + os.sep):
            return True
    a = os.path.realpath(str(APPROVALS_PATH))
    return real == a or real.startswith(a + ".")        # the file and its .lock
