"""CODEC Vision Mouse Control — voice-controlled cursor via screen vision.

Say "click the submit button", "move mouse to the search bar", "scroll down",
"double click the file" — CODEC sees your screen and acts.

Architecture: screenshot -> Qwen Vision -> coordinates -> pyautogui -> confirm
"""

SKILL_NAME = "mouse_control"
SKILL_DESCRIPTION = "Control mouse cursor by voice using screen vision — click, move, scroll, drag any element you can see"
SKILL_TRIGGERS = [
    # Direct click commands
    "click on", "click the", "click button", "press the button",
    "click where it says", "tap on", "select the",
    # Mouse movement
    "move mouse", "move cursor", "mouse to", "cursor to",
    # Right / double click
    "right click", "right-click", "double click", "double-click",
    # Scroll
    "scroll down", "scroll up", "scroll left", "scroll right",
    # Hover
    "hover over", "hover on", "point to", "point at",
    # Screen discovery
    "find on screen", "where is the", "locate the",
    "can you spot", "spot it", "find it and click",
    # Natural conversational triggers (how real users talk)
    "click it", "click that", "click for me",
    "control my mouse", "control the mouse", "take control of my mouse",
    "use my mouse", "move my mouse",
    "click it for me", "can you click",
    "find the button", "find that button",
    "look at my screen and click", "see my screen and click",
    "click on the page", "click on screen",
    "i can't find", "help me find",
]

import subprocess
import base64
import os
import re
import time
import logging
import ctypes
import ctypes.util

log = logging.getLogger("codec.mouse_control")

# ── Native macOS click via CoreGraphics (bypasses pyautogui accessibility issues) ──
_cg = None
try:
    _cg_lib = ctypes.util.find_library('CoreGraphics')
    if _cg_lib:
        _cg = ctypes.cdll.LoadLibrary(_cg_lib)
        # CRITICAL: Set proper argtypes/restype to avoid 64-bit pointer truncation on ARM64

        class _CGPoint(ctypes.Structure):
            _fields_ = [('x', ctypes.c_double), ('y', ctypes.c_double)]

        # CGEventRef CGEventCreateMouseEvent(CGEventSourceRef, CGEventType, CGPoint, CGMouseButton)
        _cg.CGEventCreateMouseEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint32, _CGPoint, ctypes.c_uint32]
        _cg.CGEventCreateMouseEvent.restype = ctypes.c_void_p

        # void CGEventPost(CGEventTapLocation, CGEventRef)
        _cg.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
        _cg.CGEventPost.restype = None

        # void CGEventSetIntegerValueField(CGEventRef, CGEventField, int64_t)
        _cg.CGEventSetIntegerValueField.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int64]
        _cg.CGEventSetIntegerValueField.restype = None

        # void CFRelease(CFTypeRef)
        _cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library('CoreFoundation'))
        _cf.CFRelease.argtypes = [ctypes.c_void_p]
        _cf.CFRelease.restype = None
except Exception:
    _cg = None


def _native_click(x, y, button="left", double=False):
    """Click using CoreGraphics events — more reliable than pyautogui on macOS."""
    if not _cg:
        return False
    try:
        point = _CGPoint(float(x), float(y))
        if button == "right":
            down_type, up_type, btn = 3, 4, 1  # kCGEventRightMouseDown/Up
        else:
            down_type, up_type, btn = 1, 2, 0  # kCGEventLeftMouseDown/Up

        # Move cursor first
        move_evt = _cg.CGEventCreateMouseEvent(None, 5, point, 0)
        if move_evt:
            _cg.CGEventPost(0, move_evt)
            _cf.CFRelease(move_evt)
        time.sleep(0.05)

        clicks = 2 if double else 1
        for i in range(clicks):
            down_evt = _cg.CGEventCreateMouseEvent(None, down_type, point, btn)
            if down_evt:
                _cg.CGEventSetIntegerValueField(down_evt, 1, i + 1)
                _cg.CGEventPost(0, down_evt)
                _cf.CFRelease(down_evt)
            time.sleep(0.02)
            up_evt = _cg.CGEventCreateMouseEvent(None, up_type, point, btn)
            if up_evt:
                _cg.CGEventSetIntegerValueField(up_evt, 1, i + 1)
                _cg.CGEventPost(0, up_evt)
                _cf.CFRelease(up_evt)
            if i < clicks - 1:
                time.sleep(0.05)
        log.info(f"Native CG click at ({x}, {y}) button={button} double={double}")
        return True
    except Exception as e:
        log.warning(f"Native click failed: {e}")
        return False

