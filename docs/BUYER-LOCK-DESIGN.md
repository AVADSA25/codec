# Buyer lock: every runtime import declared, no GPL pieces (D-35, audit P-011)

Status: built 2026-10-30. Owner decision D-35 (dictation, speech to text and the Google skills ship).

## What was wrong
The bundled Python was installed with a plain `pip install -r requirements.txt`. About 14 of
the 100 third-party modules the services import were never declared: `mlx_whisper`
(`whisper-stt`), `pyperclip` and `faster_whisper` (`codec-dictate`), the Google API packages
(every Google skill), `zeroconf`, `AVFoundation`. They crashed on a buyer's Mac with
`ModuleNotFoundError` while the Studio worked on packages installed by hand. The only import
check (`verify/deps.mjs`) tried 4 modules. Models were pinned to `main`.

## What changed
- **requirements.txt** declares `mlx-whisper`, `pyperclip`, `google-api-python-client`,
  `google-auth`, `google-auth-oauthlib`, `zeroconf`, and the PyObjC frameworks used
  (`AVFoundation`, `Quartz`, `Cocoa`).
- **requirements-lock.txt** is regenerated with `-c` on the old lock, so the 127 packages
  already pinned keep their versions; 24 are added (151 total; opencv-python was then taken out again, see below: 150).
- **bundle_python.sh** installs only from the lock: `--require-hashes --only-binary=:all:
  --no-deps`. It refuses a file without hashes. `--requirements` became `--lock`.
- **verify/bundle_imports.py** (called by `verify/deps.mjs`, and by `tests/test_buyer_lock.py`)
  reads the fleet from `ecosystem.config.js` or the built `services.json`, follows each
  service's imports through the repo, and requires every third-party module to be in the lock
  or in `optional_imports.json` (with a reason) or imported only inside `try/except ImportError`.
  With `--python` it then imports each one in the bundle's interpreter (`-B`, so the signed app
  is not touched).
- **codec_paste.py** replaces pyautogui in `codec_dictate.py`, `codec_textassist.py` and
  `codec_watcher.py`: Cmd down, V down, V up, Cmd up as Quartz events on the HID tap, Cmd always
  released, osascript as the fallback.
- **models.json** pins all five models to a 40-hex commit; `codec_setup.py` passes the pin.

## Decisions taken while building (each one found in the code, not in the plan)
1. **faster-whisper is not shipped.** It requires PyAV, whose wheel contains `libx264` and
   `libx265` (GPL). The plan said to declare it; the licence rule (permissive or LGPL only) says
   no. `codec_dictate.py` still uses it when it is installed (the Studio, unchanged). Without
   it, `_ServerModel` sends the recording to the `whisper-stt` server and pins English with a new
   form field `pin_language`. Other clients of the server do not send it and behave as before.
2. **torch (580 MB) and four helper packages are left out of the lock.** `mlx-whisper` declares
   torch but imports it only in `torch_whisper.py` (conversion). Checked: a real transcription
   runs with torch absent. The lock header says so; CI installs with `--no-deps` and accepts
   exactly two `pip check` lines (`mlx-whisper requires torch`, `mlx-vlm requires opencv-python`).
3. **`whisper_server.py` decodes WAV itself when `ffmpeg` is missing.** `mlx_whisper` reads a
   file path by running the `ffmpeg` command, and a buyer's Mac has none. Where `ffmpeg` exists
   the path is passed through exactly as before; without it the file is read with `soundfile`
   (resampled to 16 kHz with scipy if needed). Unreadable formats answer 415.
4. **Not shipped, listed in `optional_imports.json`:** `pyautogui` (Vision
   Mouse scroll and screen size in `skills/mouse_control.py`: scroll and size may fail on the
   buyer build until a Quartz replacement exists), `mlx_lm` (cookbook, P-012), `fitz` (AGPL),
   and the disk fallback of the MCP token store (the Keychain store is used on every Mac).

## opencv-python (cv2) is out of the lock (owner decision 30 Oct 2026: replace, do not lose the webcam)
`opencv-python` was in the lock (pulled in by `mlx-vlm`) and used by the webcam routes. Its wheel bundles
an FFmpeg build with `libx264` and `libx265`, both GPL, and the installed v3.6.0 app has the same two
files. It is now out: the webcam routes use AVFoundation through `codec_webcam.py`
(docs/WEBCAM-AVFOUNDATION-DESIGN.md), and `mlx-vlm` uses cv2 only to read video files
(`mlx_vlm/utils.py`, `video_generate.py`), which CODEC never sends. A rebuilt app has neither GPL file;
`no_gpl_helpers.mjs` proves it on the built .app.

## Not done here
- PDF text without `fitz` and `pdftotext`, and microphone recording without SoX: part 2 (P-033).
- `THIRD-PARTY-NOTICES` (P-056): zeroconf is LGPL-2.1-or-later, pynput LGPL-3, soundfile's
  libsndfile LGPL-2.1.
- A rebuilt `.app`, the clean macOS 14 install and the Mac Air test (P-055 / D-34). No DMG goes
  out before that test, so nothing a buyer has changes with this PR.

## Rollback
Revert the PR. The old lock and `requirements.txt` come back; the Studio is not affected either
way (the changes take effect on a restart, and each code path keeps the old behaviour first when
its tool is present).
