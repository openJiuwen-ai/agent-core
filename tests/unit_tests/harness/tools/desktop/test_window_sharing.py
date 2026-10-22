"""Capture and input must respect window grants, including private same-app siblings."""

import base64
import io
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from PIL import Image
from test_window_navigation import NativeRig, row

from openjiuwen.harness.tools.desktop import worker
from openjiuwen.harness.tools.desktop.macos_navigation import MacOSNavigation
from openjiuwen.harness.tools.desktop.schema import Action, DesktopSettings, Target


@pytest.fixture
def rig():
    worker.STOPPING.clear()
    value = NativeRig()
    value.d.targets["editor"] = Target(name="editor", pid=10, window_ids=[1])
    value.d.navigation = MacOSNavigation(value.d)
    value.d.navigation.capture_window = value.capture
    yield value
    worker.STOPPING.clear()


@pytest.mark.parametrize("selector", [{"window_ids": [1]}, {"window_title": "Shared document"}])
def test_only_selected_window_pixels_titles_and_ax_content_are_exposed(rig, selector):
    rig.rows[0]["kCGWindowName"] = "Shared document"
    rig.rows[1]["kCGWindowName"] = "PRIVATE sibling"
    rig.d.targets["editor"] = Target(name="editor", pid=10, **selector)
    snapshot = Mock(return_value=([{"value": "shared content"}], "available"))
    rig.d.accessibility = NS(clear=Mock(), snapshot=snapshot)
    result = rig.observe()
    assert rig.captures == [1, 3]
    assert "PRIVATE" not in str(result)
    bitmap = Image.open(io.BytesIO(base64.b64decode(result.png_b64)))
    assert bitmap.getpixel((250, 60)) == (0, 0, 0)
    assert snapshot.call_args.kwargs["window_bounds"] == (10, 30, 150, 100)
    rig.focused[10] = 2
    result = rig.observe()
    assert result.context["active_window_id"] is None and not result.elements
    assert snapshot.call_count == 1  # No private tree traversal.


@pytest.mark.parametrize("kind", ["click", "type", "shortcut", "scroll", "move", "drag", "hold_key", "hold_mouse"])
def test_unshared_focused_window_cannot_receive_any_input(rig, kind):
    rig.focused[10] = 2
    observed = rig.observe()
    fields = {
        "click": {"x": 220, "y": 60},
        "type": {"text": "private"},
        "shortcut": {"keys": ["a"]},
        "scroll": {"direction": "down"},
        "move": {"x": 220, "y": 60},
        "drag": {"path": [[220, 60], [230, 60]]},
        "hold_key": {"keys": ["a"], "duration": 0.05},
        "hold_mouse": {"x": 220, "y": 60, "duration": 0.05},
    }
    with pytest.raises(RuntimeError, match="not shared"):
        rig.act(Action(kind=kind, **fields[kind]), observed)
    assert not rig.posts


def test_private_sibling_cannot_be_focused_and_shared_focus_still_switches(rig):
    with pytest.raises(ValueError, match="latest observation"):
        rig.act(Action(kind="focus", window_id="2"), rig.observe())
    rig.act(Action(kind="focus", target="viewer"), rig.observe())
    rig.act(Action(kind="focus", target="editor"), rig.observe())
    assert rig.raised == [3, 1]  # No ambiguity from the unshared sibling.


def test_capture_entrypoint_rejects_unshared_same_process_window(rig):
    rig.observe()
    backend = NS(refresh=Mock(), capture=Mock())
    rig.d.window_capture = lambda: backend
    with pytest.raises(PermissionError, match="not shared"):
        MacOSNavigation.capture_window(rig.d.navigation, 2)
    backend.capture.assert_not_called()


@pytest.mark.parametrize("hit_available", [False, True])
def test_same_pid_hit_cannot_bypass_private_sibling_occlusion(rig, hit_available):
    rig.rows[1] = row(2, bounds=(20, 40, 40, 40), title="PRIVATE")
    rig.rows.insert(0, rig.rows.pop(1))
    if hit_available:
        rig.hit_error, rig.hit_pid = 0, 10
    with pytest.raises(RuntimeError, match="occluded"):
        rig.act(Action(kind="click", x=30, y=50), rig.observe())
    assert 2 not in rig.captures and not rig.posts


def test_ax_hit_in_unshared_window_cannot_be_authorized_by_pid_alone(rig):
    rig.hit_error, rig.hit_pid, rig.hit_window = 0, 10, 2
    with pytest.raises(RuntimeError, match="occluded"):
        rig.act(Action(kind="click", x=30, y=50), rig.observe())
    assert not rig.posts


def test_new_document_does_not_inherit_shared_application_scope(rig):
    rig.observe()
    rig.rows.insert(0, row(4, bounds=(180, 40, 180, 120), title="PRIVATE new document"))
    result = rig.observe()
    assert {rig.d.navigation.catalog.resolve(w["window_id"]).native_id for w in result.context["windows"]} == {1, 3}
    assert 4 not in rig.captures and "PRIVATE" not in str(result.context)


