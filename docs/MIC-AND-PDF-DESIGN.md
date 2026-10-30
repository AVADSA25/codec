# Record the microphone and read PDFs without Homebrew tools (D-35, audit P-033)

Status: built 2026-10-30. Part 2 of D-35; part 1 is docs/BUYER-LOCK-DESIGN.md.

## What was wrong
A clean Mac has no `sox` and no `pdftotext` (both GPL, both from Homebrew, neither bundled).
Wake word, push-to-talk (F18), dictation and the live-dictation loop all started `sox`; PDF
upload ran `pdftotext` (a missing program raised into a bare 500); document input used PyMuPDF
(AGPL, not declared). Each failed only in a log.

## What changed
- **codec_record.py** has three calls: `start_recording` (open-ended, returns a Popen-like handle
  with `terminate()`, `wait()`, `poll()`), `record_chunk` (fixed length) and `record_until_silence`
  (ends 1.5 s after speech stops, capped). **When `sox` is found, every call runs exactly the
  command line it ran before** (PATH first, then the Homebrew folders); a test pins each argv.
  Without it the call records with `sounddevice` into 16-bit mono WAV through `soundfile`, both
  already in the buyer lock. The silence rule copies the sox effect: wait for speech (2 % of full
  scale), drop the leading silence, stop after 1.5 s below 2 %.
- **Callers:** `codec.py` (F18 recording, wake-word polling, wake follow-up) and
  `codec_dictate.py` (live-dictation chunks; hold-to-talk keeps its own sox command and falls back
  to `start_recording` only when sox cannot start; the start-up check passes with sounddevice).
- **codec_pdftext.py:** `extract_text` uses `pdftotext -layout` when it is installed (same output
  as before on the Studio) and Apple's PDFKit (`Quartz.PDFDocument`, from the pyobjc Quartz package
  already in the lock) when it is not. `PdfTextError` carries a message that is safe to show:
  not a readable PDF, password-protected, or "needs macOS or pdftotext". `routes/upload.py` turns
  it into a 422; a `pdftotext` timeout still answers 408. `codec.py` document input uses it too,
  so `fitz` (PyMuPDF) is gone from the code.
- **verify/no_gpl_helpers.mjs [App.app]** fails when the built app holds `sox`, `rec`, `play`,
  `soxi`, `pdftotext`, `pdftoppm`, `pdfinfo`, `pdftocairo`, `ffmpeg`, `libsox`, `libx264`, `libx265`
  or `libpoppler`. libsoxr (LGPL) and folders such as `numpy/rec` are not flagged.

## Checked
- argv equality with sox present (3 shapes); synthetic audio without sox: chunk length, open-ended
  stop, silence start/stop/cap/short pause; named microphone lookup.
- PDFKit on a real PDF with real Quartz (lock venv), a non-PDF file, a missing extractor (422).
- The GPL-helper script on fixture trees (clean, each helper, look-alikes, missing app).

## Not checked
- A real microphone through sounddevice: needs the microphone permission for the bundled Python and
  belongs to the Mac Air test (D-34). The Studio uses sox, which is unchanged.
- THIRD-PARTY-NOTICES entry for libsndfile (LGPL-2.1, inside soundfile's wheel): P-056.

## Finding
Running the new script on the installed v3.6.0 app finds `cv2/.dylibs/libx264` and `libx265`
(GPL). See "Open finding" in docs/BUYER-LOCK-DESIGN.md. It needs the owner's decision.

## Rollback
Revert the PR. With sox present nothing changes; without it the recording and PDF features go back
to failing in a log.
