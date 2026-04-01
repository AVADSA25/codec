"""CODEC Vision Mouse Control — voice-controlled cursor via screen vision.

Say "click the submit button", "move mouse to the search bar", "scroll down",
"double click the file" — CODEC sees your screen and acts.

Architecture: screenshot -> Qwen Vision -> coordinates -> pyautogui -> confirm
"""

SKILL_NAME = "mouse_control"
SKILL_DESCRIPTION = "Control mouse cursor by voice using screen vision — click, move, scroll, drag any element you can see"
SKILL_TRIGGERS = [
    "click on", "click the", "click button", "press the button",
    "move mouse", "move cursor", "mouse to", "cursor to",
    "right click", "right-click", "double click", "double-click",
    "scroll down", "scroll up", "scroll left", "scroll right",
    "hover over", "hover on", "point to", "point at",
    "find on screen", "where is the", "locate the",
    "click where it says", "tap on", "select the",
    "can you spot", "spot it", "find it and click",
]

import subprocess
import base64
import json
import os
import re
import time
import logging

log = logging.getLogger("codec.mouse_control")

# ── Config (from codec_config if available, else sensible defaults) ──────────
try:
    from codec_config import QWEN_VISION_URL, QWEN_VISION_MODEL
    VISION_URL = QWEN_VISION_URL.rstrip("/") + "/chat/completions"
    VISION_MODEL = QWEN_VISION_MODEL
except ImportError:
    VISION_URL = "http://localhost:8082/v1/chat/completions"
    VISION_MODEL = "mlx-community/Qwen2.5-VL-7B-Instruct-4bit"

_SCREENSHOT_PATH = os.path.expanduser("~/.codec/mouse_screen.png")
_screen_size = None


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_screen_size():
    """Get actual screen resolution via pyautogui (cached)."""
    global _screen_size
    if _screen_size:
        return _screen_size
    try:
        import pyautogui
        _screen_size = pyautogui.size()
        return _screen_size
    except Exception:
        return (1920, 1080)


def _take_screenshot():
    """Capture screen and return base64-encoded PNG."""
    try:
        os.makedirs(os.path.dirname(_SCREENSHOT_PATH), exist_ok=True)
        subprocess.run(
            ["screencapture", "-x", "-C", _SCREENSHOT_PATH],
            capture_output=True, timeout=5
        )
        if os.path.exists(_SCREENSHOT_PATH) and os.path.getsize(_SCREENSHOT_PATH) > 1000:
            with open(_SCREENSHOT_PATH, "rb") as f:
                return base64.b64encode(f.read()).decode()
    except Exception as e:
        log.warning(f"Screenshot error: {e}")
    return None


def _downscale_screenshot(image_b64, max_width=1280):
    """Downscale screenshot for faster vision processing (keeps aspect ratio)."""
    try:
        from PIL import Image
        import io
        img_bytes = base64.b64decode(image_b64)
        img = Image.open(io.BytesIO(img_bytes))
        orig_w, orig_h = img.size

        if orig_w <= max_width:
            return image_b64, 1.0  # No scaling needed

        scale = orig_w / max_width
        new_h = int(orig_h / scale)
        img = img.resize((max_width, new_h), Image.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return base64.b64encode(buf.getvalue()).decode(), scale
    except ImportError:
        # PIL not available — send full-res
        return image_b64, 1.0
    except Exception as e:
        log.warning(f"Downscale error: {e}")
        return image_b64, 1.0


def _ask_vision(image_b64, prompt):
    """Send image + prompt to Qwen Vision, return response text."""
    import requests
    try:
        # Downscale for faster inference
        scaled_b64, scale = _downscale_screenshot(image_b64)
        mime = "image/jpeg" if scale > 1.0 else "image/png"

        payload = {
            "model": VISION_MODEL,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{scaled_b64}"}},
                    {"type": "text", "text": prompt}
                ]
            }],
            "max_tokens": 300,
            "temperature": 0.1,
        }
        r = requests.post(VISION_URL, json=payload, headers={"Content-Type": "application/json"}, timeout=30)
        if r.status_code == 200:
            text = r.json()["choices"][0]["message"]["content"].strip()
            text = re.sub(r'<think>[\s\S]*?</think>', '', text).strip()
            return text, scale
    except Exception as e:
        log.warning(f"Vision error: {e}")
    return None, 1.0


