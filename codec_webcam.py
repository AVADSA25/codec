"""Mac webcam capture through AVFoundation (pyobjc), without OpenCV.

OpenCV's wheel bundles FFmpeg with GPL libx264 and libx265, which the buyer app
may not ship (D-35, docs/WEBCAM-AVFOUNDATION-DESIGN.md). AVFoundation is the same
camera API OpenCV used on macOS, so a frame looks the same. pyobjc-framework-
AVFoundation, -CoreMedia and -Quartz are already in the buyer lock.

    snapshot_jpeg()           one sharp still: opens the camera, waits for focus and
                              exposure to settle, returns JPEG bytes, closes the camera
    Camera().read_jpeg()      a live feed for the MJPEG stream: open once, ask for the
                              next frame as often as needed, close when done

Errors are `WebcamError`; `WebcamPermissionError` means the owner has not allowed the
camera for this app (System Settings > Privacy & Security > Camera).

Every AVFoundation name is imported on first use, so this module imports on any OS and
the tests can run without a camera: they pass a fake `backend` to `Camera`.
"""
from __future__ import annotations

import ctypes
import threading
import time

# AVAuthorizationStatus values (not importable without AVFoundation).
_AUTH_NOT_DETERMINED, _AUTH_RESTRICTED, _AUTH_DENIED, _AUTH_AUTHORIZED = 0, 1, 2, 3

PERMISSION_HELP = (
    "Camera access is off for CODEC. Turn it on in System Settings > Privacy & Security > Camera."
)

# How long a first frame may take after the session starts, and how long the owner
# has to answer the macOS permission prompt.
FIRST_FRAME_TIMEOUT_S = 10.0
PERMISSION_PROMPT_TIMEOUT_S = 60.0
# Auto-exposure, white balance and autofocus need about this long after the camera
# starts; a frame taken sooner is dark and soft (the cause of the blurry captures
# the old OpenCV code worked around with 25 discarded frames).
SETTLE_S = 1.25


class WebcamError(RuntimeError):
    """The camera could not be opened or gave no picture."""


class WebcamPermissionError(WebcamError):
    """The owner has not allowed the camera for this app."""


class _Frames:
    """The newest frame from the camera, handed from the capture thread to readers."""

    def __init__(self):
        self._cond = threading.Condition()
        self._frame = None
        self._seq = 0
        self._closed = False

    def put(self, frame):
        with self._cond:
            self._frame = frame
            self._seq += 1
            self._cond.notify_all()

    def close(self):
        with self._cond:
            self._closed = True
            self._frame = None
            self._cond.notify_all()

    @property
    def seq(self):
        with self._cond:
            return self._seq

    def wait_newer(self, seq, timeout):
        """Return (frame, seq) of the first frame newer than `seq`, or (None, seq) on timeout."""
        deadline = time.monotonic() + timeout
        with self._cond:
            while not self._closed and self._seq <= seq:
                left = deadline - time.monotonic()
                if left <= 0:
                    return None, seq
                self._cond.wait(left)
            if self._closed:
                return None, seq
            return self._frame, self._seq


