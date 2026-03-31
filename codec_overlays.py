"""CODEC Overlays — AppKit-based overlays that float above fullscreen apps.
Falls back to tkinter if PyObjC is not available."""
import os
import subprocess
import sys


def _has_appkit():
    """Check if PyObjC is available for native overlays."""
    try:
        import AppKit  # noqa: F401
        return True
    except ImportError:
        return False


# ── Native AppKit overlay (works above fullscreen) ──────────────────────

def _appkit_overlay(text, color="#E8711A", duration=2500, font_size=13, bold=False, subtitle=""):
    """Launch a native NSPanel overlay that floats above everything including fullscreen."""
    dur_sec = duration / 1000.0 if duration else 0
    s = f'''
import os, sys, time, threading
from AppKit import (NSApplication, NSPanel, NSColor, NSTextField, NSFont,
                    NSView, NSMakeRect, NSScreen, NSBezierPath,
                    NSFloatingWindowLevel, NSBorderlessWindowMask,
                    NSNonactivatingPanelMask, NSUtilityWindowMask)
from Foundation import NSTimer, NSRunLoop, NSDefaultRunLoopMode

_text = os.environ["OVERLAY_TEXT"]
_color_hex = os.environ["OVERLAY_COLOR"]
_subtitle = os.environ.get("OVERLAY_SUBTITLE", "")

def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) / 255.0 for i in (0, 2, 4))

app = NSApplication.sharedApplication()
app.setActivationPolicy_(2)  # NSApplicationActivationPolicyAccessory

screen = NSScreen.mainScreen()
sf = screen.frame()
w, h = 440, 84
x = (sf.size.width - w) / 2
y = 50  # distance from bottom

panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
    NSMakeRect(x, y, w, h),
    NSBorderlessWindowMask | NSNonactivatingPanelMask | NSUtilityWindowMask,
    2, False  # NSBackingStoreBuffered
)
panel.setLevel_(25)  # kCGScreenSaverWindowLevel — above fullscreen
panel.setOpaque_(False)
panel.setHasShadow_(True)
panel.setAlphaValue_(0.95)
panel.setBackgroundColor_(NSColor.clearColor())
panel.setIgnoresMouseEvents_(True)
panel.setCollectionBehavior_(1 << 0 | 1 << 4)  # canJoinAllSpaces | fullScreenAuxiliary

# Content view with rounded rect background
class OverlayView(NSView):
    def drawRect_(self, rect):
        bg = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.04, 0.04, 0.04, 0.95)
        bg.setFill()
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, 10, 10)
        path.fill()
        r, g, b = hex_to_rgb(_color_hex)
        border = NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 0.8)
        border.setStroke()
        inset = NSMakeRect(0.5, 0.5, rect.size.width - 1, rect.size.height - 1)
        bp = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(inset, 10, 10)
        bp.setLineWidth_(1.0)
        bp.stroke()

view = OverlayView.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
panel.setContentView_(view)

r, g, b = hex_to_rgb(_color_hex)
tc = NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 1.0)

ty = 30 if not _subtitle else 42
font_w = {font_size}
font = NSFont.boldSystemFontOfSize_(font_w) if {str(bold).lower()} else NSFont.systemFontOfSize_(font_w)
label = NSTextField.alloc().initWithFrame_(NSMakeRect(10, ty, w - 20, 30))
label.setStringValue_(_text)
label.setFont_(font)
label.setTextColor_(tc)
label.setBackgroundColor_(NSColor.clearColor())
label.setBezeled_(False)
label.setEditable_(False)
label.setAlignment_(1)  # center
view.addSubview_(label)

if _subtitle:
    sub = NSTextField.alloc().initWithFrame_(NSMakeRect(10, 14, w - 20, 20))
    sub.setStringValue_(_subtitle)
    sub.setFont_(NSFont.systemFontOfSize_(13))
    sub.setTextColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(0.7, 0.7, 0.7, 1.0))
    sub.setBackgroundColor_(NSColor.clearColor())
    sub.setBezeled_(False)
    sub.setEditable_(False)
    sub.setAlignment_(1)
    view.addSubview_(sub)

panel.orderFrontRegardless()

if {dur_sec} > 0:
    def close_panel(timer):
        panel.close()
        app.terminate_(None)
    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        {dur_sec}, app, close_panel, None, False
    )

app.run()
'''
    env = {**os.environ, "OVERLAY_TEXT": text, "OVERLAY_COLOR": color, "OVERLAY_SUBTITLE": subtitle}
    return subprocess.Popen([sys.executable, "-c", s], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)


# ── Tkinter fallback ────────────────────────────────────────────────────

def _tk_overlay(text, color="#E8711A", duration=2500):
    d = f"root.after({duration}, root.destroy)" if duration else ""
    s = f"""
import os, tkinter as tk
root=tk.Tk()
root.overrideredirect(True)
root.attributes('-topmost',True)
root.attributes('-alpha',0.95)
root.configure(bg='#0a0a0a')
sw=root.winfo_screenwidth()
sh=root.winfo_screenheight()
w,h=440,84
x=(sw-w)//2
y=sh-130
root.geometry(f'{{w}}x{{h}}+{{x}}+{{y}}')
_color=os.environ['OVERLAY_COLOR']
_text=os.environ['OVERLAY_TEXT']
c=tk.Canvas(root,bg='#0a0a0a',highlightthickness=0,width=w,height=h)
c.pack()
c.create_rectangle(1,1,w-1,h-1,outline=_color,width=1)
c.create_text(w//2,h//2,text=_text,fill=_color,font=('Helvetica',13))
{d}
root.mainloop()
"""
    env = {**os.environ, "OVERLAY_COLOR": color, "OVERLAY_TEXT": text}
    return subprocess.Popen([sys.executable, "-c", s], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)


