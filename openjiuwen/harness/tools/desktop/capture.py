"""Window-only capture backends. No desktop pixels or cached images are returned."""

from __future__ import annotations

import importlib
import subprocess
import tempfile
import threading
import time
from pathlib import Path


def image_from_cgimage(quartz, image):
    """Normalize row padding, color space and premultiplied alpha in memory."""
    from PIL import Image

    width, height = quartz.CGImageGetWidth(image), quartz.CGImageGetHeight(image)
    if not width or not height or width * height > 64_000_000:
        raise RuntimeError("invalid native capture dimensions")
    context = quartz.CGBitmapContextCreate(
        None,
        width,
        height,
        8,
        width * 4,
        quartz.CGColorSpaceCreateDeviceRGB(),
        quartz.kCGImageAlphaPremultipliedLast | quartz.kCGBitmapByteOrder32Big,
    )
    if context is None:
        raise RuntimeError("cannot allocate native capture buffer")
    quartz.CGContextDrawImage(context, ((0, 0), (width, height)), image)
    raw = quartz.CGBitmapContextGetData(context).as_buffer(width * height * 4)
    return Image.frombytes("RGBa", (width, height), bytes(raw)).convert("RGBA")


class WindowCapture:
    """Reuse framework bindings; each request captures the specified live window."""

    def __init__(self, quartz, *, backend="auto", check_stop=lambda: None, kit=None):
        self.quartz, self.check_stop = quartz, check_stop
        self.kit = kit
        if backend != "screencapture" and self.kit is None:
            try:
                self.kit = importlib.import_module("ScreenCaptureKit")
            except ImportError:
                if backend == "screencapturekit":
                    raise RuntimeError("ScreenCaptureKit Python bindings are not installed") from None
        supported = self.kit is not None and hasattr(self.kit, "SCScreenshotManager")
        if backend == "screencapturekit" and not supported:
            raise RuntimeError("ScreenCaptureKit screenshots require macOS 14 or later")
        self.backend = "screencapturekit" if supported and backend != "screencapture" else "screencapture"
        self._windows = {}
        self._signature = None

    def _wait(self, submit, timeout=5):
        ready = threading.Event()
        result = []

        def complete(value, error):
            result.extend((value, error))
            ready.set()

        self.check_stop()
        submit(complete)
        deadline = time.monotonic() + timeout
        while not ready.wait(0.01):
            self.check_stop()
            if time.monotonic() >= deadline:
                raise TimeoutError("native window capture timed out")
        self.check_stop()
        value, error = result[0], result[1]
        if error is not None or value is None:
            raise RuntimeError(f"native window capture failed: {error}")
        return value

    def refresh(self, signature=None):
        """Refresh handles once per observation, never retain image contents."""
        if self.backend == "screencapturekit":
            if signature is not None and signature == self._signature:
                return
            content_api = self.kit.SCShareableContent
            request = content_api.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_
            content = self._wait(lambda done: request(True, True, done))
            self._windows = {int(w.windowID()): w for w in content.windows()}
            self._signature = signature

    def capture(self, native_id, *, expected_pid=None):
        """Capture one approved live window without desktop fallback."""
        self.check_stop()
        if self.backend == "screencapture":
            return self._legacy(native_id)
        window = self._windows.get(int(native_id))
        if window is None:
            raise RuntimeError("approved window is not available for capture")
        if expected_pid is not None and (
            window.owningApplication() is None or int(window.owningApplication().processID()) != expected_pid
        ):
            raise RuntimeError("capture window belongs to another application")
        kit = self.kit
        content_filter = kit.SCContentFilter.alloc().initWithDesktopIndependentWindow_(window)
        config = kit.SCStreamConfiguration.alloc().init()
        frame = window.frame()
        # A window filter is essential: rectangular desktop capture would expose other apps.
        scale = content_filter.pointPixelScale()
        config.setWidth_(max(1, round(frame.size.width * scale)))
        config.setHeight_(max(1, round(frame.size.height * scale)))
        config.setShowsCursor_(False)
        config.setIgnoreShadowsSingleWindow_(True)
        image = self._wait(
            lambda done: kit.SCScreenshotManager.captureImageWithFilter_configuration_completionHandler_(
                content_filter, config, done
            )
        )
        return image_from_cgimage(self.quartz, image)

    @staticmethod
    def _legacy(native_id):
        from PIL import Image

        with tempfile.TemporaryDirectory(prefix="jiuwen-window-") as directory:
            path = Path(directory) / "window.png"
            subprocess.run(
                ["/usr/sbin/screencapture", "-x", "-o", "-a", "-l", str(native_id), str(path)],
                check=True,
                timeout=5,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            with Image.open(path) as image:
                return image.convert("RGBA")
