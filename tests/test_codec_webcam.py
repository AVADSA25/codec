"""Webcam capture through AVFoundation (codec_webcam.py, routes/media.py).

No camera is needed: the capture logic runs against a fake backend, and one macOS-only
test pushes a made-up frame through the real frame callback and JPEG encoder. The real
camera is checked by hand (docs/WEBCAM-AVFOUNDATION-DESIGN.md).
"""
from __future__ import annotations

import ast
import base64
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import codec_webcam as w

REPO = Path(__file__).resolve().parents[1]


class FakeBackend:
    """Stands in for the AVFoundation session: frames are ints, 'JPEG' is bytes of the int."""

    def __init__(self, frames=3, interval=0.01, fail_start=None):
        self.n, self.interval, self.fail_start = frames, interval, fail_start
        self.started = self.stopped = False
        self.qualities = []
        self._thread = None

    def start(self, frames):
        if self.fail_start:
            raise self.fail_start
        self.started = True
        self._stop = threading.Event()

        def feed():
            for i in range(1, self.n + 1):
                if self._stop.wait(self.interval):
                    return
                frames.put(i)

        self._thread = threading.Thread(target=feed, daemon=True)
        self._thread.start()

    def to_jpeg(self, frame, quality):
        self.qualities.append(quality)
        return b"\xff\xd8" + bytes([frame])

    def stop(self):
        self.stopped = True
        if self._thread:
            self._stop.set()


# ── frame hand-over ───────────────────────────────────────────────────────────


def test_frames_wait_returns_only_a_newer_frame():
    f = w._Frames()
    f.put("a")
    assert f.wait_newer(0, 1) == ("a", 1)
    assert f.wait_newer(1, 0.05) == (None, 1)  # nothing newer: times out
    f.put("b")
    assert f.wait_newer(1, 1) == ("b", 2)


def test_frames_close_wakes_a_waiting_reader():
    f = w._Frames()
    out = []
    t = threading.Thread(target=lambda: out.append(f.wait_newer(0, 5)))
    t.start()
    time.sleep(0.05)
    f.close()
    t.join(2)
    assert out == [(None, 0)] and not t.is_alive()


# ── Camera / snapshot with a fake backend ─────────────────────────────────────


def test_read_jpeg_returns_each_new_frame_in_order():
    b = FakeBackend(frames=3)
    with w.Camera(backend=b) as cam:
        got = [cam.read_jpeg(quality=0.7, timeout=2) for _ in range(3)]
    assert got == [b"\xff\xd8\x01", b"\xff\xd8\x02", b"\xff\xd8\x03"]
    assert b.qualities == [0.7] * 3 and b.stopped


def test_read_jpeg_raises_when_no_picture_arrives():
    with w.Camera(backend=FakeBackend(frames=0)) as cam:
        with pytest.raises(w.WebcamError, match="no picture"):
            cam.read_jpeg(timeout=0.05)


def test_stop_unblocks_a_reader_and_closes_the_backend():
    b = FakeBackend(frames=0)
    cam = w.Camera(backend=b).start()
    errors = []

    def read():
        try:
            cam.read_jpeg(timeout=5)
        except w.WebcamError as e:
            errors.append(e)

    t = threading.Thread(target=read)
    t.start()
    time.sleep(0.05)
    cam.stop()
    t.join(2)
    assert errors and not t.is_alive() and b.stopped


def test_snapshot_waits_for_the_camera_to_settle_then_takes_a_fresh_frame():
    b = FakeBackend(frames=40, interval=0.01)
    t0 = time.monotonic()
    data = w.snapshot_jpeg(settle_s=0.2, quality=0.92, backend=b)
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.2
    assert data[:2] == b"\xff\xd8" and data[2] > 1  # not the first frame
    assert b.qualities == [0.92] and b.stopped


def test_snapshot_closes_the_camera_when_no_frame_comes():
    b = FakeBackend(frames=0)
    w.FIRST_FRAME_TIMEOUT_S, old = 0.05, w.FIRST_FRAME_TIMEOUT_S
    try:
        with pytest.raises(w.WebcamError):
            w.snapshot_jpeg(settle_s=0, backend=b)
    finally:
        w.FIRST_FRAME_TIMEOUT_S = old
    assert b.stopped


