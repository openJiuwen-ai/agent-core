"""Opened-modal permission is opt-in and never expands to unrelated app windows."""

from unittest.mock import Mock

import pytest
from test_window_navigation import NativeRig, row

from openjiuwen.harness.tools.desktop import worker
from openjiuwen.harness.tools.desktop.macos_navigation import MacOSNavigation
from openjiuwen.harness.tools.desktop.schema import Action, Target


@pytest.fixture
def rig():
    worker.STOPPING.clear()
    rig = NativeRig()
    rig.rows.pop(1)  # This process initially has one explicitly shared document.
    rig.d.targets["editor"] = Target(name="editor", pid=10, window_ids=[1], share_opened_dialogs=True)
    rig.modals, rig.hidden = set(), []
    original = rig.attribute

    def attribute(element, name, placeholder):
        if isinstance(element, tuple) and name == "AXWindows":
            status, windows = original(element, name, placeholder)
            return status, windows + rig.hidden if element[1] == 10 else windows
        if isinstance(element, int):
            if name == "AXSubrole":
                return 0, "AXDialog" if element in rig.modals else "AXStandardWindow"
            if name == "AXModal":
                return 0, element in rig.modals
        return original(element, name, placeholder)

    def pid(element, placeholder):
        if isinstance(element, int):
            found = next((r for r in rig.rows if r["kCGWindowNumber"] == element), None)
            return (0, found["kCGWindowOwnerPID"]) if found else (-25202, None)
        return (0, element[1]) if isinstance(element, tuple) else (0, rig.hit_pid)

    rig.d.ax.AXUIElementCopyAttributeValue = attribute
    rig.d.ax.AXUIElementGetPid = pid
    rig.d.navigation = MacOSNavigation(rig.d)
    rig.d.navigation.capture_window = rig.capture
    yield rig
    worker.STOPPING.clear()


def add_modal(rig, number=4):
    rig.rows.insert(0, row(number, bounds=(40 + number, 45, 90, 80), title="Dialog", layer=8))
    rig.modals.add(number)
    rig.focused[10] = number


def open_modal(rig, number=4):
    observed = rig.observe()
    rig.d._perform_action = lambda _: add_modal(rig, number)
    rig.act(Action(kind="shortcut", keys=["cmd", "s"]), observed)
    return rig.observe()


def test_flag_is_host_only_and_disabled_by_default():
    assert Target(name="editor", pid=10, window_ids=[1]).share_opened_dialogs is False
    with pytest.raises(ValueError):
        Action(kind="shortcut", keys=["cmd", "s"], share_opened_dialogs=True)


@pytest.mark.parametrize("enabled", [False, True])
def test_only_opted_in_new_modal_can_be_captured_and_receive_input(rig, enabled):
    rig.d.targets["editor"] = rig.d.targets["editor"].model_copy(update={"share_opened_dialogs": enabled})
    observed = open_modal(rig)
    assert (4 in rig.captures) is enabled
    assert bool(observed.context["active_window_id"]) is enabled
    rig.d._perform_action = Mock()
    if enabled:
        rig.act(Action(kind="shortcut", keys=["escape"]), observed)
        rig.d._perform_action.assert_called_once()
    else:
        with pytest.raises(RuntimeError, match="not shared"):
            rig.act(Action(kind="shortcut", keys=["escape"]), observed)
        rig.d._perform_action.assert_not_called()


@pytest.mark.parametrize(
    "problem",
    [
        "preexisting",
        "private",
        "hidden",
        "ordinary",
        "background",
        "duplicate",
        "expired",
        "read_failure",
        "failed_action",
        "multiple_roots",
    ],
)
def test_uncertain_or_unrelated_window_never_inherits_permission(rig, problem, monkeypatch):
    if problem == "preexisting":
        add_modal(rig)
        rig.focused[10] = 1
    if problem in {"private", "multiple_roots"}:
        rig.rows.append(row(2, bounds=(200, 30, 150, 100), title="Private"))
    if problem == "multiple_roots":
        rig.d.targets["editor"] = rig.d.targets["editor"].model_copy(update={"window_ids": [1, 2]})
    if problem == "hidden":
        rig.hidden = [99]
    observed = rig.observe()

    def perform(_):
        if problem != "preexisting":
            add_modal(rig)
        if problem == "ordinary":
            rig.modals.remove(4)
        if problem == "background":
            rig.front = 20
        if problem == "duplicate":
            rig.rows.append(row(5, bounds=(44, 45, 90, 80)))
        if problem == "failed_action":
            raise RuntimeError("partial input")

    rig.d._perform_action = perform
    if problem == "failed_action":
        with pytest.raises(RuntimeError, match="partial input"):
            rig.act(Action(kind="shortcut", keys=["cmd", "s"]), observed)
    else:
        rig.act(Action(kind="shortcut", keys=["cmd", "s"]), observed)
    if problem == "expired":
        rig.d.navigation.dialogs.pending["deadline"] = 0
    if problem == "read_failure":
        monkeypatch.setattr(rig.d.navigation.dialogs, "scan", lambda *_: None)
    rig.observe()
    assert (10, 4) not in rig.d.navigation.catalog.shared
    assert 4 not in rig.captures


def test_observe_does_not_adopt_modal_without_completed_input(rig):
    rig.observe()
    add_modal(rig)
    rig.observe()
    assert 4 not in rig.captures


