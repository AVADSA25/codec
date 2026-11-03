"""P-091: guard test for interpreter sinks in the built-in skills.

skills/.manifest.json pins each built-in skill's bytes, and a pinned skill
skips the AST gate when it loads. The pin proves the bytes did not change, not
that the code is safe, so nothing reads a pinned skill's commands again after
its hash is approved. This test does, in CI.

It parses every skills/*.py and fails when text reaches the command of a
subprocess, os.system / os.popen / os.exec* / os.spawn* or asyncio subprocess
call (or the stdin of an osascript or shell call) through an f-string,
% formatting, .format(), "+" or str.join, unless every interpolated piece is a
literal or goes through codec_applescript (escape, notification_script, or a
function in the same file that only returns one of them). Names are followed
to their assignments in the same function and module, and a parameter that
reaches a command is followed to the calls of that function in the same file.
Not followed: methods called through self, and text handed to a helper in
another module.

Reviewed exceptions are in tests/skill_sink_allowlist.json: file, the line
text where the piece enters the string, and the reason it is safe (or the
known gap). Design: docs/P091-SKILL-SINK-GUARD-DESIGN.md.
"""
import ast
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SKILLS = REPO / "skills"
ALLOWLIST = Path(__file__).resolve().parent / "skill_sink_allowlist.json"
# Audit 2026-10-25, GP05-6: osascript call sites across the repo.
AUDIT_SITES, AUDIT_FILES = 71, 34

_SUBPROCESS = {"run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput"}
_OS_FIRST = {"system", "popen"}
_OS_ALL = {"execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp", "execvpe",
           "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe",
           "posix_spawn", "posix_spawnp"}
_ASYNCIO_FIRST = {"create_subprocess_shell"}
_ASYNCIO_ALL = {"create_subprocess_exec"}
_STDIN_PROGRAMS = {"osascript", "bash", "sh", "zsh"}
_HELPER_MODULE = "codec_applescript"
_HELPER_FUNCS = {"escape", "notification_script"}
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
_NESTED = _SCOPES + (ast.ClassDef,)
_NOT_SOURCE = {"__pycache__", "node_modules", "venv", "site-packages", "build", "dist"}


@dataclass(frozen=True)
class Finding:
    file: str        # "skills/<name>.py"
    lineno: int      # line of the piece that is not escaped
    line: str        # that line, stripped: the allowlist key
    call_line: int   # line of the sink call it reaches
    program: str     # first word of the command ("osascript", "open", ...) or ""


@dataclass
class Census:
    sites: list = field(default_factory=list)      # (file, call line, program)
    findings: list = field(default_factory=list)   # Finding, one per piece
    escaped: int = 0                               # pieces that go through the helper


