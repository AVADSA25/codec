# Webcam on AVFoundation instead of OpenCV (D-35 follow-up)

Owner decision, 30 Oct 2026: "Replace it: rebuild webcam capture on AVFoundation ... I don't want to
lose some CODEC function." Status: built, merged with the PR that carries this file.

## What and why
`opencv-python` (module `cv2`) was in the buyer lock and was the only webcam code in CODEC
(`routes/media.py`: `GET /api/webcam/snapshot` and `GET /api/webcam/stream`). Its wheel bundles an FFmpeg
build with GPL `libx264` and `libx265`; the buyer app may not ship GPL code (licence rule of D-35, plan
item P-011). The installed v3.6.0 app has both files. OpenCV on macOS is a thin layer over AVFoundation,
so the camera, the frames and the permission are the same; only the wrapper changes.

## Change
- **`codec_webcam.py` (new).** `snapshot_jpeg()` opens the camera, waits 1.25 s for exposure, white
  balance and autofocus, takes a fresh frame, returns JPEG bytes (quality 0.92) and closes the camera.
  `Camera()` is the live feed: `start()`, `read_jpeg(quality)` returns the next new frame, `stop()`.
  Session preset 1920x1080 (falls back to High), continuous autofocus kept on: same as the old code.
  Errors are `WebcamError`; `WebcamPermissionError` carries the System Settings path.
- **How:** `AVCaptureSession` + `AVCaptureVideoDataOutput`; a small `NSObject` receives each frame on a
  serial dispatch queue (made with ctypes `dispatch_queue_create`, so `pyobjc-framework-libdispatch` is
  not needed) and keeps only the newest one; JPEG is made on demand with CoreImage and ImageIO
  (`Quartz`). Frames are never converted unless a reader asks.
- **Permission:** the status is checked first; "not decided" asks macOS once and waits up to 60 s; a "no"
  raises `WebcamPermissionError` and is not asked again.
- **`routes/media.py`:** both routes call `codec_webcam`. Same URLs, same JSON (`image`, `saved`,
  `filename`), same `~/.codec/photos/webcam_<time>.jpg`, same MJPEG at about 15 fps. Errors keep
  status 500 and the `error` key; the text is now the real reason (for example the permission line).
  The calls run in `asyncio.to_thread`, which also removes a leaked `ThreadPoolExecutor` per snapshot.
- **Lock and packages:** `opencv-python` out of `packaging/macos/requirements-lock.txt` (151 -> 150, no
  other pin moved); `pyobjc-framework-CoreMedia` added to `requirements.txt` (it was already in the lock
  through AVFoundation); CI `pip check` accepts the extra `mlx-vlm requires opencv-python` line.
  `mlx-vlm` uses cv2 only to read video files (`mlx_vlm/utils.py`, `video_generate.py`); CODEC sends
  pictures only. Checked with `cv2` blocked: `mlx_vlm`, `mlx_vlm.server` and the Qwen3.6 VL processor
  import and process an image.
- **Buyer app permission (found while building):** `Info.plist` had no `NSCameraUsageDescription` and
  `codec.entitlements` had no `com.apple.security.device.camera`. Under hardened runtime that means macOS
  refuses the camera with no prompt, so the webcam could never have worked in the signed app. Both are
  added, next to the microphone ones, and `verify/launcher.mjs` checks the signed app carries the
  entitlement. This needs a rebuild and re-sign (the owner's action, D-34).

## What is not touched
`mlx_vlm` (video input), the vision route that analyses a posted picture (`POST /api/webcam`), the chat and
dashboard page JS, `skills/` (hash-pinned manifest), `codec_llm.py`.

## Tests
`tests/test_codec_webcam.py`: frame hand-over, a fake camera for the open/read/stop/settle logic, every
permission state, the real frame callback and JPEG encoder on a made-up BGRA frame (colours and top/bottom
kept), the two routes with a fake camera, and a guard that no Python file imports `cv2`.
By hand: `~/Documents/CODEC-private/live-checks/live_check_webcam.py` (real Anker C200; `--route` against the
running dashboard).

## Rollback
Revert the PR: the lock gets `opencv-python` back and the routes use `cv2` again. Nothing on disk changes
shape (photos, JPEG files, URLs).

## Risks
- The dashboard's camera permission is attributed to the process macOS sees as responsible. OpenCV used
  the same AVFoundation API in the same process, so a camera that worked before should work now; it is
  proved only by the live check after the dashboard restart. If the permission is missing, the route says
  so in words instead of "Cannot open webcam".
- Two sessions on one camera (snapshot while the PIP stream runs) are allowed by AVFoundation.
