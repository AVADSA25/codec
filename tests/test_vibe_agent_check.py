"""Vibe agent: the cheap check of a file after an edit (codec_vibe_agent_check, P-045).

Pure text in, a note or None out: no files, no programs, no network.
"""
from __future__ import annotations

import sys
import time

import pytest

import codec_vibe_agent_check as vc

FIRM = vc.FIRM_TAIL


def note(path, text):
    return vc.check_file(path, text)


# ── Python ──

def test_python_that_does_not_parse_gets_a_note_with_file_line_and_the_line_itself():
    n = note("src/app.py", "def total(prices)\n    return sum(prices)\n")
    assert n and not n.advisory and n.line == 1
    assert n.text.startswith(f"Check: src/app.py does not parse as Python {sys.version_info.major}.{sys.version_info.minor}: line 1:")
    assert "   1| def total(prices)" in n.text and n.text.endswith(FIRM)
    assert n.short == "Python syntax error at line 1"
    unclosed = note("app.py", "x = 1\nprint((1, 2\n\ny = 3\n")
    assert unclosed.line == 2 and "was never closed" in unclosed.text


def test_clean_python_gets_no_note_even_with_a_warning_or_a_bom():
    assert note("app.py", "def total(prices):\n    return sum(prices)\n") is None
    assert note("app.py", "pattern = '\\d+'\n") is None                 # an invalid escape only warns
    assert note("app.py", "\ufeffx = 1\n") is None
    assert note("app.py", "") is None


# ── JSON ──

def test_json_that_does_not_load_names_line_and_column():
    n = note("package.json", '{\n  "name": "shop"\n  "version": "1.0.0"\n}\n')
    assert n and n.line == 3 and "line 3, column 3" in n.text and "Expecting ',' delimiter" in n.text
    assert '   3| "version": "1.0.0"' in n.text and n.short == "JSON error at line 3"
    assert note("package.json", '{"name": "shop", "port": 8000}\n') is None


def test_strict_json_refuses_a_trailing_comma_but_jsonc_names_allow_comments_and_trailing_commas():
    trailing = '{\n  "a": 1,\n}\n'
    strict = note("package.json", trailing)                     # Python 3.13 names the comma's line, older the brace's
    assert strict and strict.line in (2, 3) and strict.short.startswith("JSON error at line")
    jsonc = '{\n  // the compiler\n  "compilerOptions": {"strict": true,},\n  /* x */ "url": "http://a//b, }",\n}\n'
    for name in ("tsconfig.json", "tsconfig.build.json", "jsconfig.json", ".eslintrc.json", ".devcontainer/devcontainer.json",
                 ".vscode/settings.json"):
        assert note(name, jsonc) is None, name
    still_bad = note("tsconfig.json", '{\n  // c\n  "a": 1\n  "b": 2\n}\n')
    assert still_bad and still_bad.line == 4 and "column 3" in still_bad.text     # comments blanked, lines unmoved


# ── HTML ──

def test_html_tag_problems_each_name_their_line():
    n = note("index.html", "<body>\n<div>\n<span>x</div>\n</section>\n<main>\n")
    assert n and not n.advisory and n.text.startswith("Check: the tags do not balance in index.html:")
    assert "- <span> opened at line 3 is not closed before </div> at line 3" in n.text
    assert "- </section> at line 4 closes nothing that is open" in n.text
    assert "- <main> opened at line 5 is never closed" in n.text
    assert n.line == 3 and n.short == "tags do not balance at line 3" and n.text.endswith(FIRM)


def test_valid_html_with_void_tags_optional_end_tags_self_closing_and_script_text_gets_no_note():
    page = ("<!doctype html>\n<HTML><head><meta charset=utf-8><link rel=stylesheet href=s.css><title>T</title>\n"
            "<body>\n<p>one<p>two\n<ul><li>a<li>b</ul>\n<table><tr><td>1<td>2</table>\n<img src=x.png><br/><input>\n"
            "<svg><circle r='1' /><path d='m0 0'></path></svg>\n"
            "<script>var s = \"</div>\"; if (a < b) { go(); }</script>\n<!-- <div> -->\n</BODY></html>\n")
    assert note("index.html", page) is None
    assert note("page.htm", "<div><span>x</span></div>") is None


# ── JavaScript and CSS: advice only ──

def test_js_brackets_that_do_not_balance_are_advice_with_a_line():
    n = note("app.js", "function f() {\n  if (x) {\n    y();\n}\n")
    assert n and n.advisory and n.line == 1 and "a rough bracket count, it can be wrong" in n.text
    assert "- '{' opened at line 1 is never closed" in n.text and n.text.endswith(vc.ADVICE_TAIL)
    assert note("lib.mjs", "a = b)\n").text.count("')' at line 1 closes nothing") == 1
    assert note("x.cjs", "f([1, 2)\n").advisory


def test_js_strings_comments_templates_and_regex_literals_do_not_fool_the_count():
    src = ("const re = /[(]/g;\nconst s = '{', d = \"[\";\n// }\n/* ) */\nfunction f(a) { return a / 2 / (3); }\n"
           "const t = `x ${a} (`;\nif (x) { y = s.replace(/\\(/, ''); return /]/.test(y); }\n")
    assert note("app.js", src) is None


def test_css_counts_braces_outside_strings_and_comments():
    assert note("style.css", "a { color: red; }\n/* } */ b::after { content: '}'; }\n") is None
    n = note("style.css", "a { color: red;\nb { x: y }\n")
    assert n and n.advisory and "'{' opened at line 1 is never closed" in n.text


# ── limits and what is not checked ──

def test_a_note_stays_within_one_kilobyte_and_lists_at_most_three_problems():
    n = note("pages/" + "deep/" * 40 + "x.html", "<div>\n" * 3000)
    assert len(n.text.encode("utf-8")) <= vc.MAX_NOTE_BYTES
    assert n.text.count("\n- <div>") == vc.MAX_PROBLEMS and "- and 2997 more" in n.text
    long_line = note("app.py", "x = (" + "é" * 5000 + "\n")
    assert len(long_line.text.encode("utf-8")) <= vc.MAX_NOTE_BYTES


def test_files_built_to_make_a_check_slow_still_check_in_seconds_not_minutes():
    for path, text in (("app.js", "(/[" * 30000),          # a failed regex try on every `/`: 60 s without the cap
                       ("index.html", "<a b=" * 20000),     # an unfinished tag: 54 s in close() before gh-135462
                       ("page.html", "<!--" + "<a>" * 30000)):
        t0 = time.perf_counter()
        vc.check_file(path, text)
        assert time.perf_counter() - t0 < 5, path
    assert note("app.js", "(/[" * 30000).advisory                   # the count still runs to the end
    assert note("index.html", "<div>\n<a b=\"x") is not None        # tags before an unfinished one are still counted


@pytest.mark.parametrize("path", ["README.md", "notes.txt", "Makefile", "app.ts", "data.yaml", ".json"])
def test_other_kinds_of_file_get_no_note(path):
    assert note(path, "{{{ ((( <div>") is None


def test_a_check_that_raises_gives_no_note(monkeypatch):
    def boom(path, text):
        raise RecursionError("too deep")
    monkeypatch.setitem(vc._CHECKS, "python", boom)
    assert note("app.py", "def broken(") is None