class _Module:
    def __init__(self, path, rel):
        self.rel = rel
        src = path.read_text(encoding="utf-8")
        self.lines = src.splitlines()
        self.tree = ast.parse(src, filename=str(path))
        self.parent = {}
        for node in ast.walk(self.tree):
            for child in ast.iter_child_nodes(node):
                self.parent[child] = node
        self.mod_alias, self.func_alias = {}, {}
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    self.mod_alias[a.asname or a.name.split(".")[0]] = a.name
            elif isinstance(node, ast.ImportFrom) and node.module:
                for a in node.names:
                    self.func_alias[a.asname or a.name] = (node.module, a.name)
        defs = {}
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defs.setdefault(node.name, []).append(node)
        self.wrappers = {n for n, ds in defs.items() if all(self._is_wrapper(d) for d in ds)}
        self.helper_hits = set()

    # --- the P-007 helper ------------------------------------------------
    def _is_helper_direct(self, node):
        if not isinstance(node, ast.Call):
            return False
        f = node.func
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
            return self.mod_alias.get(f.value.id) == _HELPER_MODULE and f.attr in _HELPER_FUNCS
        if isinstance(f, ast.Name):
            return self.func_alias.get(f.id) in {(_HELPER_MODULE, n) for n in _HELPER_FUNCS}
        return False

    def _is_wrapper(self, fdef):
        rets = [n for n in ast.walk(fdef) if isinstance(n, ast.Return)]
        return bool(rets) and all(r.value is not None and self._is_helper_direct(r.value) for r in rets)

    def _is_helper(self, node):
        return self._is_helper_direct(node) or (
            isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in self.wrappers)

    # --- scopes and bindings ---------------------------------------------
    def scope_of(self, node):
        p = self.parent.get(node)
        while p is not None and not isinstance(p, _SCOPES):
            p = self.parent.get(p)
        return p if p is not None else self.tree

    def _walk_scope(self, scope):
        stack = [scope.body] if isinstance(scope, ast.Lambda) else list(scope.body)
        while stack:
            n = stack.pop()
            yield n
            if not isinstance(n, _NESTED):
                stack.extend(ast.iter_child_nodes(n))

    @staticmethod
    def _binds(target, name):
        return any(isinstance(x, ast.Name) and x.id == name for x in ast.walk(target))

    def bindings(self, name, scope):
        """(kind, expr, scope) for each place `name` is bound, looking in the
        enclosing scopes when the nearest one does not bind it. kind is value,
        concat (+=), for (expr = the iterable), param (expr = the def) or opaque."""
        while True:
            found, is_global = [], False
            if isinstance(scope, _SCOPES):
                a = scope.args
                params = a.posonlyargs + a.args + a.kwonlyargs + [x for x in (a.vararg, a.kwarg) if x]
                if any(p.arg == name for p in params):
                    found.append(("param", scope, scope))
            for n in self._walk_scope(scope):
                if isinstance(n, ast.Global) and name in n.names:
                    is_global = True
                elif isinstance(n, ast.Assign):
                    for t in n.targets:
                        if isinstance(t, ast.Name) and t.id == name:
                            found.append(("value", n.value, scope))
                        elif self._binds(t, name):
                            found.append(("opaque", n, scope))
                elif isinstance(n, ast.AugAssign) and self._binds(n.target, name):
                    found.append(("concat" if isinstance(n.op, ast.Add) else "opaque", n.value, scope))
                elif isinstance(n, ast.AnnAssign) and self._binds(n.target, name) and n.value is not None:
                    found.append(("value", n.value, scope))
                elif isinstance(n, ast.NamedExpr) and n.target.id == name:
                    found.append(("value", n.value, scope))
                elif isinstance(n, (ast.For, ast.AsyncFor, ast.comprehension)) and self._binds(n.target, name):
                    plain = isinstance(n.target, ast.Name)
                    found.append(("for" if plain else "opaque", n.iter, scope))
                elif isinstance(n, (ast.With, ast.AsyncWith)):
                    if any(i.optional_vars is not None and self._binds(i.optional_vars, name) for i in n.items):
                        found.append(("opaque", n, scope))
                elif isinstance(n, ast.ExceptHandler) and n.name == name:
                    found.append(("opaque", n, scope))
                elif isinstance(n, (ast.Import, ast.ImportFrom)):
                    if any((a.asname or a.name.split(".")[0]) == name for a in n.names):
                        found.append(("opaque", n, scope))
                elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == name:
                    found.append(("opaque", n, scope))
            if is_global and scope is not self.tree:
                scope = self.tree
                continue
            if found or scope is self.tree:
                return found
            scope = self.scope_of(scope)

    # --- what counts as built text -----------------------------------------
    @staticmethod
    def parts(e):
        """The parts of a "+", % or .format() expression or a "sep".join(x)
        call, or None when e is none of those (f-strings are handled apart)."""
        if isinstance(e, ast.BinOp) and isinstance(e.op, ast.Add):
            leaves, stack = [], [e]
            while stack:
                n = stack.pop()
                if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add):
                    stack += [n.right, n.left]
                else:
                    leaves.append(n)
            return leaves
        if isinstance(e, ast.BinOp) and isinstance(e.op, ast.Mod):
            r = e.right
            rest = r.elts if isinstance(r, ast.Tuple) else r.values if isinstance(r, ast.Dict) else [r]
            return [e.left] + list(rest)
        if isinstance(e, ast.Call) and isinstance(e.func, ast.Attribute):
            if e.func.attr == "format":
                return ([e.func.value] + [a.value if isinstance(a, ast.Starred) else a for a in e.args]
                        + [k.value for k in e.keywords])
            if (e.func.attr == "join" and isinstance(e.func.value, ast.Constant)
                    and isinstance(e.func.value.value, str) and len(e.args) == 1):
                return [e.args[0]]
        return None

    def piece_ok(self, e, scope, seen=frozenset()):
        """True when the piece is a literal, goes through the helper, or is
        built only from such pieces."""
        if isinstance(e, ast.Constant):
            return True
        if self._is_helper(e):
            self.helper_hits.add((e.lineno, e.col_offset))
            return True
        if isinstance(e, ast.JoinedStr):
            return all(self.piece_ok(v.value, scope, seen) for v in e.values if isinstance(v, ast.FormattedValue))
        if isinstance(e, ast.IfExp):
            return self.piece_ok(e.body, scope, seen) and self.piece_ok(e.orelse, scope, seen)
        if isinstance(e, (ast.List, ast.Tuple)):
            return all(self.piece_ok(x, scope, seen) for x in e.elts)
        parts = self.parts(e)
        if parts is not None:
            return all(self.piece_ok(x, scope, seen) for x in parts)
        if isinstance(e, ast.Name):
            key = (e.id, id(scope))
            if key in seen:
                return True
            seen = seen | {key}
            found = self.bindings(e.id, scope)
            return bool(found) and all(
                kind in ("value", "concat", "for") and self.piece_ok(expr, sc, seen)
                for kind, expr, sc in found)
        return False

    def bad_pieces(self, e, scope):
        """The pieces of a built string that are not escaped."""
        if isinstance(e, ast.JoinedStr):
            return [v.value for v in e.values
                    if isinstance(v, ast.FormattedValue) and not self.piece_ok(v.value, scope)]
        if isinstance(e, ast.IfExp):
            return self.bad_pieces(e.body, scope) + self.bad_pieces(e.orelse, scope)
        if isinstance(e, (ast.List, ast.Tuple)):
            return [b for x in e.elts for b in self.bad_pieces(x, scope)]
        parts = self.parts(e)
        if parts is not None:
            return [b for x in parts for b in self.bad_pieces(x, scope)]
        return [] if self.piece_ok(e, scope) else [e]

    def elem(self, e, scope, seen, out):
        """Collect every unescaped piece of built text that reaches a command."""
        if isinstance(e, (ast.List, ast.Tuple)):
            for x in e.elts:
                self.elem(x, scope, seen, out)
        elif isinstance(e, ast.Starred):
            self.elem(e.value, scope, seen, out)
        elif isinstance(e, ast.IfExp):
            self.elem(e.body, scope, seen, out)
            self.elem(e.orelse, scope, seen, out)
        elif isinstance(e, (ast.ListComp, ast.GeneratorExp)):
            self.elem(e.elt, scope, seen, out)
        elif isinstance(e, ast.Call) and isinstance(e.func, ast.Attribute) and e.func.attr == "encode":
            self.elem(e.func.value, scope, seen, out)
        elif isinstance(e, ast.BinOp) and isinstance(e.op, ast.Add) and any(
                isinstance(x, (ast.List, ast.Tuple, ast.ListComp)) for x in self.parts(e)):
            for x in self.parts(e):  # an argv list joined to another list
                self.elem(x, scope, seen, out)
        elif isinstance(e, ast.JoinedStr) or self.parts(e) is not None:
            out.extend(self.bad_pieces(e, scope))
        elif isinstance(e, ast.Name):
            key = (e.id, id(scope))
            if key in seen:
                return
            seen = seen | {key}
            for kind, expr, sc in self.bindings(e.id, scope):
                if kind in ("value", "for"):
                    self.elem(expr, sc, seen, out)
                elif kind == "concat":
                    out.extend(self.bad_pieces(expr, sc))
                elif kind == "param":
                    self._follow_param(expr, e.id, seen, out)

    def _follow_param(self, fdef, pname, seen, out):
        """A parameter that reaches a command: check what this file passes."""
        if isinstance(fdef, ast.Lambda):
            return
        positional = [p.arg for p in fdef.args.posonlyargs + fdef.args.args]
        idx = positional.index(pname) if pname in positional else None
        for call in ast.walk(self.tree):
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                    and call.func.id == fdef.name):
                continue
            arg = None
            if idx is not None and idx < len(call.args) and not any(
                    isinstance(x, ast.Starred) for x in call.args[:idx + 1]):
                arg = call.args[idx]
            for k in call.keywords:
                if k.arg == pname:
                    arg = k.value
            if arg is not None:
                self.elem(arg, self.scope_of(call), seen, out)

    # --- sinks -------------------------------------------------------------
    def sink(self, call):
        """(command expressions, stdin expression) for a sink call, else None."""
        f, mod, name = call.func, None, None
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
            mod, name = self.mod_alias.get(f.value.id), f.attr
        elif isinstance(f, ast.Name) and f.id in self.func_alias:
            mod, name = self.func_alias[f.id]
        stdin = next((k.value for k in call.keywords if k.arg == "input"), None)
        if (mod == "subprocess" and name in _SUBPROCESS or mod == "os" and name in _OS_FIRST
                or mod == "asyncio" and name in _ASYNCIO_FIRST):
            if call.args:
                return [call.args[0]], stdin
            return [k.value for k in call.keywords if k.arg in ("args", "cmd")], stdin
        if mod == "os" and name in _OS_ALL or mod == "asyncio" and name in _ASYNCIO_ALL:
            return list(call.args), stdin
        return None

    def program(self, e, scope, depth=0):
        """Name of the program a command starts with, '' when unknown."""
        if depth > 6:
            return ""
        if isinstance(e, ast.Constant) and isinstance(e.value, str):
            words = e.value.split()
            return words[0].rsplit("/", 1)[-1] if words else ""
        if isinstance(e, ast.JoinedStr) and e.values:
            return self.program(e.values[0], scope, depth + 1)
        if isinstance(e, (ast.List, ast.Tuple)) and e.elts:
            return self.program(e.elts[0], scope, depth + 1)
        if isinstance(e, ast.BinOp) and isinstance(e.op, ast.Add):
            return self.program(self.parts(e)[0], scope, depth + 1)
        if isinstance(e, ast.Name):
            for kind, expr, sc in self.bindings(e.id, scope):
                if kind == "value":
                    name = self.program(expr, sc, depth + 1)
                    if name:
                        return name
        return ""

    def scan(self, census):
        found = {}
        for call in ast.walk(self.tree):
            got = self.sink(call) if isinstance(call, ast.Call) else None
            if got is None:
                continue
            cmd, stdin = got
            scope = self.scope_of(call)
            prog = self.program(cmd[0], scope) if cmd else ""
            exprs = cmd + ([stdin] if stdin is not None and prog in _STDIN_PROGRAMS else [])
            census.sites.append((self.rel, call.lineno, prog))
            out = []
            for e in exprs:
                self.elem(e, scope, frozenset(), out)
            for p in out:
                found.setdefault((p.lineno, p.col_offset), Finding(
                    self.rel, p.lineno, self.lines[p.lineno - 1].strip(), call.lineno, prog))
        census.findings.extend(found.values())
        census.escaped += len(self.helper_hits)


