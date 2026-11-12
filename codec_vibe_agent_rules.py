"""Vibe coding agent: the owner's rules for "Always allow" (P-048, docs/VIBE-ALLOW-RULES-DESIGN.md).

A rule is a few literal words and then ` *`, for example `git diff *`. When the agent asks to run a command, a rule
lets it run without an approval card only when the command is exactly those words followed by plain arguments: no
shell control, no expansion, no second command, nothing outside printable ASCII. When in doubt it does not match and
the owner sees the card as before.

A rule never skips the danger check (codec_vibe_agent_cmd.check_command runs first, in codec_vibe_agent), never gives
the web (a matched command runs with no network), and the command runs in the same sandbox as every other one. Only
the owner's click at this Mac adds or removes a rule (codec_vibe_projects.allow_rule, routes/vibe_agent.py). The editor
refuses programs that run code or other commands given in their arguments, paths, and the project's own files.

Standard library only (plus codec_vibe_agent_cmd for the command environment and codec_config for is_dangerous).
"""
from __future__ import annotations

import os
import re
import shlex

MAX_RULE_CHARS = 200
MAX_RULE_WORDS = 6                  # literal words before the *
MAX_COMMAND_CHARS = 500             # the runner's own limit (codec_vibe_runner.MAX_COMMAND_CHARS)

_FIRST_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}")
_LATER_WORD = re.compile(r"[A-Za-z0-9._:=@%+,/-]{1,100}")
_WRAPPED = re.compile(r"run_check\((.*)\)")
_VERSION_END = re.compile(r"[-_.]?[0-9][0-9._]*$")
_SCRIPT_ENDS = (".sh", ".bash", ".zsh", ".fish", ".ksh", ".csh", ".py", ".pyw", ".pyc", ".js", ".mjs", ".cjs", ".ts",
                ".mts", ".cts", ".jsx", ".tsx", ".rb", ".pl", ".pm", ".php", ".lua", ".tcl", ".r", ".jl", ".swift", ".jar",
                ".command", ".tool", ".app", ".applescript", ".scpt", ".bat", ".cmd", ".ps1", ".exe", ".bin", ".run", ".out")

# What a command may hold for a rule to match. Printable ASCII and the space, minus every character a shell treats
# as control, quoting escape, expansion, wildcard, history or comment. Quotes stay: with these gone, a quoted word
# splits the same way in shlex and in bash. ~ and ^ are checked on their own below.
_SHELL_CHARS = frozenset(";&|`$<>()\\*?[]{}!#")
_PLAIN = frozenset(chr(c) for c in range(0x20, 0x7F)) - _SHELL_CHARS
_TILDE_CARET = re.compile(r"(?<![A-Za-z0-9])[~^]")      # only straight after a letter or digit (HEAD~1, HEAD^)
# Environment variables that make bash run code before the command, or an exported function standing in for it.
_ENV_HAZARDS = ("BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS", "PS4")

# ── what can never be a rule's program ──
# A rule trusts the program with any arguments, so programs that run code or another command given in their
# arguments are refused, and so are those that run the project's own files (the agent can write those). A floor,
# not a complete list: widening it is the owner's call. Compared in lower case (the Mac's disk ignores case).

SHELLS = frozenset("""
sh bash zsh dash ksh mksh oksh pdksh loksh csh tcsh fish nu nushell pwsh powershell xonsh elvish yash rc es ash
busybox toybox
""".split())

BASH_BUILTINS = frozenset("""
alias bg bind break builtin caller cd command compgen complete compopt continue declare dirs disown echo enable eval
exec exit export false fc fg getopts hash help history jobs kill let local logout mapfile popd printf pushd pwd read
readarray readonly return set shift shopt source suspend test times trap true type typeset ulimit umask unalias unset
wait if then else elif fi case esac for select while until do done in function time coproc
""".split())

INTERPRETERS = frozenset("""
node nodejs deno bun python pythonw python2 python3 pypy pypy3 ipython jupyter ruby irb jruby erb perl php lua luajit
tclsh wish expect osascript swift java jshell kotlin kotlinc scala groovy clojure clj lein ghc ghci runghc runhaskell
stack cabal ocaml utop erl escript elixir iex mix dotnet r rscript julia awk gawk nawk mawk sed gsed m4 dc gdb lldb
vim vi nvim view vimdiff ex ed emacs emacsclient
""".split())

PACKAGE_MANAGERS = frozenset("""
npm npx yarn yarnpkg pnpm pnpx corepack bunx pip pip2 pip3 pipx uv uvx poetry pipenv pdm hatch rye conda mamba
micromamba gem bundle bundler cargo rustup composer brew port nix nix-shell nix-env nix-build apt apt-get yum dnf
""".split())

