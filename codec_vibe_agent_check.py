"""Vibe coding agent: a cheap check of a file right after the agent changed it (P-045, docs/VIBE-AGENT-EDIT-CHECKS-DESIGN.md).

After edit_file or write_file, the result ends with a short note when the file no longer reads as what its name says:
Python that does not parse (ast.parse), JSON that does not load (json.loads), HTML whose tags do not balance
(html.parser). For JavaScript and CSS a bracket count is given as advice only, because strings, comments and regular
expressions can fool it. A file that checks clean gets no note, and so does a kind of file not listed here.

Standard library only. No files, no programs, no network: the text is checked in this process. A check never raises
and never blocks anything: the edit is already saved when the note is written.
"""
from __future__ import annotations

import ast
import bisect
import json
import posixpath
import re
import sys
import warnings
from dataclasses import dataclass
from html.parser import HTMLParser

MAX_NOTE_BYTES = 1024
MAX_PROBLEMS = 3
MAX_LINE_CHARS = 160        # what a note quotes of the line in question
MAX_PATH_CHARS = 120
MAX_TAG_CHARS = 40

KINDS = {".py": "python", ".json": "json", ".html": "html", ".htm": "html",
         ".js": "js", ".mjs": "js", ".cjs": "js", ".css": "css"}

FIRM_TAIL = "The change is saved, but the file is broken: fix it before you finish."
ADVICE_TAIL = ("Strings, comments and regular expressions can fool this count. Look at those lines; "
               "if they are right, leave them as they are.")

# JSON files that hold comments and trailing commas by convention (JSONC). Every other .json is strict.
_JSONC_NAME_RE = re.compile(r"^(?:tsconfig.*|jsconfig.*|\.eslintrc|devcontainer)\.json$", re.I)
_JSONC_COMMENT_RE = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?(?:\*/|\Z)', re.S)
_JSONC_COMMA_RE = re.compile(r'"(?:\\.|[^"\\])*"|,(?=\s*[\]}])', re.S)

# HTML: no end tag (void), or an end tag that HTML lets the author leave out. Neither is counted.
VOID_TAGS = frozenset("area base br col embed hr img input link meta source track wbr param keygen frame basefont "
                      "bgsound command".split())
OPTIONAL_END_TAGS = frozenset("html head body p li dt dd option optgroup rb rt rtc rp tr td th thead tbody tfoot "
                              "colgroup caption".split())

# JavaScript: comments, strings and template literals are skipped; a `/` where a value may start opens a regex literal.
_JS_TOKEN_RE = re.compile(r'//[^\n]*|/\*.*?(?:\*/|\Z)|"(?:\\.|[^"\\\n])*"?|\'(?:\\.|[^\'\\\n])*\'?|`(?:\\.|[^`\\])*`?'
                          r'|[A-Za-z_$][\w$]*|\d[\w.]*|\S', re.S)
_JS_REGEX_RE = re.compile(r'/(?:\\.|\[(?:\\.|[^\]\\\n])*\]|[^/\\\n\[])+/[A-Za-z]*')
_JS_REGEX_AFTER = frozenset("( , = : [ ! & | ? { } ; + - * % < > ~ ^".split())
_JS_REGEX_AFTER_WORDS = frozenset("return typeof case do else in of new delete void throw yield await instanceof".split())
_CSS_TOKEN_RE = re.compile(r'/\*.*?(?:\*/|\Z)|"(?:\\.|[^"\\\n])*"?|\'(?:\\.|[^\'\\\n])*\'?|[(){}\[\]]', re.S)
_OPEN_OF = {")": "(", "]": "[", "}": "{"}
_CLOSE_OF = {v: k for k, v in _OPEN_OF.items()}


@dataclass(frozen=True)
class Note:
    text: str           # what goes at the end of the result: at most MAX_NOTE_BYTES, names the file and the line
    short: str          # a few words for the step's one-line summary
    line: int
    advisory: bool      # True for the JS / CSS bracket count


def kind_of(path: str) -> "str | None":
    """python | json | html | js | css, or None for a file that is not checked."""
    name = posixpath.basename(str(path or "")).lower()
    dot = name.rfind(".")
    return KINDS.get(name[dot:]) if dot > 0 else None


