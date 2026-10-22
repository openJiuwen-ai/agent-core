"""Offline navigation contract and real worker routes with synthetic windows/pixels."""

import base64
import io
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from PIL import Image

from openjiuwen.harness.tools.desktop import worker
from openjiuwen.harness.tools.desktop.direct_protocol import decode_call
from openjiuwen.harness.tools.desktop.macos_navigation import MacOSNavigation
from openjiuwen.harness.tools.desktop.navigation import Surface, WindowCatalog
from openjiuwen.harness.tools.desktop.schema import Action, DesktopSettings, Observation, Target
from openjiuwen.harness.tools.desktop.session_contract import DesktopObservationContext, DesktopRect


def rect(x=0, y=0, width=400, height=300):
    return DesktopRect(x=x, y=y, width=width, height=height)


def row(number, pid=10, bounds=(10, 30, 150, 100), title="Document", layer=0):
    return dict(
        kCGWindowNumber=number,
        kCGWindowOwnerPID=pid,
        kCGWindowBounds=dict(zip(("X", "Y", "Width", "Height"), bounds)),
        kCGWindowName=title,
        kCGWindowLayer=layer,
    )


class NativeRig:
    """Public native boundaries only; no frameworks, screen capture or actual events."""

    def __init__(self):
        self.rows = [row(1), row(2, bounds=(200, 30, 150, 100)), row(3, 20, (20, 180, 150, 100))]
        self.front, self.focused = 10, {10: 1, 20: 3}
        self.launches = {10: 1.0, 20: 2.0}
        self.roles = {}
        self.sheets = {}
        self.screen = rect()
        self.displays = [7]
        self.posts, self.captures, self.raised = [], [], []
        self.hit_pid, self.hit_error, self.secure = None, -1, False
        self.hit_window = None
        self.ax_available = True
        self.locked = False
        self.apps = {
            pid: NS(
                processIdentifier=lambda pid=pid: pid,
                launchDate=lambda pid=pid: NS(timeIntervalSince1970=lambda: self.launches[pid]),
                isTerminated=lambda: False,
                activateWithOptions_=lambda _, pid=pid: setattr(self, "front", pid) or True,
                activationPolicy=lambda: 0,
            )
            for pid in self.launches
        }
        self.d = worker.NativeDesktop.__new__(worker.NativeDesktop)
        self.d.settings = DesktopSettings(
            enabled=True,
            execution_mode="direct",
            window_navigation=True,
            active_window_screenshots=False,
            targets=[Target(name="editor", pid=10, window_ids=[1, 2]), Target(name="viewer", pid=20, window_ids=[3])],
        )
        self.d.targets = {t.name: t for t in self.d.settings.targets}
        self.d.application = lambda name: self.apps[self.d.targets[name].pid]
        self.d.target, self.d.display_id, self.d.generation = "editor", 7, 0
        self.d.last = self.d.monitor = self.d.accessibility = None
        self.d.q = NS(
            CGSessionCopyCurrentDictionary=lambda: {
                "kCGSSessionOnConsoleKey": True,
                "kCGSessionLoginDoneKey": True,
                "CGSSessionScreenIsLocked": self.locked,
            },
            kCGWindowListOptionOnScreenOnly=1,
            kCGWindowListExcludeDesktopElements=2,
            kCGNullWindowID=0,
            kCGCursorWindowLevelKey=19,
            CGWindowLevelForKey=lambda _: 2147483630,
            CGGetActiveDisplayList=lambda *_: (0, self.displays, len(self.displays)),
            CGDisplayBounds=lambda _: NS(
                origin=NS(x=self.screen.x, y=self.screen.y), size=NS(width=self.screen.width, height=self.screen.height)
            ),
            CGWindowListCopyWindowInfo=lambda *_: self.rows,
            kCGEventLeftMouseDown="down",
            kCGEventLeftMouseUp="up",
            kCGMouseButtonLeft=0,
            kCGMouseEventClickState="count",
            kCGHIDEventTap=0,
            CGEventCreateMouseEvent=lambda _, kind, point, button: dict(kind=kind, point=point, button=button),
            CGEventSetIntegerValueField=lambda e, k, v: e.update({k: v}),
            CGEventSetFlags=lambda e, v: e.update(flags=v),
            CGEventPost=lambda _, e: self.posts.append(e),
        )
        self.d.appkit = NS(
            NSWorkspace=NS(sharedWorkspace=lambda: NS(frontmostApplication=lambda: self.apps.get(self.front))),
            NSApplicationActivateIgnoringOtherApps=1,
            NSRunLoop=NS(currentRunLoop=lambda: NS(runUntilDate_=lambda _: None)),
            NSDate=NS(dateWithTimeIntervalSinceNow_=lambda value: value),
        )
        self.d.ax = NS(
            AXUIElementCreateApplication=lambda pid: ("app", pid),
            AXUIElementSetMessagingTimeout=lambda *_: 0,
            AXUIElementCopyAttributeValue=self.attribute,
            AXValueGetValue=lambda value, *_: (True, value),
            kAXValueCGPointType=1,
            kAXValueCGSizeType=2,
            AXUIElementPerformAction=self.raise_window,
            AXUIElementIsAttributeSettable=Mock(return_value=(0, False)),
            AXUIElementSetAttributeValue=Mock(return_value=-25206),
            AXUIElementCreateSystemWide=lambda: "system",
            AXUIElementCopyElementAtPosition=lambda *_: (self.hit_error, "hit"),
            AXUIElementGetPid=lambda element, _: (0, element[1] if isinstance(element, tuple) else self.hit_pid),
        )
        self.d.navigation = MacOSNavigation(self.d)
        self.d.navigation.capture_window = self.capture
        self.d.navigation.refresh()  # Host selected the three existing fixture windows.

    def attribute(self, element, name, _):
        if not self.ax_available:
            return -25205, None
        if element == "system":
            value = ("app", self.front) if name == "AXFocusedApplication" and self.front else None
        elif isinstance(element, tuple):
            pid = element[1]
            value = {
                "AXWindows": [r["kCGWindowNumber"] for r in self.rows if r["kCGWindowOwnerPID"] == pid],
                "AXFocusedWindow": self.focused[pid],
                "AXFocusedUIElement": "field",
            }.get(name)
        elif element == "field":
            value = "AXSecureTextField" if self.secure else "AXTextField"
        elif element == "hit":
            value = (self.hit_window or self.focused.get(self.hit_pid)) if name == "AXWindow" else None
        else:
            item = next((r for r in self.rows if r["kCGWindowNumber"] == element), None)
            if item is None:
                return -25202, None
            x, y, w, h = self.d.bounds(item)
            value = {
                "AXPosition": NS(x=x, y=y),
                "AXSize": NS(width=w, height=h),
                "AXRole": self.roles.get(element, "AXWindow"),
                "AXSubrole": "AXStandardWindow",
                "AXSheets": self.sheets.get(element, []),
            }.get(name)
        return (0, value) if value is not None else (-25205, None)

    def raise_window(self, element, action):
        item = next(r for r in self.rows if r["kCGWindowNumber"] == element)
        self.focused[item["kCGWindowOwnerPID"]] = element
        self.raised.append(element)
        return 0

    def capture(self, native_id):
        self.captures.append(native_id)
        item = next(r for r in self.rows if r["kCGWindowNumber"] == native_id)
        _, _, width, height = self.d.bounds(item)
        return Image.new("RGBA", (int(width * 2), int(height * 2)), "red" if native_id == 1 else "blue")

    def observe(self):
        return Observation(**self.d.observe())

    def handle(self, native_id):
        return next(
            h for h in self.d.navigation.catalog.windows if self.d.navigation.catalog.resolve(h).native_id == native_id
        )

    def act(self, action, observation):
        return self.d.act(action, {"generation": observation.generation, "context": observation.context})