def scan_dir(directory):
    census = Census()
    for path in sorted(Path(directory).glob("*.py")):
        _Module(path, f"skills/{path.name}").scan(census)
    return census


def _repo_osascript_sites():
    """osascript call sites across the repo outside tests/, counted the way
    the audit counted them, so today's figure sits next to its 71."""
    sites, files = 0, 0
    for path in sorted(REPO.rglob("*.py")):
        rel = path.relative_to(REPO).parts
        if rel[0] == "tests" or any(p.startswith(".") or p in _NOT_SOURCE for p in rel):
            continue
        census = Census()
        try:
            _Module(path, "/".join(rel)).scan(census)
        except (SyntaxError, UnicodeDecodeError, ValueError):
            continue
        n = sum(1 for _, _, prog in census.sites if prog == "osascript")
        sites, files = sites + n, files + bool(n)
    return sites, files


def _allowlist():
    return json.loads(ALLOWLIST.read_text(encoding="utf-8"))["sites"]


def _unreviewed(census):
    allowed = {(e["file"], e["line"]) for e in _allowlist()}
    return [f for f in census.findings if (f.file, f.line) not in allowed]


def _explain(findings):
    one = {(f.file, f.lineno): f for f in findings}.values()  # one note per line
    return "\n".join(
        f"{f.file}:{f.lineno} reaches the {f.program or 'command'} call on line {f.call_line} "
        f"through formatting, not codec_applescript:\n    {f.line}\n"
        "  Escape it with codec_applescript.escape, pass it as its own argv item, or after review add\n  "
        + json.dumps({"file": f.file, "line": f.line, "reason": "<why this is safe>"}, ensure_ascii=False)
        + "\n  to tests/skill_sink_allowlist.json"
        for f in one)


