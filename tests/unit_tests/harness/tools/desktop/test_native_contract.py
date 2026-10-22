"""Isolated native API fakes: no macOS frameworks or real input required."""

from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from openjiuwen.harness.tools.desktop import capability as detector
from openjiuwen.harness.tools.desktop.schema import Action, DesktopSettings, Observation, Target
from openjiuwen.harness.tools.desktop.worker import NativeDesktop


def desktop(bounds=(10, 20, 700, 480), *, title="test", display=1):
    driver = NativeDesktop.__new__(NativeDesktop)
    driver.settings = DesktopSettings()
    driver.targets = {"test": Target(name="test", pid=100, window_title=title)}
    driver.target, driver.display_id = "test", display
    app = NS(
        processIdentifier=lambda: 100,
        isTerminated=lambda: False,
        launchDate=lambda: NS(timeIntervalSince1970=lambda: 1.0),
    )
    driver.application = Mock(return_value=app)
    window = {
        "kCGWindowOwnerPID": 100,
        "kCGWindowLayer": 0,
        "kCGWindowName": "test",
        "kCGWindowNumber": 12,
        "kCGWindowBounds": dict(zip(("X", "Y", "Width", "Height"), bounds)),
    }
    driver.q = NS(
        CGSessionCopyCurrentDictionary=lambda: {"kCGSSessionOnConsoleKey": True, "kCGSessionLoginDoneKey": True},
        CGWindowListCopyWindowInfo=lambda *_: [window],
        kCGWindowListOptionOnScreenOnly=1,
        kCGWindowListExcludeDesktopElements=2,
        kCGNullWindowID=0,
        CGGetActiveDisplayList=lambda *_: (0, [1, 2], 2),
        CGDisplayBounds=lambda _: NS(origin=NS(x=0, y=0), size=NS(width=1440, height=900)),
    )
    driver.last = Observation("", 1280, 800, bounds, "test", 12, 1)
    return driver


@pytest.mark.parametrize("bounds", [(-1, 20, 700, 480), (1200, 20, 700, 480), (10, -1, 700, 480), (10, 800, 700, 480)])
def test_window_crossing_selected_display_rejected(bounds):
    driver = desktop(bounds)
    with pytest.raises(RuntimeError, match="outside the selected display"):
        driver.window()


def test_display_disconnect_rejected():
    with pytest.raises(RuntimeError, match="disconnected"):
        desktop(display=4).window()


def test_monitor_geometry_never_rediscovers_apps_from_background_thread():
    driver = desktop()
    driver.monitor_pids = frozenset({100})
    driver.application = Mock(side_effect=RuntimeError("background registration unavailable"))
    driver.appkit = NS(NSWorkspace=NS(sharedWorkspace=lambda: NS(frontmostApplication=lambda: None)))
    snapshot = driver.monitor_snapshot()
    assert snapshot["foreground_pid"] is None
    assert snapshot["owned_windows"] == [{"pid": 100, "window_id": 12, "bounds": (10, 20, 700, 480)}]
    driver.application.assert_not_called()


@pytest.mark.parametrize("missing", [0, 1, 2, 3])
def test_pid_lookup_settles_only_missing_registration_with_bounded_reads(missing):
    driver = desktop()
    app = driver.application("test")
    del driver.application
    lookup = Mock(side_effect=[None] * missing + [app])
    pump = Mock()
    driver.appkit = NS(
        NSRunningApplication=NS(runningApplicationWithProcessIdentifier_=lookup),
        NSRunLoop=NS(currentRunLoop=lambda: NS(runUntilDate_=pump)),
        NSDate=NS(dateWithTimeIntervalSinceNow_=lambda seconds: seconds),
    )
    if missing == 3:
        with pytest.raises(RuntimeError, match="no longer running"):
            driver.application("test")
    else:
        assert driver.application("test") is app
    assert lookup.call_count == min(missing + 1, 3)
    assert pump.call_count == min(missing, 2)
    assert all(call.args == (100,) for call in lookup.call_args_list)


@pytest.mark.parametrize("terminated", [False, True])
def test_pid_lookup_does_not_retry_terminated_or_wrong_bundle(terminated):
    driver = desktop()
    del driver.application
    driver.targets["test"] = Target(name="test", pid=100, bundle_id="approved", window_title="test")
    app = NS(isTerminated=lambda: terminated, bundleIdentifier=lambda: "different")
    lookup = Mock(return_value=app)
    driver.appkit = NS(NSRunningApplication=NS(runningApplicationWithProcessIdentifier_=lookup))
    with pytest.raises(RuntimeError, match="no longer running" if terminated else "bundle"):
        driver.application("test")
    assert lookup.call_count == 1