def test_element_focus_dispatches_to_scoped_editor_without_app_activation():
    rig = NativeRig()
    observation = rig.observe()
    rig.d.settings = rig.d.settings.model_copy(update={"accessibility_enabled": True})
    rig.d.accessibility = Mock()
    rig.d.navigation.focus = Mock(side_effect=AssertionError("unexpected app focus"))
    rig.act(Action(kind="focus", element_id="observed-editor"), observation)
    rig.d.accessibility.focus.assert_called_once()
    assert rig.d.accessibility.focus.call_args.args == ("observed-editor", observation)
    assert rig.d.accessibility.focus.call_args.kwargs["app"] is rig.apps[10]
    rig.d.accessibility.clear.assert_called_once()
    assert rig.d.last is None and not rig.posts and not rig.raised


def test_element_focus_cannot_activate_a_window_from_an_unshared_foreground():
    rig = NativeRig()
    rig.front = 999
    observation = rig.observe()
    rig.d.settings = rig.d.settings.model_copy(update={"accessibility_enabled": True})
    rig.d.accessibility = Mock()
    with pytest.raises(RuntimeError, match="focus a permitted window"):
        rig.act(Action(kind="focus", element_id="observed-editor"), observation)
    rig.d.accessibility.focus.assert_not_called()
    assert not rig.posts and not rig.raised


@pytest.mark.parametrize("extra", [{"target": "editor"}, {"window_id": "shared"}, {"x": 1, "y": 2}])
def test_editor_focus_has_exactly_one_selector(extra):
    with pytest.raises(ValueError):
        decode_call({"operation": "focus", "element_id": "current", "observation_id": "token", **extra})


def test_editor_focus_can_only_be_first_in_a_batch():
    focus = {"operation": "focus", "element_id": "editor"}
    text = {"operation": "type", "text": "ordinary text"}
    operation, actions = decode_call({"operation": "batch", "observation_id": "token", "actions": [focus, text]})
    assert operation == "batch" and actions[0].element_id == "editor"
    with pytest.raises(ValueError, match="must be first"):
        decode_call({"operation": "batch", "observation_id": "token", "actions": [text, focus]})


@pytest.fixture
def rig():
    worker.STOPPING.clear()
    yield NativeRig()
    worker.STOPPING.clear()


def test_navigation_scope_requires_explicit_host_opt_in():
    assert DesktopSettings().window_navigation is False
    settings = DesktopSettings(
        window_navigation=True, targets=[Target(name="limited", pid=10, window_title="Only this")]
    )
    assert settings.targets[0].window_title == "Only this"


@pytest.mark.parametrize("payload", [{}, {"target": "editor", "window_id": "w"}])
def test_focus_requires_one_selector(payload):
    with pytest.raises(ValueError, match="exactly one"):
        Action(kind="focus", **payload)


def test_flat_and_rpc_focus_window_id():
    _, action = decode_call({"operation": "focus", "window_id": "opaque", "observation_id": "fresh"})
    assert action == Action.model_validate_json(action.model_dump_json())
    assert action.window_id == "opaque"


def test_discovery_returns_duplicate_titles_without_activating(rig):
    observation = rig.observe()
    context = DesktopObservationContext.model_validate_json(json.dumps(observation.context))
    assert len(context.windows) == 3
    assert len({w.window_id for w in context.windows}) == 3
    assert {w.title for w in context.windows} == {"Document"}
    assert {w.application_name for w in context.windows} == {"editor", "viewer"}
    assert context.view_window_id is None
    assert context.active_window_id == rig.handle(1)
    assert not rig.posts and not rig.raised


def test_active_crop_captures_one_window_and_maps_input_from_crop(rig):
    rig.d.settings = rig.d.settings.model_copy(update={"active_window_screenshots": True})
    observed = rig.observe()
    context = DesktopObservationContext.model_validate_json(json.dumps(observed.context))
    assert rig.captures == [1]
    assert len(context.windows) == 3
    assert context.view_window_id == context.active_window_id == rig.handle(1)
    assert observed.bounds == (10, 30, 150, 100)
    assert (observed.width, observed.height) == (150, 100)
    rig.act(Action(kind="click", x=20, y=20), observed)
    assert rig.posts[0]["point"] == (30, 50)


def test_active_crop_focus_switch_returns_new_geometry_and_rejects_old_token(rig):
    rig.d.settings = rig.d.settings.model_copy(update={"active_window_screenshots": True})
    first = rig.observe()
    rig.act(Action(kind="focus", window_id=rig.handle(3)), first)
    second = rig.observe()
    assert second.bounds == (20, 180, 150, 100)
    assert second.context["view_window_id"] == rig.handle(3)
    assert rig.captures == [1, 3]
    with pytest.raises(RuntimeError, match="stale"):
        rig.act(Action(kind="click", x=20, y=20), first)


def test_active_crop_metadata_has_same_geometry_without_capturing(rig):
    rig.d.settings = rig.d.settings.model_copy(update={"active_window_screenshots": True})
    rig.d.accessibility = NS(snapshot=Mock(return_value=([], "available")), clear=Mock())
    full = rig.observe()
    rig.captures.clear()
    metadata = rig.d.navigation.observe(image_mode="metadata")
    assert metadata["bounds"] == full.bounds
    assert (metadata["width"], metadata["height"]) == (full.width, full.height)
    assert metadata["context"]["view_window_id"] == full.context["view_window_id"]
    assert not rig.captures and not metadata["png_b64"]


def test_active_crop_without_shared_foreground_keeps_scoped_overview(rig):
    rig.d.settings = rig.d.settings.model_copy(update={"active_window_screenshots": True})
    rig.front = 999
    observed = rig.observe()
    assert observed.context["view_window_id"] is None
    assert observed.bounds == (0, 0, 400, 300)
    assert set(rig.captures) == {1, 2, 3}


def test_active_crop_masks_excluded_same_app_surface_and_never_captures_it(rig):
    rig.d.settings = rig.d.settings.model_copy(update={"active_window_screenshots": True})
    rig.rows.insert(0, row(99, bounds=(20, 40, 30, 20)))
    observed = rig.observe()
    bitmap = Image.open(io.BytesIO(base64.b64decode(observed.png_b64)))
    assert rig.captures == [1]
    assert bitmap.getpixel((15, 15)) == (0, 0, 0)


def test_active_crop_preserves_shared_sheet_above_focused_parent(rig):
    rig.d.settings = rig.d.settings.model_copy(update={"active_window_screenshots": True})
    rig.observe()
    rig.rows.insert(0, row(5, bounds=(40, 45, 90, 80), title="Save"))
    rig.roles[5] = "AXSheet"
    rig.sheets[1] = [5]
    observed = rig.observe()
    assert rig.handle(5) in {w["window_id"] for w in observed.context["windows"]}
    assert observed.context["view_window_id"] is None
    assert 5 in rig.captures


@pytest.mark.parametrize("mode", ["full", "pixels", "metadata"])
def test_final_observation_settling_keeps_latest_frame_and_skips_intermediates(monkeypatch, mode):
    clock = [0.0]
    monkeypatch.setattr("openjiuwen.harness.tools.desktop.macos_navigation.time.monotonic", lambda: clock[0])
    nav = MacOSNavigation.__new__(MacOSNavigation)
    nav.desktop = NS(
        settings=DesktopSettings(observation_settle_seconds=0.25),
        _inputs=lambda: NS(pause=lambda delay: clock.__setitem__(0, clock[0] + delay)),
    )
    frames = []

    def capture(**kwargs):
        frame = {
            "image_digest": "changing" if not frames else "stable",
            "bounds": (0, 0, 20, 20),
            "context": {"active_window_id": "shared", "windows": []},
            "performance": {"window_captures": 1},
            "generation": len(frames) + 1,
        }
        frames.append(frame)
        nav.desktop.last = Observation("", 20, 20, frame["bounds"], "editor", 1, frame["generation"])
        return frame

    nav._observe_once = capture
    result = nav.observe(image_mode=mode)
    assert result is frames[-1]
    assert len(frames) == (3 if mode == "full" else 1)
    if mode == "full":
        assert result["performance"]["settled"]
        assert result["performance"]["window_captures"] == 3