class _AVBackend:
    """The real camera: an AVCaptureSession feeding `_Frames`, and CoreImage for JPEG."""

    def __init__(self, width, height):
        self._width, self._height = width, height
        self._session = None
        self._sink = None
        self._queue = None
        self._ci_context = None
        self._av = None
        self._quartz = None

    # ── setup ──────────────────────────────────────────────────────────────
    def _load(self):
        try:
            import AVFoundation as av
            import CoreMedia as cm
            import objc
            import Quartz as quartz
        except ImportError as e:  # not macOS, or pyobjc is missing
            raise WebcamError(f"Camera support is not installed here ({e.name})") from e
        self._av, self._cm, self._objc, self._quartz = av, cm, objc, quartz

    def _authorize(self):
        av = self._av
        status = av.AVCaptureDevice.authorizationStatusForMediaType_(av.AVMediaTypeVideo)
        if status == _AUTH_AUTHORIZED:
            return
        if status in (_AUTH_DENIED, _AUTH_RESTRICTED):
            raise WebcamPermissionError(PERMISSION_HELP)
        answered = threading.Event()
        result = []

        def _done(granted):
            result.append(bool(granted))
            answered.set()

        av.AVCaptureDevice.requestAccessForMediaType_completionHandler_(av.AVMediaTypeVideo, _done)
        if not answered.wait(PERMISSION_PROMPT_TIMEOUT_S) or not result[0]:
            raise WebcamPermissionError(PERMISSION_HELP)

    def _device(self):
        av = self._av
        dev = av.AVCaptureDevice.defaultDeviceWithMediaType_(av.AVMediaTypeVideo)
        if dev is None:
            devices = av.AVCaptureDevice.devicesWithMediaType_(av.AVMediaTypeVideo)
            dev = devices[0] if devices else None
        if dev is None:
            raise WebcamError("Cannot open webcam: no camera found")
        return dev

    def _dispatch_queue(self):
        """A serial dispatch queue for the frame callbacks (libdispatch through ctypes, so
        pyobjc-framework-libdispatch is not needed)."""
        libsystem = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        libsystem.dispatch_queue_create.restype = ctypes.c_void_p
        libsystem.dispatch_queue_create.argtypes = [ctypes.c_char_p, ctypes.c_void_p]
        raw = libsystem.dispatch_queue_create(b"ai.avadigital.codec.webcam", None)
        if not raw:
            raise WebcamError("Cannot open webcam: no dispatch queue")
        return self._objc.objc_object(c_void_p=raw)

    def start(self, frames):
        self._load()
        self._authorize()
        av, objc = self._av, self._objc
        device = self._device()
        cam_input, err = av.AVCaptureDeviceInput.deviceInputWithDevice_error_(device, None)
        if cam_input is None:
            raise WebcamError(f"Cannot open webcam: {err}")

        session = av.AVCaptureSession.alloc().init()
        preset = self._preset(session)
        if preset:
            session.setSessionPreset_(preset)
        if not session.canAddInput_(cam_input):
            raise WebcamError("Cannot open webcam: the camera refused the session")
        session.addInput_(cam_input)
        output = av.AVCaptureVideoDataOutput.alloc().init()
        output.setAlwaysDiscardsLateVideoFrames_(True)
        if not session.canAddOutput_(output):
            raise WebcamError("Cannot open webcam: the camera refused the session")
        session.addOutput_(output)

        self._sink = _make_sink_class(objc).alloc().init()
        self._sink.frames = frames
        self._queue = self._dispatch_queue()
        output.setSampleBufferDelegate_queue_(self._sink, self._queue)
        self._keep_autofocus(device)
        self._session = session
        session.startRunning()

    def _preset(self, session):
        av = self._av
        # The old OpenCV code asked for 1920x1080 because the default 640x480 reads soft
        # once scaled up for vision analysis. Fall back to the camera's best.
        for name, label in (("1920x1080", "AVCaptureSessionPreset1920x1080"),
                            ("high", "AVCaptureSessionPresetHigh")):
            preset = getattr(av, label, None)
            if preset is not None and session.canSetSessionPreset_(preset):
                return preset
        return None

    def _keep_autofocus(self, device):
        av = self._av
        try:
            mode = av.AVCaptureFocusModeContinuousAutoFocus
            if device.isFocusModeSupported_(mode) and device.lockForConfiguration_(None)[0]:
                device.setFocusMode_(mode)
                device.unlockForConfiguration()
        except Exception:
            pass  # a camera with fixed focus has nothing to set

    # ── use ────────────────────────────────────────────────────────────────
    def to_jpeg(self, frame, quality):
        q = self._quartz
        if self._ci_context is None:
            self._ci_context = q.CIContext.contextWithOptions_(None)
        image = q.CIImage.imageWithCVPixelBuffer_(frame)
        cg = self._ci_context.createCGImage_fromRect_(image, image.extent())
        if cg is None:
            raise WebcamError("Cannot capture from webcam: the frame could not be read")
        data = self._objc.lookUpClass("NSMutableData").data()
        dest = q.CGImageDestinationCreateWithData(data, "public.jpeg", 1, None)
        q.CGImageDestinationAddImage(dest, cg, {q.kCGImageDestinationLossyCompressionQuality: quality})
        if not q.CGImageDestinationFinalize(dest):
            raise WebcamError("Cannot capture from webcam: JPEG encoding failed")
        return bytes(data)

    def stop(self):
        session, self._session = self._session, None
        if session is not None:
            try:
                session.stopRunning()
            except Exception:
                pass
        sink, self._sink = self._sink, None
        if sink is not None:
            sink.frames = None


_SINK_CLASS = None


def _make_sink_class(objc):
    """The frame-callback class AVFoundation calls on its own queue (built once)."""
    global _SINK_CLASS
    if _SINK_CLASS is None:
        import CoreMedia as cm

        class _CodecFrameSink(objc.lookUpClass("NSObject")):
            frames = None

            @objc.typedSelector(b"v@:@^{opaqueCMSampleBuffer=}@")
            def captureOutput_didOutputSampleBuffer_fromConnection_(self, output, sample, connection):
                frames = self.frames
                if frames is None:
                    return
                pixels = cm.CMSampleBufferGetImageBuffer(sample)
                if pixels is not None:
                    frames.put(pixels)

        _SINK_CLASS = _CodecFrameSink
    return _SINK_CLASS


class Camera:
    """An open webcam. Use as a context manager, or call `start()` and `stop()`."""

    def __init__(self, width=1920, height=1080, backend=None):
        self._backend = backend or _AVBackend(width, height)
        self._frames = _Frames()
        self._seq = 0
        self._started = False

    def start(self):
        if not self._started:
            self._backend.start(self._frames)
            self._started = True
        return self

    def stop(self):
        self._started = False
        self._backend.stop()
        self._frames.close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    def read_jpeg(self, quality=0.7, timeout=FIRST_FRAME_TIMEOUT_S):
        """JPEG of the next frame the camera delivers. Raises WebcamError after `timeout`."""
        frame, self._seq = self._frames.wait_newer(self._seq, timeout)
        if frame is None:
            raise WebcamError("Cannot capture from webcam: no picture arrived")
        return self._backend.to_jpeg(frame, quality)

    def settle(self, settle_s=SETTLE_S):
        """Wait for the first frame, then until the camera has run for `settle_s`."""
        started = time.monotonic()
        frame, self._seq = self._frames.wait_newer(self._seq, FIRST_FRAME_TIMEOUT_S)
        if frame is None:
            raise WebcamError("Cannot capture from webcam: no picture arrived")
        left = settle_s - (time.monotonic() - started)
        if left > 0:
            time.sleep(left)


def snapshot_jpeg(width=1920, height=1080, quality=0.92, settle_s=SETTLE_S, backend=None):
    """One still as JPEG bytes. Opens the camera, lets it settle, closes it again.

    The JPEG quality is high because this is one still for vision analysis, not a
    stream: do not throw away the detail the settle time bought."""
    with Camera(width, height, backend=backend) as cam:
        cam.settle(settle_s)
        return cam.read_jpeg(quality=quality)
