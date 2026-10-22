"""Extended input contract and failure semantics; all Quartz events are simulated."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_general_input import act, desktop

from openjiuwen.core.session.agent import Session
from openjiuwen.harness.tools.desktop import macos_input, worker
from openjiuwen.harness.tools.desktop.direct_protocol import decode_call, input_schema
from openjiuwen.harness.tools.desktop.errors import InputNotStarted
from openjiuwen.harness.tools.desktop.macos_input import InputEvents
from openjiuwen.harness.tools.desktop.schema import Action


@pytest.mark.parametrize(
    "action",
    [
        dict(kind="move", x=20, y=40),
        dict(kind="click", x=20, y=40, modifiers=["shift"]),
        dict(kind="hold_mouse", x=20, y=40, duration=0.1),
        dict(kind="drag", path=[[20, 40], [40, 60]]),
        dict(kind="scroll", direction="down"),
    ],
)
def test_initial_occlusion_is_unapplied_without_any_native_event(action):
    driver = desktop()
    driver.point_is_target.return_value = False
    with pytest.raises(InputNotStarted):
        act(driver, **action)
    assert not driver.q.events


def test_occlusion_after_modifier_press_is_fatal_and_released():
    driver = desktop()
    driver.point_is_target.side_effect = [True, False]
    with pytest.raises(RuntimeError, match="occluded") as error:
        act(driver, kind="click", x=20, y=40, modifiers=["shift"])
    assert not isinstance(error.value, InputNotStarted)
    assert [e["kind"] for e in driver.q.events] == ["key_down", "key_up"]


@pytest.fixture(autouse=True)
def no_time_or_native_input(monkeypatch):
    worker.STOPPING.clear()
    monkeypatch.setattr(InputEvents, "pause", lambda self, _: self.check_stop())
    monkeypatch.setattr(worker.time, "sleep", lambda _: None)
    yield
    worker.STOPPING.clear()


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize(
    "action",
    [
        dict(kind="move", x=20, y=40, modifiers=["Command"]),
        dict(kind="hold_mouse", x=20, y=40, button="middle", duration=2, modifiers=["Shift"]),
        dict(kind="hold_key", keys=["Control", "ArrowRight"], duration=0.05),
        dict(kind="click", x=20, y=40, click_count=3, modifiers=["Option", "shift"]),
        dict(kind="drag", path=[[20, 40], [40, 60]], modifiers=["Control"]),
        dict(kind="scroll", direction="right", modifiers=["Control"]),
    ],
)
def test_extended_actions_round_trip_through_both_transports(action, legacy):
    body = (
        {"operation": "act", "action": action}
        if legacy
        else {"operation": action["kind"], **{key: value for key, value in action.items() if key != "kind"}}
    )
    operation, parsed = decode_call({**body, "observation_id": "fresh"})
    assert operation == "act" and parsed == Action(**action)
    assert Action.model_validate(parsed.model_dump()) == parsed
    assert Action.model_validate_json(parsed.model_dump_json()) == parsed
    assert all(key == key.lower() for key in parsed.modifiers + parsed.keys)


@pytest.mark.parametrize(
    "action",
    [
        dict(kind="move", x=1),
        dict(kind="hold_mouse", y=1),
        dict(kind="move", x=-1, y=0),
        dict(kind="move", x=float("nan"), y=0),
        dict(kind="move", x=True, y=0),
        dict(kind="move", x=0, y=0, duration=1),
        dict(kind="hold_mouse", x=0, y=0, click_count=2),
        dict(kind="hold_mouse", x=0, y=0, duration=0),
        dict(kind="hold_key", keys=["a"], duration=2.1),
        dict(kind="hold_key", keys=["a"], duration=float("inf")),
        dict(kind="hold_key", keys=[]),
        dict(kind="hold_key", keys=["shift"]),
        dict(kind="hold_key", keys=["a", "shift"]),
        dict(kind="hold_key", keys=["ctrl", "Control", "a"]),
        dict(kind="hold_key", keys=["a"], modifiers=["shift"]),
        dict(kind="click", x=0, y=0, modifiers=["Shift", "shift"]),
        dict(kind="click", x=0, y=0, modifiers=["fn"]),
        dict(kind="click", x=0, y=0, modifiers="shift"),
        dict(kind="type", text="abc", modifiers=["ctrl"]),
        dict(kind="wait", modifiers=["shift"]),
    ],
)
def test_invalid_extended_actions_rejected_before_native_input(action):
    with pytest.raises(ValueError):
        Action(**action)


def test_discovery_schema_exposes_complete_input_vocabulary():
    schema = input_schema()["properties"]
    assert {"move", "hold_mouse", "hold_key"} <= set(schema["operation"]["enum"])
    assert schema["duration"]["minimum"] == 0.05 and schema["duration"]["maximum"] == 2
    assert schema["modifiers"]["maxItems"] == 4
    assert schema["click_count"]["maximum"] == 3


def test_move_posts_only_pointer_motion_in_scaled_screenshot_coordinates():
    driver = desktop()
    assert act(driver, kind="move", x=400, y=300) == [dict(kind="move", point=(-1000, 190), button=0, flags=0)]
    assert driver.last is None


@pytest.mark.parametrize("kind", ["move", "hold_mouse"])
@pytest.mark.parametrize("failure", ["outside", "occlusion"])
def test_invalid_pointer_destination_never_presses_modifiers(kind, failure):
    driver = desktop()
    if failure == "occlusion":
        driver.point_is_target.return_value = False
    with pytest.raises((ValueError, RuntimeError)):
        act(driver, kind=kind, x=1600 if failure == "outside" else 1, y=1, modifiers=["shift"])
    assert not driver.q.events


@pytest.mark.parametrize(
    "action,middle",
    [
        (dict(kind="move", x=1, y=1), ["move"]),
        (dict(kind="click", x=1, y=1), ["left_down", "left_up"]),
        (dict(kind="click", x=1, y=1, click_count=3), ["left_down", "left_up"] * 3),
        (dict(kind="scroll", x=1, y=1), ["move", "scroll"]),
        (dict(kind="hold_mouse", x=1, y=1, duration=0.05), ["left_down", "left_up"]),
    ],
)
def test_mouse_modifiers_surround_complete_gesture_and_release_in_reverse_order(action, middle):
    driver = desktop()
    events = act(driver, **action, modifiers=["ctrl", "shift"])
    assert [event["kind"] for event in events] == ["key_down", "key_down", *middle, "key_up", "key_up"]
    assert [event["key"] for event in events[:2] + events[-2:]] == [59, 56, 56, 59]
    assert [event["flags"] for event in events[:2]] == [2, 10]
    assert all(event["flags"] == 10 for event in events[2:-2])
    assert all(event["flags"] == 0 for event in events[-2:])
    assert driver.input_events._release_event is None and not driver.input_events._modifier_releases


def test_modified_drag_keeps_modifier_flags_on_every_motion_and_mouse_release():
    events = act(desktop(), kind="drag", path=[[0, 0], [160, 0]], modifiers=["alt", "cmd"])
    assert [event["key"] for event in events[:2] + events[-2:]] == [58, 55, 55, 58]
    assert events[2]["kind"] == "left_down" and events[-3]["kind"] == "left_up"
    assert all(event["kind"] == "left_drag" for event in events[3:-3])
    assert all(event["flags"] == 5 for event in events[2:-2])


@pytest.mark.parametrize("keys", [["right"], ["ctrl", "shift", "right"]])
def test_hold_key_posts_a_single_press_and_polls_until_duration(monkeypatch, keys):
    pauses = []
    monkeypatch.setattr(InputEvents, "pause", lambda self, seconds: pauses.append(seconds))
    driver = desktop()
    events = act(driver, kind="hold_key", keys=keys, duration=0.1)
    assert sum(event["kind"] == "key_down" and event["key"] == 124 for event in events) == 1
    assert events[len(keys) - 1] == dict(kind="key_down", key=124, flags=10 if len(keys) > 1 else 0)
    assert events[len(keys)] == dict(kind="key_up", key=124, flags=0)
    assert len(pauses) == 5 and sum(pauses) == pytest.approx(0.1)
    assert driver.check_focus.call_count >= 5
    assert driver.input_events._release_event is None and not driver.input_events._modifier_releases


def test_hold_duration_accounts_for_time_spent_checking_native_focus(monkeypatch):
    driver = desktop()
    clock = iter([10.0, 10.0, 10.07, 10.12])
    monkeypatch.setattr(macos_input, "monotonic", lambda: next(clock))
    pauses = []
    monkeypatch.setattr(InputEvents, "pause", lambda self, seconds: pauses.append(seconds))
    act(driver, kind="hold_key", keys=["right"], duration=0.1)
    assert pauses == pytest.approx([0.02, 0.02])
    assert [event["kind"] for event in driver.q.events] == ["key_down", "key_up"]


@pytest.mark.parametrize("construction", ["modifier", "mouse", "move", "scroll"])
def test_event_construction_failure_cleans_up_without_unpaired_down(construction):
    driver = desktop()
    action = dict(kind="click", x=1, y=1, modifiers=["shift"])
    if construction == "modifier":
        driver.q.CGEventCreateKeyboardEvent = lambda *args: None
    elif construction in {"mouse", "move"}:
        driver.q.CGEventCreateMouseEvent = lambda *args: None
        if construction == "move":
            action["kind"] = "move"
    else:
        driver.q.CGEventCreateScrollWheelEvent = lambda *args: None
        action["kind"] = "scroll"
    with pytest.raises(RuntimeError, match="could not create"):
        act(driver, **action)
    events = driver.q.events
    if construction == "modifier":
        assert not events
    else:
        assert events[0] == dict(kind="key_down", key=56, flags=8)
        assert events[-1] == dict(kind="key_up", key=56, flags=0)
        assert not any(event["kind"].endswith("_down") for event in events[1:])
    assert driver.input_events._release_event is None and not driver.input_events._modifier_releases


@pytest.mark.parametrize("kind", ["hold_mouse", "hold_key"])
@pytest.mark.parametrize("failure", ["focus", "stop", "post", "watchdog"])
def test_interrupted_holds_release_primary_and_all_modifiers(kind, failure):
    driver = desktop()
    primary = "left_down" if kind == "hold_mouse" else "key_down"

    def interrupt(event):
        if event["kind"] != primary or (kind == "hold_key" and event["key"] != 124):
            return
        if failure == "focus":
            driver.check_focus.side_effect = RuntimeError("lost focus")
        elif failure == "post":
            raise RuntimeError("unknown post outcome")
        else:
            worker.STOPPING.set()
            if failure == "watchdog":
                driver.release_inputs()

    driver.q.after_post = interrupt
    action = dict(x=1, y=1, modifiers=["shift"]) if kind == "hold_mouse" else dict(keys=["shift", "right"])
    with pytest.raises(RuntimeError):
        act(driver, kind=kind, duration=2, **action)
    assert [event["kind"] for event in driver.q.events] == [
        "key_down",
        primary,
        "left_up" if kind == "hold_mouse" else "key_up",
        "key_up",
    ]
    assert driver.q.events[-1]["key"] == 56
    assert driver.input_events._release_event is None and not driver.input_events._modifier_releases
    assert driver.last is None


@pytest.mark.parametrize("failure", ["stop", "post", "focus"])
def test_interrupt_during_modifier_press_releases_it_without_posting_gesture(failure):
    driver = desktop()

    def interrupt(event):
        if event["kind"] == "key_down":
            if failure == "stop":
                worker.STOPPING.set()
            elif failure == "focus":
                driver.check_focus.side_effect = RuntimeError("lost focus")
            else:
                raise RuntimeError("unknown modifier post")

    driver.q.after_post = interrupt
    with pytest.raises(RuntimeError):
        act(driver, kind="move", x=1, y=1, modifiers=["shift", "cmd"])
    assert [event["kind"] for event in driver.q.events] == ["key_down", "key_up"]
    assert not driver.input_events._modifier_releases


def test_failed_release_does_not_skip_other_keys_and_cleanup_retries_only_pending():
    driver = desktop()

    def fail_release(event):
        if event["kind"] == "left_up" or (event["kind"] == "key_up" and event["key"] == 56):
            raise RuntimeError("unknown release outcome")

    driver.q.after_post = fail_release
    with pytest.raises(RuntimeError, match="release failed"):
        act(driver, kind="hold_mouse", x=1, y=1, modifiers=["ctrl", "shift"])
    assert driver.q.events[-1] == dict(kind="key_up", key=59, flags=0)
    assert driver.input_events._release_event is not None
    assert len(driver.input_events._modifier_releases) == 1
    before = len(driver.q.events)
    with pytest.raises(RuntimeError, match="not been released"):
        driver.input_events.move((1, 1))
    assert len(driver.q.events) == before
    worker.STOPPING.set()
    driver.q.after_post = lambda _: None
    driver.release_inputs()
    assert [event["kind"] for event in driver.q.events[before:]] == ["left_up", "key_up"]
    assert driver.q.events[-1]["key"] == 56
    driver.release_inputs()
    assert len(driver.q.events) == before + 2


def test_held_editing_chord_checks_editability_before_pressing_any_modifier():
    driver = desktop()
    driver.settings = driver.settings.model_copy(update={"accessibility_enabled": True})
    driver.last = replace(driver.last, accessibility_status="available")
    driver.accessibility = Mock()
    driver.accessibility.validate_typing.side_effect = PermissionError("not editable")
    with pytest.raises(PermissionError, match="not editable"):
        act(driver, kind="hold_key", keys=["cmd", "v"])
    assert not driver.q.events


def test_held_chord_rechecks_focus_after_modifier_before_main_key():
    driver = desktop()

    def change_focus(event):
        if event["kind"] == "key_down":
            driver.check_focus.side_effect = RuntimeError("lost focus after modifier")

    driver.q.after_post = change_focus
    with pytest.raises(RuntimeError, match="lost focus"):
        act(driver, kind="hold_key", keys=["shift", "right"])
    assert driver.q.events == [dict(kind="key_down", key=56, flags=8), dict(kind="key_up", key=56, flags=0)]


@pytest.mark.parametrize(
    "point,allowed,expected",
    [
        ((-1000, 190), True, {"x": 400.0, "y": 300.0}),
        ((-1200, 40), True, {"x": 0.0, "y": 0.0}),
        ((-400, 40), True, None),
        ((-1200, 640), True, None),
        ((-1300, 0), True, None),
        ((-1000, 190), False, None),
        ((float("nan"), 40), True, None),
    ],
)
def test_cursor_reports_scaled_pixels_only_on_permitted_surface(point, allowed, expected):
    driver = desktop()
    driver.q.CGEventCreate = lambda _: object()
    driver.q.CGEventGetLocation = lambda _: SimpleNamespace(x=point[0], y=point[1])
    driver.point_is_target.return_value = allowed
    assert driver.cursor_position(driver.last) == expected
    assert not driver.q.events


def test_unavailable_cursor_metadata_is_null_without_posting_input():
    driver = desktop()
    assert driver.cursor_position(driver.last) is None
    driver.q.CGEventCreate = lambda _: None
    assert driver.cursor_position(driver.last) is None
    assert not driver.q.events


def test_navigation_observe_adds_cursor_without_posting_input():
    driver = desktop()
    driver.navigation = Mock()
    driver.q.CGEventCreate = lambda _: object()
    driver.q.CGEventGetLocation = lambda _: SimpleNamespace(x=-1000, y=190)
    result = driver.observe()
    driver.navigation.observe.assert_called_once_with()
    assert result["cursor"] == driver.last.cursor == {"x": 400.0, "y": 300.0}
    assert not driver.q.events


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action",
    [
        dict(operation="move", x=1, y=1),
        dict(operation="hold_mouse", x=1, y=1, modifiers=["shift"]),
        dict(operation="hold_key", keys=["ctrl", "right"]),
    ],
)
async def test_new_tool_operations_require_fresh_owner_token_and_return_cursor(action):
    from test_direct import setup

    tool, state, driver, _ = setup()
    owner = Session("owner")
    original_observe = driver.observe

    async def observe_with_cursor():
        return replace(await original_observe(), cursor={"x": 1.0, "y": 1.0})

    driver.observe = observe_with_cursor
    try:
        initial = await tool.invoke({"operation": "observe"}, session=owner)
        assert initial.data["observation"]["cursor"] == {"x": 1.0, "y": 1.0}
        token = state.token
        rejected = await tool.invoke({**action, "observation_id": token}, session=Session("other"))
        assert not rejected.success and not driver.actions
        good = await tool.invoke({**action, "observation_id": token}, session=owner)
        assert good.success and state.token != token and len(driver.actions) == 1
        assert good.data["observation"]["cursor"] == {"x": 1.0, "y": 1.0}
        stale = await tool.invoke({**action, "observation_id": token}, session=owner)
        assert not stale.success and len(driver.actions) == 1
    finally:
        await tool.close()
