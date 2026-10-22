"""Exact-point native toggle dispatch; no application names or fixture coordinates."""

from dataclasses import replace

import pytest

from openjiuwen.harness.tools.desktop.schema import Action
from tests.unit_tests.harness.tools.desktop.test_toggle_activation import fixture


def coordinate_click(driver, **changes):
    bounds = driver.last.elements[0]["bounds"]
    driver._perform_action(Action(kind="click", x=bounds[0] + 2, y=bounds[1] + 2, **changes))


@pytest.mark.parametrize("role", ["AXCheckBox", "AXRadioButton"])
@pytest.mark.parametrize("label,width", [("Receive updates", 240), ("", 30), ("گزینه", 420)])
@pytest.mark.parametrize("scale", [0.5, 1, 2])
def test_exact_point_activates_one_observed_toggle_without_moving_to_center(role, label, width, scale):
    ax, _, obs, driver = fixture(role, label=label, width=width)
    elements = [{**e, "bounds": [v * scale for v in e["bounds"]]} for e in obs.elements]
    driver.last = replace(obs, width=int(obs.width * scale), height=int(obs.height * scale), elements=elements)
    coordinate_click(driver)
    ax.AXUIElementPerformAction.assert_called_once_with("button", "AXPress")
    driver.input_events.click.assert_not_called()
    expected = (40 + 1 / scale, 60 + 1 / scale)
    assert all(call.args == (expected,) for call in driver.input_events.guard_point.call_args_list)


@pytest.mark.parametrize(
    "condition", ["outside", "ambiguous", "overlapping_button", "nonactionable", "disabled", "other_role", "occluded"]
)
def test_no_unique_verified_toggle_preserves_original_physical_point(condition):
    ax, _, obs, driver = fixture()
    e = obs.elements[0]
    if condition in ("ambiguous", "overlapping_button"):
        role = "AXButton" if condition == "overlapping_button" else e["role"]
        driver.last = replace(obs, elements=[e, {**e, "id": "other", "role": role}])
    elif condition in ("nonactionable", "disabled", "other_role"):
        changes = {
            "nonactionable": {"actionable": False},
            "disabled": {"enabled": False},
            "other_role": {"role": "AXButton"},
        }[condition]
        driver.last = replace(obs, elements=[{**e, **changes}])
    elif condition == "occluded":
        ax.hit = "text"
    x, y = e["bounds"][:2]
    x += -1 if condition == "outside" else 2
    y += 2
    driver._perform_action(Action(kind="click", x=x, y=y))
    ax.AXUIElementPerformAction.assert_not_called()
    driver.input_events.click.assert_called_once_with(obs.point(x, y), "left", 1, [])


@pytest.mark.parametrize(
    "changes",
    [{"button": "right"}, {"button": "middle"}, {"click_count": 2}, {"click_count": 3}, {"modifiers": ["shift"]}],
)
def test_nonprimary_gesture_is_not_replaced(changes):
    ax, _, _, driver = fixture()
    coordinate_click(driver, **changes)
    ax.AXUIElementCopyActionNames.assert_not_called()
    driver.input_events.click.assert_called_once()


def test_accessibility_disabled_preserves_coordinate_click():
    ax, _, _, driver = fixture()
    driver.settings = driver.settings.model_copy(update={"accessibility_enabled": False})
    coordinate_click(driver)
    ax.AXUIElementPerformAction.assert_not_called()
    driver.input_events.click.assert_called_once_with((41, 61), "left", 1, [])


@pytest.mark.parametrize("response", [(-25205, None), (0, [])])
def test_unsupported_native_action_preserves_original_point(response):
    ax, _, _, driver = fixture()
    ax.AXUIElementCopyActionNames.return_value = response
    coordinate_click(driver)
    ax.AXUIElementPerformAction.assert_not_called()
    driver.input_events.click.assert_called_once_with((41, 61), "left", 1, [])


@pytest.mark.parametrize("change", ["moved", "state", "secure", "foreign", "stale", "point_occluded"])
@pytest.mark.parametrize("supported", [False, True])
def test_changed_target_during_query_never_receives_press_or_mouse_fallback(change, supported):
    ax, tree, _, driver = fixture()

    def query(*_):
        if change == "moved":
            ax.nodes["button"]["AXPosition"].x += 1
        elif change == "state":
            ax.nodes["button"]["AXValue"] = 1
        elif change == "secure":
            ax.nodes["button"]["AXProtectedContent"] = True
        elif change == "foreign":
            ax.nodes["button"]["pid"] = 999
        elif change == "stale":
            tree.generation += 1
        else:
            # The center still hits the toggle; the requested point no longer does.
            ax.AXUIElementCopyElementAtPosition = lambda _, x, y, unused: (0, "button" if x > 100 else "text")
        return (0, ["AXPress"]) if supported else (-25205, None)

    ax.AXUIElementCopyActionNames.side_effect = query
    with pytest.raises(RuntimeError):
        coordinate_click(driver)
    ax.AXUIElementPerformAction.assert_not_called()
    driver.input_events.click.assert_not_called()


def test_unknown_press_outcome_never_retries_or_falls_back():
    ax, _, _, driver = fixture()
    ax.AXUIElementPerformAction.side_effect = RuntimeError("reply lost")
    with pytest.raises(RuntimeError, match="reply lost"):
        coordinate_click(driver)
    ax.AXUIElementPerformAction.assert_called_once()
    driver.input_events.click.assert_not_called()


@pytest.mark.parametrize("child_role", ["AXStaticText", "AXLink", "AXButton"])
def test_label_child_can_identify_toggle_but_nested_action_cannot(child_role):
    ax, _, _, driver = fixture()
    ax.nodes["text"].update(AXParent="button", AXRole=child_role)
    ax.hit = "text"
    coordinate_click(driver)
    assert ax.AXUIElementPerformAction.call_count == (child_role == "AXStaticText")
    assert driver.input_events.click.call_count == (child_role != "AXStaticText")
