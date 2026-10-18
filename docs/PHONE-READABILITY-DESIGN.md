# Phone readability and stability — design

Status: proposed, waiting for approval. Target device: Android (vivo X300 Ultra), Chrome / installed PWA. Not iOS.

## What is wrong (measured, 375 and 412 px wide)

1. **Text is small.** Today (`codec_dashboard.html`): reply text 14 px, response 12 px, meta 11–12 px. Tasks, Voice and sidebar: about 100 of 120 text nodes are 14 px or less. Chat reply text is 16 px. Text-size "Large" is only x1.12, so Today reaches 15.7 px.
2. **Today opens in the wrong place.** `loadChat()` (`codec_dashboard.html:2098`) sets `.content.scrollTop = scrollHeight` after every load. The Flash history is long, so the page lands about 2000 px down and the Today tiles (`#todayHome`) sit above the screen. This is the "page moves around".
3. **No overscroll control anywhere.** `overscroll-behavior` appears in zero files. On Android Chrome and the installed PWA, pulling down at the top of any scroller reloads the page; a scroll that reaches the end of the inner list (`.messages`, `.cs-hist`) passes to the outer one.
4. **Keyboard and text-scaling hints are missing.** No `interactive-widget` in the viewport meta, no `-webkit-text-size-adjust`. `codec_voice.html` has `user-scalable=no` (blocks pinch-zoom; accessibility problem).

Not reproduced: horizontal overflow (none at 375 or 412; the hidden side panel is off-screen by design). iOS Safari showed the same Today scroll problem (finding 2), which confirms it is not browser-specific.

## Change (CSS and small JS, no schema or API change)

All edits are under the phone media query (`max-width: 767px`) so desktop and the native app window are untouched. No root `zoom` and no transform (see `docs`/memory: #344 broke phone scrolling).

1. **Phone type scale.** In `static/codec.css`, redefine the phone tokens: `--fs-11`→12, `--fs-12`→13, `--fs-13`→14.5, `--fs-14`→16, `--fs-16`→18, code→15. S / M / L on phone become x1.0 / x1.12 / x1.28 (default M = 18 px body, L = about 23 px). The hard-coded `calc(Npx * var(--fs-scale))` rules in the page files inherit this because they use the same variable; the few plain-px text rules get converted.
2. **Today opens at the top.** First load does not scroll to the bottom; scroll to the bottom only after the user sends a message or opens the history. Tiles stay visible first.
3. **Overscroll.** `overscroll-behavior: none` on `html, body`, `.cs-main`, `.content`, `.messages`, `.cs-hist` (stops pull-to-refresh and chaining). `touch-action: manipulation` on buttons and tabs (no double-tap zoom).
4. **Viewport hints.** Add `interactive-widget=resizes-content` to the viewport meta of every page so the keyboard shrinks the layout the same way on every page; add `-webkit-text-size-adjust: 100%`; drop `user-scalable=no` from `codec_voice.html` and `codec_auth.html`.
5. **Tap size.** Tab bar labels 11→12 px (follows token), chips and small buttons keep a 44 px minimum height.

## Files

`static/codec.css`, `static/codec-shell.js` (SIZES for phone), `codec_dashboard.html`, `codec_chat.html`, `codec_tasks.html`, `codec_voice.html`, `codec_vibe.html`, `codec_cortex.html`, `codec_auth.html`. Cache-bust versions on the static links bump with the edit.

## Test plan

- Emulated 360, 375, 412 px in the browser pane for Today, Chat (long reply), Voice, Tasks, Settings: no horizontal scroll, last line of a long reply visible above the composer, Today opens on the tiles.
- Computed font sizes: body at least 18 px on phone, nothing below 12 px.
- Desktop width 1280: computed sizes unchanged (spot check).
- Console read at the end of each page (CSP lesson).
- Real device (Mickael, vivo): pull down at top does not reload; text size S/M/L; keyboard open in Chat and Today.
- Existing test suite, plus one test that the phone block keeps `overscroll-behavior` and the Today first-load scroll rule.

## Rollback

Revert the one PR; no data or config touched. Text-size choice stays in `localStorage` (`codec-text-size`), same keys.

## Not in this change

Android-specific native-app work, a Play Store / App Store listing, a redesign of Today. Anything not seen failing on the device stays as it is.