def check_file(path: str, text: str) -> "Note | None":
    """The note for a file that the agent's edit left as `text`, or None when it checks clean or is not checked."""
    try:
        kind = kind_of(path)
        if kind is None or not isinstance(text, str):
            return None
        if text.startswith("\ufeff"):               # a byte-order mark is not part of the content
            text = text[1:]
        return _CHECKS[kind](str(path), text)
    except Exception:                               # recursion, a parser bug: a step is never broken by its check
        return None


# ── the note ────────────────────────────────────────────────────────────────

def _show_path(path: str) -> str:
    path = " ".join(str(path).split())
    return path if len(path) <= MAX_PATH_CHARS else "..." + path[-(MAX_PATH_CHARS - 3):]


def _cap(text: str) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= MAX_NOTE_BYTES:
        return text
    return raw[:MAX_NOTE_BYTES - 4].decode("utf-8", "ignore") + " ..."


def _line_of(text: str, line: int) -> str:
    """Line `line` (1-based) of the text, as `   N| text`, or "" when there is no such line or it is blank."""
    lines = text.split("\n")
    if not 1 <= line <= len(lines):
        return ""
    body = lines[line - 1].rstrip("\r").strip()
    if not body:
        return ""
    return f"{line:>4}| {body[:MAX_LINE_CHARS]}"


def _firm(path: str, what: str, line: int, quoted: str, short: str) -> Note:
    end = "" if what.endswith((".", "?", "!")) else "."
    body = f"Check: {_show_path(path)} {what}{end}" + (f"\n{quoted}" if quoted else "") + "\n" + FIRM_TAIL
    return Note(_cap(body), short, line, False)


def _problems(path: str, head: str, problems: list, tail: str, short: str, advisory: bool) -> Note:
    shown = problems[:MAX_PROBLEMS]
    more = f"\n- and {len(problems) - len(shown)} more" if len(problems) > len(shown) else ""
    body = f"{head} {_show_path(path)}:\n" + "\n".join(f"- {p}" for p, _line in shown) + more + "\n" + tail
    return Note(_cap(body), short, shown[0][1], advisory)


class _Stack:
    """Openers waiting for their closers. A closer that does not match the newest opener closes the nearest opener
    that it does match (every opener above it is reported as not closed), or is reported as closing nothing."""

    def __init__(self, show_open, show_close):
        self.items: list = []
        self.problems: list = []            # (text, line)
        self.show_open, self.show_close = show_open, show_close

    def open(self, key: str, line: int) -> None:
        self.items.append((key, line))

    def close(self, key: str, line: int) -> None:
        for k in range(len(self.items) - 1, -1, -1):
            if self.items[k][0] == key:
                for name, at in reversed(self.items[k + 1:]):
                    self.problems.append((f"{self.show_open(name)} opened at line {at} is not closed before "
                                          f"{self.show_close(key)} at line {line}", at))
                del self.items[k:]
                return
        self.problems.append((f"{self.show_close(key)} at line {line} closes nothing that is open", line))

    def finish(self) -> list:
        for name, at in self.items:
            self.problems.append((f"{self.show_open(name)} opened at line {at} is never closed", at))
        self.items = []
        return self.problems


# ── Python, JSON, HTML: firm ────────────────────────────────────────────────

def _python(path: str, text: str) -> "Note | None":
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")         # invalid escapes and the like warn, they do not fail
            ast.parse(text, filename="<edit>")
    except SyntaxError as e:
        line = e.lineno or 0
        msg = " ".join(str(e.msg or "invalid syntax").split())[:200]
        where = f"line {line}: {msg}" if line else msg
        return _firm(path, f"does not parse as Python {version}: {where}", line, _line_of(text, line),
                     f"Python syntax error at line {line}" if line else "Python syntax error")
    except ValueError:                              # a null byte
        return _firm(path, f"does not parse as Python {version}: it contains a null byte", 0, "", "Python syntax error")
    return None


