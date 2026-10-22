"""Native toggle activation with injected AX/input only; no desktop or model use."""

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from openjiuwen.harness.tools.desktop.accessibility import AccessibilityTree
from openjiuwen.harness.tools.desktop.schema import Action, DesktopSettings, Observation
from openjiuwen.harness.tools.desktop.worker import NativeDesktop
from tests.unit_tests.harness.tools.desktop.test_accessibility import FakeAX


def fixture(role="AXCheckBox", *, label="Receive updates", width=240, value=0):
    ax = FakeAX()
    ax.nodes["button"].update(AXRole=role, AXTitle=label, AXValue=value, AXSize=NS(width=width, height=30))
    ax.AXUIElementCopyActionNames = Mock(return_value=(0, ["AXPress"]))

    def press(element, action):
        assert element == "button" and action == "AXPress"
        ax.nodes[element]["AXValue"] = 1 - ax.nodes[element]["AXValue"] if role == "AXCheckBox" else 1
        return 0

    ax.AXUIElementPerformAction = Mock(side_effect=press)
    tree = AccessibilityTree(ax)
    obs = Observation("", 1400, 960, (10, 20, 700, 480), "owned", 12, 1)
    elements, status = tree.snapshot(NS(processIdentifier=lambda: 100), obs)
    obs = replace(obs, elements=elements, accessibility_status=status)
    driver = NativeDesktop.__new__(NativeDesktop)
    driver.settings = DesktopSettings(accessibility_enabled=True)
    driver.last, driver.accessibility = obs, tree
    driver.input_events = NS(guard_point=Mock(), click=Mock())
    driver.monitor = NS(record=Mock())
    return ax, tree, obs, driver


def click(driver, **changes):
    driver._perform_action(Action(kind="click", element_id=driver.last.elements[0]["id"], **changes))


@pytest.mark.parametrize("role", ["AXCheckBox", "AXRadioButton"])
@pytest.mark.parametrize("label,width", [("Receive updates", 240), ("", 30), ("گزینه", 420)])
@pytest.mark.parametrize("value", [0, 1])
def test_primary_id_click_uses_exact_native_toggle_once(role, label, width, value):
    ax, _, obs, driver = fixture(role, label=label, width=width, value=value)
    click(driver)
    ax.AXUIElementPerformAction.assert_called_once_with("button", "AXPress")
    driver.input_events.click.assert_not_called()
    assert driver.input_events.guard_point.call_count == 2
    assert ax.nodes["button"]["AXValue"] == (1 - value if role == "AXCheckBox" else 1)
    driver.monitor.record.assert_called_once()
    assert driver.monitor.record.call_args.args == ("accessibility_toggle_press",)
    assert driver.monitor.record.call_args.kwargs["native_status"] == 0
    assert driver.monitor.record.call_args.kwargs["acknowledgement_timeout"] == 1
    assert obs.elements[0]["value"] == str(value)  # Never write projected state as if observed.


@pytest.mark.parametrize("capability", ["missing_api", "unsupported", "no_value", "empty", "other"])
def test_unsupported_capability_preserves_one_physical_click(capability):
    ax, _, _, driver = fixture()
    if capability == "missing_api":
        del ax.AXUIElementCopyActionNames
    else:
        ax.AXUIElementCopyActionNames.return_value = {
            "unsupported": (-25205, None),
            "no_value": (-25212, None),
            "empty": (0, []),
            "other": (0, ["AXShowMenu"]),
        }[capability]
    click(driver)
    ax.AXUIElementPerformAction.assert_not_called()
    driver.input_events.click.assert_called_once_with((160, 75), "left", 1, [])


@pytest.mark.parametrize(
    "changes",
    [
        {"button": "right"},
        {"button": "middle"},
        {"click_count": 2},
        {"click_count": 3},
        {"modifiers": ["cmd"]},
    ],
)
def test_pointer_gestures_never_become_semantic_activation(changes):
    ax, _, _, driver = fixture()
    click(driver, **changes)
    ax.AXUIElementCopyActionNames.assert_not_called()
    ax.AXUIElementPerformAction.assert_not_called()
    driver.input_events.click.assert_called_once()


def test_coordinate_click_and_ordinary_button_keep_physical_semantics():
    ax, _, _, driver = fixture("AXButton")
    click(driver)
    ax.AXUIElementCopyActionNames.assert_not_called()
    ax.AXUIElementPerformAction.assert_not_called()
    driver.input_events.click.assert_called_once()
    driver._perform_action(Action(kind="click", x=50, y=60))
    assert driver.input_events.click.call_count == 2
    ax.AXUIElementCopyActionNames.assert_not_called()