def test_every_skill_sink_is_escaped_or_reviewed(capsys):
    census = scan_dir(SKILLS)
    osa = [s for s in census.sites if s[2] == "osascript"]
    repo_sites, repo_files = _repo_osascript_sites()
    new = _unreviewed(census)
    built = {(f.file, f.lineno) for f in census.findings}
    with capsys.disabled():
        print(f"\n[P-091] osascript call sites today: {len(osa)} in {len({s[0] for s in osa})} skill files, "
              f"{repo_sites} in {repo_files} files repo-wide (audit 2026-10-25: {AUDIT_SITES} in "
              f"{AUDIT_FILES} files). Skill sinks: {len(census.sites)} calls, {census.escaped} pieces "
              f"through codec_applescript, {len(built)} lines with built text not escaped, "
              f"{len(built - {(f.file, f.lineno) for f in new})} of them reviewed in "
              f"tests/skill_sink_allowlist.json, {len({(f.file, f.lineno) for f in new})} new.")
    assert osa, "the scan found no osascript call in skills/: the scanner is broken"
    assert not new, "\n" + _explain(new)


def test_allowlist_entries_are_explained_and_still_in_the_code():
    entries = _allowlist()
    for e in entries:
        assert set(e) == {"file", "line", "reason"}, e
        assert e["file"].startswith("skills/") and e["line"].strip() == e["line"] and e["line"], e
        assert len(e["reason"].split()) >= 4, f"give a real reason: {e}"
    keys = [(e["file"], e["line"]) for e in entries]
    assert len(keys) == len(set(keys)), "duplicate allowlist entries"
    live = {(f.file, f.line) for f in scan_dir(SKILLS).findings}
    stale = [k for k in keys if k not in live]
    assert not stale, f"these lines are no longer a built sink; remove them from the allowlist: {stale}"