def _is_jsonc(path: str) -> bool:
    return bool(_JSONC_NAME_RE.match(posixpath.basename(path))) or "/.vscode/" in "/" + path


def _blank(m: "re.Match") -> str:
    """A comment or a trailing comma becomes spaces (new lines kept), so lines and columns do not move. Strings stay."""
    t = m.group(0)
    return t if t.startswith('"') else re.sub(r"[^\n]", " ", t)


def _json(path: str, text: str) -> "Note | None":
    src = _JSONC_COMMA_RE.sub(_blank, _JSONC_COMMENT_RE.sub(_blank, text)) if _is_jsonc(path) else text
    try:
        json.loads(src)
    except json.JSONDecodeError as e:
        return _firm(path, f"does not load as JSON: line {e.lineno}, column {e.colno}: {e.msg}", e.lineno,
                     _line_of(text, e.lineno), f"JSON error at line {e.lineno}")
    return None


class _TagBalance(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = _Stack(lambda t: f"<{t}>", lambda t: f"</{t}>")

    def handle_starttag(self, tag, attrs):
        if tag not in VOID_TAGS and tag not in OPTIONAL_END_TAGS:
            self.stack.open(tag[:MAX_TAG_CHARS], self.getpos()[0])

    def handle_startendtag(self, tag, attrs):
        pass                                        # <x/>: opened and closed at once

    def handle_endtag(self, tag):
        if tag not in VOID_TAGS and tag not in OPTIONAL_END_TAGS:
            self.stack.close(tag[:MAX_TAG_CHARS], self.getpos()[0])


def _html(path: str, text: str) -> "Note | None":
    parser = _TagBalance()
    parser.feed(text)
    parser.close()
    problems = parser.stack.finish()
    if not problems:
        return None
    return _problems(path, "Check: the tags do not balance in", problems, FIRM_TAIL,
                     f"tags do not balance at line {problems[0][1]}", False)


# ── JavaScript and CSS: advice only ─────────────────────────────────────────

def _line_finder(text: str):
    ends = [m.start() for m in re.finditer("\n", text)]
    return lambda pos: bisect.bisect_left(ends, pos) + 1


def _bracket_stack() -> _Stack:
    return _Stack(lambda c: f"'{c}'", lambda c: f"'{_CLOSE_OF[c]}'")


def _brackets_note(path: str, stack: _Stack) -> "Note | None":
    problems = stack.finish()
    if not problems:
        return None
    return _problems(path, "Check (a rough bracket count, it can be wrong):", problems, ADVICE_TAIL,
                     f"brackets may not match at line {problems[0][1]}", True)


def _js(path: str, text: str) -> "Note | None":
    line_at = _line_finder(text)
    stack = _bracket_stack()
    prev, pos = "", 0                               # prev: the last token that is not a comment ("" at the start)
    while True:
        m = _JS_TOKEN_RE.search(text, pos)
        if not m:
            break
        tok, start, pos = m.group(0), m.start(), m.end()
        if tok.startswith(("//", "/*")):
            continue
        if tok[0] in "\"'`":
            prev = '"'
            continue
        if tok == "/" and (prev == "" or prev in _JS_REGEX_AFTER or prev in _JS_REGEX_AFTER_WORDS):
            r = _JS_REGEX_RE.match(text, start)
            if r:
                prev, pos = "/x/", r.end()
                continue
        if tok in _CLOSE_OF:
            stack.open(tok, line_at(start))
        elif tok in _OPEN_OF:
            stack.close(_OPEN_OF[tok], line_at(start))
        prev = tok
    return _brackets_note(path, stack)


def _css(path: str, text: str) -> "Note | None":
    line_at = _line_finder(text)
    stack = _bracket_stack()
    for m in _CSS_TOKEN_RE.finditer(text):
        tok = m.group(0)
        if tok in _CLOSE_OF:
            stack.open(tok, line_at(m.start()))
        elif tok in _OPEN_OF:
            stack.close(_OPEN_OF[tok], line_at(m.start()))
    return _brackets_note(path, stack)


_CHECKS = {"python": _python, "json": _json, "html": _html, "js": _js, "css": _css}