def _parse_coordinates(vision_response, scale=1.0):
    """Extract x,y coordinates from vision model response and scale back to full res."""
    if not vision_response:
        return None

    coords = None

    # Try JSON: {"x": 847, "y": 523}
    json_match = re.search(r'\{\s*"x"\s*:\s*(\d+)\s*,\s*"y"\s*:\s*(\d+)\s*\}', vision_response)
    if json_match:
        coords = (int(json_match.group(1)), int(json_match.group(2)))

    # Try parentheses: (847, 523)
    if not coords:
        paren_match = re.search(r'\((\d+)\s*,\s*(\d+)\)', vision_response)
        if paren_match:
            coords = (int(paren_match.group(1)), int(paren_match.group(2)))

    # Try "x: 847, y: 523"
    if not coords:
        xy_match = re.search(r'x\s*[:=]\s*(\d+)\s*[,;]\s*y\s*[:=]\s*(\d+)', vision_response, re.IGNORECASE)
        if xy_match:
            coords = (int(xy_match.group(1)), int(xy_match.group(2)))

    # Fallback: two numbers that look like coordinates
    if not coords:
        nums = re.findall(r'\b(\d{2,4})\b', vision_response)
        if len(nums) >= 2:
            x, y = int(nums[0]), int(nums[1])
            sw, sh = _get_screen_size()
            max_w = sw / scale if scale > 1.0 else sw
            max_h = sh / scale if scale > 1.0 else sh
            if 0 < x < max_w and 0 < y < max_h:
                coords = (x, y)

    if coords:
        # Scale coordinates back to actual screen resolution
        return (int(coords[0] * scale), int(coords[1] * scale))

    return None


def _validate_coordinates(x, y):
    """Check if coordinates are within screen bounds."""
    sw, sh = _get_screen_size()
    return 0 <= x <= sw and 0 <= y <= sh


def _find_element(description):
    """Screenshot screen, ask vision to locate element, return (x, y) or (None, error)."""
    image_b64 = _take_screenshot()
    if not image_b64:
        return None, "Could not take screenshot."

    sw, sh = _get_screen_size()
    prompt = (
        f"Look at this screenshot carefully. The actual screen resolution is {sw}x{sh}.\n"
        f"Find the UI element described as: '{description}'\n\n"
        f"Return ONLY the center coordinates of that element as JSON: {{\"x\": N, \"y\": N}}\n"
        f"where x is pixels from the left edge and y is pixels from the top edge.\n"
        f"IMPORTANT: Base coordinates on the downscaled image you see. I will scale them back.\n"
        f"If you cannot find the element, respond with: {{\"x\": -1, \"y\": -1}}\n"
        f"Return ONLY the JSON, nothing else."
    )

    response, scale = _ask_vision(image_b64, prompt)
    if not response:
        return None, "Vision model did not respond. Is Qwen Vision running at localhost:8082?"

    log.info(f"Vision response for '{description}': {response[:200]} (scale={scale:.2f})")

    coords = _parse_coordinates(response, scale)
    if not coords:
        return None, f"Couldn't locate '{description}' on screen. Try describing it differently."

    x, y = coords
    if x == -1 and y == -1:
        return None, f"I looked but couldn't find '{description}' on screen."

    if not _validate_coordinates(x, y):
        sw, sh = _get_screen_size()
        return None, f"Coordinates ({x}, {y}) are outside screen ({sw}x{sh}). Vision may have miscalculated."

    return (x, y), None


def _describe_screen():
    """Ask vision to list all interactive elements on screen."""
    image_b64 = _take_screenshot()
    if not image_b64:
        return "Could not take screenshot."

    prompt = (
        "Look at this screenshot. List all visible interactive elements — buttons, links, "
        "text fields, menus, icons, tabs — with their approximate position described in words "
        "(e.g. 'top-right', 'center', 'bottom toolbar').\n"
        "Format: - [element] — [position]\n"
        "List the most prominent elements first. Maximum 12 elements. Be brief."
    )

    response, _ = _ask_vision(image_b64, prompt)
    return response or "Vision model did not respond."


