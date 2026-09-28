# Vendored static assets

Downloaded on 2026-09-28 from the npm registry mirror at cdn.jsdelivr.net, with
the owner's approval. They are served from `/static` so no page loads fonts or
scripts from a third-party host. The `/static` mount serves only `.css .svg
.woff2 .js .png`, so this file and the licence text are not served.

| File | Package | Licence | SHA-256 |
|---|---|---|---|
| `vendor/marked.umd.js` | marked 18.0.14 (`lib/marked.umd.js`) | MIT | `21568877a938d2c4e7d74e27f18e60da96bb73a68809610ca39216e1efebae62` |
| `vendor/purify.min.js` | dompurify 3.4.16 (`dist/purify.min.js`) | MPL-2.0 or Apache-2.0 | `2c90a9b46d6463f26038a29b686e82bc91de01fdac9d5229e7cfe3b360134ea2` |
| `vendor/highlight.min.js` | @highlightjs/cdn-assets 11.12.0 (`highlight.min.js`, common languages) | BSD-3-Clause | `8ab71eb09c51f501e5e25157d9cff100e46cc29bcbfc744d0b746d451fca7f53` |
| `fonts/IBMPlexSans-Regular.woff2` | @ibm/plex-sans 1.1.0 | OFL-1.1 | `ba711a3085ff9f27440b6b9c4550cfc47c97bf36591d5da958b975bb3add8c1a` |
| `fonts/IBMPlexSans-Medium.woff2` | @ibm/plex-sans 1.1.0 | OFL-1.1 | `5660f8a658f8bb50dbc005232f885eadffd2bc1c235c4f6fbb63469d1f9cde6d` |
| `fonts/IBMPlexSans-SemiBold.woff2` | @ibm/plex-sans 1.1.0 | OFL-1.1 | `f78048030eab62e860efa39a0df79e2e5581bf122eb95b9bc42c0b8a4988d205` |
| `fonts/IBMPlexSans-Bold.woff2` | @ibm/plex-sans 1.1.0 | OFL-1.1 | `fa7130d854a660b39a7fc9e6e0f2dc23dba5f1346e2adea3e1fe37b6d884133d` |
| `fonts/IBMPlexMono-Regular.woff2` | @ibm/plex-mono 2.5.0 | OFL-1.1 | `ba204497f16b6d334cee9d1e963a831b73e3a56e1d6300a8489d18df7214b350` |
| `fonts/IBMPlexMono-Medium.woff2` | @ibm/plex-mono 2.5.0 | OFL-1.1 | `33faf307fa6031fb4062276d7320a6d632de890cbb347576fd80cfa01077bc25` |

The IBM Plex licence text is in `fonts/LICENSE-IBM-Plex.txt`. The three
libraries carry their licence in their banner comment.

To update a file: download the new version from the same package path, replace
the file, update its row here (version and `shasum -a 256`), and run the tests.
