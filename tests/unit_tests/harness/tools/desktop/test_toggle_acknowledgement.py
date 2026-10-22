"""Delayed AX replies and strict postconditions; simulated time, no native UI."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from openjiuwen.harness.tools.desktop import accessibility as module
from tests.unit_tests.harness.tools.desktop.test_toggle_activation import click, fixture


def delayed_provider(monkeypatch, *, delay=0.2, role="AXCheckBox", value=0, status=None):
    ax, tree, obs, driver = fixture(role, value=value)
    clock = [100.0]
    timeouts = {}
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])

    def timeout(element, seconds):
        timeouts[element] = seconds
        return 0

    ax.AXUIElementSetMessagingTimeout = Mock(side_effect=timeout)

    def action(element, _action):
        # A real provider can change state before its synchronous reply arrives.
        ax.nodes[element]["AXValue"] = 1 - value if role == "AXCheckBox" else 1
        clock[0] += min(delay, timeouts[element])
        return status if status is not None else (0 if delay < timeouts[element] else -25204)

    ax.AXUIElementPerformAction.side_effect = action
    return ax, tree, obs, driver, clock, timeouts


@pytest.mark.parametrize("role", ["AXCheckBox", "AXRadioButton"])
@pytest.mark.parametrize("delay", [0.001, 0.15, 0.4, 0.9])
def test_action_wait_is_independent_of_read_timeout_without_a_fixed_sleep(monkeypatch, role, delay):
    ax, _, _, driver, clock, timeouts = delayed_provider(monkeypatch, role=role, delay=delay)
    click(driver)
    ax.AXUIElementPerformAction.assert_called_once_with("button", "AXPress")
    driver.input_events.click.assert_not_called()
    assert clock[0] == pytest.approx(100 + delay)
    assert timeouts["button"] == 0.1
    entry = driver.monitor.record.call_args.kwargs
    assert entry["native_status"] == 0
    assert entry["acknowledgement_timeout"] == 1
    assert entry["acknowledgement_seconds"] == pytest.approx(delay)


@pytest.mark.parametrize("role,value", [("AXCheckBox", 0), ("AXCheckBox", 1), ("AXRadioButton", 0)])
def test_cannot_complete_with_verified_transition_is_completed_once(monkeypatch, role, value):
    ax, tree, obs, driver, clock, timeouts = delayed_provider(
        monkeypatch,
        role=role,
        value=value,
        delay=1.2,
    )
    click(driver)
    assert clock[0] == 101 and timeouts["button"] == 0.1
    ax.AXUIElementPerformAction.assert_called_once()
    driver.input_events.click.assert_not_called()
    assert driver.input_events.guard_point.call_count == 4
    driver.monitor.record.assert_any_call("accessibility_toggle_timeout_verified", role=role, verified=True)
    # The original pre-action reference is not silently updated/reused.
    with pytest.raises(RuntimeError, match="toggle value changed"):
        tree.validate_reference(obs.elements[0]["id"], obs)


@pytest.mark.parametrize("role,value", [("AXRadioButton", 1), ("AXCheckBox", 2)])
def test_unchanged_radio_or_unknown_initial_state_cannot_prove_completion(monkeypatch, role, value):
    ax, _, _, driver, _, _ = delayed_provider(monkeypatch, role=role, value=value, status=-25204)
    with pytest.raises(RuntimeError, match="no verifiable binary transition"):
        click(driver)
    ax.AXUIElementPerformAction.assert_called_once()
    driver.input_events.click.assert_not_called()
    driver.monitor.record.assert_any_call("accessibility_toggle_timeout_verified", role=role, verified=False)


@pytest.mark.parametrize(
    "change",
    [
        "unchanged",
        "wrong_value",
        "disabled",
        "hidden",
        "hidden_parent",
        "secure",
        "secure_parent",
        "foreign_pid",
        "foreign_window",
        "moved",
        "root_moved",
        "label",
        "role",
        "occluded",
        "stale",
        "read_error",
    ],
)
def test_timeout_recovery_rejects_any_unproven_or_unsafe_postcondition(monkeypatch, change):
    from types import SimpleNamespace as NS

    ax, tree, _, driver, _, _ = delayed_provider(monkeypatch, status=-25204)
    original = ax.AXUIElementPerformAction.side_effect

    def action(*args):
        original(*args)
        updates = {
            "unchanged": {"AXValue": 0},
            "wrong_value": {"AXValue": 2},
            "disabled": {"AXEnabled": False},
            "hidden": {"AXHidden": True},
            "secure": {"AXProtectedContent": True},
            "foreign_pid": {"pid": 999},
            "foreign_window": {"AXWindow": "other"},
            "moved": {"AXPosition": NS(x=41, y=60)},
            "label": {"AXTitle": "Different"},
            "role": {"AXRole": "AXButton"},
        }
        if change in updates:
            ax.nodes["button"].update(updates[change])
        elif change in {"hidden_parent", "secure_parent"}:
            ax.nodes["window"]["AXHidden" if change == "hidden_parent" else "AXProtectedContent"] = True
        elif change == "root_moved":
            ax.nodes["window"]["AXPosition"] = NS(x=11, y=20)
        elif change == "occluded":
            ax.hit = "text"
        elif change == "stale":
            tree.generation += 1
        else:
            ax.AXUIElementCopyAttributeValue = lambda *_: (-25204, None)
        return -25204

    ax.AXUIElementPerformAction.side_effect = action
    with pytest.raises(RuntimeError):
        click(driver)
    ax.AXUIElementPerformAction.assert_called_once()
    driver.input_events.click.assert_not_called()
    driver.monitor.record.assert_any_call("accessibility_toggle_timeout_verified", role="AXCheckBox", verified=False)


@pytest.mark.parametrize("guard_number", [3, 4])
@pytest.mark.parametrize("reason", ["stopped", "external input", "unshared", "foreground changed", "occluded"])
def test_post_timeout_native_guards_can_stop_even_a_changed_control(monkeypatch, guard_number, reason):
    ax, _, _, driver, _, _ = delayed_provider(monkeypatch, status=-25204)
    driver.input_events.guard_point.side_effect = [None] * (guard_number - 1) + [RuntimeError(reason)]
    with pytest.raises(RuntimeError, match=reason):
        click(driver)
    ax.AXUIElementPerformAction.assert_called_once()
    driver.input_events.click.assert_not_called()


@pytest.mark.parametrize("outcome", ["success", "cannot_complete", "other_error", "exception"])
def test_per_element_read_timeout_is_restored_on_all_native_outcomes(monkeypatch, outcome):
    ax, _, _, driver, _, timeouts = delayed_provider(monkeypatch)
    if outcome == "exception":
        ax.AXUIElementPerformAction.side_effect = RuntimeError("IPC exception")
    elif outcome != "success":
        ax.AXUIElementPerformAction.side_effect = lambda *_: -25204 if outcome == "cannot_complete" else -25200
    if outcome == "success":
        click(driver)
    else:
        with pytest.raises(RuntimeError):
            click(driver)
    assert timeouts["button"] == 0.1
    assert all(c.args[0] == "button" for c in ax.AXUIElementSetMessagingTimeout.call_args_list if c.args[1] > 0.1)
    driver.input_events.click.assert_not_called()


@pytest.mark.parametrize("remaining", [0, -1, 0.05, 0.3])
def test_expired_or_short_phase_deadline_cannot_be_extended(monkeypatch, remaining):
    ax, _, _, driver, _, timeouts = delayed_provider(monkeypatch, delay=1.2)
    driver.phase_deadline = 100 + remaining
    with pytest.raises(TimeoutError):
        click(driver)
    if remaining <= 0:
        ax.AXUIElementPerformAction.assert_not_called()
    else:
        ax.AXUIElementPerformAction.assert_called_once()
        assert timeouts["button"] == 0.1
        assert driver.monitor.record.call_args.kwargs["acknowledgement_timeout"] == pytest.approx(remaining)
    driver.input_events.click.assert_not_called()


def test_configured_short_action_budget_caps_acknowledgement(monkeypatch):
    ax, _, _, driver, _, _ = delayed_provider(monkeypatch, delay=0.1)
    driver.settings = driver.settings.model_copy(
        update={
            "budgets": driver.settings.budgets.model_copy(update={"action_seconds": 0.25}),
        }
    )
    click(driver)
    assert driver.monitor.record.call_args.kwargs["acknowledgement_timeout"] == 0.25
    ax.AXUIElementPerformAction.assert_called_once()


def test_timeout_set_failure_prevents_press_and_restore_failure_prevents_success(monkeypatch):
    for failure in ("set", "restore"):
        ax, _, _, driver, _, _ = delayed_provider(monkeypatch)
        original = ax.AXUIElementSetMessagingTimeout.side_effect
        acted = []

        def timeout(element, seconds):
            if element == "button" and (seconds == 1 if failure == "set" else bool(acted) and seconds == 0.1):
                return -25202
            return original(element, seconds)

        native = ax.AXUIElementPerformAction.side_effect
        ax.AXUIElementPerformAction.side_effect = lambda *a: acted.append(True) or native(*a)
        ax.AXUIElementSetMessagingTimeout.side_effect = timeout
        with pytest.raises(RuntimeError, match="timeout could not be"):
            click(driver)
        assert ax.AXUIElementPerformAction.call_count == (0 if failure == "set" else 1)
        driver.input_events.click.assert_not_called()


def test_saved_native_status_is_replayed_without_assuming_that_input_was_unapplied(monkeypatch):
    receipt = json.loads((Path(__file__).parent / "fixtures/toggle-timeout-20261020.json").read_text())
    assert receipt["native_press"]["native_status"] == -25204 and receipt["model_calls"] == 0
    ax, _, _, driver, _, _ = delayed_provider(monkeypatch, status=receipt["native_press"]["native_status"])
    click(driver)
    ax.AXUIElementPerformAction.assert_called_once()
    driver.input_events.click.assert_not_called()
    driver.monitor.record.assert_any_call("accessibility_toggle_timeout_verified", role="AXCheckBox", verified=True)