def test_animation_settling_is_bounded_and_never_executes_input(monkeypatch):
    clock, calls = [0.0], []
    monkeypatch.setattr("openjiuwen.harness.tools.desktop.macos_navigation.time.monotonic", lambda: clock[0])
    nav = MacOSNavigation.__new__(MacOSNavigation)
    nav.desktop = NS(
        settings=DesktopSettings(observation_settle_seconds=0.1),
        _inputs=lambda: NS(pause=lambda delay: clock.__setitem__(0, clock[0] + delay)),
    )

    def capture(**kwargs):
        calls.append(1)
        nav.desktop.last = Observation("", 20, 20, (0, 0, 20, 20), "editor", 1, len(calls))
        return {
            "image_digest": str(len(calls)),
            "bounds": (0, 0, 20, 20),
            "context": {"active_window_id": "shared", "windows": []},
            "performance": {"window_captures": 1},
        }

    nav._observe_once = capture
    result = nav.observe()
    assert len(calls) == 3 and clock[0] == 0.1
    assert not result["performance"]["settled"]


def test_focus_requires_specific_window_when_target_ambiguous(rig):
    observed = rig.observe()
    with pytest.raises(ValueError, match="multiple"):
        rig.act(Action(kind="focus", target="editor"), observed)
    assert not rig.raised
    assert rig.d.last is None


def test_focus_selects_exact_window_even_with_duplicate_titles(rig):
    observed = rig.observe()
    rig.act(Action(kind="focus", window_id=rig.handle(2)), observed)
    assert rig.raised == [2]
    assert rig.observe().context["active_window_id"] == rig.handle(2)


def test_focus_target_switches_to_other_approved_app(rig):
    rig.act(Action(kind="focus", target="viewer"), rig.observe())
    assert rig.raised == [3]
    assert rig.observe().target == "viewer"


def test_unknown_handle_rejected_without_activation(rig):
    with pytest.raises(ValueError, match="latest observation"):
        rig.act(Action(kind="focus", window_id="invented"), rig.observe())
    assert not rig.raised


def test_title_change_keeps_handle_and_does_not_choose_window(rig):
    observed = rig.observe()
    handle = rig.handle(2)
    rig.rows[1]["kCGWindowName"] = "Unsaved copy"
    rig.act(Action(kind="focus", window_id=handle), observed)
    assert rig.observe().context["active_window_id"] == handle


@pytest.mark.parametrize("change", ["geometry", "pid", "closed", "restart", "display", "foreground"])
def test_native_changes_reject_input_and_consume_observation(rig, change):
    observed = rig.observe()
    if change == "geometry":
        rig.rows[0]["kCGWindowBounds"]["X"] += 1
    elif change == "pid":
        rig.rows[0]["kCGWindowOwnerPID"] = 999
    elif change == "closed":
        rig.rows.pop(0)
    elif change == "restart":
        rig.launches[10] += 1
    elif change == "display":
        rig.displays.clear()
    else:
        rig.front = 20
    with pytest.raises(RuntimeError):
        rig.act(Action(kind="click", x=30, y=50), observed)
    assert not rig.posts and rig.d.last is None


def test_retired_handle_never_rebinds_to_reappearing_native_id(rig):
    rig.observe()
    handle = rig.handle(2)
    saved = rig.rows.pop(1)
    rig.observe()
    rig.rows.insert(1, saved)
    observed = rig.observe()
    assert all(w["window_id"] != handle for w in observed.context["windows"])
    assert (10, 2) not in rig.d.navigation.catalog.shared
    with pytest.raises(ValueError, match="latest observation"):
        rig.act(Action(kind="focus", window_id=handle), observed)


def test_unlinked_menu_is_private_but_attached_sheet_is_shared(rig):
    first = rig.observe()
    rig.rows.insert(0, row(4, bounds=(40, 45, 90, 80), title="", layer=101))
    rig.roles[4] = "AXMenu"
    menu = rig.observe()
    assert 4 not in rig.captures
    assert all(w["kind"] != "menu" for w in menu.context["windows"])
    rig.rows.pop(0)
    rig.rows.insert(0, row(5, bounds=(40, 45, 90, 80), title="Save"))
    rig.roles[5] = "AXSheet"
    rig.sheets[1] = [5]
    rig.focused[10] = 5
    dialog = rig.observe()
    assert dialog.context["windows"][0]["kind"] == "dialog"
    assert dialog.context["active_window_id"] == rig.handle(5)
    assert len({o.context["observation_id"] for o in (first, menu, dialog)}) == 3
    rig.act(Action(kind="click", x=50, y=60), dialog)
    assert [e["kind"] for e in rig.posts] == ["down", "up"]


def test_missing_native_window_focus_blocks_input_even_without_ax_tree(rig):
    rig.ax_available = False
    observed = rig.observe()
    assert observed.elements == [] and observed.accessibility_status == "disabled"
    assert observed.context["active_window_id"] is None
    with pytest.raises(RuntimeError, match="not shared"):
        rig.act(Action(kind="click", x=30, y=50), observed)
    assert not rig.posts


@pytest.mark.parametrize("pid", [20, 999])
def test_ax_hit_cannot_override_different_owner(rig, pid):
    rig.hit_error, rig.hit_pid = 0, pid
    with pytest.raises(RuntimeError, match="occluded"):
        rig.act(Action(kind="click", x=30, y=50), rig.observe())
    assert not rig.posts


def test_foreign_windows_are_omitted_and_never_captured_or_clickable(rig):
    rig.rows.insert(0, row(99, 999, (20, 40, 40, 40), "PRIVATE"))
    observed = rig.observe()
    bitmap = Image.open(io.BytesIO(base64.b64decode(observed.png_b64)))
    # The composition shows the approved capture underneath; this is not input permission.
    assert bitmap.getpixel((30, 50)) == (255, 0, 0)
    assert bitmap.getpixel((10, 30)) == (255, 0, 0)
    assert bitmap.getpixel((390, 290)) == (0, 0, 0)
    assert 99 not in rig.captures
    assert "PRIVATE" not in str(observed.context)
    with pytest.raises(RuntimeError, match="occluded"):
        rig.act(Action(kind="click", x=30, y=50), observed)
    assert not rig.posts


@pytest.mark.parametrize("layer", [0, 20, 24, 1499])
def test_full_display_foreign_overlay_cannot_erase_approved_captures(rig, layer):
    rig.rows.insert(0, row(99, 999, (0, 0, 400, 300), "PRIVATE", layer=layer))
    observed = rig.observe()
    bitmap = Image.open(io.BytesIO(base64.b64decode(observed.png_b64)))
    assert bitmap.getpixel((30, 50)) == (255, 0, 0)
    assert bitmap.getpixel((30, 190)) == (0, 0, 255)
    assert bitmap.getpixel((390, 290)) == (0, 0, 0)
    assert rig.captures == [1, 2, 3]
    assert "PRIVATE" not in str(observed.context)


