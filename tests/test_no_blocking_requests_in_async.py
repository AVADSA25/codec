"""No blocking call inside an `async def` of the dashboard.

A blocking call there stops the event loop: every page, poll, chat stream and the
voice socket wait until it returns. The vision warmup did this at startup (fixed
2026-10-07, #446), and three photo routes did it while a picture was analysed
(webcam capture, image upload, vision). P-016 (Nov 2026) widened the scan from
`requests` to every call below, after a pasted link, a web search or a
non-stream reply froze the dashboard for up to 300 s. Put the call in
`asyncio.to_thread(...)`.

The scan reads each async def's own body. A call to a sync function defined in
the same file is followed one hop: that function's own body is checked too. A
nested function or a lambda counts where it is called, so passing it to
`asyncio.to_thread` is fine and calling it inline is reported (a lambda called in
place, `(lambda: ...)()`, is read as the caller's own body). A sync generator
is not followed (calling it runs nothing; StreamingResponse iterates it in a
worker thread). Two hops are not followed.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
FILES = [REPO / "codec_dashboard.py", *sorted((REPO / "routes").glob("*.py"))]
# Bare names used for the requests module (the original rule, kept).
REQUEST_NAMES = {"requests", "rq", "_requests"}
HTTP_VERBS = {"get", "post", "put", "delete", "patch", "head", "request"}
# module -> its blocking functions; None means every function of the module.
BLOCKING = {
    "requests": HTTP_VERBS,
    "httpx": HTTP_VERBS | {"stream", "Client"},
    "subprocess": None,
    "urllib.request": {"urlopen"},
    "socket": {"getaddrinfo"},
    "codec_llm": {"call"},
    # Cross-module calls that make one of the calls above.
    "codec_update": {"check_for_update", "download_and_verify"},
    "codec_search": {"search"},
    "codec_pdftext": {"extract_text"},
}
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _own_nodes(scope):
    """The nodes of `scope` itself; a nested function, lambda or class is its own scope."""
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, _SCOPES):
            stack.extend(ast.iter_child_nodes(node))


def _imports(scope) -> dict:
    """Names the imports of `scope` bind -> the dotted module or function they stand for."""
    out = {}
    for node in _own_nodes(scope):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                out[alias.asname or top] = alias.name if alias.asname else top
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                out[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return out


def _blocking_name(call: ast.Call, binds: dict) -> str | None:
    func, parts = call.func, []
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if not isinstance(func, ast.Name):
        return None
    parts = [func.id, *reversed(parts)]
    if parts[-1] == "urlopen":
        return "urlopen"
    head = binds.get(parts[0])
    if head is None and parts[0] in REQUEST_NAMES and len(parts) == 2 and parts[1] in HTTP_VERBS:
        return f"requests.{parts[1]}"
    qual = ".".join([head or parts[0], *parts[1:]])
    module, _, name = qual.rpartition(".")
    if module in BLOCKING and (BLOCKING[module] is None or name in BLOCKING[module]):
        return qual
    return None


def _is_generator(fn) -> bool:
    return any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in _own_nodes(fn))


def blocking_calls(path: Path, root: Path | None = None) -> list:
    """Every blocking call the async defs of `path` make on the event loop."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    rel = path.relative_to(root or REPO)
    hits = []

    def check(fn, binds, defs, body=None):
        for node in _own_nodes(body or fn):
            if not isinstance(node, ast.Call):
                continue
            name = _blocking_name(node, binds)
            if name:
                hits.append(f"{rel}:{node.lineno} in {fn.name}(): {name}")
                continue
            if isinstance(node.func, ast.Lambda):  # (lambda: ...)() runs right here: its body is fn's own
                check(fn, binds, defs, body=node.func)
                continue
            if isinstance(node.func, ast.Name) and node.func.id not in binds and node.func.id in defs:
                (helper, helper_binds), label = defs[node.func.id], f"{node.func.id}()"
            else:
                continue
            if _is_generator(helper):
                continue
            helper_binds = {**helper_binds, **_imports(helper)}
            for inner in _own_nodes(helper):
                if isinstance(inner, ast.Call):
                    name = _blocking_name(inner, helper_binds)
                    if name:
                        hits.append(f"{rel}:{inner.lineno} in {fn.name}() via {label} "
                                    f"(called at line {node.lineno}): {name}")

    def visit(scope, binds, defs):
        if not isinstance(scope, ast.ClassDef):  # a method cannot call another by bare name
            binds = {**binds, **_imports(scope)}
            local = {n.name: (n, binds) for n in _own_nodes(scope) if isinstance(n, ast.FunctionDef)}
            for n in _own_nodes(scope):  # f = lambda: ... is called like a def
                if isinstance(n, ast.Assign) and isinstance(n.value, ast.Lambda):
                    local.update({t.id: (n.value, binds) for t in n.targets if isinstance(t, ast.Name)})
            defs = {**defs, **local}
        if isinstance(scope, ast.AsyncFunctionDef):
            check(scope, binds, defs)
        for child in _own_nodes(scope):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                visit(child, binds, defs)

    visit(tree, {}, {})
    return hits