def _tk_recording(key_label):
    s = """
import os, tkinter as tk
_key=os.environ['OVERLAY_KEY_LABEL']
root=tk.Tk()
root.overrideredirect(True)
root.attributes('-topmost',True)
root.attributes('-alpha',0.95)
root.configure(bg='#0a0a0a')
sw=root.winfo_screenwidth()
sh=root.winfo_screenheight()
w,h=440,84
x=(sw-w)//2
y=sh-130
root.geometry(f'{w}x{h}+{x}+{y}')
cv=tk.Canvas(root,bg='#0a0a0a',highlightthickness=0,width=w,height=h)
cv.pack()
cv.create_rectangle(1,1,w-1,h-1,outline='#E8711A',width=1)
dot=cv.create_oval(14,29,27,42,fill='#ff3b3b',outline='')
cv.create_text(w//2+8,42,text='\U0001f3a4  Recording — release ' + _key + ' to send',fill='#eeeeee',font=('Helvetica',13))
on=[True]
def pulse():
    on[0]=not on[0]
    cv.itemconfig(dot,fill='#ff3b3b' if on[0] else '#550000')
    root.after(400,pulse)
pulse()
root.mainloop()
"""
    env = {**os.environ, "OVERLAY_KEY_LABEL": key_label}
    return subprocess.Popen([sys.executable, "-c", s], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)


# ── Public API ──────────────────────────────────────────────────────────

_USE_APPKIT = _has_appkit()


def show_overlay(text, color="#E8711A", duration=2500):
    if _USE_APPKIT:
        return _appkit_overlay(text, color, duration)
    return _tk_overlay(text, color, duration)


def show_recording_overlay(key_label="F18"):
    if _USE_APPKIT:
        return _appkit_overlay(f"\U0001f3a4  Recording — release {key_label} to send", "#E8711A", duration=0)
    return _tk_recording(key_label)


def show_processing_overlay(text="Transcribing...", duration=4000):
    if _USE_APPKIT:
        return _appkit_overlay(f"\u26a1 {text}", "#00aaff", duration)
    # tkinter fallback
    s = f"""
import os, tkinter as tk
_text=os.environ['OVERLAY_TEXT']
root=tk.Tk()
root.overrideredirect(True)
root.attributes('-topmost',True)
root.attributes('-alpha',0.95)
root.configure(bg='#0a0a0a')
sw=root.winfo_screenwidth()
sh=root.winfo_screenheight()
w,h=440,84
x=(sw-w)//2
y=sh-130
root.geometry(f'{{w}}x{{h}}+{{x}}+{{y}}')
cv=tk.Canvas(root,bg='#0a0a0a',highlightthickness=0,width=w,height=h)
cv.pack()
cv.create_rectangle(1,1,w-1,h-1,outline='#00aaff',width=1)
cv.create_text(w//2,h//2,text='\u26a1 '+_text,fill='#00aaff',font=('Helvetica',13))
root.after({duration},root.destroy)
root.mainloop()
"""
    env = {**os.environ, "OVERLAY_TEXT": text}
    subprocess.Popen([sys.executable, "-c", s], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)


def show_toggle_overlay(is_on, shortcuts=""):
    color = '#E8711A' if is_on else '#ff3333'
    label = 'C O D E C' if is_on else 'S I G N I N G   O U T'
    dur = 3000 if is_on else 1500
    import threading
    snd = '/System/Library/Sounds/Blow.aiff' if is_on else '/System/Library/Sounds/Funk.aiff'
    threading.Thread(
        target=lambda: subprocess.run(['afplay', snd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
        daemon=True
    ).start()
    if _USE_APPKIT:
        return _appkit_overlay(label, color, dur, font_size=18, bold=True, subtitle=shortcuts)
    # tkinter fallback
    s = f"""
import os, tkinter as tk
_color=os.environ['OVERLAY_COLOR']
_label=os.environ['OVERLAY_LABEL']
_shortcuts=os.environ.get('OVERLAY_SHORTCUTS','')
root=tk.Tk()
root.overrideredirect(True)
root.attributes('-topmost',True)
root.attributes('-alpha',0.95)
root.configure(bg='#0a0a0a')
sw=root.winfo_screenwidth()
sh=root.winfo_screenheight()
w,h=440,84
x=(sw-w)//2
y=sh-130
root.geometry(f'{{w}}x{{h}}+{{x}}+{{y}}')
cv=tk.Canvas(root,bg='#0a0a0a',highlightthickness=0,width=w,height=h)
cv.pack()
cv.create_rectangle(1,1,w-1,h-1,outline=_color,width=1)
cv.create_text(w//2,39 if not _shortcuts else 24,text=_label,fill=_color,font=('Helvetica',18,'bold'))
if _shortcuts: cv.create_text(w//2,55,text=_shortcuts,fill='#aaaaaa',font=('Helvetica',13))
root.after({dur},root.destroy)
root.mainloop()
"""
    env = {**os.environ, "OVERLAY_COLOR": color, "OVERLAY_LABEL": label, "OVERLAY_SHORTCUTS": shortcuts}
    subprocess.Popen([sys.executable, "-c", s], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
