"""General desktop metadata boundaries; no framework imports, input or model calls."""

import pytest
from pydantic import ValidationError

from openjiuwen.harness.tools.desktop.session_contract import (
    DesktopObservationContext,
    DesktopRect,
    DesktopSessionScope,
    DesktopWindow,
    ScreenshotGeometry,
)


def window(**changes):
    values = dict(
        window_id="window-1",
        application_id="process-instance-1",
        display_id="display-1",
        title="Untitled",
        desktop_rect=DesktopRect(x=-1200, y=40, width=800, height=600),
    )
    return DesktopWindow(**(values | changes))


def observation(**changes):
    item = window()
    values = dict(
        session_id="session-1",
        observation_id="frame-1",
        display_id=item.display_id,
        geometry=ScreenshotGeometry(pixel_width=1600, pixel_height=1200, desktop_rect=item.desktop_rect),
        windows=(item,),
        active_window_id=item.window_id,
        view_window_id=item.window_id,
    )
    return DesktopObservationContext(**(values | changes))


def scope():
    return DesktopSessionScope(session_id="session-1", display_id="display-1", application_ids=("process-instance-1",))


@pytest.mark.parametrize("pixel_size", [(1600, 1200), (400, 300), (1000, 300)])
def test_pixel_mapping_with_negative_origin_retina_downsampling_and_independent_axes(pixel_size):
    width, height = pixel_size
    geometry = ScreenshotGeometry(pixel_width=width, pixel_height=height, desktop_rect=window().desktop_rect)
    assert geometry.to_desktop(0, 0) == (-1200, 40)
    assert geometry.to_desktop(width / 2, height / 2) == (-800, 340)
    assert geometry.to_desktop(width / 4, height * 3 / 4) == (-1000, 490)


@pytest.mark.parametrize(
    "point",
    [(-1, 0), (0, -1), (1600, 0), (0, 1200), (float("nan"), 1), (1, float("inf")), (True, 0), ("1", 0)],
)
def test_invalid_pixels_are_rejected_before_coordinate_mapping(point):
    with pytest.raises(ValueError):
        observation().geometry.to_desktop(*point)


@pytest.mark.parametrize(
    "changes",
    [{"width": 0}, {"height": -1}, {"x": float("inf")}, {"width": float("nan")}, {"x": 1e308, "width": 1e308}],
)
def test_invalid_native_geometry_cannot_enter_an_observation(changes):
    with pytest.raises(ValidationError):
        DesktopRect(**(dict(x=0, y=0, width=800, height=600) | changes))


@pytest.mark.parametrize("changes", [{"pixel_width": 0}, {"pixel_height": -1}, {"pixel_width": True}])
def test_invalid_bitmap_dimensions_cannot_enter_an_observation(changes):
    with pytest.raises(ValidationError):
        ScreenshotGeometry(**(dict(pixel_width=800, pixel_height=600, desktop_rect=window().desktop_rect) | changes))


def test_duplicate_and_changed_titles_do_not_define_window_identity():
    first, second = window(), window(window_id="window-2")
    context = observation(windows=(first, second))
    scope().validate_observation(context)
    renamed = DesktopWindow(**(first.model_dump() | {"title": "A new title - 7 pages"}))
    scope().validate_observation(observation(windows=(renamed, second)))
    assert renamed.window_id == first.window_id != second.window_id


@pytest.mark.parametrize("kind", ["dialog", "menu"])
def test_new_surfaces_in_the_granted_application_do_not_need_title_configuration(kind):
    surface = window(window_id="new-surface", title="", kind=kind)
    context = observation(windows=(window(), surface), active_window_id=surface.window_id)
    scope().validate_observation(context)


def test_screenshot_only_context_needs_no_control_labels_or_accessibility_tree():
    context = observation()
    scope().validate_observation(context)
    assert context.geometry.to_desktop(400, 300) == (-1000, 190)


@pytest.mark.parametrize("changes", [{"session_id": "another-session"}, {"display_id": "another-display"}])
def test_empty_display_view_cannot_bypass_session_or_display_scope(changes):
    context = observation(windows=(), active_window_id=None, view_window_id=None, **changes)
    with pytest.raises(PermissionError):
        scope().validate_observation(context)


def test_another_process_instance_is_rejected_even_when_title_and_window_handle_match():
    context = observation(windows=(window(application_id="restarted-process"),))
    with pytest.raises(PermissionError, match="outside"):
        scope().validate_observation(context)


@pytest.mark.parametrize(
    "changes",
    [
        {"windows": (window(), window())},
        {"windows": (window(display_id="another-display"),)},
        {"active_window_id": "unknown"},
        {"view_window_id": "unknown"},
        {
            "geometry": ScreenshotGeometry(
                pixel_width=800, pixel_height=600, desktop_rect=DesktopRect(x=0, y=0, width=800, height=600)
            )
        },
    ],
)
def test_ambiguous_or_inconsistent_observation_metadata_is_rejected(changes):
    with pytest.raises(ValidationError):
        observation(**changes)


@pytest.mark.parametrize("application_ids", [("same", "same"), ("",), ("two words",)])
def test_scope_requires_explicit_unambiguous_application_handles(application_ids):
    with pytest.raises(ValidationError):
        DesktopSessionScope(session_id="session", display_id="display", application_ids=application_ids)


def test_scope_and_observation_round_trip_without_mutable_collections():
    original_scope, original_context = scope(), observation()
    restored_scope = DesktopSessionScope.model_validate_json(original_scope.model_dump_json())
    restored_context = DesktopObservationContext.model_validate_json(original_context.model_dump_json())
    assert (restored_scope, restored_context) == (original_scope, original_context)
    restored_scope.validate_observation(restored_context)
    with pytest.raises(ValidationError, match="frozen"):
        restored_scope.application_ids += ("other",)
    with pytest.raises(ValidationError, match="frozen"):
        restored_context.windows[0].title = "changed"


def test_task_content_and_unknown_policy_fields_are_not_silently_accepted():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DesktopSessionScope(**(scope().model_dump() | {"task": "type an answer"}))
