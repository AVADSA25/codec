# Release documents for the buyer build (P-056)

Status: built 2026-11-01. Version number approved by the owner the same day: **3.7.0**.

## What

The buyer app is sold, and it contains LGPL libraries (pynput LGPL-3.0, zeroconf LGPL-2.1+,
libsndfile LGPL-2.1 with libmpg123 and LAME inside it) and many MIT, BSD and Apache packages whose
licences ask for their text and copyright line to travel with the software. Until now the repo
had no notices file, and `VERSION`, `Info.plist` and `CHANGELOG.md` said 3.6.0 although 47 pull
requests had been merged since that release.

1. **Version 3.7.0**: `VERSION`, `Info.plist` (`CFBundleShortVersionString` and `CFBundleVersion`),
   the `codec_version.py` fallback, `FEATURES.md` and a dated `CHANGELOG.md` section in which every PR
   merged since the 3.6.0 release commit (`d716735`) appears or is listed as internal. The existing
   `tests/test_versioning.py` and `tests/test_app_bundle.py` already pin VERSION = plist = CHANGELOG.
2. **`THIRD-PARTY-NOTICES.md`** at the repo root: every package of the buyer lock with name, version,
   licence and source address; the licence files each wheel ships, each text stored once; the LGPL
   libraries and where they sit in the app; the models; the vendored web assets; the bundled Python.
3. **Copied into the app** at `Contents/Resources/THIRD-PARTY-NOTICES.md` by `build_app.sh` (the build
   fails if the file is missing).
4. **Settings > About** (General tab): the version, and a link that opens the file as plain text.

## How the file is made

`tools/gen_third_party_notices.py --wheels DIR` reads `requirements-lock.txt` and, for each pinned
package, the wheel's `METADATA` (licence, source address) and its licence files. DIR is made with
`pip download --require-hashes --only-binary=:all: --no-deps` from the same lock, so the wheels are the
hash-checked files the app installs. Nothing is installed. The output has no date and a fixed order;
`--check` exits 1 when the committed file differs.

Decisions inside the generator, all visible in the file:

- Licence name: `License-Expression`, else a short `License` field, else the classifiers. Where the
  metadata holds only text or a vague word (`OVERRIDES`: tiktoken, scipy, python-dateutil, qrcode),
  the name was read from the shipped licence file and the table says so.
- A wheel that ships no licence file (`SAME_TEXT_AS`: dnspython, fastmcp-slim, mlx-whisper,
  sentencepiece, tokenizers, five pyobjc packages) gets the text of the same project family or the
  standard text, marked `*`, and the file says the copyright line is then not the package's own.
  A new wheel with no licence file and no entry stops the run.
- GPL-3.0 text (`packaging/macos/licences/GPL-3.0.txt`, from gnu.org) is included because the LGPL-3.0
  text of pynput is supplemented by it. No package in the table is GPL or AGPL; GPL words in numpy,
  scipy (GCC runtime libraries, with the GCC Runtime Library Exception) and pillow (FreeType, used under
  the FreeType Licence) come from texts the wheels ship and are explained in the Notes section.
- Models: `packaging/macos/model_licences.json` holds the licence read from each model card at the
  pinned revision (2026-11-01). `tests/test_third_party_notices.py` fails when a pin in `models.json`
  moves without a new reading, and when a licence is unverified, non-commercial or research-only.
  The whisper-turbo card (mlx-community) states no licence; the file says MIT from the upstream OpenAI card.
- Web assets: rows come from `static/vendor/SOURCES.md`; the licence file of marked, DOMPurify and
  highlight.js was fetched from the same npm version (`static/vendor/LICENSE-*.txt`).

## Files

New: `THIRD-PARTY-NOTICES.md` (about 780 KB, 158 texts), `tools/gen_third_party_notices.py`,
`packaging/macos/model_licences.json`, `packaging/macos/licences/GPL-3.0.txt`, `routes/about.py`,
`static/vendor/LICENSE-{marked,dompurify,highlightjs}.txt`, `tests/test_third_party_notices.py`,
`tests/test_about.py`. Changed: `VERSION`, `Info.plist`, `codec_version.py`, `CHANGELOG.md`,
`FEATURES.md`, `build_app.sh`, `codec_dashboard.py` (one router line), `codec_dashboard.html`
(the About block and `loadAbout`), `static/vendor/SOURCES.md`, `tests/test_app_bundle.py`.

## Test plan

Five focused tests on the notices file (lock coverage, no GPL/AGPL package, LGPL named, texts present,
models at the pinned revision, web assets), three on the route and page, one on the bundle copy.
Negative controls: removing a package row, moving a model pin and marking pynput GPL each fail a test.
Outside CI: the generator `--check` against the downloaded wheels, and a headless Chrome look at the block.

## Not in this change

The release itself (appcast entry, rebuild, notarise, DMG) is the owner's step after this merges.
The nine wheels without a licence file, and the whisper-turbo card, are listed in the file; a better
text for them needs the projects' own repositories.

## Rollback

Revert the PR. Nothing is stored outside the repo; the Settings block hides itself when
`/api/about` fails or the file is missing.