@pytest.mark.parametrize("hit_pid", [10, 20, 999, None])
def test_overlay_click_requires_ax_hit_on_foreground_scoped_candidate(rig, hit_pid):
    rig.rows.insert(0, row(99, 999, (0, 0, 400, 300), layer=20))
    rig.hit_error, rig.hit_pid = 0, hit_pid
    observed = rig.observe()
    if hit_pid == 10:
        rig.act(Action(kind="click", x=30, y=50), observed)
        assert [e["kind"] for e in rig.posts] == ["down", "up"]
    else:
        with pytest.raises(RuntimeError, match="occluded"):
            rig.act(Action(kind="click", x=30, y=50), observed)
        assert not rig.posts


@pytest.mark.parametrize("hit", [(-25205, None), (0, None)])
def test_overlay_without_ax_hit_still_blocks_input(rig, hit):
    rig.rows.insert(0, row(99, 999, (0, 0, 400, 300), layer=20))
    rig.d.ax.AXUIElementCopyElementAtPosition = lambda *_: hit
    with pytest.raises(RuntimeError, match="occluded"):
        rig.act(Action(kind="click", x=30, y=50), rig.observe())
    assert not rig.posts


def test_overlay_ax_pid_error_cannot_fall_back_to_candidate(rig):
    rig.rows.insert(0, row(99, 999, (0, 0, 400, 300), layer=20))
    rig.hit_error = 0
    rig.d.ax.AXUIElementGetPid = lambda element, _: (0, element[1]) if isinstance(element, tuple) else (-25205, 10)
    with pytest.raises(RuntimeError, match="occluded"):
        rig.act(Action(kind="click", x=30, y=50), rig.observe())
    assert not rig.posts


def test_matching_ax_pid_cannot_authorize_excluded_same_app_surface(rig):
    rig.rows.insert(0, row(4, 10, (-10, 40, 60, 40)))
    rig.hit_error, rig.hit_pid = 0, 10
    observed = rig.observe()
    bitmap = Image.open(io.BytesIO(base64.b64decode(observed.png_b64)))
    assert bitmap.getpixel((30, 50)) == (0, 0, 0)
    assert 4 not in rig.captures
    with pytest.raises(RuntimeError, match="occluded"):
        rig.act(Action(kind="click", x=30, y=50), observed)
    assert not rig.posts


def test_matching_ax_pid_cannot_authorize_outside_approved_capture(rig):
    rig.rows.insert(0, row(99, 999, (0, 0, 400, 300), layer=20))
    rig.hit_error, rig.hit_pid = 0, 10
    with pytest.raises(RuntimeError, match="occluded"):
        rig.act(Action(kind="click", x=390, y=290), rig.observe())
    assert not rig.posts


def test_ax_hit_does_not_implicitly_switch_to_other_approved_app(rig):
    rig.rows.insert(0, row(99, 999, (0, 0, 400, 300), layer=20))
    rig.hit_error, rig.hit_pid = 0, 20
    with pytest.raises(RuntimeError, match="occluded"):
        rig.act(Action(kind="click", x=30, y=190), rig.observe())
    assert not rig.posts


def test_capture_race_discards_pixels(rig):
    original = rig.d.navigation.capture_window

    def moved(native_id):
        result = original(native_id)
        rig.rows[0]["kCGWindowBounds"]["X"] += 1
        return result

    rig.d.navigation.capture_window = moved
    with pytest.raises(RuntimeError, match="during capture"):
        rig.observe()
    assert rig.d.last is None


def test_other_display_foreign_changes_do_not_invalidate_input(rig):
    foreign = row(99, 999, (410, 30, 100, 100), "PRIVATE")
    rig.rows.insert(0, foreign)
    observed = rig.observe()
    foreign["kCGWindowBounds"]["X"] += 10
    rig.act(Action(kind="click", x=30, y=50), observed)
    assert [e["kind"] for e in rig.posts] == ["down", "up"]
    assert 99 not in rig.captures


def test_cursor_surface_disappearance_does_not_block_clicks(rig):
    cursor = row(99, 999, (20, 40, 40, 40), layer=rig.d.q.CGWindowLevelForKey(19))
    rig.rows.insert(0, cursor)
    observed = rig.observe()
    bitmap = Image.open(io.BytesIO(base64.b64decode(observed.png_b64)))
    assert bitmap.getpixel((30, 50)) == (255, 0, 0)
    rig.rows.remove(cursor)
    rig.act(Action(kind="click", x=30, y=50, click_count=2), observed)
    assert [e["kind"] for e in rig.posts] == ["down", "up", "down", "up"]
    assert 99 not in rig.captures


def test_neighboring_high_overlay_is_still_an_occluder(rig):
    rig.rows.insert(0, row(99, 999, (20, 40, 40, 40), layer=rig.d.q.CGWindowLevelForKey(19) - 1))
    observed = rig.observe()
    with pytest.raises(RuntimeError, match="occluded"):
        rig.act(Action(kind="click", x=30, y=50), observed)
    assert not rig.posts


def test_foreign_window_entering_approved_area_invalidates_input_and_logs_geometry_only(rig):
    foreign = row(99, 999, (410, 30, 100, 100), "PRIVATE")
    rig.rows.insert(0, foreign)
    observed = rig.observe()
    foreign["kCGWindowBounds"]["X"] = 349  # One unit inside the second approved window.
    rig.d.monitor = Mock()
    with pytest.raises(RuntimeError, match="observe again"):
        rig.act(Action(kind="click", x=30, y=50), observed)
    assert not rig.posts
    log = rig.d.monitor.record.call_args
    assert log.args == ("stale_observation",)
    assert "PRIVATE" not in str(log)
    assert log.kwargs["actual"]["surfaces"][0][1] == 99


@pytest.mark.parametrize("change", ["added", "removed", "moved", "replaced", "restacked", "layer"])
@pytest.mark.parametrize("offset", [(0, 0), (150, 80)])
def test_unrelated_same_display_changes_preserve_scoped_input(rig, change, offset):
    dx, dy = offset
    rig.screen = rect(width=800, height=500)
    for item in rig.rows:
        item["kCGWindowBounds"]["X"] += dx
        item["kCGWindowBounds"]["Y"] += dy
    foreign = row(99, 999, (700, 400, 25, 25), "PRIVATE")
    if change != "added":
        rig.rows.insert(0, foreign)
    observed = rig.observe()
    if change == "added":
        rig.rows.insert(0, foreign)
    elif change == "removed":
        rig.rows.remove(foreign)
    elif change == "moved":
        foreign["kCGWindowBounds"]["X"] += 10
    elif change == "replaced":
        foreign["kCGWindowNumber"] = 100
    elif change == "restacked":
        rig.rows.remove(foreign)
        rig.rows.append(foreign)
    else:
        foreign["kCGWindowLayer"] = 25
    rig.act(Action(kind="click", x=30 + dx, y=50 + dy), observed)
    assert [e["kind"] for e in rig.posts] == ["down", "up"]
    assert 99 not in rig.captures and 100 not in rig.captures
    # The full catalog remains available to the native point guard/diagnostics.
    assert any(s.pid == 999 for s in rig.d.navigation.catalog.surfaces) is (change != "removed")


@pytest.mark.parametrize("change", ["removed", "moved", "restacked", "layer", "replaced"])
def test_overlapping_foreign_change_still_invalidates_input(rig, change):
    foreign = row(99, 999, (20, 40, 25, 25))
    rig.rows.append(foreign)  # Even overlapping background windows remain guarded.
    observed = rig.observe()
    if change == "removed":
        rig.rows.remove(foreign)
    elif change == "moved":
        foreign["kCGWindowBounds"]["X"] = 370
    elif change == "restacked":
        rig.rows.remove(foreign)
        rig.rows.insert(0, foreign)
    elif change == "layer":
        foreign["kCGWindowLayer"] = 25
    else:
        foreign["kCGWindowNumber"] = 100
    with pytest.raises(RuntimeError, match="observe again"):
        rig.act(Action(kind="click", x=30, y=50), observed)
    assert not rig.posts