_NEW_SINK = '''

def _quit_app(app_name):
    subprocess.run(["osascript", "-e", f'tell application "{app_name}" to quit'])
'''


def test_a_new_unquoted_osascript_line_in_a_pinned_skill_fails(tmp_path):
    src = (SKILLS / "volume.py").read_bytes()
    pinned = json.loads((SKILLS / ".manifest.json").read_text(encoding="utf-8"))["skills"]
    assert pinned["volume.py"] == hashlib.sha256(src).hexdigest(), "volume.py is not a pinned skill"
    copy = tmp_path / "skills"
    copy.mkdir()
    (copy / "volume.py").write_bytes(src)
    assert _unreviewed(scan_dir(copy)) == []  # its reviewed lines stay allowed in the copy
    (copy / "volume.py").write_bytes(src + _NEW_SINK.encode())
    new = _unreviewed(scan_dir(copy))
    assert [(f.file, f.line, f.program) for f in new] == [(
        "skills/volume.py",
        'subprocess.run(["osascript", "-e", f\'tell application "{app_name}" to quit\'])',
        "osascript")]
    assert "tests/skill_sink_allowlist.json" in _explain(new)


_HEAD = "import os\nimport subprocess\nimport codec_applescript\n\n"

FLAGGED = {
    "inline f-string": '''
def f(name):
    subprocess.run(["osascript", "-e", f'tell application "{name}" to quit'])''',
    "f-string in a variable": '''
def f(name):
    s = f'tell application "{name}" to quit'
    subprocess.run(["osascript", "-e", s])''',
    "% format": '''
def f(name):
    subprocess.run(["osascript", "-e", 'tell application "%s" to quit' % name])''',
    ".format": '''
def f(name):
    subprocess.run(["osascript", "-e", 'tell application "{}" to quit'.format(name)])''',
    "concatenation": '''
def f(name):
    subprocess.run(["osascript", "-e", 'tell application "' + name + '" to quit'])''',
    "+= onto a script": '''
def f(name):
    s = 'tell application "'
    s += name
    subprocess.run(["osascript", "-e", s])''',
    "str.join": '''
def f(name):
    subprocess.run(["bash", "-c", " ".join(["say", name])])''',
    "command list in a variable": '''
def f(name):
    cmd = ["osascript", "-e", f'tell application "{name}" to quit']
    subprocess.Popen(cmd)''',
    "wrapper parameter": '''
def _osa(script):
    return subprocess.run(["osascript", "-e", script])
def run(task):
    _osa(f'display dialog "{task}"')''',
    "loop over built scripts": '''
def f(name):
    scripts = [f'tell application "{name}" to quit']
    for s in scripts:
        subprocess.run(["osascript", "-e", s])''',
    "os.system": '''
def f(text):
    os.system(f"say {text}")''',
    "aliased module": '''
import subprocess as sp
def f(url):
    sp.call("open " + url, shell=True)''',
    "from-import": '''
from subprocess import check_output
def f(x):
    check_output(["bash", "-c", "echo %s" % x])''',
    "osascript stdin": '''
def f(x):
    subprocess.run(["osascript", "-"], input=f'display dialog "{x}"'.encode())''',
    "escaped then changed": '''
def f(x):
    safe = codec_applescript.escape(x).replace("a", "b")
    subprocess.run(["osascript", "-e", f'display dialog "{safe}"'])''',
}

