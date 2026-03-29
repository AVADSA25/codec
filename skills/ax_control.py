"""CODEC AXUIElement Accessibility Bridge — control any macOS app by UI elements"""
import subprocess
import json
import os

SKILL_NAME = "ax_control"
SKILL_TRIGGERS = [
    "click button", "press button", "click ok", "click cancel",
    "show ui elements", "show ui tree", "read field", "read text field",
    "find button", "accessibility tree", "ui elements"
]
SKILL_DESCRIPTION = "Control any macOS app using native accessibility (AXUIElement). Click buttons, read fields, navigate UI."

AX_BRIDGE = os.path.expanduser("~/codec-repo/ax_bridge/ax_bridge")


def _run_ax(args: list) -> dict:
    """Run ax_bridge binary and return parsed JSON result."""
    if not os.path.exists(AX_BRIDGE):
        return {"error": "ax_bridge not found. Run: cd ~/codec-repo/ax_bridge && swiftc -O -o ax_bridge main.swift"}
    try:
        result = subprocess.run(
            [AX_BRIDGE] + args,
            capture_output=True, text=True, timeout=10
        )
        return json.loads(result.stdout or "{}")
    except subprocess.TimeoutExpired:
        return {"error": "ax_bridge timed out"}
    except json.JSONDecodeError as e:
        return {"error": f"Invalid JSON from ax_bridge: {e}"}
    except Exception as e:
        return {"error": str(e)}


def _get_frontmost_pid() -> str:
    """Get PID of frontmost app via osascript."""
    try:
        r = subprocess.run(
            ["osascript", "-e", "tell application \"System Events\" to get unix id of first process whose frontmost is true"],
            capture_output=True, text=True, timeout=5
        )
        return r.stdout.strip()
    except Exception:
        return ""


def run(task: str, context: str = "") -> str:
    task_lower = task.lower()
    pid = _get_frontmost_pid()

    # "show ui elements" / "show ui tree" / "accessibility tree"
    if any(w in task_lower for w in ["ui tree", "ui element", "accessibility tree", "show element"]):
        depth = 2
        result = _run_ax(["--pid", pid, "--action", "tree", "--depth", str(depth)])
        if "error" in result:
            return f"AX bridge error: {result['error']}"
        tree = result.get("tree", {})
        role = tree.get("role", "?")
        title = tree.get("title", "")
        children = tree.get("children", [])
        lines = [f"UI tree for frontmost app ({role}: {title})"]
        for child in children[:15]:
            cr = child.get("role", "?")
            ct = child.get("title", "") or child.get("label", "") or child.get("value", "")
            lines.append(f"  └ {cr}: {ct[:60]}")
        return "\n".join(lines)

    # "click button OK" / "press button X" / "click cancel"
    if any(w in task_lower for w in ["click button", "press button", "click ok", "click cancel", "click submit", "click apply"]):
        # Extract button name
        for prefix in ["click button ", "press button ", "click ", "press "]:
            if prefix in task_lower:
                name = task_lower.split(prefix, 1)[1].strip().rstrip(".")
                break
        else:
            name = ""

        if name:
            selector = f"role:AXButton name:{name}"
        else:
            selector = "role:AXButton"

        result = _run_ax(["--pid", pid, "--action", "click", "--selector", selector, "--depth", "5"])
        if "error" in result:
            return f"Could not click '{name}': {result['error']}"
        return result.get("message", f"Clicked {name}")

    # "read field search" / "read text field"
    if any(w in task_lower for w in ["read field", "read text", "get field value", "what is in field"]):
        for prefix in ["read field ", "read text field ", "read text ", "get field value ", "what is in field "]:
            if prefix in task_lower:
                name = task_lower.split(prefix, 1)[1].strip().rstrip(".")
                break
        else:
            name = ""

        selector = f"role:AXTextField name:{name}" if name else "role:AXTextField"
        result = _run_ax(["--pid", pid, "--action", "read", "--selector", selector, "--depth", "5"])
        if "error" in result:
            return f"Could not read field: {result['error']}"
        value = result.get("value", "") or result.get("title", "")
        return f"Field value: {value}" if value else "Field is empty"

    # "find button X"
    if "find button" in task_lower or "find element" in task_lower:
        for prefix in ["find button ", "find element "]:
            if prefix in task_lower:
                name = task_lower.split(prefix, 1)[1].strip().rstrip(".")
                break
        else:
            name = ""

        selector = f"role:AXButton name:{name}" if name else "role:AXButton"
        result = _run_ax(["--pid", pid, "--action", "find", "--selector", selector, "--depth", "5"])
        if "error" in result:
            return f"Find error: {result['error']}"
        count = result.get("count", 0)
        elements = result.get("elements", [])
        if count == 0:
            return f"No elements found matching '{name}'"
        lines = [f"Found {count} element(s) matching '{name}':"]
        for el in elements[:5]:
            r = el.get("role", "?")
            t = el.get("title", "") or el.get("label", "")
            lines.append(f"  {r}: {t}")
        return "\n".join(lines)

    return "Tell me what to do. Examples: 'show ui elements', 'click button OK', 'read field search'"
