"""Real input implementation against fake Quartz; no desktop access or inference."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openjiuwen.core.session.agent import Session
from openjiuwen.harness.tools.desktop import worker
from openjiuwen.harness.tools.desktop.direct_protocol import decode_call, input_schema
from openjiuwen.harness.tools.desktop.macos_input import KEY_CODES, InputEvents
from openjiuwen.harness.tools.desktop.schema import Action, DesktopSettings, Observation
from openjiuwen.harness.tools.desktop.shortcuts import SUPPORTED_KEYS


class Quartz:
    """Preserve event data and order so assertions check emitted native semantics."""

    kCGHIDEventTap = "hid"
    kCGMouseButtonLeft, kCGMouseButtonRight, kCGMouseButtonCenter = 0, 1, 2
    kCGEventLeftMouseDown, kCGEventLeftMouseUp, kCGEventLeftMouseDragged = "left_down", "left_up", "left_drag"
    kCGEventRightMouseDown, kCGEventRightMouseUp, kCGEventRightMouseDragged = "right_down", "right_up", "right_drag"
    kCGEventOtherMouseDown, kCGEventOtherMouseUp, kCGEventOtherMouseDragged = "middle_down", "middle_up", "middle_drag"
    kCGEventMouseMoved, kCGMouseEventClickState, kCGScrollEventUnitLine = "move", "count", "lines"
    kCGEventFlagMaskCommand, kCGEventFlagMaskControl = 1, 2
    kCGEventFlagMaskAlternate, kCGEventFlagMaskShift = 4, 8

    def __init__(self):
        self.events = []
        self.after_post = lambda _: None

    def CGEventCreateMouseEvent(self, source, kind, point, button):
        return dict(kind=kind, point=point, button=button)

    def CGEventCreateKeyboardEvent(self, source, key, down):
        return dict(kind="key_down" if down else "key_up", key=key)

    def CGEventCreateScrollWheelEvent(self, source, unit, axes, vertical, horizontal):
        return dict(kind="scroll", unit=unit, axes=axes, vertical=vertical, horizontal=horizontal)

    def CGEventSetIntegerValueField(self, event, name, value):
        event[name] = value

    def CGEventSetFlags(self, event, flags):
        event["flags"] = flags

    def CGEventKeyboardSetUnicodeString(self, event, length, text):
        event.update(text=text, utf16_length=length)

    def CGEventPost(self, tap, event):
        self.events.append(dict(event))
        self.after_post(event)


@pytest.fixture(autouse=True)
def no_time_or_native_input(monkeypatch):
    worker.STOPPING.clear()
    monkeypatch.setattr(InputEvents, "pause", lambda self, _: self.check_stop())
    monkeypatch.setattr(worker.time, "sleep", lambda _: None)
    yield
    worker.STOPPING.clear()


def desktop():
    driver = worker.NativeDesktop.__new__(worker.NativeDesktop)
    driver.settings = DesktopSettings()
    driver.q = Quartz()
    driver.last = Observation("", 1600, 1200, (-1200, 40, 800, 600), "target", 12, 1)
    driver.check_focus = Mock()
    driver.point_is_target = Mock(return_value=True)
    driver.window = Mock(
        return_value=(None, dict(kCGWindowNumber=12, kCGWindowBounds=dict(X=-1200, Y=40, Width=800, Height=600)))
    )
    return driver


def act(driver, **action):
    driver.act(Action(**action), {"generation": 1})
    return driver.q.events


def test_keyboard_text_posts_unicode_without_modifiers_and_checks_every_character():
    driver = desktop()
    driver.settings = DesktopSettings(accessibility_enabled=True, window_navigation=True)
    driver.last = replace(driver.last, accessibility_status="truncated")
    driver.navigation = SimpleNamespace(typed_keys=0)
    driver.accessibility = SimpleNamespace(validate_keyboard=Mock(), validate_typing=Mock())
    driver._perform_action(Action(kind="type", text="510*19+70=", input_mode="keyboard"))
    assert "".join(e["text"] for e in driver.q.events if e["kind"] == "key_down") == "510*19+70="
    assert all(e.get("flags", 0) == 0 for e in driver.q.events)
    assert driver.accessibility.validate_keyboard.call_count == 10
    driver.accessibility.validate_typing.assert_not_called()


@pytest.mark.parametrize("problem", ["changed_focus", "stop", "missing_ax", "missing_navigation"])
def test_keyboard_text_stops_without_replaying_or_leaving_held_keys(problem):
    driver = desktop()
    driver.settings = DesktopSettings(accessibility_enabled=True, window_navigation=True)
    driver.last = replace(driver.last, accessibility_status="available")
    driver.navigation = SimpleNamespace(typed_keys=0)
    driver.accessibility = SimpleNamespace(validate_keyboard=Mock())
    if problem == "changed_focus":
        driver.accessibility.validate_keyboard.side_effect = [None, RuntimeError("changed focus")]
    elif problem == "stop":
        driver.q.after_post = lambda event: worker.STOPPING.set() if event["kind"] == "key_up" else None
    elif problem == "missing_ax":
        driver.last = replace(driver.last, accessibility_status="unavailable")
    else:
        driver.navigation = None
    with pytest.raises(RuntimeError):
        driver._perform_action(Action(kind="type", text="ab", input_mode="keyboard"))
    driver.release_inputs()
    down = [e for e in driver.q.events if e["kind"] == "key_down"]
    up = [e for e in driver.q.events if e["kind"] == "key_up"]
    assert len(down) == len(up) == (1 if problem in {"changed_focus", "stop"} else 0)


def test_keyboard_input_mode_is_explicit_strict_and_transportable():
    operation, parsed = decode_call(
        {"operation": "type", "text": "abc+1=", "input_mode": "keyboard", "observation_id": "fresh"}
    )
    assert operation == "act" and Action.model_validate(parsed.model_dump()).input_mode == "keyboard"
    assert input_schema()["properties"]["input_mode"]["enum"] == ["editable", "keyboard"]
    assert Action(kind="type", text="normal\ntext").input_mode == "editable"
    for invalid in [
        dict(kind="click", x=1, y=2, input_mode="keyboard"),
        dict(kind="type", text="abc\t", input_mode="keyboard"),
        dict(kind="type", text="abc\n", input_mode="keyboard"),
        dict(kind="type", text="abc", input_mode="unknown"),
    ]:
        with pytest.raises(ValueError):
            Action(**invalid)


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize(
    "action",
    [
        dict(kind="click", x=4, y=8, button="right", click_count=2),
        dict(kind="drag", path=[[0, 0], [200, 100]], duration=0.5, button="middle"),
        dict(kind="scroll", x=4, y=8, direction="up", amount=7),
        dict(kind="shortcut", keys=["Control", "Option", "Shift", "Command", "ArrowLeft"]),
    ],
)
def test_new_operations_decode_and_survive_existing_full_dump_transport(action, legacy):
    body = (
        {"operation": "act", "action": action}
        if legacy
        else {"operation": action["kind"], **{k: v for k, v in action.items() if k != "kind"}}
    )
    operation, parsed = decode_call({**body, "observation_id": "fresh"})
    assert operation == "act"
    assert Action.model_validate_json(parsed.model_dump_json()) == parsed
    assert Action.model_validate(parsed.model_dump()) == parsed


@pytest.mark.parametrize(
    "action",
    [
        dict(kind="click", x=1, y=2, click_count=4),
        dict(kind="click", x=1, y=2, button="extra"),
        dict(kind="click", x=1, y=2, path=[[0, 0], [1, 1]]),
        dict(kind="drag", path=[[0, 0]]),
        dict(kind="drag", path=[[0, 0]] * 65),
        dict(kind="drag", path=[[0, 0], [1, float("inf")]]),
        dict(kind="drag", path=[[0, 0], [-1, 0]]),
        dict(kind="drag", path=[[0, 0], [1, 2, 3]]),
        dict(kind="drag", path=[[0, 0], [True, 0]]),
        dict(kind="drag", path=[[0, 0], [1, 1]], duration=0),
        dict(kind="drag", path=[[0, 0], [1, 1]], duration=3),
        dict(kind="drag", path=[[0, 0], [1, 1]], x=2, y=2),
        dict(kind="scroll", x=1),
        dict(kind="scroll", x=float("nan"), y=2),
        dict(kind="shortcut", keys=["cmd", "cmd", "s"]),
        dict(kind="shortcut", keys=["cmd", "a", "s"]),
    ],
)
def test_invalid_native_payloads_are_rejected(action):
    with pytest.raises(ValueError):
        Action(**action)


def test_model_schema_exposes_general_primitives_and_native_key_map_covers_vocabulary():
    schema = input_schema()
    assert "drag" in schema["properties"]["operation"]["enum"]
    assert schema["properties"]["click_count"]["maximum"] == 3
    assert schema["properties"]["path"]["maxItems"] == 64
    assert SUPPORTED_KEYS == set(KEY_CODES)


@pytest.mark.parametrize("button,number", [("left", 0), ("right", 1), ("middle", 2)])
@pytest.mark.parametrize("count", [1, 2, 3])
def test_click_event_pair_button_count_and_scaled_point(button, number, count):
    driver = desktop()
    events = act(driver, kind="click", x=400, y=300, button=button, click_count=count)
    assert [e["kind"] for e in events] == [f"{button}_down", f"{button}_up"] * count
    assert [e["count"] for e in events] == [index for index in range(1, count + 1) for _ in range(2)]
    assert all(e["button"] == number and e["point"] == (-1000, 190) for e in events)
    assert driver.last is None and driver.input_events._release_event is None


@pytest.mark.parametrize(
    "keys,keycode,flags",
    [
        (["ctrl", "alt", "shift", "cmd", "left"], 123, 15),
        (["ctrl", "a"], 0, 2),
        (["cmd", "a"], 0, 1),
        (["alt", "backspace"], 51, 4),
        (["cmd", "shift", "z"], 6, 9),
        (["f12"], 111, 0),
        (["delete"], 117, 0),
    ],
)
def test_key_chord_preserves_distinct_modifiers_and_releases_without_flags(keys, keycode, flags):
    events = act(desktop(), kind="shortcut", keys=keys)
    assert events == [dict(kind="key_down", key=keycode, flags=flags), dict(kind="key_up", key=keycode, flags=0)]


def test_unicode_typing_preserves_non_bmp_text_tabs_and_final_newline():
    events = act(desktop(), kind="type", text="é🙂\t\n")
    assert [e.get("text") for e in events[::2]] == ["é", "🙂", None, None]
    assert events[2]["utf16_length"] == 2
    assert [e["key"] for e in events[-4::2]] == [48, 36]
    assert all(e["kind"] == "key_up" and e["flags"] == 0 for e in events[1::2])


@pytest.mark.parametrize(
    "direction,vertical,horizontal", [("up", 4, 0), ("down", -4, 0), ("left", 0, 4), ("right", 0, -4)]
)
def test_positioned_scroll_uses_requested_pane(direction, vertical, horizontal):
    events = act(desktop(), kind="scroll", x=400, y=300, direction=direction, amount=4)
    assert events[0]["point"] == (-1000, 190)
    assert events[1] == dict(kind="scroll", unit="lines", axes=2, vertical=vertical, horizontal=horizontal, flags=0)


@pytest.mark.parametrize("button", ["left", "right", "middle"])
def test_drag_interpolates_in_desktop_coordinates_and_releases_at_destination(button):
    driver = desktop()
    events = act(driver, kind="drag", button=button, path=[[0, 0], [160, 0], [160, 160]], duration=0.2)
    assert events[0]["kind"] == f"{button}_down" and events[0]["point"] == (-1200, 40)
    assert events[-1]["kind"] == f"{button}_up" and events[-1]["point"] == (-1120, 120)
    assert all(e["kind"] == f"{button}_drag" for e in events[1:-1])
    # Preflight the recipient before modifiers, then recheck before every down/motion.
    assert [call.args[0] for call in driver.point_is_target.call_args_list] == [events[0]["point"]] + [
        event["point"] for event in events[:-1]
    ]
    assert driver.last is None and driver.input_events._release_event is None


@pytest.mark.parametrize(
    "action",
    [
        dict(kind="drag", path=[[0, 0], [1600, 5]]),
        dict(kind="drag", path=[[0, 0], [1599, 1199]] * 32),
        dict(kind="scroll", x=1600, y=5),
        dict(kind="click", x=1600, y=5),
    ],
)
def test_out_of_image_or_excessively_long_path_posts_nothing(action):
    driver = desktop()
    with pytest.raises(ValueError):
        act(driver, **action)
    assert driver.q.events == []


@pytest.mark.parametrize("failure", ["stop", "focus", "occlusion", "post"])
def test_drag_failure_releases_last_attempted_point_and_never_moves_again(failure):
    driver = desktop()

    def interrupt(event):
        if event["kind"] == "left_drag":
            if failure == "stop":
                worker.STOPPING.set()
            elif failure == "focus":
                driver.check_focus.side_effect = RuntimeError("focus lost")
            elif failure == "occlusion":
                driver.point_is_target.return_value = False
            else:
                raise RuntimeError("post outcome unknown")

    driver.q.after_post = interrupt
    with pytest.raises(RuntimeError):
        act(driver, kind="drag", path=[[0, 0], [160, 0]], duration=0.3)
    assert [e["kind"] for e in driver.q.events] == ["left_down", "left_drag", "left_up"]
    assert driver.q.events[-1]["point"] == driver.q.events[-2]["point"]
    assert driver.input_events._release_event is None
    assert driver.last is None
    with pytest.raises(RuntimeError, match="stale"):
        act(driver, kind="drag", path=[[0, 0], [160, 0]])
    assert len(driver.q.events) == 3


@pytest.mark.parametrize("kind", ["click", "shortcut"])
def test_down_post_failure_still_sends_release(kind):
    driver = desktop()
    driver.q.after_post = lambda e: (
        (_ for _ in ()).throw(RuntimeError("unknown post")) if e["kind"].endswith("down") else None
    )
    with pytest.raises(RuntimeError):
        act(driver, **(dict(kind="click", x=1, y=1) if kind == "click" else dict(kind="shortcut", keys=["cmd", "s"])))
    assert len(driver.q.events) == 2 and driver.q.events[-1]["kind"].endswith("up")
    assert driver.input_events._release_event is None


def test_stop_after_first_click_prevents_second_click_but_finishes_release():
    driver = desktop()
    driver.q.after_post = lambda _: worker.STOPPING.set()
    with pytest.raises(RuntimeError, match="stopping"):
        act(driver, kind="click", x=1, y=1, click_count=2)
    assert [e["kind"] for e in driver.q.events] == ["left_down", "left_up"]


def test_watchdog_release_can_run_before_drag_unwinds_without_double_release():
    driver = desktop()

    def stop_and_release(event):
        if event["kind"] == "left_drag":
            worker.STOPPING.set()
            driver.release_inputs()

    driver.q.after_post = stop_and_release
    with pytest.raises(RuntimeError):
        act(driver, kind="drag", path=[[0, 0], [160, 0]])
    assert [e["kind"] for e in driver.q.events] == ["left_down", "left_drag", "left_up"]


def test_failed_release_is_retained_for_cleanup_retry_without_another_press():
    driver = desktop()

    def fail_release(event):
        if event["kind"] == "left_up":
            raise RuntimeError("release outcome unknown")

    driver.q.after_post = fail_release
    with pytest.raises(RuntimeError, match="release outcome"):
        act(driver, kind="click", x=1, y=1)
    assert driver.input_events._release_event is not None
    worker.STOPPING.set()
    driver.q.after_post = lambda _: None
    driver.release_inputs()
    assert driver.input_events._release_event is None
    assert [event["kind"] for event in driver.q.events] == ["left_down", "left_up", "left_up"]


@pytest.mark.asyncio
async def test_direct_drag_returns_fresh_observation_and_bad_payload_does_not_consume_token():
    from test_direct import setup

    tool, state, driver, _ = setup()
    owner = Session("owner")
    try:
        await tool.invoke({"operation": "observe"}, session=owner)
        token = state.token
        bad = await tool.invoke({"operation": "drag", "path": [[0, 0]], "observation_id": token}, session=owner)
        assert not bad.success and bad.data["executed"] is False and state.token == token
        assert not driver.actions
        good = await tool.invoke(
            {"operation": "drag", "path": [[0, 0], [1, 1]], "observation_id": token}, session=owner
        )
        assert good.success and state.token != token and len(driver.actions) == 1
        assert driver.actions[0].kind == "drag"
    finally:
        await tool.close()