def test_unrelated_animation_during_capture_does_not_discard_approved_pixels(rig):
    foreign = row(99, 999, (370, 200, 20, 20))
    rig.rows.insert(0, foreign)
    capture = rig.d.navigation.capture_window

    def animate(native_id):
        foreign["kCGWindowBounds"]["Y"] += 1
        return capture(native_id)

    rig.d.navigation.capture_window = animate
    assert rig.observe().png_b64
    assert rig.captures == [1, 2, 3]


def test_scoped_freshness_does_not_ignore_external_input(rig):
    observed = rig.observe()
    rig.rows.insert(0, row(99, 999, (370, 200, 20, 20)))
    rig.d.monitor = Mock()
    rig.d.monitor.check.side_effect = RuntimeError("outside input")
    with pytest.raises(RuntimeError, match="outside input"):
        rig.act(Action(kind="click", x=30, y=50), observed)
    assert not rig.posts


def test_current_ax_keyboard_recipient_overrides_stale_appkit_cache(rig):
    rig.d.appkit.NSWorkspace = NS(sharedWorkspace=lambda: NS(frontmostApplication=lambda: rig.apps[10]))
    observed = rig.observe()
    rig.front = 20
    with pytest.raises(RuntimeError, match="observe again"):
        rig.act(Action(kind="click", x=30, y=50), observed)
    assert not rig.posts
    updated = rig.observe()
    assert updated.context["active_window_id"] == rig.handle(3)
    assert rig.d.navigation.focus_source == "AXFocusedApplication"


def test_foreign_keyboard_recipient_cannot_hide_behind_unrelated_geometry(rig):
    rig.d.appkit.NSWorkspace = NS(sharedWorkspace=lambda: NS(frontmostApplication=lambda: rig.apps[10]))
    observed = rig.observe()
    rig.front = 999
    rig.rows.insert(0, row(99, 999, (370, 200, 20, 20)))
    with pytest.raises(RuntimeError, match="observe again"):
        rig.act(Action(kind="click", x=30, y=50), observed)
    assert not rig.posts


@pytest.mark.parametrize("pid_result", [(-25202, 10), (0, 0), (0, None), (0, "10"), (0, True)])
def test_unverifiable_ax_recipient_never_uses_cached_foreground(rig, pid_result):
    observed = rig.observe()
    rig.d.ax.AXUIElementGetPid = lambda *_: pid_result
    with pytest.raises(RuntimeError, match="current keyboard recipient"):
        rig.act(Action(kind="click", x=30, y=50), observed)
    assert not rig.posts


def test_missing_ax_recipient_services_appkit_before_fallback_read(rig):
    attribute = rig.attribute
    rig.d.ax.AXUIElementCopyAttributeValue = lambda element, name, out: (
        (-25205, None) if name == "AXFocusedApplication" else attribute(element, name, out)
    )
    cached = [10]
    rig.d.appkit.NSWorkspace = NS(sharedWorkspace=lambda: NS(frontmostApplication=lambda: rig.apps[cached[0]]))
    rig.d.appkit.NSRunLoop = NS(currentRunLoop=lambda: NS(runUntilDate_=lambda _: cached.__setitem__(0, rig.front)))
    observed = rig.observe()
    assert rig.d.navigation.focus_source == "NSWorkspace"
    rig.front = 20
    with pytest.raises(RuntimeError, match="observe again"):
        rig.act(Action(kind="click", x=30, y=50), observed)
    assert cached[0] == 20 and not rig.posts


def test_recorded_live_geometry_removes_only_irrelevant_stale_stops():
    records = json.loads((Path(__file__).with_name("fixtures") / "scene-freshness-20261019.json").read_text())
    outcomes = []
    for record in records:
        signatures = []
        for state in (record["expected"], record["actual"]):
            catalog = WindowCatalog("1", {pid: str(pid) for pid in record["approved_pids"]})
            catalog.shared = {(pid, wid) for pid, wid, *_ in state["surfaces"] if pid in record["approved_pids"]}
            catalog.update(
                [
                    Surface(wid, pid, DesktopRect(**bounds), layer=layer)
                    for pid, wid, bounds, layer in state["surfaces"]
                ],
                DesktopRect(**state["display"]),
            )
            signatures.append((catalog.signature(), state["front_pid"], state["focused_rect"]))
        stable = signatures[0] == signatures[1]
        assert stable is record["irrelevant_change"]
        assert record["expected"] != record["actual"]  # The former whole-display check rejected all four.
        outcomes.append(stable)
    assert outcomes == [True, False, True, True]


def typing_rig(rig, transition, *, ax_status="available", popup_pid=10, popup_role="AXSheet"):
    """Replay a native transition after the second complete Unicode key pair."""
    popup = row(99, popup_pid, (8, 60, 15, 18), title="")
    rig.rows.insert(0, popup)
    rig.roles[99] = popup_role
    if popup_role == "AXSheet" and popup_pid == 10:
        rig.sheets[1] = [99]
    rig.d.settings = rig.d.settings.model_copy(update={"accessibility_enabled": True})
    rig.d.accessibility = NS(
        snapshot=lambda *a, **k: ([], ax_status),
        validate_typing=Mock(),
        clear=Mock(),
    )
    rig.d.q.CGEventCreateKeyboardEvent = lambda _, code, down: {"kind": "keydown" if down else "keyup"}
    rig.d.q.CGEventKeyboardSetUnicodeString = lambda event, length, text: event.update(text=text)

    def post(_, event):
        rig.posts.append(event)
        if event["kind"] == "keyup" and len(rig.posts) == 4:
            transition(popup)

    rig.d.q.CGEventPost = post
    return rig.observe(), popup


@pytest.mark.parametrize("optimized", [False, True])
def test_typing_survives_only_dismissed_auxiliary_popup_without_replaying_keys(rig, optimized):
    rig.d.settings = rig.d.settings.model_copy(update={"optimized_batches": optimized})
    observation, _ = typing_rig(rig, rig.rows.remove)
    rig.act(Action(kind="type", text="Demo checklist"), observation)
    assert "".join(e["text"] for e in rig.posts if e["kind"] == "keydown") == "Demo checklist"
    assert len(rig.posts) == 2 * len("Demo checklist")
    assert rig.d.accessibility.validate_typing.call_count > len("Demo checklist")
    assert not rig.d.navigation.typing and rig.d.navigation.typed_keys == 0
    assert rig.d.last is None and rig.d.input_events._release_event is None


def test_recorded_popup_transition_reproduces_original_two_character_failure(rig, monkeypatch):
    observation, _ = typing_rig(rig, rig.rows.remove)
    monkeypatch.setattr(rig.d.navigation, "_typing_popup_dismissed", lambda *_: False)
    with pytest.raises(RuntimeError, match="windows or focus changed"):
        rig.act(Action(kind="type", text="Demo checklist"), observation)
    assert "".join(e["text"] for e in rig.posts if e["kind"] == "keydown") == "De"


@pytest.mark.parametrize("optimized", [False, True])
def test_unrelated_window_change_during_typing_preserves_each_key_once(rig, optimized):
    from dataclasses import asdict

    from openjiuwen.harness.tools.desktop.native_batch import run_batch

    foreign = row(100, 999, (370, 200, 20, 20))
    rig.rows.insert(0, foreign)
    observation, _ = typing_rig(rig, lambda _: rig.rows.remove(foreign))
    rig.d.q.CGEventCreateKeyboardEvent = lambda _, code, down: {
        "kind": "keydown" if down else "keyup",
        "code": code,
    }
    actions = [Action(kind="type", text="Résumé\nPlan tomorrow."), Action(kind="type", text=" Done.")]
    if optimized:
        result = run_batch(rig.d, actions, asdict(observation), max_steps=2, max_observations=3)
        assert result["completed"] == 2 and not result["reason"]
    else:
        for action in actions:
            rig.act(action, observation)
            observation = rig.observe()
    expected = "".join(action.text for action in actions)
    assert (
        "".join(
            e.get("text", "\n" if e["code"] == 36 else "<unexpected key>") for e in rig.posts if e["kind"] == "keydown"
        )
        == expected
    )
    assert len(rig.posts) == 2 * len(expected)
    assert 100 not in rig.captures and not rig.d.navigation.typing