@pytest.mark.parametrize("transient", ["busy", "geometry"])
@pytest.mark.parametrize("busy_scans", [1, 5, 100])
def test_brief_ax_busy_during_opening_retries_observation_without_input_replay(rig, monkeypatch, transient, busy_scans):
    observed = rig.observe()
    rig.d._perform_action = Mock(side_effect=lambda _: add_modal(rig))
    rig.act(Action(kind="shortcut", keys=["cmd", "s"]), observed)
    policy = rig.d.navigation.dialogs
    scan = policy.scan
    calls = 0
    now = [policy.pending["deadline"] - 2]
    monkeypatch.setattr("openjiuwen.harness.tools.desktop.macos_navigation.time.monotonic", lambda: now[0])
    monkeypatch.setattr(rig.d._inputs(), "pause", lambda seconds: now.__setitem__(0, now[0] + seconds))

    def busy_once(*args):
        nonlocal calls
        calls += 1
        if calls <= busy_scans:
            assert not rig.captures
            policy.scan_failure = (
                {"reason": "window enumeration unavailable", "ax_failure": ("AXWindows", -25204)}
                if transient == "busy"
                else {"reason": "AX/native window geometry not yet matched"}
            )
            return None
        return scan(*args)

    monkeypatch.setattr(policy, "scan", busy_once)
    rig.captures.clear()
    if busy_scans == 100:
        with pytest.raises(RuntimeError, match="opening modal window metadata"):
            rig.observe()
        assert not rig.captures and not policy.grants and calls < busy_scans
        rig.d._perform_action.assert_called_once()
        return
    result = rig.observe()
    assert calls > busy_scans and result.context["active_window_id"] == rig.handle(4)
    assert 4 in rig.captures
    rig.d._perform_action.assert_called_once()


@pytest.mark.parametrize("kind", ["type", "wait", "focus"])
def test_non_trigger_actions_never_arm_permission(rig, kind):
    rig.observe()
    fields = {"text": "abc"} if kind == "type" else {"target": "editor"} if kind == "focus" else {}
    action = Action(kind=kind, **fields)
    assert rig.d.navigation.dialogs.before_action(action) is None


def test_nested_new_modals_require_a_fresh_action_and_revoke_with_parent(rig):
    open_modal(rig)
    open_modal(rig, 5)
    assert {(10, 1), (10, 4), (10, 5)} <= rig.d.navigation.catalog.shared
    rig.rows = [r for r in rig.rows if r["kCGWindowNumber"] != 4]
    rig.observe()
    assert not rig.d.navigation.dialogs.grants


@pytest.mark.parametrize("change", ["private", "root_removed", "root_missing_ax", "nonmodal", "identity", "disabled"])
def test_grants_are_revalidated_and_revoked_before_input(rig, change, monkeypatch):
    observed = open_modal(rig)
    if change == "private":
        rig.rows.append(row(6, bounds=(200, 30, 150, 100)))
    elif change == "root_removed":
        rig.rows = [r for r in rig.rows if r["kCGWindowNumber"] != 1]
    elif change == "root_missing_ax":
        scan = rig.d.navigation.dialogs.scan
        monkeypatch.setattr(
            rig.d.navigation.dialogs,
            "scan",
            lambda *args: {key: value for key, value in scan(*args).items() if key != (10, 1)},
        )
    elif change == "nonmodal":
        rig.modals.clear()
    elif change == "identity":
        rig.d.navigation.reader.equal = lambda *_: False
    else:
        rig.d.targets["editor"] = rig.d.targets["editor"].model_copy(update={"share_opened_dialogs": False})
    rig.d._perform_action = Mock()
    with pytest.raises(RuntimeError, match="observe again"):
        rig.act(Action(kind="shortcut", keys=["escape"]), observed)
    rig.d._perform_action.assert_not_called()
    assert not rig.d.navigation.dialogs.grants


def test_dialog_closed_and_reappearing_is_not_regranted(rig):
    open_modal(rig)
    rig.rows = [r for r in rig.rows if r["kCGWindowNumber"] != 4]
    rig.focused[10] = 1
    rig.observe()
    add_modal(rig)
    rig.captures.clear()
    rig.observe()
    assert 4 not in rig.captures


def test_unenumerated_native_document_prevents_modal_grant(rig, monkeypatch):
    rig.rows.append(row(6, bounds=(200, 30, 150, 100)))
    original = rig.d.ax.AXUIElementCopyAttributeValue

    def missing(element, name, placeholder):
        status, value = original(element, name, placeholder)
        return (status, [w for w in value if w != 6]) if name == "AXWindows" else (status, value)

    monkeypatch.setattr(rig.d.ax, "AXUIElementCopyAttributeValue", missing)
    open_modal(rig)
    assert 4 not in rig.captures


def test_revocation_during_capture_discards_dialog_pixels(rig):
    open_modal(rig)
    capture = rig.d.navigation.capture_window

    def changed(native_id):
        image = capture(native_id)
        if native_id == 4:
            rig.modals.clear()
        return image

    rig.d.navigation.capture_window = changed
    observation = rig.observe()
    assert observation.context["active_window_id"] is None
    assert not rig.d.navigation.dialogs.grants
    assert all(w["kind"] != "dialog" for w in observation.context["windows"])
