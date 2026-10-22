"""Locked or unavailable desktops cannot capture, activate or keep injecting input."""

import sys
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from test_native_contract import desktop
from test_window_navigation import NativeRig

from openjiuwen.harness.tools.desktop import worker
from openjiuwen.harness.tools.desktop.macos_session import interactive_session_status, require_interactive_session
from openjiuwen.harness.tools.desktop.schema import Action


@pytest.mark.parametrize(
    "state",
    [
        None,
        {},
        {"kCGSessionLoginDoneKey": True},
        {"kCGSSessionOnConsoleKey": True},
        {"kCGSSessionOnConsoleKey": True, "kCGSessionLoginDoneKey": True, "CGSSessionScreenIsLocked": True},
    ],
)
def test_unusable_session_is_not_ready(state):
    q = NS(CGSessionCopyCurrentDictionary=lambda: state)
    assert interactive_session_status(q)[0] is False
    with pytest.raises(PermissionError):
        require_interactive_session(q)


def test_unlocked_console_does_not_require_a_present_lock_flag():
    q = NS(
        CGSessionCopyCurrentDictionary=lambda: {
            "kCGSSessionOnConsoleKey": True,
            "kCGSessionLoginDoneKey": True,
        }
    )
    assert interactive_session_status(q) == (True, "ready")


def test_failed_native_lookup_is_unavailable():
    q = NS(CGSessionCopyCurrentDictionary=Mock(side_effect=RuntimeError("unavailable")))
    assert interactive_session_status(q)[0] is False


def test_capability_does_not_confuse_tcc_with_unlocked_desktop(monkeypatch):
    monkeypatch.setattr(worker.sys, "platform", "darwin")
    q = NS(
        CGGetActiveDisplayList=lambda *_: (0, [1], 1),
        CGPreflightScreenCaptureAccess=lambda: True,
        CGSessionCopyCurrentDictionary=lambda: {"CGSSessionScreenIsLocked": True},
    )
    monkeypatch.setitem(sys.modules, "Quartz", q)
    monkeypatch.setitem(sys.modules, "ApplicationServices", NS(AXIsProcessTrusted=lambda: True))
    monkeypatch.setitem(sys.modules, "AppKit", NS())
    result = worker.capability(1)
    assert result["screen_recording"] and result["accessibility"]
    assert not result["available"] and not result["interactive_session"]
    assert "locked" in result["reason"]


def test_exact_window_rejects_lock_before_app_lookup_or_activation():
    native = desktop()
    native.q.CGSessionCopyCurrentDictionary = lambda: {"CGSSessionScreenIsLocked": True}
    with pytest.raises(PermissionError, match="locked"):
        native.focus("test")
    native.application.assert_not_called()


def test_navigation_lock_prevents_capture_and_focus():
    rig = NativeRig()
    observed = rig.observe()
    rig.captures.clear()
    rig.locked = True
    with pytest.raises(PermissionError, match="locked"):
        rig.act(Action(kind="focus", window_id=rig.handle(1)), observed)
    with pytest.raises(PermissionError, match="locked"):
        rig.observe()
    assert not rig.posts and not rig.captures and not rig.raised


def test_lock_during_drag_releases_button_without_further_drag_input():
    rig = NativeRig()
    observed = rig.observe()
    rig.front, rig.focused[10] = 10, 1

    def post(_tap, event):
        rig.posts.append(event)
        if event["kind"] == "down":
            rig.locked = True

    rig.d.q.CGEventPost = post
    rig.d.q.kCGEventLeftMouseDragged = "drag"
    with pytest.raises(PermissionError, match="locked"):
        rig.act(Action(kind="drag", path=[[20, 40], [80, 70]], duration=0.1), observed)
    assert [event["kind"] for event in rig.posts] == ["down", "up"]