def test_no_async_handler_makes_a_blocking_call_on_the_event_loop():
    async_defs = sum(isinstance(n, ast.AsyncFunctionDef)
                     for f in FILES for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"))))
    assert async_defs >= 100, f"the scan found only {async_defs} async defs: wrong folder?"
    hits = [h for f in FILES for h in blocking_calls(f)]
    assert not hits, "blocking call on the event loop: " + ", ".join(hits)


CAUGHT = {
    "requests.post": ("import requests as rq2\nasync def h():\n    return rq2.post('http://x')\n",
                      "requests.post"),
    "bare rq.get": ("async def h():\n    return rq.get('http://x')\n", "requests.get"),
    "urlopen": ("from urllib.request import urlopen\nasync def h():\n    return urlopen('http://x')\n",
                "urlopen"),
    "urllib.request.urlopen": ("import urllib.request as ur\nasync def h():\n    return ur.urlopen('x')\n",
                               "urlopen"),
    "subprocess.run": ("import subprocess\nasync def h():\n    subprocess.run(['pgrep', 'x'])\n",
                       "subprocess.run"),
    "subprocess.check_output": ("from subprocess import check_output\nasync def h():\n"
                                "    check_output(['pm2', 'jlist'])\n", "subprocess.check_output"),
    "subprocess.Popen": ("import subprocess\nasync def h():\n    subprocess.Popen(['pbcopy'])\n",
                         "subprocess.Popen"),
    "httpx.get": ("import httpx as _httpx\nasync def h():\n    return _httpx.get('http://x')\n", "httpx.get"),
    "httpx.post": ("import httpx\nasync def h():\n    return httpx.post('http://x')\n", "httpx.post"),
    "httpx.Client": ("import httpx\nasync def h():\n    with httpx.Client() as c:\n        pass\n",
                     "httpx.Client"),
    "socket.getaddrinfo": ("import socket\nasync def h():\n    return socket.getaddrinfo('x', 80)\n",
                           "socket.getaddrinfo"),
    "codec_llm.call": ("import codec_llm\nasync def h(m):\n    return codec_llm.call(m)\n", "codec_llm.call"),
    "codec_update.check_for_update": ("async def h():\n    import codec_update\n"
                                      "    return codec_update.check_for_update()\n",
                                      "codec_update.check_for_update"),
    "codec_update.download_and_verify": ("import codec_update\nasync def h(i):\n"
                                         "    return codec_update.download_and_verify(i)\n",
                                         "codec_update.download_and_verify"),
    "codec_search.search": ("async def h(q):\n    from codec_search import search\n    return search(q)\n",
                            "codec_search.search"),
    "codec_pdftext.extract_text": ("import codec_pdftext\nasync def h(p):\n"
                                   "    return codec_pdftext.extract_text(p)\n", "codec_pdftext.extract_text"),
    "one hop: a module helper": ("import subprocess\ndef helper():\n    return subprocess.run(['x'])\n"
                                 "async def h():\n    return helper()\n", "via helper()"),
    "one hop: a nested def called inline": ("async def h():\n    import requests\n    def work():\n"
                                            "        return requests.get('x')\n    return work()\n",
                                            "via work()"),
    "a lambda called inline": ("import codec_llm\nasync def h(m):\n    return (lambda: codec_llm.call(m))()\n",
                               "in h(): codec_llm.call"),
    "one hop through a lambda called inline": ("import subprocess\ndef helper():\n    subprocess.run(['x'])\n"
                                               "async def h():\n    return (lambda: helper())()\n",
                                               "via helper()"),
    "a lambda bound to a name and called": ("import codec_llm\nasync def h(m):\n"
                                            "    f = lambda: codec_llm.call(m)\n    return f()\n", "via f()"),
    "an argument of to_thread runs on the loop": ("import asyncio, codec_llm\nasync def h(m):\n"
                                                  "    return await asyncio.to_thread(str, codec_llm.call(m))\n",
                                                  "codec_llm.call"),
    "an async method": ("import subprocess\nclass A:\n    async def h(self):\n        subprocess.run(['x'])\n",
                        "subprocess.run"),
}


@pytest.mark.parametrize("case", sorted(CAUGHT))
def test_the_scan_catches_each_blocking_call(tmp_path, case):
    source, expected = CAUGHT[case]
    bad = tmp_path / "bad.py"
    bad.write_text(source)
    hits = blocking_calls(bad, tmp_path)
    assert len(hits) == 1 and expected in hits[0], hits


SAFE = {
    "to_thread with the function": ("import asyncio, subprocess\nasync def h():\n"
                                    "    return await asyncio.to_thread(subprocess.run, ['x'], timeout=3)\n"),
    "to_thread with a lambda": ("import asyncio, codec_llm\nasync def h(m):\n"
                                "    return await asyncio.to_thread(lambda: codec_llm.call(m))\n"),
    "to_thread with a lambda bound to a name": ("import asyncio, codec_llm\nasync def h(m):\n"
                                                "    f = lambda: codec_llm.call(m)\n"
                                                "    return await asyncio.to_thread(f)\n"),
    "to_thread with a nested def": ("import asyncio, requests\nasync def h():\n    def work():\n"
                                    "        return requests.get('x')\n    return await asyncio.to_thread(work)\n"),
    "a sync generator": ("import codec_llm\ndef gen(m):\n    yield codec_llm.call(m)\n"
                         "async def h(m):\n    return Stream(gen(m))\n"),
    "two hops": ("import subprocess\ndef inner():\n    subprocess.run(['x'])\ndef outer():\n    inner()\n"
                 "async def h():\n    outer()\n"),
    "httpx.AsyncClient": ("import httpx\nasync def h():\n    async with httpx.AsyncClient() as c:\n"
                          "        return await c.get('x')\n"),
    "an asyncio subprocess": ("import asyncio\nasync def h():\n"
                              "    p = await asyncio.create_subprocess_exec('ls', stdout=asyncio.subprocess.PIPE)\n"
                              "    return await p.communicate()\n"),
    "loop.getaddrinfo": ("import asyncio\nasync def h():\n"
                         "    return await asyncio.get_running_loop().getaddrinfo('x', 80)\n"),
    "a sync def": "import subprocess\ndef h():\n    subprocess.run(['x'])\n",
}


@pytest.mark.parametrize("case", sorted(SAFE))
def test_the_scan_passes_the_offloaded_and_async_forms(tmp_path, case):
    ok = tmp_path / "ok.py"
    ok.write_text(SAFE[case])
    assert blocking_calls(ok, tmp_path) == []


def test_a_hit_names_the_file_line_function_and_helper(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("import subprocess\ndef helper():\n    subprocess.run(['x'])\n"
                   "async def h():\n    helper()\n    subprocess.run(['y'])\n")
    assert sorted(blocking_calls(bad, tmp_path)) == [
        "bad.py:3 in h() via helper() (called at line 5): subprocess.run",
        "bad.py:6 in h(): subprocess.run",
    ]