# ── Config ───────────────────────────────────────────────────────────────────
# Mouse control uses UI-TARS (UI-specialist model) on its own port.
# Qwen Vision (8082) stays for general image/document analysis.
try:
    from codec_config import cfg
    VISION_URL = cfg.get("ui_tars_base_url", "http://localhost:8082/v1").rstrip("/") + "/chat/completions"
    VISION_MODEL = cfg.get("ui_tars_model", "mlx-community/UI-TARS-1.5-7B-4bit")
except ImportError:
    VISION_URL = "http://localhost:8082/v1/chat/completions"
    VISION_MODEL = "mlx-community/UI-TARS-1.5-7B-4bit"

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


def _downscale_screenshot(image_b64, max_width=1920):
    """Downscale screenshot for vision processing (keeps aspect ratio).
    1920px keeps sidebar/menu text readable on Retina displays."""
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

        # PNG screenshots have alpha channel — convert to RGB for JPEG
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

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

    # Strip UI-TARS box tokens: <|box_start|>(x,y)<|box_end|>
    _resp = re.sub(r'<\|box_start\|>', '', vision_response)
    _resp = re.sub(r'<\|box_end\|>', '', _resp)

    # Normalize: strip quotes around numbers — model sometimes returns {"x": "847", "y": "523"}
    _resp = re.sub(r'"(-?\d+)"', r'\1', _resp)

    # Try JSON: {"x": 847, "y": 523} — also handles unquoted keys like {x: 847, y: 523}
    json_match = re.search(r'\{\s*"?x"?\s*:\s*(-?\d+)\s*,\s*"?y"?\s*:\s*(-?\d+)\s*\}', _resp)
    if json_match:
        coords = (int(json_match.group(1)), int(json_match.group(2)))

    # Try malformed JSON: {"x": 847, 523} (model sometimes omits "y":)
    if not coords:
        partial_match = re.search(r'\{\s*"?x"?\s*:\s*(-?\d+)\s*,\s*(-?\d+)\s*\}', _resp)
        if partial_match:
            coords = (int(partial_match.group(1)), int(partial_match.group(2)))

    # Try parentheses: (847, 523)
    if not coords:
        paren_match = re.search(r'\((-?\d+)\s*,\s*(-?\d+)\)', _resp)
        if paren_match:
            coords = (int(paren_match.group(1)), int(paren_match.group(2)))

    # Try "x: 847, y: 523" or "x=847, y=523" or loose format
    if not coords:
        xy_match = re.search(r'x\s*[:=]\s*(-?\d+)\s*[,;\s]+y?\s*[:=]?\s*(-?\d+)', _resp, re.IGNORECASE)
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
        # Check for "not found" response (-1, -1)
        if coords[0] < 0 or coords[1] < 0:
            return None

        x, y = coords
        sw, sh = _get_screen_size()
        max_downscaled_w = sw / scale if scale > 1.0 else sw

        if scale > 1.0 and x > max_downscaled_w:
            # Model returned coordinates in FULL resolution instead of downscaled image
            # Don't scale — they're already in screen space
            log.info(f"Coords ({x},{y}) exceed downscaled width {max_downscaled_w:.0f} — assuming full-res coords")
            return (x, y)

        # Scale coordinates back to actual screen resolution
        return (int(x * scale), int(y * scale))

    return None


def _validate_coordinates(x, y):
    """Check if coordinates are within screen bounds."""
    sw, sh = _get_screen_size()
    return 0 <= x <= sw and 0 <= y <= sh


