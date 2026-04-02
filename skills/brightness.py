"""Control screen brightness via macOS CoreGraphics gamma (works on all displays including external)"""
SKILL_NAME = "brightness"
SKILL_TRIGGERS = ["brightness", "screen bright", "dim screen", "brighten", "dark screen"]
SKILL_DESCRIPTION = "Adjust screen brightness"

import re, ctypes

def run(task, app="", ctx=""):
    t = task.lower()
    if any(w in t for w in ["max", "full", "100"]):
        level = 1.0
    elif any(w in t for w in ["dim", "low", "dark", "minimum", "min"]):
        level = 0.2
    elif any(w in t for w in ["half", "50", "medium"]):
        level = 0.5
    else:
        nums = re.findall(r'\d+', t)
        if nums:
            level = min(int(nums[0]), 100) / 100
        else:
            level = 0.7
    try:
        CG = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        CG.CGMainDisplayID.restype = ctypes.c_uint32
        CG.CGSetDisplayTransferByFormula.argtypes = [
            ctypes.c_uint32,
            ctypes.c_float, ctypes.c_float, ctypes.c_float,
            ctypes.c_float, ctypes.c_float, ctypes.c_float,
            ctypes.c_float, ctypes.c_float, ctypes.c_float,
        ]
        CG.CGSetDisplayTransferByFormula.restype = ctypes.c_int32
        display = CG.CGMainDisplayID()
        result = CG.CGSetDisplayTransferByFormula(display,
            0.0, level, 1.0,
            0.0, level, 1.0,
            0.0, level, 1.0)
        if result == 0:
            return f"Brightness set to {int(level * 100)}%"
        return f"Brightness error: CoreGraphics returned {result}"
    except Exception as e:
        return f"Brightness error: {e}"
