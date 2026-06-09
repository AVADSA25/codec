# CODEC Overlay Redesign + Chat Action Row

**Date:** 2026-06-09 · **Status:** implemented (uncommitted), pending final visual sign-off + F16 typing test.

## 1. What & why

Two user-facing surfaces were dated and partly broken:

1. **Keyboard/HUD overlays** (F13 toggle, F18 recording, transcribing, SIGNING OUT,
   the 3 CODEC Dictate pills, skill-fired) looked like "Windows 98" (square tkinter
   boxes, flat black, 1px border) **and did not appear over fullscreen apps**.
2. **Chat message action buttons** (`/chat`) were corner-floating absolute icons that
   clipped off-screen and didn't form a clean row; the Speak button was unstyled.

### Root cause (overlays) — one regression, two symptoms
CODEC already ships a **Swift `CODECOverlay` NSPanel** (PM2 `codec-overlay`) that renders
rounded, blurred, branded HUDs and **floats over fullscreen** via
`collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]` + screen-saver window level.
Commit `faf3bef` (dead-code cleanup) deleted the event-writers that fed it, so since then
only `skill_fired` reached it — every other overlay fell through to the tkinter path, which
is both ugly *and* can't beat fullscreen (`-topmost` is not Space-aware). **Both complaints
collapse to: re-feed the Swift renderer.**

## 2. Architecture

`codec_overlays.py` (and `codec_dictate.py`) **emit JSON event lines** to
`~/.codec/overlay_events.jsonl`; the Swift NSPanel polls that file every 0.15s and renders.
tkinter is kept as an **automatic fallback** when the Swift process isn't running
(`_swift_alive()` = cached `pgrep`), and the whole thing reverts via one flag
(`codec_overlays._USE_SWIFT = False`).

```
codec.py / codec_dictate.py / codec_voice.py / codec_core.py
        │  show_*()  →  codec_overlays._emit({...})
        ▼
~/.codec/overlay_events.jsonl   (append-only JSON lines)
        ▼  poll 0.15s
swift-overlay (CODECOverlay NSPanel)  →  glass HUD over fullscreen
```

### Event types
`toggle_on{shortcuts,duration?}` · `toggle_off{duration?}` · `recording_start{title?,subtitle?}` ·
`recording_stop` · `transcribing{text,duration?}` · `live` · `live_stop` · `refining` ·
`skill_fired{name,duration?}` · `notify{text,color,duration}` · `hide` ·
`input_request{id,prompt}` (F16).

## 3. Visual system (Swift `OverlayPanel`)
- Glass: `NSVisualEffectView .hudWindow`, **forced dark** (`.darkAqua`) so text reads on a
  light desktop; 24px corner radius; `maskImage` clips blur **and** shadow (transparent corners).
- CODEC hexagon mark (`~/.codec/overlay_mark.png`, template-tinted per state), pinned left.
- Title **centered in the full pill**, **accent-colored** (orange/red/blue — never white),
  soft dark halo for legibility.
- Shortcut hints render as **chips** (`F18·voice` …), not a monospace run.
- **Uniform fixed frame** (620×128) for every state.
- Per-state: toggle ON = orange "CODEC" + chips; SIGNING OUT = red; recording = orange +
  pulsing dot; transcribing/refining = blue (breathing); notify = mapped color.

### F16 — branded glass input panel (`InputPanel`)
Focusable borderless glass panel (mark + text field + Send; Enter submits, Esc cancels).
Triggered by `input_request`; writes the typed text to `~/.codec/overlay_input_<id>.json` and
an `.ack` on appear. `codec_core.get_text_dialog()` emits the request, waits ≤2s for the ack
(else **fast-falls-back to the native osascript dialog**), then waits for the reply. Contract
preserved: returns the typed string, `""` on cancel.

## 4. Files changed
| File | Change |
|---|---|
| `swift-overlay/Sources/main.swift` | redesigned `OverlayPanel` + new `InputPanel`; event handling |
| `codec_overlays.py` | `_emit`/`_swift_alive`/`_play_sound`/`_ensure_mark`; 4 public fns route to Swift + `show_recording_stop`/`hide_overlay`/`show_live_overlay`/`show_refining_overlay`; tkinter fallback kept |
| `codec.py` | F18 release + stop-voice emit `recording_stop` |
| `codec_dictate.py` | Listening / Transcribing / LIVE delegate to `codec_overlays` |
| `codec_core.py` | `get_text_dialog()` → Swift `InputPanel` with osascript fallback |
| `codec_chat.html` | message buttons → one inline `.msg-meta` action row (copy/edit/regen/speak) |
| `tests/test_overlays.py` | 7 new routing tests (TDD) |
| `tools/overlay_preview.py` | dev preview harness |

No skill-manifest gate applies (engine modules, not `skills/`).

## 5. Test + rollback
- `tests/test_overlays.py` (7) — emit routing + tkinter fallback. Green.
- Live preview: `python3 tools/overlay_preview.py [seconds]`.
- Rollback: `codec_overlays._USE_SWIFT = False` reverts all overlays to tkinter instantly;
  `git checkout swift-overlay/` + `swift build -c release` + `pm2 restart codec-overlay`
  restores the prior binary. tkinter path never deleted.

## 6. Open items
- **F16 typing/focus** needs a human test (couldn't screen-capture under Screen Recording perms).
- `_live_overlay_script()` in `codec_dictate.py` is now dead code (safe to delete in PR).
- Restart to deploy: `pm2 restart codec-overlay open-codec codec-dictate`.