def test_popup_dismissal_finishes_typing_but_stops_before_the_next_batch_action(rig):
    from dataclasses import asdict

    from openjiuwen.harness.tools.desktop.native_batch import run_batch

    observation, _ = typing_rig(rig, rig.rows.remove)
    result = run_batch(
        rig.d,
        [Action(kind="type", text="Demo checklist"), Action(kind="type", text="must not run")],
        asdict(observation),
        max_steps=2,
        max_observations=3,
    )
    assert result["status"] == "action_completed" and result["completed"] == 1
    assert "window set changed" in result["reason"]
    assert result["observation"]["png_b64"]
    assert "".join(e["text"] for e in rig.posts if e["kind"] == "keydown") == "Demo checklist"


@pytest.mark.parametrize(
    "change",
    [
        "foreign",
        "other_owned_app",
        "normal_window",
        "new_popup",
        "moved_window",
        "reordered",
        "focused_window",
        "different_focus",
        "other_app_focus",
        "display",
        "secure",
        "editable_changed",
        "ax_unavailable",
        "ax_truncated",
        "race_during_ax_check",
    ],
)
def test_typing_popup_fix_still_stops_on_unverified_recipient_or_scene_change(rig, change):
    def transition(popup):
        rig.rows.remove(popup)
        if change == "new_popup":
            rig.rows.insert(0, row(100, 10, (8, 60, 15, 18), title=""))
        elif change == "moved_window":
            rig.rows[1]["kCGWindowBounds"]["X"] += 1
        elif change == "reordered":
            rig.rows.reverse()
        elif change == "focused_window":
            rig.rows = [r for r in rig.rows if r["kCGWindowNumber"] != 1]
        elif change == "different_focus":
            rig.focused[10] = 2
        elif change == "other_app_focus":
            rig.front = 20
        elif change == "display":
            rig.screen = rect(width=401)
        elif change == "secure":
            rig.secure = True
        elif change == "editable_changed":
            rig.d.accessibility.validate_typing.side_effect = RuntimeError("editable focus changed")
        elif change == "race_during_ax_check":
            rig.d.accessibility.validate_typing.side_effect = lambda _: setattr(rig, "front", 20)

    observed, _ = typing_rig(
        rig,
        transition,
        ax_status={"ax_unavailable": "unavailable", "ax_truncated": "truncated"}.get(change, "available"),
        popup_pid={"foreign": 999, "other_owned_app": 20}.get(change, 10),
        popup_role="AXWindow" if change == "normal_window" else "AXSheet",
    )
    with pytest.raises((RuntimeError, PermissionError)):
        rig.act(Action(kind="type", text="Demo checklist"), observed)
    assert "".join(e["text"] for e in rig.posts if e["kind"] == "keydown") == "De"
    assert rig.d.input_events._release_event is None
    assert not rig.d.navigation.typing and rig.d.last is None


def test_popup_disappearance_before_first_key_or_before_click_still_requires_observe(rig):
    observation, popup = typing_rig(rig, rig.rows.remove)
    rig.rows.remove(popup)
    with pytest.raises(RuntimeError, match="windows or focus changed"):
        rig.act(Action(kind="type", text="no stale input"), observation)
    assert not rig.posts
    observation, popup = typing_rig(rig, rig.rows.remove)
    rig.rows.remove(popup)
    with pytest.raises(RuntimeError, match="windows or focus changed"):
        rig.act(Action(kind="click", x=30, y=50), observation)
    assert not rig.posts


def test_other_display_owned_changes_still_invalidate_input(rig):
    outside = row(4, 10, (410, 30, 100, 100))
    rig.rows.append(outside)
    rig.d.navigation.sharing.roots["editor"].add((10, 4))
    observed = rig.observe()
    outside["kCGWindowBounds"]["X"] += 10
    with pytest.raises(RuntimeError, match="observe again"):
        rig.act(Action(kind="click", x=30, y=50), observed)
    assert not rig.posts


def test_transient_animation_retries_capture_only(rig):
    original = rig.d.navigation.capture_window

    def animated(native_id):
        result = original(native_id)
        if len(rig.captures) == 1:
            rig.rows[0]["kCGWindowBounds"]["X"] += 1
        return result

    rig.d.navigation.capture_window = animated
    observed = rig.observe()
    assert observed.context["windows"][0]["desktop_rect"]["x"] == 11
    assert not rig.posts and len(rig.captures) == 4


def test_unknown_focus_does_not_prevent_discovery_or_explicit_selection(rig):
    rig.front = 999
    observed = rig.observe()
    assert observed.target == "" and observed.context["active_window_id"] is None
    rig.act(Action(kind="focus", window_id=rig.handle(1)), observed)
    assert rig.raised == [1]


def test_secure_input_still_requires_takeover(rig):
    rig.secure = True
    with pytest.raises(PermissionError, match="secure"):
        rig.act(Action(kind="type", text="secret"), rig.observe())
    assert not rig.posts


def test_scaled_display_with_negative_origin_and_outside_window(rig):
    rig.screen = rect(-800, -100, 800, 600)
    rig.d.settings = rig.d.settings.model_copy(update={"screenshot_max_side": 400})
    rig.rows = [row(1, bounds=(-700, 0, 300, 200)), row(2, bounds=(-20, 0, 100, 100))]
    observed = rig.observe()
    assert (observed.width, observed.height) == (400, 300)
    assert len(observed.context["windows"]) == 1
    assert rig.captures == [1]
    rig.act(Action(kind="click", x=100, y=100), observed)
    assert rig.posts[0]["point"] == (-600, 100)


def test_transparency_never_reveals_foreign_pixels():
    catalog = WindowCatalog("7", {10: "app"})
    catalog.shared = {(10, 1)}
    catalog.update([Surface(1, 10, rect(0, 0, 10, 10)), Surface(2, 999, rect())], rect())
    handle = next(iter(catalog.windows))
    bitmap = catalog.compose(rect(), {handle: Image.new("RGBA", (10, 10), (255, 0, 0, 0))}, 400)
    assert bitmap.getpixel((5, 5)) == (0, 0, 0)


def test_optional_accessibility_uses_window_root_and_display_coordinates(rig):
    rig.d.accessibility = NS(snapshot=Mock(return_value=([{"id": "control"}], "available")), clear=Mock())
    observed = rig.observe()
    args, kwargs = rig.d.accessibility.snapshot.call_args
    assert args[1].bounds == (0, 0, 400, 300)
    assert kwargs["window_bounds"] == (10, 30, 150, 100)
    assert observed.elements == [{"id": "control"}]


@pytest.mark.parametrize("ax_available", [True, False])
def test_off_display_focus_cannot_receive_input(rig, ax_available):
    rig.rows[1]["kCGWindowBounds"]["X"] = 1000
    rig.focused[10] = 2
    rig.ax_available = ax_available
    observed = rig.observe()
    with pytest.raises(RuntimeError, match="outside the selected display"):
        rig.act(Action(kind="type", text="must not type"), observed)
    assert not rig.posts


def test_observe_failure_invalidates_previous_native_token(rig):
    old = rig.observe()
    rig.d.navigation.capture_window = Mock(side_effect=RuntimeError("capture failed"))
    with pytest.raises(RuntimeError, match="capture failed"):
        rig.observe()
    assert rig.d.last is None
    with pytest.raises(RuntimeError, match="stale"):
        rig.act(Action(kind="click", x=30, y=50), old)