def test_start_failure_propagates_and_is_not_started_twice():
    err = w.WebcamError("Cannot open webcam: no camera found")
    cam = w.Camera(backend=FakeBackend(fail_start=err))
    with pytest.raises(w.WebcamError, match="no camera"):
        cam.start()


# ── permission ────────────────────────────────────────────────────────────────


def _backend_with_status(status, grant=None):
    asked = []

    class Device:
        @staticmethod
        def authorizationStatusForMediaType_(_):
            return status

        @staticmethod
        def requestAccessForMediaType_completionHandler_(_, done):
            asked.append(True)
            threading.Thread(target=done, args=(grant,)).start()

    b = w._AVBackend(1920, 1080)
    b._av = SimpleNamespace(AVCaptureDevice=Device, AVMediaTypeVideo="vide")
    return b, asked


@pytest.mark.parametrize("status", [1, 2])
def test_denied_or_restricted_camera_raises_permission_error(status):
    b, asked = _backend_with_status(status)
    with pytest.raises(w.WebcamPermissionError, match="System Settings"):
        b._authorize()
    assert not asked  # does not ask again once the owner has said no


def test_authorized_camera_passes_without_asking():
    b, asked = _backend_with_status(3)
    b._authorize()
    assert not asked


def test_undecided_camera_asks_and_follows_the_answer():
    b, asked = _backend_with_status(0, grant=True)
    b._authorize()
    assert asked
    b, asked = _backend_with_status(0, grant=False)
    with pytest.raises(w.WebcamPermissionError):
        b._authorize()


def test_permission_error_is_a_webcam_error():
    assert issubclass(w.WebcamPermissionError, w.WebcamError)


# ── the real frame callback and JPEG encoder (macOS with pyobjc only) ─────────


