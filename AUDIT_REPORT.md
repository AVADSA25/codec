# CODEC Competitive Audit Report
## Terminator + Fazm — What Can CODEC Learn?

**Date:** 2026-03-29
**Audited by:** Claude Sonnet 4.6
**Status:** Read-only analysis — no CODEC files modified

---

## Executive Summary

**Terminator** is a Windows-only Rust library for UI automation via Microsoft's UIA (UI Automation) API. It is architecturally irrelevant to CODEC's macOS platform — but contains breakthrough insights on **accessibility tree caching** that map directly to Apple's AXUIElement API.

**Fazm** is a macOS voice-first AI agent (Swift + TypeScript) built on Claude's ACP protocol. It is the closest thing to a direct CODEC competitor. Its audio pipeline, session warmup pattern, PTT state machine, and streaming architecture contain several immediately actionable techniques.

**Bottom line:** Neither project threatens CODEC's position directly. Terminator doesn't run on macOS. Fazm requires a Claude account and has no local LLM support, no skill system, and no Skill Forge. But both codebases contain surgical improvements we can extract.

---

## Part 1 — Terminator Audit

### Architecture

Terminator is a **Rust monorepo** (~215 source files) organized as a workspace:

```
crates/terminator/          Core library (Windows UIA, element model, selector engine)
crates/terminator-mcp-agent/ MCP server exposing Terminator tools to Claude/Cursor
crates/terminator-cli/       Workflow runner
crates/terminator-workflow-recorder/ Record human actions → deterministic code
packages/terminator-python/  PyO3 Python bindings (pip install terminator)
packages/terminator-nodejs/  Napi-RS Node.js bindings
```

**Language stack:** Rust core → PyO3 for Python / Napi-RS for Node.js
**Platform:** **Windows only.** macOS and Linux explicitly unsupported. All code is gated `#[cfg(target_os = "windows")]`.

### AX Layer — How the Accessibility Tree Works

Terminator uses Microsoft's **IUIAutomation COM interface** (Windows UIA). The macOS equivalent is Apple's `AXUIElement` C API — same concept, different implementation.

#### Tree Traversal — The Key Insight

The most important file in the entire repo is `tree_builder.rs`. It implements two modes:

**Uncached mode** (slow): One IPC round-trip per element property.
- 245 elements × ~15 properties × ~1ms/call = **~3.5 seconds**

**Cached mode** (fast): One IPC round-trip fetches ALL properties for ALL children.
- Same 245 elements = **~200ms** — a 17x speedup
- Implemented via UIA's `CacheRequest` — a batch descriptor telling the OS which properties to pre-load when you traverse children

```rust
// (From tree_builder.rs — Windows UIA version)
// Build cache request once
let cache_request = automation.create_cache_request()?;
cache_request.add_property(UIA_NamePropertyId)?;
cache_request.add_property(UIA_BoundingRectanglePropertyId)?;
cache_request.add_property(UIA_ControlTypePropertyId)?;
// ... 4 more properties

// Single call fetches children WITH all properties pre-loaded
let children = element.find_all_build_cache(TreeScope_Children, &condition, &cache_request)?;
// Now reading Name, Bounds, ControlType etc. = zero additional IPC calls
```

**The macOS equivalent:** AXUIElement has `AXUIElementCopyMultipleAttributeValues()` which does the same thing — fetch multiple attributes in a single Mach port round-trip. This is the exact same optimization, available to a Swift helper binary.

#### Element Identification — Selector Language

Terminator implements a full selector DSL with boolean algebra (Shunting Yard parser):

```
role:Button && name:OK                    # role + name
text:Submit                               # text content search
id:login-button                           # automation ID
process:chrome && role:Tab                # app-scoped search
RightOf(role:Label && name:Username)      # spatial selectors
role:Window >> role:Panel >> role:Button  # parent >> child traversal
role:Button || role:Checkbox              # OR
!role:Window                              # NOT
Nth(2, role:Button)                       # index-based
```

Resolution priority: cached tree first → UIA find methods → depth-limited search.

#### What Makes It Sub-100ms

1. **Batch property fetching** (one IPC → all properties) — 17x speedup
2. **Shallow search**: Limit depth to 5 for container discovery (apps are near root)
3. **Selective bounds loading**: Only load bounding rect for keyboard-focusable elements
4. **CPU yielding**: `thread::sleep(1ms)` every N elements — prevents system freeze
5. **Pure Rust**: No Python/JS overhead in the hot path

#### Element Interaction

