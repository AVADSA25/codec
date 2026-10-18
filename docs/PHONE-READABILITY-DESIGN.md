# Phone readability and stability — design

Status: shipped. Target: an Android phone in Chrome or the installed app. iOS Safari was also checked in the simulator.

## What is wrong (measured, 375 and 412 px wide)

1. **Text is small.** Today (`codec_dashboard.html`): reply text 14 px, response 12 px, meta 11–12 px. Tasks, Voice and sidebar: about 100 of 120 text nodes are 14 px or less. Chat reply text is 16 px. Text-size "Large" is only x1.12, so Today reaches 15.7 px.
2. **Today opens in the wrong place.** `loadChat()` (`codec_dashboard.html:2098`) sets `.content.scrollTop = scrollHeight` after every load. The Flash history is long, so the page lands about 2000 px down and the Today tiles (`#todayHome`) sit above the screen. This is the "page moves around".
3. **No overscroll control anywhere.** `overscroll-behavior` appears in zero files. On Android Chrome and the installed PWA, pulling down at the top of any scroller reloads the page; a scroll that reaches the end of the inner list (`.messages`, `.cs-hist`) passes to the outer one.
4. **Keyboard and text-scaling hints are missing.** No `interactive-widget` in the viewport meta, no `-webkit-text-size-adjust`. `codec_voice.html` has `user-scalable=no` (blocks pinch-zoom; accessibility problem).

Not reproduced: horizontal overflow (none at 375 or 412; the hidden side panel is off-screen by design). iOS Safari showed the same Today scroll problem (finding 2), which confirms it is not browser-specific.

## Change (CSS and small JS, no schema or API change)

All edits apply only on a phone (width under 768 px, or a touch screen for the overscroll guard), so desktop and the native app window are untouched. No root `zoom` and no transform (see `docs`/memory: #344 broke phone scrolling).

What shipped (differences from the first draft of this doc are marked):

1. **Phone type scale.** Every text size on every page already runs through `--fs-scale`, so one phone-only multiplier covers them all. `static/codec-shell.js` sets `--fs-scale` to the S / M / L step times 1.125 on a phone (`effectiveScale`): reply text 16 px becomes 18 px, Large about 20 px, labels 11 px become 12.4 px. The S / M / L steps themselves (0.93 / 1 / 1.12) are unchanged, and the scale follows a resize across the phone width. *(First draft redefined each token in `codec.css`; the multiplier reaches the many `calc(Npx * var(--fs-scale))` rules too, so it replaced that.)*
2. **Today opens at the top.** `loadChat(toEnd)` scrolls to the newest message only after a sent message, a vision reply or a polled reply. First load and switching back to Today stay on the tiles.
3. **Overscroll.** On touch screens `overscroll-behavior-y: none` on `html, body` and `contain` on `.cs-main`, `.content`, `.messages`, `.cs-hist`, `.side-panel-body`. Vertical only, so a sideways swipe still goes back. `touch-action: manipulation` was dropped: Chrome already removes double-tap zoom on pages with a device-width viewport.
4. **Viewport hints.** `interactive-widget=resizes-content` on every page; `text-size-adjust: 100%`; `user-scalable=no` removed from the voice and sign-in pages. Android keyboard behaviour can only be confirmed on a real device.
5. **Two layouts the bigger text broke, fixed.** Today's open-thread row kept its text at about 5 characters wide next to two buttons (the text now has a 14-character floor and the buttons wrap below it); the Voice transcript buttons were cut off at 360 px (the header now wraps).

## Files

`static/codec.css`, `static/codec-shell.js` (SIZES for phone), `codec_dashboard.html`, `codec_chat.html`, `codec_tasks.html`, `codec_voice.html`, `codec_vibe.html`, `codec_cortex.html`, `codec_auth.html`. Cache-bust versions on the static links bump with the edit.

## Test plan

- Emulated 360, 375, 412 px in the browser pane for Today, Chat (long reply), Voice, Tasks, Settings: no horizontal scroll, last line of a long reply visible above the composer, Today opens on the tiles.
- Computed font sizes: body at least 18 px on phone, nothing below 12 px.
- Desktop width 1280: computed sizes unchanged (spot check).
- Console read at the end of each page (CSP lesson).
- Real Android phone: pull down at top does not reload; text size S/M/L; keyboard open in Chat and Today.
- Existing test suite, plus one test that the phone block keeps `overscroll-behavior` and the Today first-load scroll rule.

## Rollback

Revert the one PR; no data or config touched. Text-size choice stays in `localStorage` (`codec-text-size`), same keys.

## Not in this change

Android-specific native-app work, a Play Store / App Store listing, a redesign of Today. Anything not seen failing on the device stays as it is.