@pytest.mark.skipif(sys.platform != "darwin", reason="AVFoundation is macOS only")
def test_frame_callback_and_jpeg_encoder_keep_colours_and_orientation():
    pytest.importorskip("AVFoundation")
    pil = pytest.importorskip("PIL.Image")
    import io

    import CoreMedia as cm
    import objc
    import Quartz

    width, height = 64, 32
    ret, pixels = Quartz.CVPixelBufferCreate(
        None, width, height, Quartz.kCVPixelFormatType_32BGRA,
        {Quartz.kCVPixelBufferIOSurfacePropertiesKey: {}}, None)
    assert ret == 0
    Quartz.CVPixelBufferLockBaseAddress(pixels, 0)
    stride = Quartz.CVPixelBufferGetBytesPerRow(pixels)
    buf = Quartz.CVPixelBufferGetBaseAddress(pixels).as_buffer(stride * height)
    for y in range(height):
        for x in range(width):
            i = y * stride + x * 4
            # BGRA: red on the left half, blue on the right half, top 8 rows white
            bgra = (255, 255, 255, 255) if y < 8 else ((0, 0, 255, 255) if x < width // 2 else (255, 0, 0, 255))
            buf[i:i + 4] = bytes(bgra)
    Quartz.CVPixelBufferUnlockBaseAddress(pixels, 0)
    _, fmt = cm.CMVideoFormatDescriptionCreateForImageBuffer(None, pixels, None)
    timing = cm.CMSampleTimingInfo(duration=cm.kCMTimeInvalid, presentationTimeStamp=cm.CMTimeMake(0, 30),
                                   decodeTimeStamp=cm.kCMTimeInvalid)
    _, sample = cm.CMSampleBufferCreateReadyWithImageBuffer(None, pixels, fmt, timing, None)

    frames = w._Frames()
    sink = w._make_sink_class(objc).alloc().init()
    sink.frames = frames
    sink.captureOutput_didOutputSampleBuffer_fromConnection_(None, sample, None)  # what AVFoundation calls
    frame, _ = frames.wait_newer(0, 1)
    assert frame is not None

    backend = w._AVBackend(width, height)
    backend._load()
    jpeg = backend.to_jpeg(frame, 0.95)
    assert jpeg[:3] == b"\xff\xd8\xff"
    img = pil.open(io.BytesIO(jpeg)).convert("RGB")
    assert img.size == (width, height)
    r, g, b = img.getpixel((8, 20))
    assert r > 200 and b < 60, "left half must stay red"
    r, g, b = img.getpixel((56, 20))
    assert b > 200 and r < 60, "right half must stay blue"
    assert min(img.getpixel((20, 2))) > 200 and max(img.getpixel((20, 28))) < 255, "white band must stay on top"


# ── routes ────────────────────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import routes.media as media

    # The route saves into ~/.codec/photos: send that to a temp folder.
    real = media.os.path.expanduser
    monkeypatch.setattr(media.os.path, "expanduser",
                        lambda p: str(tmp_path / p[2:]) if p.startswith("~/.codec/photos") else real(p))
    app = FastAPI()
    app.include_router(media.router)
    return TestClient(app), tmp_path


def test_snapshot_route_returns_and_saves_the_picture(client, monkeypatch):
    c, tmp = client
    monkeypatch.setattr(w, "snapshot_jpeg", lambda: b"\xff\xd8JPEGDATA")
    r = c.get("/api/webcam/snapshot")
    assert r.status_code == 200
    body = r.json()
    assert base64.b64decode(body["image"]) == b"\xff\xd8JPEGDATA"
    assert body["filename"].startswith("webcam_") and body["filename"].endswith(".jpg")
    assert (tmp / ".codec" / "photos" / body["filename"]).read_bytes() == b"\xff\xd8JPEGDATA"


def test_snapshot_route_says_why_when_the_camera_is_off_limits(client, monkeypatch):
    c, _ = client

    def deny():
        raise w.WebcamPermissionError(w.PERMISSION_HELP)

    monkeypatch.setattr(w, "snapshot_jpeg", deny)
    r = c.get("/api/webcam/snapshot")
    assert r.status_code == 500 and "Privacy & Security > Camera" in r.json()["error"]


def test_stream_route_sends_frames_then_closes_the_camera(client, monkeypatch):
    c, _ = client
    cams = []

    class FakeCam:
        def __init__(self):
            self.left, self.stopped = 2, False
            cams.append(self)

        def start(self):
            return self

        def read_jpeg(self, quality=0.7, timeout=10):
            if not self.left:
                raise w.WebcamError("no picture arrived")
            self.left -= 1
            return b"\xff\xd8F%d" % self.left

        def stop(self):
            self.stopped = True

    monkeypatch.setattr(w, "Camera", FakeCam)
    r = c.get("/api/webcam/stream")
    assert r.headers["content-type"].startswith("multipart/x-mixed-replace")
    assert r.content.count(b"--frame") == 2 and b"\xff\xd8F1" in r.content and b"\xff\xd8F0" in r.content
    assert cams[0].stopped


def test_stream_route_reports_a_camera_that_will_not_open(client, monkeypatch):
    c, _ = client

    class Broken:
        def start(self):
            raise w.WebcamError("Cannot open webcam: no camera found")

    monkeypatch.setattr(w, "Camera", Broken)
    r = c.get("/api/webcam/stream")
    assert r.status_code == 500 and "no camera found" in r.json()["error"]


# ── OpenCV stays out ──────────────────────────────────────────────────────────


def test_no_python_file_imports_opencv():
    """cv2 bundles GPL libx264/libx265; the buyer app may not ship it (D-35)."""
    skip = {".git", "node_modules", "vendor", "__pycache__", ".venv", "venv"}
    offenders = []
    for path in REPO.rglob("*.py"):
        if skip & set(path.relative_to(REPO).parts) or path.name == Path(__file__).name:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        for node in ast.walk(tree):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                    [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            if any(n == "cv2" or n.startswith("cv2.") for n in names):
                offenders.append(str(path.relative_to(REPO)))
    assert not offenders, f"cv2 imported in {offenders}"