def test_native_token_mismatch_cannot_execute(rig):
    observed = rig.observe()
    with pytest.raises(RuntimeError, match="stale"):
        rig.d.act(
            Action(kind="click", x=30, y=50),
            {"generation": observed.generation, "context": {"observation_id": "other"}},
        )
    assert not rig.posts


def test_missing_cocoa_launch_date_uses_fresh_process_identity(monkeypatch):
    import psutil

    app = NS(launchDate=lambda: None, isTerminated=lambda: False, processIdentifier=lambda: 42)
    creates = Mock(side_effect=[NS(create_time=lambda: 1.25), NS(create_time=lambda: 2.5)])
    monkeypatch.setattr(psutil, "Process", creates)
    assert MacOSNavigation.identity(app) == (42, 1.25)
    assert MacOSNavigation.identity(app) == (42, 2.5)
    assert creates.call_count == 2


def test_focus_geometry_ambiguity_cannot_raise(rig):
    rig.rows[1]["kCGWindowBounds"] = dict(rig.rows[0]["kCGWindowBounds"])
    observed = rig.observe()
    with pytest.raises(RuntimeError, match="unambiguously"):
        rig.act(Action(kind="focus", window_id=rig.handle(1)), observed)
    assert not rig.raised


def test_stop_prevents_navigation_and_capture(rig):
    observed = rig.observe()
    rig.captures.clear()
    worker.STOPPING.set()
    with pytest.raises(RuntimeError, match="stopping"):
        rig.act(Action(kind="focus", window_id=rig.handle(1)), observed)
    with pytest.raises(RuntimeError, match="stopping"):
        rig.observe()
    assert not rig.raised and not rig.captures


def test_capture_command_never_requests_a_full_display(rig, monkeypatch):
    rig.d.settings = rig.d.settings.model_copy(update={"capture_backend": "screencapture"})
    rig.d.navigation.refresh(describe=True)

    def capture(command, **kwargs):
        assert command[:-1] == ["/usr/sbin/screencapture", "-x", "-o", "-a", "-l", "1"]
        Image.new("RGBA", (300, 200), "red").save(command[-1])

    monkeypatch.setattr("openjiuwen.harness.tools.desktop.macos_navigation.subprocess.run", capture)
    result = MacOSNavigation.capture_window(rig.d.navigation, 1)
    assert result.size == (300, 200)


def test_navigation_bootstrap_is_used_by_real_worker_constructor(rig, monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "AppKit", rig.d.appkit)
    monkeypatch.setitem(sys.modules, "ApplicationServices", rig.d.ax)
    monkeypatch.setitem(sys.modules, "CoreFoundation", NS(CFEqual=lambda a, b: a == b))
    rig.d.appkit.NSRunningApplication = NS(runningApplicationWithProcessIdentifier_=rig.apps.get)
    monkeypatch.setitem(sys.modules, "Quartz", rig.d.q)
    monkeypatch.setattr(worker, "capability", lambda _: {"available": True, "display_ids": [7]})
    native = worker.NativeDesktop(rig.d.settings)
    assert isinstance(native.navigation, MacOSNavigation)
    assert not rig.posts and not rig.raised and not rig.captures


@pytest.mark.asyncio
async def test_direct_tool_roundtrip_preserves_navigation_tokens_and_windows(rig):
    from openjiuwen.core.session.agent import Session
    from openjiuwen.harness.tools.desktop.direct import DirectDesktopSession
    from openjiuwen.harness.tools.desktop.direct_tool import DirectComputerUseTool

    lease = NS(fd=99, acquire=Mock(), release=Mock())

    class Driver:
        async def start(self):
            pass

        async def observe(self):
            # Match the subprocess JSON roundtrip, including tuple-to-list conversion.
            return Observation(**json.loads(json.dumps(rig.d.observe())))

        async def act(self, action, observation):
            rig.act(action, observation)

        async def close(self):
            pass

    state = DirectDesktopSession(rig.d.settings, driver_factory=lambda _: Driver(), lease_factory=lambda: lease)
    tool = DirectComputerUseTool(rig.d.settings, session_factory=lambda: state)
    session = Session("navigation-owner")
    try:
        first = await tool.invoke({"operation": "observe"}, session=session)
        assert first.success
        token = first.data["observation_id"]
        assert token == first.data["observation"]["context"]["observation_id"]
        rejected = await tool.invoke(
            {"operation": "focus", "window_id": "invented", "observation_id": token}, session=session
        )
        assert not rejected.success and state.token == token and not state.closed
        changed = await tool.invoke(
            {"operation": "focus", "window_id": rig.handle(2), "observation_id": token}, session=session
        )
        assert changed.success and changed.data["observation_id"] != token
        assert changed.data["model_calls"] == 0
        assert rig.raised == [2]
        await tool.invoke({"operation": "done"}, session=session)
        lease.release.assert_called_once()
    finally:
        await tool.close()


def test_unsupported_raise_accepts_only_verified_exact_window_focus(rig):
    rig.d.ax.AXUIElementPerformAction = lambda *_: -25206
    rig.act(Action(kind="focus", target="viewer"), rig.observe())
    assert rig.observe().target == "viewer"
    assert not rig.raised and not rig.posts


def test_unsupported_raise_cannot_accept_wrong_window_focus(rig):
    rig.d.ax.AXUIElementPerformAction = lambda *_: -25206
    rig.d.appkit.NSRunLoop = NS(currentRunLoop=lambda: NS(runUntilDate_=lambda _: None))
    rig.d.appkit.NSDate = NS(dateWithTimeIntervalSinceNow_=lambda _: None)
    observed = rig.observe()
    with pytest.raises(RuntimeError, match="did not acquire focus"):
        rig.act(Action(kind="focus", window_id=rig.handle(2)), observed)
    assert not rig.raised and not rig.posts


def focus_clock(rig, monkeypatch):
    from openjiuwen.harness.tools.desktop import macos_navigation

    clock = [100.0]
    monkeypatch.setattr(macos_navigation.time, "monotonic", lambda: clock[0])
    rig.d.appkit.NSRunLoop = NS(
        currentRunLoop=lambda: NS(
            runUntilDate_=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
        )
    )
    rig.d.appkit.NSDate = NS(dateWithTimeIntervalSinceNow_=lambda seconds: seconds)
    return clock


def test_focus_reissues_only_activation_to_same_window_after_pending_request(rig, monkeypatch):
    clock = focus_clock(rig, monkeypatch)
    calls = []

    def activate(_):
        calls.append(clock[0])
        if len(calls) == 2:
            rig.front = 20
        return True

    rig.apps[20].activateWithOptions_ = activate
    rig.d.monitor = Mock()
    rig.act(Action(kind="focus", target="viewer"), rig.observe())
    assert len(calls) == 2 and calls[1] - calls[0] >= 0.35
    assert rig.raised == [3, 3] and not rig.posts
    verified = [c for c in rig.d.monitor.record.call_args_list if c.args == ("focus_verified",)]
    assert verified[0].kwargs["front_pid"] == verified[0].kwargs["target_pid"] == 20


@pytest.mark.parametrize("phase_seconds, expected_attempts", [(None, 3), (0.2, 1)])
def test_focus_acknowledgements_never_override_wrong_recipient_or_deadline(
    rig,
    monkeypatch,
    phase_seconds,
    expected_attempts,
):
    clock = focus_clock(rig, monkeypatch)
    if phase_seconds:
        rig.d.phase_deadline = clock[0] + phase_seconds
    rig.apps[20].activateWithOptions_ = Mock(return_value=True)
    rig.d.monitor = Mock()
    with pytest.raises(RuntimeError, match=r"target_pid=20, front_pid=10.*AXRaise status=0"):
        rig.act(Action(kind="focus", target="viewer"), rig.observe())
    assert rig.apps[20].activateWithOptions_.call_count == expected_attempts
    assert rig.raised == [3] * expected_attempts and not rig.posts
    assert clock[0] <= 100 + (phase_seconds or 2)
    assert any(c.args == ("focus_failed",) for c in rig.d.monitor.record.call_args_list)


