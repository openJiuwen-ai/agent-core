"""Window scope, cancellation, compatibility and offscreen pixel conversion."""

from types import SimpleNamespace as NS

import pytest

from openjiuwen.harness.tools.desktop import capture


def test_auto_falls_back_only_when_optional_framework_is_unavailable(monkeypatch):
    def missing(_):
        raise ImportError("optional")

    monkeypatch.setattr(capture.importlib, "import_module", missing)
    assert capture.WindowCapture(None).backend == "screencapture"
    with pytest.raises(RuntimeError, match="not installed"):
        capture.WindowCapture(None, backend="screencapturekit")


def fake_kit(error=None):
    calls = []
    window = NS(
        windowID=lambda: 17,
        frame=lambda: NS(size=NS(width=100, height=60)),
        owningApplication=lambda: NS(processID=lambda: 10),
    )
    selected = NS(pointPixelScale=lambda: 2)
    config = NS(
        **{
            name: (lambda value, name=name: calls.append((name, value)))
            for name in ["setWidth_", "setHeight_", "setShowsCursor_", "setIgnoreShadowsSingleWindow_"]
        }
    )

    def shareable_content(_exclude, _visible, done):
        done(NS(windows=lambda: [window]), None)

    kit = NS(
        SCShareableContent=NS(
            getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_=shareable_content
        ),
        SCContentFilter=NS(
            alloc=lambda: NS(
                initWithDesktopIndependentWindow_=lambda w: (calls.append(("window", w.windowID())), selected)[1]
            )
        ),
        SCStreamConfiguration=NS(alloc=lambda: NS(init=lambda: config)),
        SCScreenshotManager=NS(
            captureImageWithFilter_configuration_completionHandler_=lambda filt, cfg, done: done(
                "pixels" if error is None else None, error
            )
        ),
    )
    return kit, calls


def test_capture_requests_exact_window_filter_and_never_display(monkeypatch):
    kit, calls = fake_kit()
    monkeypatch.setattr(capture, "image_from_cgimage", lambda q, image: image)
    backend = capture.WindowCapture(None, kit=kit)
    backend.refresh()
    assert backend.capture(17, expected_pid=10) == "pixels"
    assert ("window", 17) in calls
    assert ("setWidth_", 200) in calls and ("setHeight_", 120) in calls
    assert ("setShowsCursor_", False) in calls and ("setIgnoreShadowsSingleWindow_", True) in calls
    with pytest.raises(RuntimeError, match="not available"):
        backend.capture(18)
    with pytest.raises(RuntimeError, match="another application"):
        backend.capture(17, expected_pid=20)


def test_stable_catalog_reuses_handles_but_every_capture_requests_fresh_pixels(monkeypatch):
    kit, calls = fake_kit()
    reads = []
    original = kit.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_

    def read(*args):
        reads.append(1)
        return original(*args)

    kit.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_ = read
    monkeypatch.setattr(capture, "image_from_cgimage", lambda q, image: image)
    backend = capture.WindowCapture(None, kit=kit)
    for signature in ["same geometry", "same geometry", "new geometry"]:
        backend.refresh(signature)
        backend.capture(17, expected_pid=10)
    assert len(reads) == 2 and calls.count(("window", 17)) == 3


def test_capture_failure_never_uses_legacy_or_stale_pixels(monkeypatch):
    kit, _ = fake_kit(error="permission denied")
    backend = capture.WindowCapture(None, kit=kit)
    monkeypatch.setattr(backend, "_legacy", lambda _: pytest.fail("unexpected fallback"))
    backend.refresh()
    with pytest.raises(RuntimeError, match="permission denied"):
        backend.capture(17)


def test_cancellation_and_deadline_bound_async_capture():
    backend = capture.WindowCapture(None, backend="screencapture")
    with pytest.raises(TimeoutError):
        backend._wait(lambda _: None, timeout=0)
    backend.check_stop = lambda: (_ for _ in ()).throw(RuntimeError("cancelled"))
    with pytest.raises(RuntimeError, match="cancelled"):
        backend._wait(lambda _: None)


def test_offscreen_cgimage_conversion_preserves_color_alpha_and_orientation():
    q = pytest.importorskip("Quartz")
    # Construct pixels in memory; this test does not read any window or display.
    raw = bytes([255, 0, 0, 255, 0, 128, 0, 128, 0, 0, 255, 255, 0, 0, 0, 0])
    provider = q.CGDataProviderCreateWithData(None, raw, len(raw), None)
    image = q.CGImageCreate(
        2,
        2,
        8,
        32,
        8,
        q.CGColorSpaceCreateDeviceRGB(),
        q.kCGImageAlphaPremultipliedLast | q.kCGBitmapByteOrder32Big,
        provider,
        None,
        False,
        q.kCGRenderingIntentDefault,
    )
    converted = capture.image_from_cgimage(q, image)
    assert list(converted.getdata()) == [(255, 0, 0, 255), (0, 255, 0, 128), (0, 0, 255, 255), (0, 0, 0, 0)]