**Clicking:**
- Primary: `GetClickablePoint()` — UIA's recommended click target
- Fallback: Element bounds center
- Sends `SendInput()` with normalized 0–65535 coordinates
- Validates clickability first (visible, enabled, in viewport)

**Typing:**
- Short text: keyboard events
- Long text: clipboard paste (faster)
- Reads back after typing to verify

**Focus restoration:**
- Saves focused element + caret position before automation
- Restores after — so user's cursor position is not disturbed

#### Python Bridge

The `terminator-python` package (PyO3) wraps the Rust library:

```python
pip install terminator
from terminator import Desktop
desktop = Desktop()
element = desktop.find_element("role:Button && name:OK")
element.click()
element.type_text("hello")
```

This is a production-quality Python API for accessibility automation on Windows.

#### macOS Verdict

Terminator provides **zero direct code we can copy** (Windows-only APIs throughout). But the **architecture patterns** — especially cached batch property fetching and the selector language — map 1:1 to what a macOS `AXUIElement` bridge in Swift would look like. The Python binding pattern (PyO3 or subprocess bridge) is exactly the integration model CODEC would use.

---

## Part 2 — Fazm Audit

### Architecture

Fazm is a **macOS desktop agent** built in three layers:

```
Desktop/Sources/          Swift/SwiftUI app (UI, audio, OS integration)
acp-bridge/src/           TypeScript bridge (Claude ACP protocol translation)
web/                      Next.js web dashboard + phone relay
Backend/src/              Rust backend (Cloudflare tunnel registry, auth)
```

**Language stack:** Swift (UI/audio) → JSON-lines stdio → TypeScript (Claude ACP) → Anthropic API
**Platform:** macOS 14.0+ only
**LLM:** Claude only (Anthropic API, OAuth, or Vertex AI)

### Audio Pipeline

#### Audio Capture — CoreAudio Direct (Not AVAudioEngine)