@pytest.mark.parametrize("change", ["move", "restart", "stop", "contamination"])
def test_focus_transition_never_reissues_after_identity_or_control_loss(rig, monkeypatch, change):
    focus_clock(rig, monkeypatch)
    rig.d.monitor = Mock()

    def activate(_):
        if change == "move":
            rig.rows[-1]["kCGWindowBounds"]["X"] += 1
        elif change == "restart":
            rig.launches[20] += 1
        elif change == "stop":
            worker.STOPPING.set()
        else:
            rig.d.monitor.check.side_effect = RuntimeError("outside input")
        return True

    rig.apps[20].activateWithOptions_ = Mock(side_effect=activate)
    with pytest.raises(RuntimeError):
        rig.act(Action(kind="focus", target="viewer"), rig.observe())
    assert rig.apps[20].activateWithOptions_.call_count == 1
    assert not rig.posts


@pytest.mark.parametrize("native_window, pid", [(2, 10), (3, 20)])
def test_rejected_activation_can_focus_exact_window_through_accessibility(rig, monkeypatch, native_window, pid):
    focus_clock(rig, monkeypatch)
    rig.front = 20 if pid == 10 else 10
    rig.apps[pid].activateWithOptions_ = Mock(return_value=False)
    rig.d.ax.AXUIElementIsAttributeSettable.return_value = (0, True)

    def set_frontmost(element, attribute, value):
        assert (element, attribute, value) == (("app", pid), "AXFrontmost", True)
        rig.front = pid
        return 0

    rig.d.ax.AXUIElementSetAttributeValue.side_effect = set_frontmost
    rig.d.monitor = Mock()
    observed = rig.observe()
    rig.act(Action(kind="focus", window_id=rig.handle(native_window)), observed)
    assert rig.observe().context["active_window_id"] == rig.handle(native_window)
    rig.apps[pid].activateWithOptions_.assert_called_once()
    rig.d.ax.AXUIElementIsAttributeSettable.assert_called_once_with(("app", pid), "AXFrontmost", None)
    rig.d.ax.AXUIElementSetAttributeValue.assert_called_once()
    assert rig.raised == [native_window] and not rig.posts
    verified = [c for c in rig.d.monitor.record.call_args_list if c.args == ("focus_verified",)][0]
    assert verified.kwargs["attempts"][0]["ax_frontmost_status"] == 0
    assert verified.kwargs["attempts"][0]["activation_policy"] == 0


@pytest.mark.parametrize("accepted", [False, True])
def test_verified_primary_activation_does_not_write_accessibility_focus(rig, accepted):
    rig.apps[20].activateWithOptions_ = lambda _: setattr(rig, "front", 20) or accepted
    rig.act(Action(kind="focus", target="viewer"), rig.observe())
    rig.d.ax.AXUIElementIsAttributeSettable.assert_not_called()
    rig.d.ax.AXUIElementSetAttributeValue.assert_not_called()


@pytest.mark.parametrize(
    "query, write_status", [((0, False), 0), ((-25205, True), 0), ((0, True), -25206), ((0, True), 0)]
)
def test_unsupported_or_ineffective_focus_fallback_remains_bounded(rig, monkeypatch, query, write_status):
    clock = focus_clock(rig, monkeypatch)
    rig.apps[20].activateWithOptions_ = Mock(return_value=False)
    rig.d.ax.AXUIElementIsAttributeSettable.return_value = query
    rig.d.ax.AXUIElementSetAttributeValue.return_value = write_status
    with pytest.raises(RuntimeError, match="did not acquire focus"):
        rig.act(Action(kind="focus", target="viewer"), rig.observe())
    assert rig.apps[20].activateWithOptions_.call_count == 3
    rig.d.ax.AXUIElementIsAttributeSettable.assert_called_once()
    assert rig.d.ax.AXUIElementSetAttributeValue.call_count == int(query == (0, True))
    assert clock[0] <= 102 and rig.front == 10 and not rig.posts


def test_focus_fallback_acknowledgement_cannot_accept_wrong_window(rig, monkeypatch):
    focus_clock(rig, monkeypatch)
    rig.apps[10].activateWithOptions_ = Mock(return_value=False)
    rig.front = 20
    rig.d.ax.AXUIElementIsAttributeSettable.return_value = (0, True)
    rig.d.ax.AXUIElementSetAttributeValue.side_effect = lambda *_: setattr(rig, "front", 10) or 0
    rig.d.ax.AXUIElementPerformAction = lambda *_: 0  # Acknowledged without changing window 1 to window 2.
    observed = rig.observe()
    with pytest.raises(RuntimeError, match="did not acquire focus"):
        rig.act(Action(kind="focus", window_id=rig.handle(2)), observed)
    assert rig.front == 10 and rig.focused[10] == 1 and not rig.posts


@pytest.mark.parametrize("boundary", ["activation", "capability", "fallback"])
@pytest.mark.parametrize("change", ["move", "restart", "stop", "contamination", "deadline", "lock"])
def test_no_focus_mutation_after_identity_or_control_loss(rig, monkeypatch, boundary, change):
    clock = focus_clock(rig, monkeypatch)
    rig.d.monitor = Mock()
    rig.apps[20].activateWithOptions_ = Mock(return_value=False)
    rig.d.ax.AXUIElementIsAttributeSettable.return_value = (0, True)

    def lost(*_):
        if change == "move":
            rig.rows[-1]["kCGWindowBounds"]["X"] += 1
        elif change == "restart":
            rig.launches[20] += 1
        elif change == "stop":
            worker.STOPPING.set()
        elif change == "contamination":
            rig.d.monitor.check.side_effect = RuntimeError("outside input")
        elif change == "deadline":
            clock[0] += 2.1
        else:
            rig.locked = True
        return (0, True) if boundary == "capability" else False if boundary == "activation" else 0

    hook = {
        "activation": rig.apps[20].activateWithOptions_,
        "capability": rig.d.ax.AXUIElementIsAttributeSettable,
        "fallback": rig.d.ax.AXUIElementSetAttributeValue,
    }[boundary]
    hook.side_effect = lost
    with pytest.raises((RuntimeError, PermissionError)):
        rig.act(Action(kind="focus", target="viewer"), rig.observe())
    assert rig.d.ax.AXUIElementSetAttributeValue.call_count == int(boundary == "fallback")
    assert not rig.raised and not rig.posts


def test_disappearing_dialog_capture_retries_only_observation(rig):
    original = rig.d.navigation.capture_window
    rig.focused[10] = 2

    def vanished(native_id):
        if native_id == 1:
            rig.rows = [r for r in rig.rows if r["kCGWindowNumber"] != 1]
            raise subprocess.CalledProcessError(1, ["screencapture"])
        return original(native_id)

    rig.d.navigation.capture_window = vanished
    observed = rig.observe()
    assert len(observed.context["windows"]) == 2
    assert rig.d.last is not None and not rig.posts


def test_unchanged_window_capture_error_is_never_retried(rig):
    failed = Mock(side_effect=subprocess.CalledProcessError(1, ["screencapture"]))
    rig.d.navigation.capture_window = failed
    with pytest.raises(subprocess.CalledProcessError):
        rig.observe()
    assert failed.call_count == 1 and rig.d.last is None and not rig.posts
