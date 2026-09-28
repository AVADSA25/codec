# UI Phase 1 — design

Status: approved 2026-09-28 by the owner ("go phase 1") after the UI review.
Mockups: the owner's private CODEC UI refresh canvas. The review's full result is
kept outside the repo.

Phase 1 makes text readable, makes replies look current, stops the phone
layout from breaking, fixes silent chat bugs and closes a Vibe sandbox hole.
It ships as four PRs, in this order, each tested at 375x812 and 1470x956.

## PR-A — phone shell, chat bugs, Vibe sandbox

1. **Text-size zoom.** Every page applies the S/M/L text size as a root
   `zoom`. Only Home skips it on the phone shell (`_isPhoneShell()`, after #344).
   The other seven pages (chat, tasks, voice, vibe, cortex, audit, auth) get
   the same guard. On desktop, a zoomed root makes `100vh`/`100dvh` containers
   taller than the window, which pushed the chat composer below the screen.
   The pages set `--tz` to the zoom and size full-height containers with
   `calc(100dvh / var(--tz, 1))`. PR-C replaces the zoom with a type scale.
2. **Layering on the phone.** The history drawer, side panel, modals and
   toasts sit above the composer and bottom nav. The composer hides while a
   drawer is open. The closed side panel is `visibility: hidden`, so its
   shadow stops showing on the right edge. The delete button on history rows
   is visible on touch screens. The Mac-only working-folder button says
   "on Mac" on the phone. The voice and cortex headers stop trapping the
   bottom nav (a `backdrop-filter` containing block).
3. **Chat bugs.**
   - Stats render on a normal send, not only on regenerate.
   - Stop keeps the partial answer (the streamed buffer, marked "stopped"),
     in the history and the saved session, and so does a failed regenerate.
   - Each message keeps its own timestamp.
   - The working-folder open call uses `/api/agents/{id}/open-folder`, with
     an error toast when it fails.
   - Dead web-search and speak-toggle code is removed.
   - Text-size selectors that match nothing are fixed.
   - The chat allowlist names the news digest by its registry name.
   - The Tasks history parser reads the JSON lines its writer emits.
4. **Vibe preview sandbox (security).** The preview iframe drops
   `allow-same-origin` (`sandbox="allow-scripts"`), so previewed code can no
   longer read the CSRF cookie or call `/api/*` with the owner's session.
   Inspect and the automatic error capture move to `postMessage`: the preview
   document gets a small injected script that reports errors and answers
   inspect requests. The parent checks `event.source === iframe.contentWindow`.
   On the phone, a Preview / Code switcher makes the Run and Preview toolbar
   reachable.

## PR-B — shared stylesheet, local assets, emoji, theme

- A repo `static/` folder is served at `/static` (already an auth-public
  prefix, so it holds only generic assets).
- `static/codec.css` holds the tokens: type scale, spacing, radii, light and
  dark colours, focus ring and `--fs-scale`.
- IBM Plex Sans (400/500/600/700) and Plex Mono (400/500) are self-hosted as
  woff2 (OFL-1.1), and a small SVG logo replaces the i.imgur.com references.
  The CSP drops the Google Fonts origins.
- Emoji and glyph icons are replaced by line SVGs and words.
- The hard-coded dark panels (approvals, strict consent, badges, status
  pills) follow the theme, with contrast fixed. A small `<head>` script stops
  the dark flash.

## PR-C — type scale and Markdown replies

- The root zoom is replaced by tokens times `--fs-scale`, with a 12 px floor:
  body and composer 16 px, labels 13–14 px.
- Assistant replies render full-width in a 760 px column through vendored
  marked + DOMPurify + highlight.js, with code-block and table actions. This
  needs the owner's OK for the new vendored libraries (AGENTS.md §9).

## PR-D — composer, scrolling, starters, keys

- One composer card with a + menu, mode pill and model pill.
  - The mode-bar row above the messages is gone. The history button moves to
    the header, and on the phone the model pill does too.
  - The + menu holds Attach files, Photo or camera (touch screens), Screenshot
    of Mac, Webcam snapshot, Working folder ("on Mac") and a Search the web
    toggle that sends `force_search`.
  - The mode pill picks Chat (fast), Think (reasoning scaffold, the default),
    Agents, Project or Image. Agents opens a sheet with the crews, the research
    model, the custom-agent builder and Schedule daily.
- The page follows the stream only while the reader is within 80px of the
  bottom, and shows "Jump to latest" otherwise.
- A message sent during a reply becomes a queued chip that can be edited or
  removed. It sends when the reply ends. Stop keeps the queue until Send.
- A new chat shows a time-of-day greeting, starter cards and the 3 most
  recent chats. On desktop the composer sits under them and docks at the
  bottom after the first message. The starters send real trigger phrases:
  "start my day" (daily_kickoff), the calendar (google_calendar) and unread
  email (google_gmail).
- Enter is IME-safe, and on touch screens Enter adds a new line.
  - Esc stops a reply (or closes a menu).
  - Cmd/Ctrl+Shift+O starts a new chat.
  - Up in an empty composer edits the last message.
  - Cmd/Ctrl+Shift+C copies the last reply.
  - The Flash composer binds Enter once.
- A stopped reply is a normal message with its action row. A stop before any
  text removes the "thinking" placeholder.
- Later: temporary chat (it needs a no-save path on the server), 56px
  attachment tiles, and the model picker popover (all Phase 2). The Flash
  composer card and Flash starters wait for the Today-or-Flash decision.

## Tests and rollback

- Each PR adds focused checks where the repo has a test surface. Examples:
  `/static` serving and the CSP, the chat allowlist name, and the Tasks
  history parser.
- The UI is checked by hand at phone and desktop sizes on a throwaway
  dashboard.
- Rollback: revert the PR. The only state change is a new static route. No
  database or config change happens in PR-A or PR-B.