@pytest.mark.parametrize(
    "change",
    [
        "disabled",
        "secure",
        "secure_parent",
        "hidden",
        "hidden_parent",
        "foreign_pid",
        "foreign_window",
        "moved",
        "root_moved",
        "label",
        "role",
        "state",
        "occluded",
        "stale",
    ],
)
@pytest.mark.parametrize("during_query", [False, True])
def test_changed_or_unauthorized_toggle_never_receives_input(change, during_query):
    ax, tree, _, driver = fixture()

    def mutate():
        edits = {
            "disabled": {"AXEnabled": False},
            "secure": {"AXProtectedContent": True},
            "hidden": {"AXHidden": True},
            "foreign_pid": {"pid": 101},
            "foreign_window": {"AXWindow": "other"},
            "moved": {"AXPosition": NS(x=41, y=60)},
            "label": {"AXTitle": "Replaced"},
            "role": {"AXRole": "AXButton"},
            "state": {"AXValue": 1},
        }
        if change in edits:
            ax.nodes["button"].update(edits[change])
        elif change in {"hidden_parent", "secure_parent"}:
            ax.nodes["window"]["AXHidden" if change == "hidden_parent" else "AXProtectedContent"] = True
        elif change == "root_moved":
            ax.nodes["window"]["AXPosition"] = NS(x=11, y=20)
        elif change == "occluded":
            ax.hit = "text"
        elif change == "stale":
            tree.generation += 1

    if during_query:

        def query(*_):
            mutate()
            return 0, ["AXPress"]

        ax.AXUIElementCopyActionNames.side_effect = query
    else:
        mutate()
    with pytest.raises(RuntimeError):
        click(driver)
    ax.AXUIElementPerformAction.assert_not_called()
    driver.input_events.click.assert_not_called()


@pytest.mark.parametrize("guard_number", [1, 2])
@pytest.mark.parametrize("reason", ["cancelled", "window changed", "occluded", "external input"])
def test_final_native_recipient_and_stop_guards_apply_to_semantic_press(guard_number, reason):
    ax, _, _, driver = fixture()
    driver.input_events.guard_point.side_effect = ([None] if guard_number == 2 else []) + [RuntimeError(reason)]
    with pytest.raises(RuntimeError, match=reason):
        click(driver)
    ax.AXUIElementPerformAction.assert_not_called()
    driver.input_events.click.assert_not_called()


@pytest.mark.parametrize("response", [(-25202, None), (0, "AXPress"), (0, None)])
def test_failed_or_malformed_capability_query_is_not_permission_for_input(response):
    ax, _, _, driver = fixture()
    ax.AXUIElementCopyActionNames.return_value = response
    with pytest.raises(RuntimeError, match="could not be verified"):
        click(driver)
    ax.AXUIElementPerformAction.assert_not_called()
    driver.input_events.click.assert_not_called()


@pytest.mark.parametrize("raise_error", [False, True])
def test_unknown_native_error_is_never_retried_or_followed_by_pointer_click(raise_error):
    ax, _, _, driver = fixture()

    def ambiguous(*_):
        ax.nodes["button"]["AXValue"] = 1  # Application did act, but IPC reply is lost.
        if raise_error:
            raise RuntimeError("reply lost")
        return -25200

    ax.AXUIElementPerformAction.side_effect = ambiguous
    with pytest.raises(RuntimeError):
        click(driver)
    ax.AXUIElementPerformAction.assert_called_once()
    driver.input_events.click.assert_not_called()
    driver.monitor.record.assert_called_once()
    assert ax.nodes["button"]["AXValue"] == 1


def test_unsupported_query_does_not_allow_stale_physical_fallback():
    ax, _, _, driver = fixture()

    def query(*_):
        ax.nodes["button"]["AXValue"] = 1
        return -25205, None

    ax.AXUIElementCopyActionNames.side_effect = query
    with pytest.raises(RuntimeError, match="toggle value changed"):
        click(driver)
    driver.input_events.click.assert_not_called()
    ax.AXUIElementPerformAction.assert_not_called()


def test_saved_center_miss_trace_uses_native_activation_without_fixture_coordinates():
    saved = json.loads((Path(__file__).parent / "fixtures/toggle-click-20261020.json").read_text())
    before, after = saved["optimized_before"]["element"], saved["optimized_after_center"]["element"]
    assert before["bounds"] == after["bounds"] and before["value"] == after["value"] == "0"
    assert saved["batching_after_indicator"]["element"]["value"] == "1"
    ax, tree, obs, driver = fixture(label=before["label"])
    record = saved["optimized_before"]
    x, y, width, height = before["bounds"]
    left, top, desktop_width, desktop_height = record["bounds"]
    sx, sy = desktop_width / record["width"], desktop_height / record["height"]
    ax.nodes["button"].update(
        AXPosition=NS(x=left + x * sx, y=top + y * sy),
        AXSize=NS(width=width * sx, height=height * sy),
    )
    ax.nodes["window"].update(AXPosition=NS(x=left, y=top), AXSize=NS(width=desktop_width, height=desktop_height))
    obs = replace(obs, bounds=tuple(record["bounds"]), width=record["width"], height=record["height"])
    elements, status = tree.snapshot(NS(processIdentifier=lambda: 100), obs)
    driver.last = replace(obs, elements=elements, accessibility_status=status)
    click(driver)
    assert ax.nodes["button"]["AXValue"] == 1
    driver.input_events.click.assert_not_called()
