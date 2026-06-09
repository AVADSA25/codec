#!/usr/bin/env python3
"""Throwaway preview: fire each overlay state at the running Swift CODECOverlay
NSPanel by appending events to ~/.codec/overlay_events.jsonl. Run while
`pm2 status codec-overlay` is online to watch the redesigned HUD live.

    python3 tools/overlay_preview.py
"""
import json
import os
import sys
import time

EVENTS = os.path.expanduser("~/.codec/overlay_events.jsonl")
HOLD = float(sys.argv[1]) if len(sys.argv) > 1 else 6.0  # seconds each state stays


def fire(ev, label, hold=HOLD):
    # For persistent states (recording/live/refining/transcribing) re-fire with a
    # duration so they hold for `hold` seconds then auto-clear between samples.
    with open(EVENTS, "a") as f:
        f.write(json.dumps(ev) + "\n")
    print(f"  → [{label}]")
    time.sleep(hold)
    # clear between samples so each is seen in isolation
    with open(EVENTS, "a") as f:
        f.write(json.dumps({"type": "hide"}) + "\n")
    time.sleep(0.8)


seq = [
    ("1 · NOTIFY (orange glass pill)",
     {"type": "notify", "text": "New CODEC overlay", "color": "#E8711A", "duration": HOLD}),
    ("2 · TOGGLE ON — CODEC wordmark + shortcut chips",
     {"type": "toggle_on", "duration": HOLD,
      "shortcuts": "F18=voice  F16=text  **=screen  ++=doc  --=chat"}),
    ("3 · RECORDING — orange mark + pulsing red dot",
     {"type": "recording_start", "title": "Listening"}),
    ("4 · TRANSCRIBING — blue, breathing mark",
     {"type": "transcribing", "text": "Transcribing…"}),
    ("5 · LIVE dictate — red mark",
     {"type": "live"}),
    ("6 · REFINING dictate — blue",
     {"type": "refining"}),
    ("7 · SKILL FIRED — orange",
     {"type": "skill_fired", "name": "philips_hue", "duration": HOLD}),
    ("8 · ANALYZING SCREEN — blue notify",
     {"type": "notify", "text": "Analyzing your screen…", "color": "#0A84FF", "duration": HOLD}),
    ("9 · SIGNING OUT — red wordmark",
     {"type": "toggle_off", "duration": HOLD}),
]

print(f"CODEC overlay preview ({HOLD:.0f}s each) — watch bottom-center of your screen:")
for label, ev in seq:
    fire(ev, label)
print("done.")