RUNNERS = frozenset("""
make gmake bmake cmake ninja meson scons bazel bazelisk buck just task invoke fab tox nox pytest py.test jest vitest
mocha ava karma playwright cypress tsx ts-node esno vite webpack rollup babel eslint prettier stylelint next nuxt turbo
nx lerna gradle gradlew mvn mvnw ant sbt rake xcodebuild xcrun flutter dart go rustc cc gcc g++ c++ clang clang++ ld
javac docker podman kubectl helm terraform ansible ansible-playbook mypy pylint coverage pre-commit lint-staged husky
sphinx-build mkdocs nodemon pm2 electron wrangler
""".split())

COMMAND_RUNNERS = frozenset("""
env sudo su doas nohup nice renice timeout gtimeout xargs parallel watch caffeinate script unbuffer stdbuf chroot arch
sandbox-exec open launchctl at batch crontab ssh scp sftp rsync tmux screen find fd entr strace dtruss dtrace ltrace
valgrind flock chronic setsid unshare nsenter tar zip rg sort sdiff diff3 man nc ncat netcat socat openssl sqlite3
psql mysql wget defaults shortcuts automator scutil security
""".split())

DESTRUCTIVE = frozenset("rm rmdir unlink mv dd shred truncate srm".split())

REFUSED = SHELLS | BASH_BUILTINS | INTERPRETERS | PACKAGE_MANAGERS | RUNNERS | COMMAND_RUNNERS | DESTRUCTIVE

# git's read-only commands: a git rule must name one of them as its second word. Not config, fetch, pull, push,
# clone (they can run a program given in an argument), grep (-O runs a pager), rebase (-x), branch or tag (-D),
# stash, submodule, bisect, difftool, mergetool, help (-w opens a browser), and never git alone (git -c ...).
GIT_READ_ONLY = frozenset("""
status diff log show blame annotate describe rev-parse rev-list ls-files ls-tree shortlog name-rev merge-base cherry
range-diff diff-tree diff-files diff-index check-ignore check-attr count-objects whatchanged show-ref
""".split())


class RuleRefused(Exception):
    """The rule cannot be stored; the message says why, in plain words, for the page."""


def _kind(word: str) -> str | None:
    """Why this program name cannot start a rule, or None."""
    w = word.lower()
    names = {w, _VERSION_END.sub("", w), w.split(".", 1)[0]}
    for name in names:
        if name in SHELLS:
            return "a shell"
        if name in BASH_BUILTINS:
            return "part of the shell itself"
        if name in INTERPRETERS:
            return "a program that runs code"
        if name in PACKAGE_MANAGERS:
            return "a package manager"
        if name in RUNNERS:
            return "a build or test runner, which runs the project's own files"
        if name in COMMAND_RUNNERS:
            return "a program that can run another command given in its arguments"
        if name in DESTRUCTIVE:
            return "a program that deletes or moves files"
    if w.endswith(_SCRIPT_ENDS):
        return "a script"
    return None


def parse(text) -> tuple[str, ...]:
    """The rule's literal words (the syntax only). Raises RuleRefused."""
    if not isinstance(text, str):
        raise RuleRefused("Send the rule as text.")
    t = text.strip(" ")                     # spaces only: a tab or a new line is refused below
    wrapped = _WRAPPED.fullmatch(t)
    if wrapped:
        t = wrapped.group(1).strip(" ")
    if not t:
        raise RuleRefused("The rule is empty.")
    if len(t) > MAX_RULE_CHARS:
        raise RuleRefused(f"A rule is at most {MAX_RULE_CHARS} characters.")
    if any(c not in _PLAIN and c not in "*" for c in t):
        raise RuleRefused("Use plain letters, digits, spaces and - . _ : = @ % + , / only.")
    parts = t.split(" ")
    if "" in parts:
        raise RuleRefused("Put one space between words.")
    if parts[-1] != "*":
        raise RuleRefused("End the rule with a space and *, as in: git diff *")
    words = parts[:-1]
    if not words:
        raise RuleRefused("A rule needs at least one word before the *, as in: git diff *")
    if any("*" in w for w in words):
        raise RuleRefused("Only the last word of a rule can be *.")
    if len(words) > MAX_RULE_WORDS:
        raise RuleRefused(f"A rule has at most {MAX_RULE_WORDS} words before the *.")
    first = words[0]
    if "/" in first:
        raise RuleRefused("A rule cannot start with a path. Name an installed program, like git.")
    if not _FIRST_WORD.fullmatch(first):
        raise RuleRefused("A rule starts with a program name: letters, digits and . _ + - only.")
    for w in words[1:]:
        if not _LATER_WORD.fullmatch(w):
            raise RuleRefused(f"\"{w}\" cannot be in a rule: no quotes, ~, ^ or other signs.")
    return tuple(words)