def _find_element(description, retries=2):
    """Screenshot screen, ask vision to locate element, return (x, y) or (None, error).

    Retries with a refined prompt if the first attempt returns edge coordinates
    (likely wrong) or fails.
    """
    image_b64 = _take_screenshot()
    if not image_b64:
        return None, "Could not take screenshot."

    sw, sh = _get_screen_size()

    # Calculate the downscaled dimensions for the prompt (must match _downscale_screenshot max_width)
    img_w = min(sw, 1920)
    img_h = int(sh * img_w / sw) if sw > 0 else 1080

    for attempt in range(retries):
        if attempt == 0:
            # UI-TARS works best with simple action-oriented prompts
            prompt = f"Click on {description}"
        else:
            # Retry with slightly different phrasing
            prompt = f"Find and click the element labeled '{description}'"

        response, scale = _ask_vision(image_b64, prompt)
        if not response:
            return None, "Vision model did not respond. Is it running at localhost:8082?"

        log.info(f"Vision response for '{description}' (attempt {attempt+1}): {response[:200]} (scale={scale:.2f})")

        coords = _parse_coordinates(response, scale)
        if not coords:
            if attempt < retries - 1:
                log.info(f"Retrying vision for '{description}' — no valid coords")
                continue
            return None, f"Couldn't locate '{description}' on screen. Try describing it differently."

        x, y = coords

        # Check for suspicious edge coordinates (likely model confusion)
        # On the 1280-wide image, x < 10 pixels is almost certainly wrong
        raw_x = x / scale if scale > 1.0 else x
        if raw_x < 10 and attempt < retries - 1:
            log.info(f"Suspicious x={raw_x:.0f} (too close to left edge) — retrying")
            continue

        if not _validate_coordinates(x, y):
            if attempt < retries - 1:
                log.info(f"Coords ({x},{y}) outside screen — retrying")
                continue
            return None, f"Coordinates ({x}, {y}) are outside screen ({sw}x{sh}). Vision may have miscalculated."

        return (x, y), None

    return None, f"Couldn't locate '{description}' after {retries} attempts."


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
    """Extract the UI element description from a natural language command.

    Handles conversational requests like:
    'Hey CODEC, I'm on Cloudflare, can you click the SSL button for me?'
    → extracts 'SSL button'
    """
    import re as _re

    # ── Step 1: Split into sentences and process each one ────────────────
    sentences = _re.split(r'[.?!]+', task_lower)
    sentences = [s.strip() for s in sentences if s.strip()]

    # ── Step 2: Noise removal from each sentence ─────────────────────────
    noise_phrases = [
        # Filler / location
        "on my screen", "on the screen", "on screen",
        "for me please", "for me", "please",
        "i cannot find it", "i can't find it", "i can not find it",
        "anywhere", "right now", "now",
        # Preamble
        "can you look at it and", "can you look at it",
        "i'm looking at my screen and",
        "i need to find it",
        # Hedging
        "i mean", "i think",
        # Repeated action tail ("click on it", "click it")
        "click on it", "click it", "click on that", "click that",
        "press it", "tap it", "select it",
        # Positional context
        "on the top right", "on the top left", "on the bottom right", "on the bottom left",
        "at the top right", "at the top left", "at the bottom right", "at the bottom left",
        "in the top right", "in the top left", "in the bottom right", "in the bottom left",
        "on the right side", "on the left side",
        "on the top", "on the bottom", "on the right", "on the left",
        "at the top", "at the bottom",
        "in the corner", "in the middle", "in the center",
    ]

    def _clean_sentence(s):
        for phrase in sorted(noise_phrases, key=len, reverse=True):
            s = s.replace(phrase, " ")
        return " ".join(s.split()).strip()

    # ── Step 3: Action markers ───────────────────────────────────────────
    action_markers = [
        # Click variants
        "click on the ", "click on ", "click the ", "click button ",
        "click where it says ",
        "press the button ", "press the ", "press ",
        # Right / double click
        "right click on the ", "right click the ", "right click on ", "right click ",
        "right-click on the ", "right-click the ", "right-click ",
        "double click on the ", "double click the ", "double click on ", "double click ",
        "double-click on the ", "double-click the ", "double-click ",
        # Hover / move
        "hover over the ", "hover over ", "hover on the ", "hover on ", "hover ",
        "move mouse to the ", "move mouse to ", "move cursor to the ", "move cursor to ",
        "point to the ", "point to ", "point at the ", "point at ",
        # Select / tap
        "select the ", "select ", "tap on the ", "tap on ", "tap ",
        # Find / locate
        "where is the ", "where is ", "locate the ", "locate ",
        "find and click the ", "find and click ",
        "find the ", "find ",
        "look for the ", "look for ",
        # Generic (last resort)
        "click ",
    ]

    # Noise-only sentences to skip entirely
    skip_patterns = [
        r"^(can you )?(click|find|locate) it",
        r"^i (can'?t|cannot) find",
        r"^(hey |hi )?(codec|kodec)",
        r"^can you do it",
        r"^sorry",
    ]

    # ── Step 4: Extract target from each sentence, pick the best one ─────
    candidates = []
    for sent in sentences:
        cleaned = _clean_sentence(sent)
        if not cleaned or len(cleaned) < 3:
            continue
        # Skip pure noise sentences
        if any(_re.match(p, cleaned) for p in skip_patterns):
            if not any(m in cleaned for m in action_markers[:10]):
                continue

        # Try to find an action marker
        for marker in action_markers:
            idx = cleaned.find(marker)
            if idx >= 0:
                target = cleaned[idx + len(marker):].strip().rstrip(".,!?")
                if target and len(target) >= 2:
                    candidates.append(target)
                break  # Use first (longest) marker match per sentence

    # ── Step 5: Pick the best candidate ──────────────────────────────────
    # Prefer shorter, cleaner targets (less noise) — but at least 2 chars
    best_target = ""
    if candidates:
        # Remove duplicates, prefer the shortest non-trivial one
        # (shorter = cleaner extraction, longer = more noise leaked through)
        unique = list(dict.fromkeys(candidates))
        # Filter out single common words that are clearly noise
        noise_words = {"it", "that", "this", "them", "there", "here"}
        meaningful = [c for c in unique if c not in noise_words]
        if meaningful:
            # Prefer shortest meaningful target (least noise)
            best_target = min(meaningful, key=len)
        elif unique:
            best_target = unique[0]

    if not best_target:
        # Fallback: clean the whole thing and try once more
        full_cleaned = _clean_sentence(task_lower)
        for marker in action_markers:
            idx = full_cleaned.rfind(marker)
            if idx >= 0:
                best_target = full_cleaned[idx + len(marker):].strip().rstrip(".,!?")
                if best_target:
                    break
        if not best_target:
            best_target = task_lower

    # ── Step 6: Final cleanup ────────────────────────────────────────────
    noise_prefixes = [
        "hey codec ", "hey kodec ", "hi codec ", "codec ",
        "can you ", "could you ", "would you ",
        "the button ", "button the ", "the section ", "the tab ",
        "button called ", "button named ", "button labeled ",
        "section called ", "section named ",
        "button ", "section ", "the ",
    ]
    for prefix in noise_prefixes:
        if best_target.startswith(prefix):
            best_target = best_target[len(prefix):].strip()

    # Truncate at known noise boundaries that leaked through
    truncate_at = [
        " just read", " just look", " just click", " just find",
        " read the screen", " look at the screen", " on the screen",
        " can you click", " can you find", " can you locate",
        " i need to find", " i need to", " i cannot",
        " i can't", " i cant", " i can not", " do it",
        " and can you", " could you",
        " and click", " and find", " and select",
        " for me", " please",
        # Repeated action instructions ("click on X click on it")
        " click on it", " click it", " click on that", " click that",
        " press it", " press on it", " tap it", " tap on it",
        " select it", " find it", " locate it",
        # Location qualifiers ("upgrade on the top right")
        " on the top right", " on the top left", " on the bottom right", " on the bottom left",
        " on the right side", " on the left side", " on the top", " on the bottom",
        " at the top right", " at the top left", " at the bottom right", " at the bottom left",
        " at the top", " at the bottom", " in the top right", " in the top left",
        " in the bottom right", " in the bottom left",
        " on the right", " on the left",
        " in the corner", " in the middle", " in the center",
    ]
    for trunc in truncate_at:
        idx = best_target.find(trunc)
        if idx > 0:
            best_target = best_target[:idx].strip()

    # Remove context qualifiers ("on cloudflare", "on the page") — anywhere trailing
    best_target = _re.sub(r'\s+on\s+(cloudflare|the page|the website|chrome|safari|firefox)\b.*$', '', best_target)
    best_target = best_target.rstrip(".,!?").strip()

    log.info(f"Target extraction: '{task_lower[:80]}' → '{best_target}'")
    return best_target


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

    # ── Execute action with error handling ──
    try:
        if any(w in task_lower for w in ["right click", "right-click"]):
            pyautogui.moveTo(x, y, duration=0.3)
            time.sleep(0.1)
            if not _native_click(x, y, button="right"):
                pyautogui.rightClick()
            action_msg = f"Right-clicked '{target}' at ({x}, {y})."

        elif any(w in task_lower for w in ["double click", "double-click"]):
            pyautogui.moveTo(x, y, duration=0.3)
            time.sleep(0.1)
            if not _native_click(x, y, double=True):
                pyautogui.doubleClick()
            action_msg = f"Double-clicked '{target}' at ({x}, {y})."

        elif any(w in task_lower for w in ["hover", "move mouse", "move cursor", "point to", "point at"]):
            pyautogui.moveTo(x, y, duration=0.3)
            action_msg = f"Moved cursor to '{target}' at ({x}, {y})."

        else:
            # Default: single click — use native CG click with pyautogui fallback
            pyautogui.moveTo(x, y, duration=0.3)
            time.sleep(0.1)
            if not _native_click(x, y):
                pyautogui.click()
            action_msg = f"Clicked '{target}' at ({x}, {y})."

        log.info(f"Mouse action completed: {action_msg}")
        return action_msg

    except Exception as e:
        log.error(f"pyautogui action failed at ({x}, {y}): {e}")
        return f"Mouse action failed at ({x}, {y}): {e}. Check that Accessibility permissions are enabled for this app in System Preferences > Privacy & Security."