`AudioCaptureService.swift` uses `AudioDeviceIOProc` (CoreAudio's raw hardware callback) instead of `AVAudioEngine`.

**Why this matters:** `AVAudioEngine` creates implicit aggregate audio devices when active. This triggers Bluetooth A2DP → SCO handoff, degrading wireless headphone quality from stereo music mode to mono phone mode. CoreAudio IOProc avoids this entirely.

**Format:** 16-bit signed PCM at 16kHz — direct bytes, no codec overhead.

**Device resampling:** If hardware doesn't natively support 16kHz (common — most devices are 44.1kHz or 48kHz), `AVAudioConverter` resamples transparently.

**CODEC relevance:** CODEC uses Python's `sounddevice` + `sox`. The Bluetooth degradation issue is real. A Swift helper binary using CoreAudio IOProc would fix it, but it's optional — most CODEC users won't notice on wired mics.

#### STT — DeepGram WebSocket Streaming

`TranscriptionService.swift` streams audio to DeepGram's `nova-3` model over WebSocket.

**Key difference from CODEC's Whisper:** Fazm gets **interim transcripts** in ~500ms while the user is still speaking. CODEC's Whisper batch approach waits for speech to end, then transcribes — adding 1–3s of perceived latency.

**Transcript segments include:**
- `isFinal` / `speechFinal` flags
- Per-word timestamps + confidence
- Speaker diarization (mic channel vs system audio channel)
- `punctuatedWord` (auto-punctuated output)

**Smart transcription cleanup (31 built-in rules):**
```swift
"dot com"  → ".com"
"at sign"  → "@"
"dot swift" → ".swift"
"open paren" → "("
// ... 27 more technical term mappings
```

**CODEC relevance:** High. We can apply the same cleanup rules to Whisper output without switching STT providers.

#### Push-to-Talk State Machine

`PushToTalkManager.swift` implements a full state machine on the Option (⌥) key:

```
idle → [Option down] → listening → [Option up] → finalizing → send → idle

LOCK MODE:
idle → [Option double-tap within 400ms] → lockedListening → [Option tap] → send
```

Additional details:
- 500ms debounce between PTT activations (prevents audio subsystem crashes from rapid toggling)
- 5-minute max PTT duration (prevents stuck state)
- Displays interim transcripts in real-time during `listening` phase
- Left Control delayed activation (distinguish Control-alone from Ctrl+key combos)

**CODEC relevance:** CODEC's F18 hold-to-talk already implements this concept. The **lock mode** (double-tap to stay listening) is not implemented in CODEC and would be useful.

### Agent Framework — ACP Bridge

Fazm runs a long-lived TypeScript subprocess (`acp-bridge`) that speaks Claude's Agent Client Protocol (ACP). Swift communicates via JSON-lines over stdin/stdout.

**Session warmup:**
```typescript
// Before user speaks, pre-create Claude session in background
WarmupMessage { sessions: [{ key: "main", model: "claude-3-5-sonnet" }] }
// → ACP creates session, caches system prompt tokens
// → When user speaks: first response comes back 500ms faster
```

**Session reuse:** Each query reuses the same ACP session. System prompt is set once at creation and never resent. Context accumulates across turns.

**Prompt caching:** Tracks `cacheReadTokens` and `cacheWriteTokens`. Large system prompts (10K+ tokens) are cached by Anthropic, reducing cost and latency on subsequent turns.

**CODEC relevance:** CODEC Voice (`codec_voice.py`) could implement session warmup — pre-send the system prompt to the LLM before the user finishes speaking, so the LLM is ready by the time the transcription arrives.

### Streaming Protocol (JSON-lines over stdio)

All Fazm components communicate via newline-delimited JSON:

```
Swift → Bridge:  QueryMessage, ToolResultMessage, StopMessage, WarmupMessage
Bridge → Swift:  TextDeltaMessage, ToolUseMessage, ResultMessage, AuthRequiredMessage
```

Each text chunk streams individually — no buffering until complete. Tool calls are round-tripped: bridge → Swift → executes natively → Swift → bridge → Claude.

**CODEC relevance:** CODEC Voice already streams LLM output. The **tool round-trip** pattern (LLM requests tool → Python executes skill → result sent back → LLM continues) is exactly how CODEC's skill dispatch in voice calls should work, and is already partially implemented.

### Phone/Web Relay

```
Desktop (Swift) → cloudflared tunnel → Backend registry → WebSocket → Phone/Web
```

Fazm's relay is architecturally identical to CODEC's Cloudflare Tunnel setup. One Fazm-specific detail: **orphan cleanup on startup** — kill any `cloudflared tunnel --url` processes from previous app runs that may have survived a crash. CODEC's `codec_dashboard.py` doesn't do this.

### What Fazm Does NOT Have

- Local LLM support (Claude-only, requires API key or OAuth)
- Skill system (no install-your-own-skill concept)
- Skill Forge or code-to-skill converter
- 36 built-in skills (only: execute_sql, complete_task, delete_task via MCP)
- Wake word detection (PTT only)
- FTS5 memory search
- Any equivalent to CODEC Dictate (system-wide paste)
- Right-click text services (CODEC Assist)

---

## Part 3 — Feature Comparison Table

| Feature | CODEC | Terminator | Fazm |
|---------|-------|-----------|------|
| **Platform** | macOS | Windows only | macOS 14+ |
| **LLM support** | Any (local + cloud) | Any (via MCP to Claude) | Claude only |
| **Local LLM** | ✅ Ollama, MLX, LM Studio | ❌ | ❌ |
| **Voice input** | ✅ F18 hold-to-talk + wake word | ❌ | ✅ Option PTT + lock mode |
| **STT** | ✅ Local Whisper (batch) | ❌ | ✅ DeepGram streaming (interim results) |
| **TTS** | ✅ Kokoro 82M (local) | ❌ | ❌ (text only) |
| **Wake word** | ✅ "Hey CODEC" | ❌ | ❌ |
| **AX/UI automation** | ❌ (AppleScript only) | ✅ Windows UIA (sub-100ms, cached) | ✅ via MCP macOS-use server |
| **Screen reading** | ✅ Vision model (screenshot OCR) | ✅ OCR + UIA text patterns | ✅ screenshot tool via Claude |
| **Browser control** | ✅ Chrome AppleScript skills | ✅ Extension + WebSocket DOM | ✅ Playwright MCP |
| **Skill system** | ✅ 36 built-in + install-your-own | ❌ | ❌ minimal (3 DB tools) |
| **Skill Forge** | ✅ Code → skill conversion | ❌ | ❌ |
| **AI agent crews** | ✅ 5 pre-built + custom builder | ✅ Workflow recorder | ❌ |
| **Right-click services** | ✅ 8 text services | ❌ | ❌ |
| **Dictate (paste anywhere)** | ✅ Right CMD hold | ❌ | ❌ |
| **Phone dashboard** | ✅ PWA via Cloudflare | ❌ | ✅ via Cloudflare |
| **Memory / history** | ✅ FTS5 SQLite search | ❌ | basic (Firebase) |
| **Interim STT** | ❌ (batch Whisper only) | ❌ | ✅ real-time streaming |
| **PTT lock mode** | ❌ | ❌ | ✅ double-tap lock |
| **Session warmup** | ❌ | N/A | ✅ pre-create LLM session |
| **Transcription cleanup** | ❌ raw Whisper output | ❌ | ✅ 31 domain rules |
| **Audio: no BT degradation** | ❌ (uses sounddevice) | N/A | ✅ CoreAudio IOProc |
| **Open source** | ✅ MIT | ✅ Apache 2.0 | ✅ MIT |
| **Python API** | ✅ native | ✅ PyO3 | ❌ |
| **Cloudflare orphan cleanup** | ❌ | N/A | ✅ |

**CODEC's moat:** Local LLM support, 36-skill system, Skill Forge, CODEC Agents, CODEC Dictate, CODEC Assist (right-click), wake word, and the ability to run fully offline — none of which Terminator or Fazm offers.

**CODEC's gaps:** Real UI element access (AXUIElement), real-time streaming STT, PTT lock mode, session warmup for CODEC Voice.

---

## Part 4 — Actionable Recommendations

Ordered by impact × (1/effort).

---

### 1. Transcription Post-Processing Rules
**Impact: High | Effort: 1–2 hours | File: `codec.py` → `transcribe()`**

Fazm ships 31 domain-specific find-and-replace rules applied to every transcript. CODEC outputs raw Whisper text which often includes "dot com" for `.com`, "at sign" for `@`, "open paren" for `(`, etc.

Add a `_clean_transcript(text)` function after the Whisper API call:

```python
_TRANSCRIPT_FIXES = [
    (r'\bdot com\b', '.com'), (r'\bdot org\b', '.org'), (r'\bdot io\b', '.io'),
    (r'\bat sign\b', '@'), (r'\bopen paren\b', '('), (r'\bclose paren\b', ')'),
    (r'\bdot py\b', '.py'), (r'\bdot js\b', '.js'), (r'\bdot json\b', '.json'),
    # ... expand as needed
]
def _clean_transcript(text):
    for pattern, replacement in _TRANSCRIPT_FIXES:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text
```

Apply after `r.json().get("text", "")` in `transcribe()`. Zero latency cost, immediate UX improvement for technical users.

---

### 2. PTT Lock Mode (Double-Tap F18)
**Impact: High | Effort: 2–3 hours | File: `codec.py` → key listener**

Fazm's double-tap Option = stay in listening mode until next tap. CODEC currently requires holding F18 the whole time — uncomfortable for long dictations.

Implement: second F18 press within 400ms of first = locked listening. Next F18 tap finalizes.

```python
LOCK_DOUBLE_TAP_MS = 400
_last_voice_tap = 0.0
_voice_locked = False

def on_press(key):
    if key == KEY_VOICE:
        now = time.time() * 1000
        if not _voice_locked and (now - _last_voice_tap) < LOCK_DOUBLE_TAP_MS:
            _voice_locked = True
            push(lambda: show_overlay('Locked — tap again to send', '#E8711A', 10000))
            return
        _last_voice_tap = now
        if not _voice_locked:
            state["recording"] = True
            push(do_start_recording)

def on_release(key):
    if key == KEY_VOICE:
        if not _voice_locked:
            push(do_stop_voice)

# On second tap while locked:
# if _voice_locked: _voice_locked = False; push(do_stop_voice)
```

---

### 3. Cloudflare Orphan Cleanup at Startup
**Impact: Medium | Effort: 30 minutes | File: `codec_dashboard.py` → startup**

Fazm kills stale `cloudflared tunnel` processes on launch. CODEC doesn't. If the dashboard crashes, the old cloudflared process keeps the tunnel alive but pointing nowhere — new tunnel fails to bind.

Add to `codec_dashboard.py` startup (before launching cloudflared):

```python
import subprocess
subprocess.run(
    ["pkill", "-f", "cloudflared tunnel --url"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
)
time.sleep(0.5)  # Let OS reclaim the port
# then launch fresh cloudflared
```

---

### 4. CODEC Voice Session Warmup
**Impact: High | Effort: 3–4 hours | File: `codec_voice.py`**

Fazm pre-creates the LLM session in the background while the user is still speaking. By the time the final transcript arrives, the LLM has already received the system prompt and is ready to generate. This cuts first-token latency by 500ms–1s.

For CODEC Voice: start streaming the system prompt + any recent memory context to the LLM as soon as VAD detects speech start. When the full transcript arrives, append it as the user turn and stream the response.

The implementation requires the LLM to support streamed multi-part requests or a pre-send ping — feasible with the current OpenAI-compatible API used by `codec_voice.py`.

---

### 5. Transcription Smoothing — Display Interim Chunks
**Impact: High | Effort: 4–6 hours | File: `codec_voice.py` + `codec_voice.html`**

CODEC Voice currently waits for the full audio → sends to Whisper → waits for response → displays. Total silence: 2–4 seconds.

Fazm displays interim transcripts (from DeepGram WebSocket) while the user is still speaking. The effect is that the UI immediately reflects what's being said — perception of latency drops dramatically even if total processing time is the same.

**Option A (no API change):** Switch CODEC Voice's STT to DeepGram streaming. Requires a DeepGram API key — not local.

**Option B (local, partial improvement):** Run Whisper in shorter rolling windows (2s chunks) and stream partial transcripts to the UI. Less accurate than DeepGram but keeps it local. Already partially viable with `mlx-whisper`.

**Option C (pragmatic):** Display a "listening..." animation with real-time energy level bar during VAD activity. Not true interim transcription, but fills the silence perceptually. Zero backend changes.

---

### 6. macOS AXUIElement Accessibility Bridge (Swift Binary)
**Impact: Very High | Effort: 20–40 hours | Files: new `ax_bridge.swift` + new `codec_ax.py` skill**

This is the largest gap between CODEC and what Terminator achieves (on Windows). CODEC currently controls other apps via:
- AppleScript (app-specific, limited)
- Chrome AppleScript skills (browser tabs only)
- Vision model screenshot OCR (no interaction, just reading)

What CODEC cannot do: click a specific button in a native macOS app, read the current value of a text field, check whether a dialog is open, navigate a system preferences pane.

Terminator solves this on Windows with UIA + caching. The macOS equivalent is a Swift binary that:

1. Accepts JSON commands on stdin: `{"action": "find", "selector": "role:AXButton name:OK"}`
2. Uses `AXUIElementCreateApplication(pid)` to get the target app's accessibility root
3. Uses `AXUIElementCopyMultipleAttributeValues()` to batch-fetch: `AXRole`, `AXTitle`, `AXValue`, `AXFrame`, `AXEnabled`, `AXChildren`
4. Traverses the tree (same depth-limited recursive approach as Terminator's `tree_builder.rs`)
5. Matches elements against selector
6. Executes action: `AXUIElementPerformAction(element, kAXPressAction)` for click
7. Returns result JSON on stdout

CODEC calls it via `subprocess.run(["./ax_bridge", "--pid", str(pid), "--action", "click", "--selector", "role:AXButton name:OK"])`.

**Why this matters:** This unlocks **every native macOS application** to CODEC — System Settings, Calendar, Messages, Finder, any Electron app — for reading and interaction. It is the single highest-impact capability gap.

Required macOS permission: Accessibility (already required by CODEC for keyboard listening).

---

### 7. MCP Server for CODEC Skills
**Impact: Medium | Effort: 4–6 hours | File: new `codec_mcp.py`**

Terminator exposes all its capabilities as an MCP server. Fazm integrates MCP servers for browser control (Playwright) and Google Workspace.

CODEC's 36 skills + 6 agent tool functions could be exposed as an MCP server, allowing Claude Code, Cursor, and any MCP-compatible client to invoke CODEC skills directly.

```python
# codec_mcp.py — FastMCP server
from fastmcp import FastMCP
mcp = FastMCP("CODEC Skills")

@mcp.tool()
def web_search(query: str) -> str:
    """Search DuckDuckGo instant answers"""
    ...  # calls existing web_search skill

@mcp.tool()
def google_calendar_check(date: str = "today") -> str:
    """Check Google Calendar events"""
    ...
```

This positions CODEC as a platform other AI tools can build on top of, not just a standalone agent.

---

### 8. Selector-Based Element Matching Language (for AX Bridge)
**Impact: Medium | Effort: 4–8 hours | File: new `codec_ax.py`**

Terminator's selector language (`role:Button && name:OK >> role:Panel`) is elegant and powerful. When building the AX bridge (Recommendation 6), implement the same DSL in Python for CODEC skills:

```python
# In a future "control" skill or ax_bridge wrapper
find("role:AXButton && name:OK")                    # role + name
find("role:AXTextField").in_app("Safari")           # app-scoped
find("role:AXButton").right_of("role:AXTextField")  # spatial
find("role:AXWindow >> role:AXButton && name:Sign In") # parent >> child
```

Parse rules: split on `>>` (chain), `&&` (AND), `||` (OR), `role:`, `name:`, `value:`.

---

### 9. Audio Level Decay / Smoothing for Wake Word
**Impact: Low-Medium | Effort: 2–3 hours | File: `codec.py` → `wake_word_listener()`**

Fazm's `AudioCaptureService` implements smooth audio level calculation:
```swift
smoothedLevel = max(currentLevel, smoothedLevel * decayRate)  // decayRate = 0.85
noiseFloor = 0.005  // very sensitive
```

CODEC's wake word uses `np.abs(audio).mean()` with a hard `WAKE_ENERGY` threshold. High background noise → false positives. Low mic gain → misses real commands.

Smoothed energy with a noise floor and decay would make the threshold adaptive and reduce the need for manual `WAKE_ENERGY` tuning in `config.json`. Apply to the energy gate in `wake_word_listener()`.

---

### 10. Wake Word Confidence-Based Noise Filter (Extend Existing)
**Impact: Low-Medium | Effort: 1–2 hours | File: `codec.py` → `wake_word_listener()`**

CODEC already has a basic noise word list. Fazm's approach: after transcribing the wake chunk, reject if fewer than 1 real word (non-noise) remains after stripping the wake phrase. This is already partially implemented in CODEC's `_is_noise()` function. Tighten the threshold: require ≥2 real words for a command to fire (not just ≥1), reducing music/TV false triggers.

---

## Summary Priority Matrix

| # | Recommendation | Impact | Effort | Priority |
|---|----------------|--------|--------|----------|
| 1 | Transcription post-processing rules | High | 1–2h | **Ship this week** |
| 3 | Cloudflare orphan cleanup | Medium | 0.5h | **Ship this week** |
| 2 | PTT lock mode (double-tap F18) | High | 2–3h | **Next sprint** |
| 4 | CODEC Voice session warmup | High | 3–4h | **Next sprint** |
| 5c | Listening animation during VAD | Medium | 1–2h | **Next sprint** |
| 7 | MCP server for CODEC skills | Medium | 4–6h | This month |
| 9 | Audio level smoothing for wake word | Low-Med | 2–3h | This month |
| 10 | Tighten noise filter threshold | Low-Med | 1–2h | This month |
| 6 | macOS AXUIElement Swift bridge | Very High | 20–40h | **Q2 milestone** |
| 8 | Selector DSL (part of AX bridge) | Medium | 4–8h | **Q2 milestone** |

---

## Appendix — Key Files Read

### Terminator
- `crates/terminator/src/platforms/windows/tree_builder.rs` — Cached UIA tree construction
- `crates/terminator/src/platforms/windows/engine.rs` — UIA engine, shallow search optimization
- `crates/terminator/src/platforms/windows/input.rs` — SendInput click/keyboard implementation
- `crates/terminator/src/element.rs` — UIElement trait, clickability validation
- `crates/terminator/src/selector.rs` — Selector DSL, Shunting Yard parser
- `crates/terminator/src/locator.rs` — Element finding with timeout
- `crates/terminator/browser-extension/worker.js` — WebSocket DOM bridge
- `packages/terminator-python/src/` — PyO3 Python binding
- `README.md`, `CHANGELOG.md`

### Fazm
- `Desktop/Sources/AudioCaptureService.swift` — CoreAudio IOProc capture
- `Desktop/Sources/TranscriptionService.swift` — DeepGram WebSocket STT
- `Desktop/Sources/FloatingControlBar/PushToTalkManager.swift` — PTT state machine
- `Desktop/Sources/Chat/ACPBridge.swift` — ACP subprocess management
- `Desktop/Sources/Chat/WebRelay.swift` — Cloudflare tunnel relay
- `acp-bridge/src/protocol.ts` — JSON-lines protocol definition
- `acp-bridge/src/index.ts` — Bridge orchestrator, session warmup
- `acp-bridge/src/ws-relay.ts` — WebSocket relay server
- `acp-bridge/src/fazm-tools-stdio.ts` — MCP tool server
- `web/hooks/useVoiceInput.ts` — Browser mic capture
- `web/hooks/useDesktopRelay.ts` — Web WebSocket relay client
- `Backend/src/routes/relay.rs` — Tunnel registry
- `README.md`, `CLAUDE.md`