def test_ambiguous_or_missing_title_rejected():
    with pytest.raises(RuntimeError, match="exactly one"):
        desktop(title="wrong").window()


def test_single_window_mode_cannot_capture_app_only_target():
    with pytest.raises(PermissionError, match="app-wide access is disabled"):
        desktop(title="").window()


def test_single_window_grant_is_retired_after_observed_disappearance():
    driver = desktop()
    _, selected = driver.window()
    driver.q.CGWindowListCopyWindowInfo = lambda *_: []
    with pytest.raises(RuntimeError, match="exactly one"):
        driver.window()
    driver.q.CGWindowListCopyWindowInfo = lambda *_: [selected]
    with pytest.raises(PermissionError, match="disappeared"):
        driver.window()


def test_stale_observation_rejected_before_input():
    driver = desktop()
    driver.check_focus = Mock()
    with pytest.raises(RuntimeError, match="stale"):
        driver.act(Action(kind="type", text="x"), {"generation": 0})
    driver.check_focus.assert_not_called()


def test_moved_window_rejected():
    driver = desktop()
    driver.check_focus = Mock()
    driver.last = Observation("", 1280, 800, (99, 20, 700, 480), "test", 12, 1)
    with pytest.raises(RuntimeError, match="moved"):
        driver.act(Action(kind="type", text="x"), {"generation": 1})


def test_worker_revalidates_shortcut_before_native_input():
    driver = desktop()
    driver.check_focus = Mock()
    with pytest.raises(ValueError, match="unsupported shortcut"):
        driver.act(Action.model_construct(kind="shortcut", keys=["cmd", "f99"]), {"generation": 1})


def test_target_allowlist_rejected_before_activation():
    driver = desktop()
    with pytest.raises(PermissionError, match="allowlist"):
        driver.focus("unconfigured")


def test_missing_permissions_reported_without_registration(monkeypatch):
    settings = DesktopSettings(
        enabled=True,
        targets=[Target(name="test", pid=100)],
    )
    monkeypatch.setattr(detector.sys, "platform", "darwin")
    monkeypatch.setattr(
        detector.subprocess,
        "run",
        lambda *a, **k: NS(returncode=0, stdout='{"available":false,"reason":"Accessibility denied"}'),
    )
    assert detector.detect_desktop_capability(settings)["available"] is False


def test_headless_does_not_spawn(monkeypatch):
    monkeypatch.setattr(detector.subprocess, "run", Mock(side_effect=AssertionError("unexpected probe")))
    assert detector.detect_desktop_capability(DesktopSettings())["reason"] == "disabled"


@pytest.mark.parametrize(
    "pid,title,error,expected",
    [(100, "test", 0, True), (101, "test", 0, False), (100, "other", 0, False), (100, "test", -1, False)],
)
def test_accessibility_hit_test_rejects_other_apps_or_windows(pid, title, error, expected):
    driver = desktop()
    driver.ax = NS(
        AXUIElementCreateSystemWide=lambda: "system",
        AXUIElementCopyElementAtPosition=lambda *_: (error, "element"),
        AXUIElementGetPid=lambda *_: (0, pid),
        AXUIElementCopyAttributeValue=lambda _, attr, __: (0, "window" if attr == "AXWindow" else title),
    )
    assert driver.point_is_target((20, 30)) is expected


@pytest.mark.parametrize("occluded", [True, False])
def test_scroll_moves_to_scaled_target_center_and_refuses_occlusion(occluded):
    driver = desktop()
    driver.check_focus = Mock()
    driver.point_is_target = Mock(return_value=not occluded)
    driver.q.CGEventPost = Mock()
    driver.q.CGEventCreateMouseEvent = Mock(return_value="move")
    driver.q.CGEventCreateScrollWheelEvent = Mock(return_value="scroll")
    driver.q.CGEventSetFlags = Mock()
    driver.q.kCGHIDEventTap = 1
    driver.q.kCGEventMouseMoved = 2
    driver.q.kCGMouseButtonLeft = 0
    driver.q.kCGScrollEventUnitLine = 1
    if occluded:
        with pytest.raises(RuntimeError, match="occluded"):
            driver.act(Action(kind="scroll", direction="left"), {"generation": 1})
        driver.q.CGEventPost.assert_not_called()
    else:
        driver.act(Action(kind="scroll", direction="left"), {"generation": 1})
        assert all(call.args == ((360.0, 260.0),) for call in driver.point_is_target.call_args_list)
        driver.q.CGEventCreateScrollWheelEvent.assert_called_once_with(None, 1, 2, 0, 3)
        assert driver.last is None  # the next action needs a fresh screenshot


