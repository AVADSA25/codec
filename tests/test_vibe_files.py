"""Vibe page helpers (static/vibe-files.js): files out of an AI reply, names, preview inlining.

The helpers are plain functions on strings, so Node runs them without a browser.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
JS = REPO / "static" / "vibe-files.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def run(expr: str):
    script = f"const V = require({json.dumps(str(JS))}); console.log(JSON.stringify({expr}));"
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip())


def blocks(text: str):
    return run(f"V.parseBlocks({json.dumps(text)})")


TWO_FILES = (
    "Here is a small page.\n\n"
    "```html file:index.html\n<!DOCTYPE html>\n<link rel=\"stylesheet\" href=\"style.css\">\n<h1>Hi</h1>\n```\n\n"
    "```css file:style.css\nh1 { color: red; }\n```\n\nDone.\n"
)


def test_reads_every_complete_block_with_its_path_language_and_code():
    b = blocks(TWO_FILES)
    assert [(x["path"], x["lang"], x["closed"]) for x in b] == [("index.html", "html", True), ("style.css", "css", True)]
    assert b[1]["code"] == "h1 { color: red; }\n"
    assert b[0]["code"].startswith("<!DOCTYPE html>\n") and b[0]["code"].endswith("<h1>Hi</h1>\n")


def test_a_reply_that_is_still_streaming_gives_the_open_block_so_far():
    text = TWO_FILES[:TWO_FILES.index("h1 { color: red; }") + 8]
    b = blocks(text)
    assert [x["path"] for x in b] == ["index.html", "style.css"]
    assert b[0]["closed"] is True and b[1]["closed"] is False
    assert b[1]["code"].startswith("h1 {")


def test_an_opening_line_that_is_still_arriving_makes_no_block_yet():
    assert blocks("Sure.\n\n```ht") == []
    assert blocks("Sure.\n\n```html file:ind") == []
    assert [x["path"] for x in blocks("Sure.\n\n```html file:index.html\n")] == ["index.html"]


def test_a_partial_closing_fence_is_not_shown_as_code():
    b = blocks("```python\nprint(1)\n``")
    assert b[0]["code"] == "print(1)" and b[0]["closed"] is False


@pytest.mark.parametrize("info,path,lang", [
    ("html file:index.html", "index.html", "html"),
    ("file:index.html", "index.html", "html"),
    ("index.html", "index.html", "html"),
    ("html:index.html", "index.html", "html"),
    ('js title="app.js"', "app.js", "javascript"),
    ("css filename=styles/main.css", "styles/main.css", "css"),
    ("python main.py", "main.py", "python"),
    ("typescript path:src/app.ts", "src/app.ts", "typescript"),
    ("html", "", "html"),
    ("", "", ""),
    ("sh", "", "bash"),
    ("tsx", "", "typescript"),
])
def test_odd_ways_of_naming_a_file_are_understood(info, path, lang):
    got = run(f"V.parseInfo({json.dumps(info)})")
    assert (got["path"], got["lang"]) == (path, lang)


def test_a_file_name_in_the_first_line_is_used_and_removed_from_the_code():
    b = blocks("```html\n<!-- file: index.html -->\n<!DOCTYPE html>\n<p>x</p>\n```\n")
    assert b[0]["path"] == "index.html" and b[0]["code"] == "<!DOCTYPE html>\n<p>x</p>\n"
    b = blocks("```js\n// file: lib/util.js\nexport const a = 1\n```\n")
    assert b[0]["path"] == "lib/util.js" and b[0]["code"] == "export const a = 1\n"
    b = blocks("```python\n# note: not a file marker\nprint(1)\n```\n")
    assert b[0]["path"] == "" and b[0]["code"].startswith("# note")


def test_a_longer_fence_can_hold_a_shorter_one_and_tildes_work():
    b = blocks("````markdown file:README.md\n# Title\n```bash\nnpm start\n```\n````\n")
    assert len(b) == 1 and b[0]["path"] == "README.md" and "```bash" in b[0]["code"]
    b = blocks("~~~python\nprint('a')\n~~~\n")
    assert b[0]["lang"] == "python" and b[0]["closed"] is True


def test_inline_triple_backticks_in_a_sentence_are_not_a_fence():
    assert blocks("Use ```js``` for code and ```py``` too.\n") == []


@pytest.mark.parametrize("reply,active,expected", [
    # one unnamed block of the open file's language replaces the open file
    ("```html\n<p>x</p>\n```\n", "index.html", ["index.html"]),
    # the language does not fit the open file: named after the language
    ("```html\n<p>x</p>\n```\n", "style.css", ["index.html"]),
    ("```python\nprint(1)\n```\n", None, ["main.py"]),
    # two unnamed blocks: the one that fits takes the open file, the other is named by language
    ("```html\n<p>x</p>\n```\n```css\np{}\n```\n", "index.html", ["index.html", "style.css"]),
    # a named block keeps its name, an unnamed one does not take a name that is already used
    ("```html file:index.html\n<p>a</p>\n```\n```html\n<p>b</p>\n```\n", "index.html", ["index.html", "index-2.html"]),
    # no language and several blocks: never guessed onto the open file
    ("```\none\n```\n```\ntwo\n```\n", "index.html", ["notes.txt", "notes-2.txt"]),
])
def test_files_the_model_did_not_name_get_a_sensible_path(reply, active, expected):
    got = run(f"V.resolvePaths(V.parseBlocks({json.dumps(reply)}), {json.dumps(active)}).map(b => b.path)")
    assert got == expected


def test_the_explanation_around_the_code_is_kept_and_the_code_is_not():
    text = run(f"V.textOutsideBlocks({json.dumps(TWO_FILES)})")
    assert text == "Here is a small page.\n\nDone."
    assert run('V.textOutsideBlocks("```js\\nwip")') == ""


def test_the_preview_gets_local_css_and_js_inlined_and_leaves_everything_else():
    html = ('<!DOCTYPE html><head><link rel="stylesheet" href="./style.css">'
            '<link rel="stylesheet" href="https://cdn.example/x.css"><link rel="icon" href="a.png">'
            '<link href="missing.css" rel="stylesheet"></head>'
            '<body><script src="app.js"></script><script type="module" src="/lib/m.js?x=1"></script>'
            '<script src="https://cdn.example/lib.js"></script></body>')
    files = {"style.css": "h1{color:red}", "app.js": "console.log('</script>')", "lib/m.js": "export const m=1"}
    out = run(f"V.inlineAssets({json.dumps(html)}, {json.dumps(files)})")
    assert "<style>\nh1{color:red}\n</style>" in out
    assert "console.log('<\\/script>')" in out and 'src="app.js"' not in out
    assert '<script type="module">\nexport const m=1\n</script>' in out
    for kept in ('href="https://cdn.example/x.css"', 'href="a.png"', 'href="missing.css"', 'src="https://cdn.example/lib.js"'):
        assert kept in out


def test_names_extensions_and_editor_languages():
    got = run("[V.defaultName('html'), V.defaultName('JS'), V.defaultName('weird'), V.extFor('python'), "
              "V.monacoLang('src/a.tsx'), V.monacoLang('notes'), V.monacoLang('run.sh'), V.langOfPath('x.py'), V.langOfPath('x')]")
    assert got == ["index.html", "script.js", "notes.txt", ".py", "typescript", "plaintext", "shell", "python", ""]