def test_identical_geometry_cannot_identify_private_sibling_as_the_shared_window(rig):
    rig.rows[1] = row(2, bounds=(10, 30, 150, 100), title="PRIVATE")
    rig.focused[10] = 2
    snapshot = Mock()
    rig.d.accessibility = NS(clear=Mock(), snapshot=snapshot)
    observed = rig.observe()
    assert observed.context["active_window_id"] is None
    snapshot.assert_not_called()
    with pytest.raises(RuntimeError, match="not shared"):
        rig.act(Action(kind="type", text="must not type"), observed)
    assert not rig.posts


@pytest.mark.parametrize("change", ["title", "disappearance", "same_title_replacement"])
def test_title_selector_binds_once_and_never_expands(rig, change):
    rig.rows[0]["kCGWindowName"] = "Shared"
    rig.d.targets["editor"] = Target(name="editor", pid=10, window_title="Shared")
    rig.observe()
    first = rig.handle(1)
    if change == "title":
        rig.rows[0]["kCGWindowName"] = "Renamed"
        rig.rows[1]["kCGWindowName"] = "Shared"
        rig.observe()
        assert rig.handle(1) == first and (10, 2) not in rig.d.navigation.catalog.shared
    else:
        original = rig.rows.pop(0)
        rig.observe()
        rig.rows.insert(0, original if change == "disappearance" else row(4, title="Shared"))
        rig.observe()
        assert not rig.d.navigation.sharing.roots["editor"]


@pytest.mark.parametrize(
    "parent,enabled,role,duplicate,expected",
    [
        (1, True, "AXSheet", False, True),
        (2, True, "AXSheet", False, False),
        (None, True, "AXSheet", False, False),
        (1, False, "AXSheet", False, False),
        (1, True, "AXWindow", False, False),
        (1, True, "AXSheet", True, False),
    ],
)
def test_only_uniquely_attached_sheets_inherit_window_sharing(rig, parent, enabled, role, duplicate, expected):
    rig.d.targets["editor"] = rig.d.targets["editor"].model_copy(update={"share_attached_sheets": enabled})
    rig.observe()
    rig.rows.insert(0, row(4, bounds=(40, 45, 90, 80), title="Save"))
    rig.roles[4] = role
    if parent:
        rig.sheets[parent] = [4]
    if duplicate:
        rig.rows.insert(0, row(5, bounds=(40, 45, 90, 80)))
    rig.focused[10] = 4
    result = rig.observe()
    assert (4 in rig.captures) is expected
    assert bool(result.context["active_window_id"]) is expected
    if expected:
        rig.act(Action(kind="click", x=50, y=60), result)
        assert len(rig.posts) == 2
    else:
        with pytest.raises(RuntimeError, match="not shared"):
            rig.act(Action(kind="click", x=50, y=60), result)


def test_app_only_target_cannot_capture(rig):
    rig.d.targets["editor"] = Target(name="editor", pid=10)
    with pytest.raises(PermissionError, match="app-wide access is disabled"):
        rig.observe()
    assert not rig.captures


def test_sheet_relationship_revocation_invalidates_input_even_without_geometry_change(rig):
    rig.observe()
    rig.rows.insert(0, row(4, bounds=(40, 45, 90, 80)))
    rig.roles[4], rig.sheets[1], rig.focused[10] = "AXSheet", [4], 4
    observed = rig.observe()
    rig.sheets[1] = []
    with pytest.raises(RuntimeError, match="observe again"):
        rig.act(Action(kind="click", x=50, y=60), observed)
    assert not rig.posts


def test_sheet_relationship_change_during_capture_discards_previously_shared_pixels(rig):
    rig.observe()
    rig.rows.insert(0, row(4, bounds=(40, 45, 90, 80)))
    rig.roles[4], rig.sheets[1], rig.focused[10] = "AXSheet", [4], 4

    def capture(native_id):
        image = rig.capture(native_id)
        rig.sheets[1] = []
        return image

    rig.d.navigation.capture_window = capture
    observed = rig.observe()
    assert (10, 4) not in rig.d.navigation.catalog.handles
    assert all(w["kind"] != "dialog" for w in observed.context["windows"])
    assert observed.context["active_window_id"] is None


@pytest.mark.parametrize("values", [[0], [-1], [True], ["1"], [1, 1]])
def test_shared_native_window_ids_are_strict_positive_unique(values):
    with pytest.raises(ValueError):
        Target(name="editor", pid=10, window_ids=values)


def test_model_action_cannot_add_a_native_window_grant():
    with pytest.raises(ValueError):
        Action(kind="focus", target="editor", window_ids=[99])
    with pytest.raises(ValueError):
        DesktopSettings(window_navigation=False, targets=[Target(name="editor", pid=10, window_ids=[1, 2])])
