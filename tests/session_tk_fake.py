"""A stand-in for tkinter for the session approval tests (P-060).

codec_session draws its approval dialogs with tkinter. No test may open a real
window on the owner's screen, and CI runners have no display, so the tests put
this module in sys.modules["tkinter"]. It runs the dialog's after() callbacks on
the real clock inside mainloop() and can click a button by its label at a given
time. Everything else a dialog calls (title, geometry, create_text, pack ...) is
accepted and ignored.
"""
from __future__ import annotations

import heapq
import itertools
import time
import types


class _Widget:
    def __init__(self, master=None, **kw):
        self.master = master
        self.kw = kw

    def __getattr__(self, name):
        return lambda *a, **k: None


class Tk:
    """One fake window: a timer queue, a mainloop, and the dialog's buttons."""

    def __init__(self, module):
        self.module = module
        self.buttons = []          # (text, command), in creation order
        self.clicked = []          # labels clicked, in order
        self.handlers = {}         # protocol name -> callback
        self.quit_called = False
        self.destroyed = False
        self.safety_stop = False   # mainloop gave up after max_seconds
        self.opened_at = time.monotonic()
        self._timers = []
        self._seq = itertools.count()
        module.roots.append(self)
        for delay, what in module.clicks:
            if callable(what):
                self.after(int(delay * 1000), lambda w=what: w(self))
            else:
                self.after(int(delay * 1000), lambda label=what: self.click(label))

    def __getattr__(self, name):
        # title, overrideredirect, attributes, configure, geometry, focus_force,
        # winfo_screenwidth, winfo_screenheight ...
        return lambda *a, **k: 1000

    def after(self, ms, fn):
        heapq.heappush(self._timers, (time.monotonic() + ms / 1000.0, next(self._seq), fn))
        return "after#%d" % len(self._timers)

    def protocol(self, name, fn):
        self.handlers[name] = fn

    def click(self, label):
        if label == "close":
            self.clicked.append(label)
            self.handlers["WM_DELETE_WINDOW"]()
            return
        for text, command in self.buttons:
            if label in text:
                self.clicked.append(label)
                command()
                return
        raise AssertionError(f"no button labelled {label!r}")

    def quit(self):
        self.quit_called = True

    def destroy(self):
        self.destroyed = True

    def mainloop(self):
        stop_at = time.monotonic() + self.module.max_seconds
        while not self.quit_called:
            now = time.monotonic()
            if now >= stop_at:
                self.safety_stop = True
                return
            if self._timers and self._timers[0][0] <= now:
                _, _, fn = heapq.heappop(self._timers)
                fn()
                continue
            nxt = self._timers[0][0] if self._timers else stop_at
            time.sleep(max(0.0, min(0.01, nxt - now)))


def module(clicks=(), max_seconds=30.0):
    """A fake tkinter module. clicks: (seconds after the window opens, button
    label or "close" or a callable taking the window)."""
    mod = types.ModuleType("tkinter")
    mod.roots = []
    mod.clicks = list(clicks)
    mod.max_seconds = max_seconds
    mod.Tk = lambda: Tk(mod)
    mod.Canvas = _Widget
    mod.Frame = _Widget

    class Button(_Widget):
        def __init__(self, master=None, **kw):
            super().__init__(master, **kw)
            mod.roots[-1].buttons.append((kw.get("text", ""), kw.get("command")))

    mod.Button = Button
    return mod