@pytest.mark.parametrize(
    "actual_title,bounds,error",
    [
        ("other", (10, 20, 700, 480), "another window"),
        ("test", (1200, 20, 700, 480), "selected display"),
        ("test", (20, 20, 700, 480), "moved"),
    ],
)
def test_each_key_pair_rechecks_window_identity_and_display_scope(actual_title, bounds, error):
    driver = desktop(bounds)
    driver.last = Observation("", 1280, 800, (10, 20, 700, 480), "test", 12, 1)
    driver.appkit = NS(
        NSWorkspace=NS(sharedWorkspace=lambda: NS(frontmostApplication=lambda: driver.application("test")))
    )
    driver.ax = NS(
        AXUIElementCreateApplication=lambda _: "application",
        AXUIElementCopyAttributeValue=lambda _, attr, __: (0, "window" if attr == "AXFocusedWindow" else actual_title),
    )
    driver.q.CGEventCreateKeyboardEvent = Mock()
    with pytest.raises(RuntimeError, match=error):
        driver.post_key(0, text="x")
    driver.q.CGEventCreateKeyboardEvent.assert_not_called()


@pytest.mark.parametrize("actual_title,accepted", [("test – 1 page", True), ("other", False), ("test", False)])
def test_distinct_ax_title_keeps_quartz_and_focused_window_checks_exact(actual_title, accepted):
    driver = desktop()
    driver.targets["test"] = Target(
        name="test", pid=100, window_title="test", accessibility_window_title="test – 1 page"
    )
    driver.appkit = NS(
        NSWorkspace=NS(sharedWorkspace=lambda: NS(frontmostApplication=lambda: driver.application("test")))
    )
    driver.ax = NS(
        AXUIElementCreateApplication=lambda _: "application",
        AXUIElementCreateSystemWide=lambda: "system",
        AXUIElementCopyElementAtPosition=lambda *_: (0, "element"),
        AXUIElementGetPid=lambda *_: (0, 100),
        AXUIElementCopyAttributeValue=lambda _, attr, __: (
            (0, "window")
            if attr in ("AXFocusedWindow", "AXWindow")
            else (0, actual_title)
            if attr == "AXTitle"
            else (-25205, None)
        ),
    )
    # Quartz still sees the undecorated title. A permissive suffix/substring rule
    # would wrongly accept actual_title="test" in the explicit AX configuration.
    assert driver.window()[1]["kCGWindowName"] == "test"
    if accepted:
        driver.check_focus()
    else:
        with pytest.raises(RuntimeError, match="another window"):
            driver.check_focus()
    assert driver.point_is_target((20, 30)) is accepted


def test_separate_ax_title_defaults_and_scope_validation():
    assert Target(name="a", pid=100, window_title="original").ax_window_title == "original"
    with pytest.raises(ValueError, match="requires a Quartz window title"):
        Target(name="a", pid=100, accessibility_window_title="AX only")


def test_focus_raises_exact_configured_ax_window(monkeypatch):
    driver = desktop()
    driver.targets["test"] = Target(
        name="test", pid=100, window_title="test", accessibility_window_title="different AX title"
    )
    app = driver.application("test")
    app.activateWithOptions_ = Mock()
    driver.appkit = NS(
        NSApplicationActivateIgnoringOtherApps=1,
        NSWorkspace=NS(sharedWorkspace=lambda: NS(frontmostApplication=lambda: app)),
    )
    driver.ax = NS(
        AXUIElementCreateApplication=lambda _: "application",
        AXUIElementCopyAttributeValue=lambda element, attr, _: (
            0,
            {
                ("application", "AXWindows"): ["wrong", "correct"],
                ("wrong", "AXTitle"): "test",
                ("correct", "AXTitle"): "different AX title",
            }[(element, attr)],
        ),
        AXUIElementPerformAction=Mock(),
    )
    driver.check_focus, driver.point_is_target = Mock(), Mock(return_value=True)
    monkeypatch.setattr("openjiuwen.harness.tools.desktop.worker.time.sleep", lambda _: None)
    driver.focus("test")
    driver.ax.AXUIElementPerformAction.assert_called_once_with("correct", "AXRaise")
    driver.check_focus.assert_called_once()