PASSES = {
    "inline escape": '''
def f(name):
    subprocess.run(["osascript", "-e", f'tell application "{codec_applescript.escape(name)}" to quit'])''',
    "escape in a variable": '''
def f(name):
    safe = codec_applescript.escape(name)
    subprocess.run(["osascript", "-e", f'tell application "{safe}" to quit'])''',
    "from-import alias": '''
from codec_applescript import escape as esc
def f(name):
    subprocess.run(["osascript", "-e", 'tell application "' + esc(name) + '" to quit'])''',
    "wrapper in the same file": '''
def _esc(text):
    return codec_applescript.escape(text)
def f(name):
    subprocess.run(["osascript", "-e", f'tell application "{_esc(name)}" to quit'])''',
    "notification_script": '''
def f(body):
    subprocess.run(["osascript", "-e", codec_applescript.notification_script(body, "CODEC")])''',
    "literal pieces only": '''
def f(down):
    subprocess.run(["osascript", "-e", f'tell application "System Events" to key code {121 if down else 116}'])''',
    "plain argv items": '''
def f(app_name, url):
    subprocess.run(["open", "-a", app_name, url])''',
    "constant script": '''
S = 'tell application "Finder" to activate'
def f():
    subprocess.run(["osascript", "-e", S])''',
    "f-string kept out of the command": '''
def f(x):
    r = subprocess.run(["pbpaste"], capture_output=True, text=True)
    return f"{x}: {r.stdout}"''',
}


def _scan_source(tmp_path, body):
    d = tmp_path / "skills"
    d.mkdir()
    (d / "snippet.py").write_text(_HEAD + body.lstrip("\n") + "\n", encoding="utf-8")
    return scan_dir(d)


@pytest.mark.parametrize("case", sorted(FLAGGED))
def test_built_command_text_is_flagged(tmp_path, case):
    census = _scan_source(tmp_path, FLAGGED[case])
    assert len(census.findings) == 1, census.findings


@pytest.mark.parametrize("case", sorted(PASSES))
def test_escaped_or_plain_command_text_passes(tmp_path, case):
    census = _scan_source(tmp_path, PASSES[case])
    assert census.sites and census.findings == []