def _static_check(words: tuple[str, ...]) -> None:
    first = words[0]
    why = _kind(first)
    if why:
        raise RuleRefused(f"{first} is {why}, so it cannot be a rule. Its commands keep asking"
                          " (or store one exact command with Always allow).")
    if first.lower() == "git":
        if len(words) < 2 or words[1] not in GIT_READ_ONLY:
            raise RuleRefused("After git, a rule names one of its read-only commands (status, diff, log, show, blame,"
                              " describe, ls-files, ...), as in: git diff *")
    try:
        from codec_config import is_dangerous
    except Exception:
        raise RuleRefused("The safety check is not available, so the rule was not stored.") from None
    if is_dangerous(" ".join(words)):
        raise RuleRefused("Those words look dangerous, so they cannot be a rule.")


def check_rule(text, project=None) -> str:
    """The rule as stored ("git diff *"), or RuleRefused with the reason. With `project` (when the owner adds one) it
    also refuses a program that names a file of the project or is not found outside it."""
    words = parse(text)
    _static_check(words)
    if project is not None:
        first = words[0]
        proj = os.path.realpath(str(project))
        if os.path.lexists(os.path.join(proj, first)):
            raise RuleRefused(f"There is a file or folder called {first} in this project: a rule cannot point at the"
                              " project's own scripts.")
        where, real = locate(first, proj, command_path())
        if where == "inside":
            raise RuleRefused(f"{first} is found inside this project: a rule cannot point at the project's own scripts.")
        if where == "refused":
            raise RuleRefused(f"{first} leads to {os.path.basename(real)}, which cannot be a rule.")
        if where != "found":
            raise RuleRefused(f"No program called {first} was found on this Mac.")
    return " ".join(words) + " *"


# ── the matcher ──

def command_env() -> dict:
    """The environment a command will get (codec_vibe_agent_cmd.command_env; its temp folder does not matter here)."""
    import codec_vibe_agent_cmd as cmdmod
    return cmdmod.command_env("/nonexistent-vibe-temp")


def command_path(env: dict | None = None) -> str:
    return (env if env is not None else command_env()).get("PATH", "")


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _is_program(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def locate(word: str, project, path: str) -> tuple[str, str]:
    """Where bash, started in the project folder, finds `word` on `path`, as (where, real path):
    "found" outside the project; "inside" the project, or through a PATH entry that is empty or relative (bash reads
    those from the command's own folder, the project); "refused" when its real file is a refused program under
    another name (a link `foo` that leads to node); "missing" when it is not found."""
    proj = os.path.realpath(str(project))
    for entry in path.split(os.pathsep):
        if not entry or not os.path.isabs(entry):
            cand = os.path.join(proj, entry, word)
            if _is_program(cand):
                return "inside", os.path.realpath(cand)
            continue
        cand = os.path.join(entry, word)
        if not _is_program(cand):
            continue
        real = os.path.realpath(cand)
        if _inside(real, proj):
            return "inside", real
        if _kind(os.path.basename(real)):
            return "refused", real
        return "found", real
    return "missing", ""


def command_words(command) -> list[str] | None:
    """The command split the way bash would split it, or None when it holds anything a rule cannot pass."""
    if not isinstance(command, str) or not command or len(command) > MAX_COMMAND_CHARS:
        return None
    if any(c not in _PLAIN for c in command) or _TILDE_CARET.search(command):
        return None
    try:
        words = shlex.split(command, posix=True)
    except ValueError:
        return None
    return words or None


def _env_ok(env: dict) -> bool:
    return not any(k in _ENV_HAZARDS or k.startswith("BASH_FUNC_") for k in env)


def match(rules, command, project, env: dict | None = None) -> str | None:
    """The first stored rule that lets `command` run without a card, or None. `command` has already passed
    check_command; this never replaces it. Anything unexpected means no match: the owner sees the card."""
    try:
        return _match(rules, command, project, env)
    except Exception:
        return None


def _match(rules, command, project, env: dict | None) -> str | None:
    words = command_words(command)
    if words is None:
        return None
    env = command_env() if env is None else env
    if not _env_ok(env):
        return None
    path = command_path(env)
    for rule in rules or ():
        try:
            lit = parse(rule)
            _static_check(lit)
        except RuleRefused:
            continue
        head = " ".join(lit)
        if not (command == head or command.startswith(head + " ")):
            continue
        if tuple(words[:len(lit)]) != lit:
            continue
        if locate(lit[0], project, path)[0] != "found":
            continue
        return head + " *"
    return None
