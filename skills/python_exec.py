"""CODEC Skill: Python Execution — run safe Python snippets."""
SKILL_NAME = "python_exec"
SKILL_DESCRIPTION = "Execute a Python code snippet and return the output"
SKILL_TRIGGERS = [
    "run python", "execute python", "python code", "python script",
    "calculate with python", "eval python",
]
SKILL_MCP_EXPOSE = False  # Too powerful for remote

import subprocess, os, re, tempfile

# Blocked imports / patterns in user code
_BLOCKED = [
    "import os", "import sys", "import subprocess", "import shutil",
    "import ctypes", "import signal", "import socket",
    "__import__", "eval(", "exec(", "compile(",
    "open(", "globals(", "locals(",
    "os.system", "os.popen", "os.exec", "os.remove", "os.unlink",
    "shutil.rmtree", "subprocess.",
]


def _is_safe_code(code):
    """Check code doesn't contain dangerous patterns."""
    code_lower = code.lower()
    for pattern in _BLOCKED:
        if pattern.lower() in code_lower:
            return False, f"Blocked pattern: {pattern}"
    return True, ""


def _extract_code(task):
    """Pull Python code from the task text."""
    # Try triple backtick block
    m = re.search(r'```(?:python)?\s*\n?(.*?)```', task, re.DOTALL)
    if m:
        return m.group(1).strip()
    # Try after "code:" or "run:"
    for kw in ["code:", "run:", "execute:", "python:"]:
        idx = task.lower().find(kw)
        if idx >= 0:
            return task[idx + len(kw):].strip()
    # Strip trigger words and use remaining text
    t = task
    for w in SKILL_TRIGGERS:
        t = re.sub(re.escape(w), "", t, flags=re.IGNORECASE).strip()
    return t.strip() if t.strip() else None


def run(task, app="", ctx=""):
    code = _extract_code(task)
    if not code:
        return "No Python code provided. Use: run python ```your code here```"

    safe, reason = _is_safe_code(code)
    if not safe:
        return f"Blocked for safety: {reason}"

    # Write to temp file and execute in isolated subprocess
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            # Wrap in safe execution: redirect print output
            f.write("import math, json, re, datetime, collections, itertools, functools\n")
            f.write("import statistics, decimal, fractions, random, string, textwrap\n")
            f.write(code + "\n")
            tmp_path = f.name

        result = subprocess.run(
            ["python3", tmp_path],
            capture_output=True, text=True,
            timeout=10,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        os.unlink(tmp_path)

        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        if result.returncode == 0:
            output = stdout or "(no output)"
            return output[:2000]
        else:
            error = stderr or stdout or "Unknown error"
            return f"Error:\n{error[:1000]}"

    except subprocess.TimeoutExpired:
        try:
            os.unlink(tmp_path)
        except:
            pass
        return "Execution timed out (10s limit)"
    except Exception as e:
        return f"Execution error: {e}"