def _extract_target(task_lower):
    """Extract the element description from the user's command."""
    target = task_lower
    prefixes = [
        "click on the ", "click on ", "click the ", "click button ", "click where it says ",
        "click ", "press the button ", "press the ", "press ",
        "right click on the ", "right click the ", "right click on ", "right click ",
        "right-click on the ", "right-click the ", "right-click ",
        "double click on the ", "double click the ", "double click on ", "double click ",
        "double-click on the ", "double-click the ", "double-click ",
        "hover over the ", "hover over ", "hover on the ", "hover on ", "hover ",
        "move mouse to the ", "move mouse to ", "move cursor to the ", "move cursor to ",
        "point to the ", "point to ", "point at the ", "point at ",
        "select the ", "select ", "tap on the ", "tap on ", "tap ",
        "find on screen ", "where is the ", "where is ", "locate the ", "locate ",
        "can you spot ", "spot the ", "find it and click ",
        "find the ", "spot it ", "find and click the ", "find and click ",
    ]
    for prefix in prefixes:
        if task_lower.startswith(prefix):
            target = task_lower[len(prefix):].strip().rstrip(".")
            break

    # Also strip trailing "for me", "please", etc.
    for suffix in [" for me", " please", " now", " right now"]:
        if target.endswith(suffix):
            target = target[:-len(suffix)].strip()

    return target


# ── Main ─────────────────────────────────────────────────────────────────────

def run(task, app="", ctx=""):
    """Main entry point for mouse control skill."""
    try:
        import pyautogui
    except ImportError:
        return "pyautogui not installed. Run: pip3 install pyautogui"

    # Safety
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.1

    task_lower = task.lower().strip()

    # ── Scroll commands (no vision needed) ──
    if "scroll down" in task_lower:
        amount = 5
        nums = re.findall(r'(\d+)', task_lower)
        if nums:
            amount = min(int(nums[0]), 50)
        pyautogui.scroll(-amount)
        return f"Scrolled down {amount} clicks."

    if "scroll up" in task_lower:
        amount = 5
        nums = re.findall(r'(\d+)', task_lower)
        if nums:
            amount = min(int(nums[0]), 50)
        pyautogui.scroll(amount)
        return f"Scrolled up {amount} clicks."

    if "scroll left" in task_lower:
        pyautogui.hscroll(-5)
        return "Scrolled left."

    if "scroll right" in task_lower:
        pyautogui.hscroll(5)
        return "Scrolled right."

    # ── Describe screen ──
    if any(w in task_lower for w in [
        "find on screen", "what can i click", "show elements",
        "what's on screen", "what is on screen", "what do you see",
    ]):
        desc = _describe_screen()
        return f"Here's what I see on screen:\n{desc}"

    # ── Move to specific coordinates ──
    coord_match = re.search(r'(?:move|go|cursor|mouse|click)\s+(?:to\s+|at\s+)?(\d{2,4})\s*[,\s]\s*(\d{2,4})', task_lower)
    if coord_match and not any(w in task_lower for w in ["the ", "button", "icon", "menu", "bar", "link"]):
        x, y = int(coord_match.group(1)), int(coord_match.group(2))
        if _validate_coordinates(x, y):
            if "click" in task_lower:
                pyautogui.click(x, y)
                return f"Clicked at ({x}, {y})."
            else:
                pyautogui.moveTo(x, y, duration=0.3)
                return f"Moved cursor to ({x}, {y})."
        return f"Coordinates ({x}, {y}) are outside screen bounds."

    # ── Extract target description ──
    target = _extract_target(task_lower)
    if not target or len(target) < 2:
        return "What should I click? Describe what you see on screen — a button, link, icon, or text."

    # ── Find element via vision ──
    coords, error = _find_element(target)
    if error:
        return error

    x, y = coords

    # ── Determine action ──
    if any(w in task_lower for w in ["right click", "right-click"]):
        pyautogui.moveTo(x, y, duration=0.3)
        time.sleep(0.1)
        pyautogui.rightClick()
        return f"Right-clicked '{target}' at ({x}, {y})."

    elif any(w in task_lower for w in ["double click", "double-click"]):
        pyautogui.moveTo(x, y, duration=0.3)
        time.sleep(0.1)
        pyautogui.doubleClick()
        return f"Double-clicked '{target}' at ({x}, {y})."

    elif any(w in task_lower for w in ["hover", "move mouse", "move cursor", "point to", "point at"]):
        pyautogui.moveTo(x, y, duration=0.3)
        return f"Moved cursor to '{target}' at ({x}, {y})."

    else:
        # Default: single click with visible move
        pyautogui.moveTo(x, y, duration=0.3)
        time.sleep(0.1)
        pyautogui.click()
        return f"Clicked '{target}' at ({x}, {y})."
