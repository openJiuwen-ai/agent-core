"""Target selection contracts with fake AX references; no native input or models."""

import json
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from openjiuwen.harness.tools.desktop import accessibility as module
from openjiuwen.harness.tools.desktop.accessibility import AccessibilityTree
from openjiuwen.harness.tools.desktop.schema import Action, DesktopSettings, Observation
from openjiuwen.harness.tools.desktop.worker import NativeDesktop


class FakeAX:
    kAXValueCGPointType = 1
    kAXValueCGSizeType = 2

    def __init__(self):
        self.nodes = {
            "application": {"AXWindows": ["window"]},
            "window": {
                "AXRole": "AXWindow",
                "AXTitle": "test",
                "AXPosition": NS(x=10, y=20),
                "AXSize": NS(width=700, height=480),
                "AXChildren": ["button", "text"],
            },
            "button": {
                "AXRole": "AXButton",
                "AXTitle": "Calculate",
                "AXPosition": NS(x=40, y=60),
                "AXSize": NS(width=80, height=30),
                "AXEnabled": True,
                "AXFocused": False,
                "AXWindow": "window",
                "AXParent": "window",
                "pid": 100,
            },
            "text": {
                "AXRole": "AXStaticText",
                "AXValue": "Not calculated",
                "AXPosition": NS(x=40, y=100),
                "AXSize": NS(width=100, height=20),
                "AXWindow": "window",
                "AXParent": "window",
                "pid": 100,
            },
        }
        self.reads = []
        self.hit = "button"

    def AXUIElementCopyAttributeValue(self, element, name, unused):
        self.reads.append((element, name))
        return (0, self.nodes[element][name]) if name in self.nodes[element] else (-25205, None)

    def AXUIElementGetPid(self, element, unused):
        return 0, self.nodes[element].get("pid", 100)

    def AXValueGetValue(self, value, kind, unused):
        return True, value

    def AXUIElementCopyElementAtPosition(self, system, x, y, unused):
        return 0, self.hit

    def AXUIElementCreateApplication(self, pid):
        return "application"

    def AXUIElementCreateSystemWide(self):
        return "system"

    def AXUIElementSetMessagingTimeout(self, application, timeout):
        return 0

    def AXUIElementIsAttributeSettable(self, element, name, unused):
        if name == "AXFocused":
            return 0, self.nodes[element].get("focusable", self.nodes[element].get("editable", False))
        return 0, self.nodes[element].get("editable", False)


def projected():
    ax = FakeAX()
    tree = AccessibilityTree(ax)
    obs = Observation("", 1400, 960, (10, 20, 700, 480), "test", 12, 1)
    elements, status = tree.snapshot(NS(processIdentifier=lambda: 100), obs, window_title="test")
    return ax, tree, replace(obs, elements=elements, accessibility_status=status)


@pytest.mark.parametrize("focused", [None, "window", "text", "button"])
def test_keyboard_text_accepts_stable_noneditable_focus_in_exact_window(focused):
    ax, tree, obs = projected()
    ax.nodes["application"].update(AXFocusedWindow="window", AXFocusedUIElement=focused)
    elements, status = tree.snapshot(NS(processIdentifier=lambda: 100), obs)
    obs = replace(obs, elements=elements, accessibility_status=status)
    tree.validate_keyboard(obs)
    ax.nodes["text"]["AXValue"] = "A changed display is not a changed keyboard recipient"
    tree.validate_keyboard(obs)
    with pytest.raises(RuntimeError, match="observed window"):
        tree.validate_keyboard(replace(obs, generation=99))
    tree.clear()
    with pytest.raises(RuntimeError, match="observed window"):
        tree.validate_keyboard(obs)


@pytest.mark.parametrize("change", ["focus", "window", "geometry", "protected", "editor", "foreign", "hidden"])
def test_keyboard_text_rejects_changed_or_unsafe_recipient(change):
    ax, tree, obs = projected()
    ax.nodes["application"].update(AXFocusedWindow="window", AXFocusedUIElement="text")
    tree.snapshot(NS(processIdentifier=lambda: 100), obs)
    tree.validate_keyboard(obs)
    if change == "focus":
        ax.nodes["application"]["AXFocusedUIElement"] = "button"
    elif change == "window":
        ax.nodes["application"]["AXFocusedWindow"] = "other"
    elif change == "geometry":
        ax.nodes["window"]["AXPosition"] = NS(x=11, y=20)
    elif change == "protected":
        ax.nodes["text"]["AXProtectedContent"] = True
    elif change == "editor":
        ax.nodes["text"]["AXRole"] = "AXTextArea"
    elif change == "foreign":
        ax.nodes["text"]["pid"] = 999
    else:
        ax.nodes["text"]["AXHidden"] = True
    with pytest.raises(RuntimeError):
        tree.validate_keyboard(obs)


def test_keyboard_text_never_bypasses_an_unfocused_editor():
    ax, tree, obs = projected()
    ax.nodes["application"].update(AXFocusedWindow="window", AXFocusedUIElement="window")
    ax.nodes["text"].update(AXRole="AXTextArea", editable=True, AXFocused=False, AXEnabled=True)
    tree.snapshot(NS(processIdentifier=lambda: 100), obs)
    with pytest.raises(RuntimeError, match="observed window"):
        tree.validate_keyboard(obs)


def test_keyboard_focus_validation_does_not_depend_on_unrelated_quarantined_controls(monkeypatch):
    ax, tree, obs = projected()
    ax.nodes["application"].update(AXFocusedWindow="window", AXFocusedUIElement="text")
    original = ax.AXUIElementCopyAttributeValue

    def read(element, name, unused):
        if element == "button" and name == "AXSubrole":
            return -25200, None
        return original(element, name, unused)

    monkeypatch.setattr(ax, "AXUIElementCopyAttributeValue", read)
    _, status = tree.snapshot(NS(processIdentifier=lambda: 100), obs)
    assert status == "truncated"
    tree.validate_keyboard(obs)
    ax.nodes["application"]["AXFocusedUIElement"] = "button"
    with pytest.raises(RuntimeError):
        tree.validate_keyboard(obs)


def test_keyboard_text_rechecks_required_attributes_and_secure_ancestors(monkeypatch):
    ax, tree, obs = projected()
    ax.nodes["application"].update(AXFocusedWindow="window", AXFocusedUIElement="text")
    tree.snapshot(NS(processIdentifier=lambda: 100), obs)
    original = ax.AXUIElementCopyAttributeValue
    monkeypatch.setattr(
        ax,
        "AXUIElementCopyAttributeValue",
        lambda element, name, unused: (-25200, None) if name == "AXFocusedWindow" else original(element, name, unused),
    )
    with pytest.raises(RuntimeError):
        tree.validate_keyboard(obs)
    monkeypatch.setattr(ax, "AXUIElementCopyAttributeValue", original)
    ax.nodes["window"]["AXProtectedContent"] = True
    with pytest.raises(RuntimeError):
        tree.validate_keyboard(obs)


def test_display_projection_keeps_exact_window_reference_for_click_and_typing():
    ax = FakeAX()
    ax.nodes["button"].update(AXRole="AXTextField", AXFocused=True, editable=True)
    ax.nodes["application"]["AXFocusedUIElement"] = "button"
    tree = AccessibilityTree(ax)
    obs = Observation("", 1280, 800, (0, 0, 1280, 800), "test", 0, 1)
    elements, status = tree.snapshot(NS(processIdentifier=lambda: 100), obs, window_bounds=(10, 20, 700, 480))
    assert status == "available"
    assert elements[0]["bounds"] == [40, 60, 80, 30]
    assert tree.resolve(elements[0]["id"], obs) == (80, 75)
    tree.validate_typing(obs)
    ax.nodes["window"]["AXPosition"] = NS(x=11, y=20)
    with pytest.raises(RuntimeError, match="another window"):
        tree.resolve(elements[0]["id"], obs)


def test_snapshot_projects_visible_ui_without_native_references():
    ax, tree, obs = projected()
    assert obs.accessibility_status == "available" and len(obs.elements) == 2
    button, text = obs.elements
    assert button["bounds"] == [60, 80, 160, 60] and button["actionable"] is True
    assert text["value"] == "Not calculated" and text["actionable"] is False
    assert all(isinstance(item["id"], str) for item in obs.elements)
    assert "button" not in json.dumps(obs.elements)
    assert tree.resolve(button["id"], obs) == (80, 75)
    assert ("button", "AXValue") in ax.reads  # actual UI attribute, no fixture receipt


def test_unreadable_security_metadata_quarantines_only_its_subtree():
    ax = FakeAX()
    ax.nodes["button"]["AXChildren"] = ["secret"]
    ax.nodes["secret"] = {"AXRole": "AXStaticText", "AXValue": "must not read"}
    original = ax.AXUIElementCopyAttributeValue

    def broken(element, name, unused):
        if element == "button" and name == "AXSubrole":
            ax.reads.append((element, name))
            return -25200, None
        return original(element, name, unused)

    ax.AXUIElementCopyAttributeValue = broken
    tree = AccessibilityTree(ax)
    obs = Observation("", 1400, 960, (10, 20, 700, 480), "test", 12, 1)
    elements, status = tree.snapshot(NS(processIdentifier=lambda: 100), obs)
    assert status == "truncated" and [e["role"] for e in elements] == ["AXStaticText"]
    assert ("button", "AXValue") not in ax.reads
    assert not any(element == "secret" for element, _ in ax.reads)


def test_missing_window_attribute_uses_exact_parent_chain_for_typing():
    ax = FakeAX()
    ax.nodes["button"].pop("AXWindow")
    ax.nodes["button"].update(AXRole="AXTextField", editable=True, AXParent="group")
    ax.nodes["group"] = {"AXRole": "AXSplitGroup", "AXParent": "window", "pid": 100}
    ax.nodes["application"]["AXFocusedUIElement"] = "button"
    tree = AccessibilityTree(ax)
    obs = Observation("", 700, 480, (10, 20, 700, 480), "test", 12, 1)
    elements, status = tree.snapshot(NS(processIdentifier=lambda: 100), obs)
    assert status == "available" and elements[0]["editable"]
    tree.validate_typing(obs)
    ax.nodes["group"]["AXParent"] = "application"
    with pytest.raises(RuntimeError):
        tree.validate_typing(obs)


def test_missing_application_focus_requires_unique_positive_editable_focus():
    ax = FakeAX()
    ax.nodes["button"].update(AXRole="AXTextArea", AXFocused=True, editable=True)
    tree = AccessibilityTree(ax)
    obs = Observation("", 700, 480, (10, 20, 700, 480), "test", 12, 1)
    elements, status = tree.snapshot(NS(processIdentifier=lambda: 100), obs)
    assert status == "available" and elements[0]["focused"]
    tree.validate_typing(obs)
    ax.nodes["button"]["AXFocused"] = False
    with pytest.raises(RuntimeError, match="focus changed"):
        tree.validate_typing(obs)
    ax.nodes["button"]["AXFocused"] = True
    ax.nodes["application"]["AXFocusedUIElement"] = "text"
    with pytest.raises(RuntimeError, match="focus changed"):
        tree.validate_typing(obs)


def test_mismatched_center_is_not_advertised_as_clickable_but_preserves_typing():
    ax = FakeAX()
    ax.nodes["button"].update(AXRole="AXTextArea", AXFocused=True, editable=True)
    ax.hit = "text"
    tree = AccessibilityTree(ax)
    obs = Observation("", 700, 480, (10, 20, 700, 480), "test", 12, 1)
    elements, status = tree.snapshot(NS(processIdentifier=lambda: 100), obs)
    assert status == "available" and not elements[0]["actionable"] and elements[0]["editable"]
    tree.validate_typing(obs)
    with pytest.raises(RuntimeError, match="occluded"):
        tree.resolve(elements[0]["id"], obs)


def test_ambiguous_element_focus_never_substitutes_for_missing_application_focus():
    ax = FakeAX()
    for name in ("button", "text"):
        ax.nodes[name].update(AXRole="AXTextArea", AXFocused=True, AXEnabled=True, editable=True)
    tree = AccessibilityTree(ax)
    obs = Observation("", 700, 480, (10, 20, 700, 480), "test", 12, 1)
    tree.snapshot(NS(processIdentifier=lambda: 100), obs)
    with pytest.raises(RuntimeError, match="no observed editable"):
        tree.validate_typing(obs)


@pytest.mark.parametrize(
    "changes",
    [
        {"AXParent": "button"},
        {"AXParent": None},
        {"pid": 101},
        {"AXSubrole": "AXSecureTextField"},
    ],
)
def test_missing_window_attribute_rejects_unproven_or_secure_ancestry(changes):
    ax = FakeAX()
    ax.nodes["button"].pop("AXWindow")
    ax.nodes["button"]["AXParent"] = "group"
    ax.nodes["group"] = {"AXRole": "AXGroup", "AXParent": "window", **changes}
    tree = AccessibilityTree(ax)
    obs = Observation("", 700, 480, (10, 20, 700, 480), "test", 12, 1)
    elements, _ = tree.snapshot(NS(processIdentifier=lambda: 100), obs)
    assert all(item["role"] != "AXButton" for item in elements)
    assert ("button", "AXValue") not in ax.reads


def test_pixel_and_element_forms_are_exclusive_and_default_is_unchanged():
    assert not DesktopSettings().accessibility_enabled
    assert Action(kind="click", x=1, y=2).element_id == ""
    assert Action(kind="click", element_id="1-observation-0").x is None
    for data in (
        {"kind": "click", "element_id": "1-x", "x": 1, "y": 2},
        {"kind": "click", "element_id": "1-x", "x": 1},
        {"kind": "type", "element_id": "1-x", "text": "x"},
        {"kind": "click"},
    ):
        with pytest.raises(ValueError):
            Action(**data)


def test_ids_expire_even_when_ui_is_unchanged():
    _, tree, old = projected()
    identifier = old.elements[0]["id"]
    tree.snapshot(NS(processIdentifier=lambda: 100), replace(old, generation=2), window_title="test")
    with pytest.raises(RuntimeError, match="stale"):
        tree.resolve(identifier, old)
    with pytest.raises(RuntimeError, match="stale"):
        tree.resolve(identifier, replace(old, generation=2))
    tree.clear()
    assert not tree.references


@pytest.mark.parametrize(
    "attribute,value,error",
    [
        ("AXEnabled", False, "disabled"),
        ("AXPosition", NS(x=45, y=60), "moved"),
        ("AXSize", NS(width=90, height=30), "moved"),
        ("AXRole", "AXTextField", "changed"),
        ("AXTitle", "Delete", "changed"),
        ("AXWindow", "other", "another window"),
        ("pid", 101, "another window"),
        ("AXHidden", True, "hidden"),
        ("AXSubrole", "AXSecureTextField", "secure"),
    ],
)
def test_changed_element_is_rejected_before_click(attribute, value, error):
    ax, tree, obs = projected()
    ax.nodes["button"][attribute] = value
    with pytest.raises(RuntimeError, match=error):
        tree.resolve(obs.elements[0]["id"], obs)


@pytest.mark.parametrize("secure_attribute", ["AXSubrole", "AXRole", "AXProtectedContent"])
def test_secure_subtrees_are_never_read_or_returned(secure_attribute):
    ax = FakeAX()
    ax.nodes["button"][secure_attribute] = True if secure_attribute == "AXProtectedContent" else "AXSecureTextField"
    ax.nodes["button"]["AXChildren"] = ["secret"]
    ax.nodes["button"]["AXValue"] = "do-not-read"
    ax.nodes["secret"] = {"AXValue": "secret-child"}
    tree = AccessibilityTree(ax)
    obs = Observation("", 700, 480, (10, 20, 700, 480), "test", 12, 1)
    elements, _ = tree.snapshot(NS(processIdentifier=lambda: 100), obs)
    assert len(elements) == 1 and elements[0]["role"] == "AXStaticText"
    assert ("button", "AXValue") not in ax.reads and ("button", "AXChildren") not in ax.reads
    assert not any(node == "secret" for node, _ in ax.reads)


def test_newly_secure_ancestor_and_reparented_element_are_rejected():
    ax, tree, obs = projected()
    ax.nodes["group"] = {"AXRole": "AXGroup", "AXSubrole": "AXSecureTextField", "AXParent": "window"}
    ax.nodes["button"]["AXParent"] = "group"
    with pytest.raises(RuntimeError, match="secure"):
        tree.resolve(obs.elements[0]["id"], obs)
    ax.nodes["button"]["AXParent"] = None
    with pytest.raises(RuntimeError, match="secure"):
        tree.resolve(obs.elements[0]["id"], obs)


def test_replaced_or_occluding_control_is_not_clicked():
    ax, tree, obs = projected()
    ax.nodes["replacement"] = {**ax.nodes["button"]}
    ax.hit = "replacement"
    with pytest.raises(RuntimeError, match="occluded or replaced"):
        tree.resolve(obs.elements[0]["id"], obs)


def test_button_child_hit_is_allowed_but_foreign_child_is_not():
    ax, tree, obs = projected()
    ax.nodes["child"] = {"AXRole": "AXStaticText", "AXParent": "button", "AXWindow": "window", "pid": 100}
    ax.hit = "child"
    assert tree.resolve(obs.elements[0]["id"], obs) == (80, 75)
    ax.nodes["child"]["pid"] = 101
    with pytest.raises(RuntimeError, match="occluded"):
        tree.resolve(obs.elements[0]["id"], obs)


def test_missing_ambiguous_window_returns_no_targets():
    ax, tree, obs = projected()
    ax.nodes["application"]["AXWindows"] = ["window", "window"]
    elements, status = tree.snapshot(NS(processIdentifier=lambda: 100), obs)
    assert elements == [] and status == "unavailable" and not tree.references


def test_projection_is_bounded_and_never_truncates_ids(monkeypatch):
    ax, tree, obs = projected()
    ax.nodes["button"]["AXTitle"] = "a" * 1000
    ax.nodes["text"]["AXValue"] = "b" * 1000
    elements, status = tree.snapshot(NS(processIdentifier=lambda: 100), obs)
    assert status == "available" and len(elements[0]["label"]) == 160 and len(elements[1]["value"]) == 512
    assert elements[0]["id"] in tree.references
    monkeypatch.setattr(module, "MAX_NODES", 1)
    elements, status = tree.snapshot(NS(processIdentifier=lambda: 100), obs)
    assert status == "truncated" and not elements


@pytest.mark.parametrize("pixel", [True, False])
def test_native_input_uses_same_physical_event_path_and_consumes_snapshot(monkeypatch, pixel):
    _, tree, obs = projected()
    driver = NativeDesktop.__new__(NativeDesktop)
    driver.settings = DesktopSettings(accessibility_enabled=True)
    driver.accessibility, driver.last = tree, obs
    driver.check_focus = Mock()
    driver.window = Mock(
        return_value=(None, {"kCGWindowNumber": 12, "kCGWindowBounds": dict(X=10, Y=20, Width=700, Height=480)})
    )
    driver.point_is_target = Mock(return_value=True)
    driver.q = NS(kCGEventLeftMouseDown=1, kCGEventLeftMouseUp=2, kCGMouseButtonLeft=0, CGEventCreateMouseEvent=Mock())
    driver.q.CGEventSetIntegerValueField, driver.q.CGEventSetFlags = Mock(), Mock()
    driver.q.kCGMouseEventClickState = 1
    driver.post_event = Mock()
    monkeypatch.setattr("openjiuwen.harness.tools.desktop.worker.time.sleep", lambda _: None)
    action = Action(kind="click", x=140, y=110) if pixel else Action(kind="click", element_id=obs.elements[0]["id"])
    driver.act(action, {"generation": 1})
    assert driver.last is None and not tree.references
    assert driver.post_event.call_count == 2 and driver.check_focus.call_count >= 2
    assert [call.args[2] for call in driver.q.CGEventCreateMouseEvent.call_args_list] == [(80, 75), (80, 75)]


def test_disabled_native_feature_rejects_element_before_input():
    _, _, obs = projected()
    driver = NativeDesktop.__new__(NativeDesktop)
    driver.settings, driver.last = DesktopSettings(), obs
    driver.check_focus = Mock()
    driver.window = Mock(
        return_value=(None, {"kCGWindowNumber": 12, "kCGWindowBounds": dict(X=10, Y=20, Width=700, Height=480)})
    )
    driver.post_event = Mock()
    with pytest.raises(PermissionError, match="disabled"):
        driver.act(Action(kind="click", element_id=obs.elements[0]["id"]), {"generation": 1})
    driver.post_event.assert_not_called()


def typing_snapshot(*, focused="button", editable=True):
    ax = FakeAX()
    ax.nodes["button"].update(AXRole="AXTextArea", AXTitle="Editor", AXValue="", AXFocused=True, editable=editable)
    ax.nodes["application"]["AXFocusedUIElement"] = focused
    tree = AccessibilityTree(ax)
    observation = Observation("", 1400, 960, (10, 20, 700, 480), "test", 12, 1)
    elements, status = tree.snapshot(NS(processIdentifier=lambda: 100), observation, window_title="test")
    return ax, tree, replace(observation, elements=elements, accessibility_status=status)


def typing_driver(tree, observation):
    driver = NativeDesktop.__new__(NativeDesktop)
    driver.settings = DesktopSettings(accessibility_enabled=True)
    driver.accessibility, driver.last = tree, observation
    driver.check_focus = Mock()
    driver.window = Mock(
        return_value=(None, {"kCGWindowNumber": 12, "kCGWindowBounds": dict(X=10, Y=20, Width=700, Height=480)})
    )
    driver.q = NS(
        CGEventCreateKeyboardEvent=Mock(return_value="key"),
        CGEventSetFlags=Mock(),
        CGEventKeyboardSetUnicodeString=Mock(),
        kCGEventFlagMaskCommand=1,
        kCGEventFlagMaskShift=2,
    )
    driver.post_event = Mock()
    return driver


def editor_focus_fixture(role="AXTextField", owner="direct"):
    """A control with independent pointer/focus capabilities, unrelated to any app."""
    ax = FakeAX()
    ax.nodes["button"].update(AXRole=role, AXTitle="Arbitrary input", AXValue="", editable=True)
    ax.nodes["group"] = {
        "AXRole": "AXGroup",
        "AXWindow": "window",
        "AXParent": "window",
        "AXChildren": ["button"],
    }
    ax.nodes["button"]["AXParent"] = "group"
    ax.nodes["window"]["AXChildren"] = ["group", "text"]
    ax.nodes["application"]["AXFocusedUIElement"] = "window"
    ax.hit = "text"  # Pointer hit testing must remain unavailable for the editor.

    def set_focus(element, name, value):
        assert (element, name, value) == ("button", "AXFocused", True)
        ax.nodes[element]["AXFocused"] = True
        if owner == "missing":
            ax.nodes["application"].pop("AXFocusedUIElement")
        else:
            ax.nodes["application"]["AXFocusedUIElement"] = {
                "direct": element,
                "window": "window",
                "container": "group",
            }[owner]
        return 0

    ax.AXUIElementSetAttributeValue = Mock(side_effect=set_focus)
    tree = AccessibilityTree(ax)
    obs = Observation("", 700, 480, (10, 20, 700, 480), "test", 12, 1)
    elements, status = tree.snapshot(NS(processIdentifier=lambda: 100), obs)
    return ax, tree, replace(obs, elements=elements, accessibility_status=status)


def request_editor_focus(tree, obs, *, check_scope=None, pause=None, record=None):
    tree.focus(
        obs.elements[0]["id"],
        obs,
        app=NS(processIdentifier=lambda: 100),
        check_scope=check_scope or Mock(),
        pause=pause or Mock(),
        record=record,
    )


def lazy_text_provider(ax, *, actual_focus=True, selection_error=0):
    """Model focus that needs both real window focus and refreshed caret state."""
    original = ax.AXUIElementCopyAttributeValue
    state = {"actual_focus": actual_focus, "caret_known": False, "queries": 0}

    def read(element, name, unused):
        if element == "button" and name == "AXSelectedTextRange":
            state["queries"] += 1
            if selection_error:
                return selection_error, None
            state["caret_known"] = True
            return 0, object()  # Deliberately opaque; not permission or model data.
        if element == "button" and name == "AXFocused":
            return 0, state["actual_focus"] and state["caret_known"]
        return original(element, name, unused)

    ax.AXUIElementCopyAttributeValue = read
    return state


@pytest.mark.parametrize("role", ["AXTextField", "AXTextArea", "AXComboBox"])
@pytest.mark.parametrize("owner", ["window", "group", None])
def test_lazy_text_focus_refresh_requires_independent_native_focus(role, owner):
    ax, tree, obs = editor_focus_fixture(role)
    ax.nodes["application"]["AXFocusedUIElement"] = owner
    native = lazy_text_provider(ax)
    elements, status = tree.snapshot(NS(processIdentifier=lambda: 100), obs)
    assert status == "available" and native["queries"] == 1
    assert elements[0]["focused"] and elements[0]["typing_ready"]
    assert not elements[0]["actionable"]
    assert set(tree.selection_probes[elements[0]["id"]]) == {"error", "available"}
    json.dumps(elements)  # The opaque native range must never escape.
    ax.AXUIElementSetAttributeValue.assert_not_called()
    tree.validate_typing(obs)
    # A cached caret alone cannot keep authorizing input after real focus leaves.
    native["actual_focus"] = False
    with pytest.raises(RuntimeError, match="focus changed"):
        tree.validate_typing(obs)


@pytest.mark.parametrize("problem", ["unfocused", "different_owner", "ambiguous", "selection_unsupported"])
def test_selection_range_does_not_establish_typing_permission(problem):
    ax, tree, obs = editor_focus_fixture()
    native = lazy_text_provider(
        ax, actual_focus=problem != "unfocused", selection_error=-25205 if problem == "selection_unsupported" else 0
    )
    if problem == "different_owner":
        ax.nodes["application"]["AXFocusedUIElement"] = "text"
    elif problem == "ambiguous":
        ax.nodes["text"].update(AXRole="AXTextField", AXFocused=True, AXEnabled=True, editable=True)
    elements, status = tree.snapshot(NS(processIdentifier=lambda: 100), obs)
    assert status == "available" and native["queries"] == 1
    assert not any(e.get("typing_ready") for e in elements)
    with pytest.raises(RuntimeError, match="no observed editable"):
        tree.validate_typing(obs)
    ax.AXUIElementSetAttributeValue.assert_not_called()


@pytest.mark.parametrize(
    "problem",
    ["protected", "secure_parent", "hidden", "hidden_parent", "foreign_pid", "foreign_window", "readonly", "disabled"],
)
def test_optional_selection_read_is_confined_to_safe_editable_controls(problem):
    ax, tree, obs = editor_focus_fixture()
    native = lazy_text_provider(ax)
    updates = {
        "protected": {"AXProtectedContent": True},
        "hidden": {"AXHidden": True},
        "foreign_pid": {"pid": 200},
        "foreign_window": {"AXWindow": "other"},
        "readonly": {"editable": False},
        "disabled": {"AXEnabled": False},
    }
    ax.nodes["button"].update(updates.get(problem, {}))
    if problem in {"secure_parent", "hidden_parent"}:
        ax.nodes["group"]["AXProtectedContent" if problem == "secure_parent" else "AXHidden"] = True
    tree.snapshot(NS(processIdentifier=lambda: 100), obs)
    assert native["queries"] == 0


@pytest.mark.parametrize("error", [-25205, -25200, -25204, "exception"])
def test_optional_selection_errors_do_not_remove_independently_verified_focus(error):
    ax, tree, obs = editor_focus_fixture()
    ax.nodes["application"]["AXFocusedUIElement"] = "button"
    original = ax.AXUIElementCopyAttributeValue

    def read(element, name, unused):
        if name == "AXSelectedTextRange":
            if error == "exception":
                raise RuntimeError("optional selection read failed")
            return error, None
        return original(element, name, unused)

    ax.AXUIElementCopyAttributeValue = read
    elements, status = tree.snapshot(NS(processIdentifier=lambda: 100), obs)
    assert status == "available" and elements[0]["typing_ready"]
    tree.validate_typing(obs)


@pytest.mark.parametrize("attribute", ["AXFocused", "AXProtectedContent", "AXParent", "AXWindow"])
def test_required_state_error_after_optional_read_still_blocks_typing(attribute):
    ax, tree, obs = editor_focus_fixture()
    original = ax.AXUIElementCopyAttributeValue
    queried = False

    def read(element, name, unused):
        nonlocal queried
        if element == "button" and name == "AXSelectedTextRange":
            queried = True
            ax.nodes[element]["AXFocused"] = True
            return 0, object()
        if element == "button" and queried and name == attribute:
            return -25200, None
        return original(element, name, unused)

    ax.AXUIElementCopyAttributeValue = read
    elements, _ = tree.snapshot(NS(processIdentifier=lambda: 100), obs)
    assert queried and not any(e.get("typing_ready") for e in elements)
    with pytest.raises(RuntimeError):
        tree.validate_typing(obs)
    ax.AXUIElementSetAttributeValue.assert_not_called()


def test_already_focused_editor_does_not_query_selection():
    ax, tree, obs = typing_snapshot()
    ax.reads.clear()
    elements, status = tree.snapshot(NS(processIdentifier=lambda: 100), obs)
    assert status == "available" and elements[0]["typing_ready"]
    assert not tree.selection_probes
    assert not any(name == "AXSelectedTextRange" for _, name in ax.reads)


def test_explicit_focus_verification_refreshes_lazy_state_and_records_no_content():
    ax, tree, obs = editor_focus_fixture(owner="window")
    native = lazy_text_provider(ax, actual_focus=False)
    setter = ax.AXUIElementSetAttributeValue.side_effect

    def request(*args):
        result = setter(*args)
        native.update(actual_focus=True, caret_known=False)
        return result

    ax.AXUIElementSetAttributeValue.side_effect = request
    records = []
    request_editor_focus(tree, obs, record=lambda event, **data: records.append({"event": event, **data}))
    assert native["queries"] == 1
    assert records[0] == {"event": "editor_focus_requested", "role": "AXTextField", "native_status": 0}
    poll = records[-1]
    assert poll["typing_ready"] and poll["focused"] and poll["binding"] == "container"
    assert poll["selection_probe"] == {"error": 0, "available": True}
    assert poll["typing_validation_error"] is None
    assert not any(k in json.dumps(records) for k in ("Arbitrary input", "AXSelectedTextRange", "AXValue"))
    tree.validate_typing(obs)


def test_focus_failure_records_missing_readiness_without_retrying_mutation():
    ax, tree, obs = editor_focus_fixture()
    ax.AXUIElementSetAttributeValue.side_effect = lambda *_: 0  # Accept but do nothing.
    native = lazy_text_provider(ax, actual_focus=False)
    record = Mock()
    with pytest.raises(RuntimeError, match="did not acquire verified"):
        request_editor_focus(tree, obs, record=record)
    ax.AXUIElementSetAttributeValue.assert_called_once()
    polls = [c.kwargs for c in record.call_args_list if c.args == ("editor_focus_poll",)]
    assert 1 <= len(polls) <= 4 and native["queries"] == len(polls)
    assert all(not p["focused"] and not p["typing_ready"] and p["binding"] == "unbound" for p in polls)


@pytest.mark.parametrize("role", ["AXTextField", "AXTextArea", "AXComboBox"])
@pytest.mark.parametrize("owner", ["direct", "window", "container", "missing"])
def test_explicit_editor_focus_supports_native_roles_and_focus_reports(role, owner):
    ax, tree, obs = editor_focus_fixture(role, owner)
    assert obs.elements[0]["focusable"] and not obs.elements[0]["typing_ready"]
    assert not obs.elements[0]["actionable"]
    with pytest.raises(RuntimeError, match="occluded"):
        tree.resolve(obs.elements[0]["id"], obs)
    request_editor_focus(tree, obs)
    ax.AXUIElementSetAttributeValue.assert_called_once_with("button", "AXFocused", True)
    tree.validate_typing(obs)
    # Only a new observation can supply IDs for the subsequent keyboard action.
    with pytest.raises(RuntimeError, match="stale or unknown"):
        tree.validate_reference(obs.elements[0]["id"], obs)
    elements, status = tree.snapshot(NS(processIdentifier=lambda: 100), replace(obs, generation=2))
    assert elements[0]["typing_ready"] and not elements[0]["actionable"]
    driver = typing_driver(tree, replace(obs, generation=2, elements=elements, accessibility_status=status))
    driver._perform_action(Action(kind="type", text="x"))
    assert driver.post_event.call_count == 2  # Ordinary paired keyboard events, not AXValue.


@pytest.mark.parametrize(
    "change",
    [
        "unsupported",
        "readonly",
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
        "stale",
        "noneditable_role",
    ],
)
def test_editor_focus_rejects_changed_or_unauthorized_targets_before_mutation(change):
    ax, tree, obs = editor_focus_fixture()
    updates = {
        "unsupported": {"focusable": False},
        "readonly": {"editable": False},
        "disabled": {"AXEnabled": False},
        "secure": {"AXProtectedContent": True},
        "hidden": {"AXHidden": True},
        "foreign_pid": {"pid": 200},
        "foreign_window": {"AXWindow": "other"},
        "moved": {"AXPosition": NS(x=41, y=60)},
        "label": {"AXTitle": "Replaced"},
        "noneditable_role": {"AXRole": "AXButton"},
    }
    ax.nodes["button"].update(updates.get(change, {}))
    if change in {"secure_parent", "hidden_parent"}:
        ax.nodes["group"]["AXProtectedContent" if change == "secure_parent" else "AXHidden"] = True
    elif change == "root_moved":
        ax.nodes["window"]["AXPosition"] = NS(x=11, y=20)
    elif change == "stale":
        obs = replace(obs, generation=2)
    with pytest.raises(RuntimeError):
        request_editor_focus(tree, obs)
    ax.AXUIElementSetAttributeValue.assert_not_called()


@pytest.mark.parametrize("stop_at", [1, 2, 3, 4, 5, 6])
def test_editor_focus_fences_cancellation_before_and_after_the_single_mutation(stop_at):
    ax, tree, obs = editor_focus_fixture()
    guard = Mock(side_effect=[None] * (stop_at - 1) + [RuntimeError("scope lost or cancelled")])
    with pytest.raises(RuntimeError, match="scope lost"):
        request_editor_focus(tree, obs, check_scope=guard)
    assert ax.AXUIElementSetAttributeValue.call_count == (0 if stop_at <= 3 else 1)


@pytest.mark.parametrize("failure", ["native_error", "no_change", "different_editor", "label", "root", "removed"])
def test_native_focus_acknowledgement_is_not_proof_of_selected_editor_focus(failure):
    ax, tree, obs = editor_focus_fixture()

    def setter(*_):
        ax.nodes["button"]["AXFocused"] = failure != "no_change"
        if failure == "different_editor":
            ax.nodes["text"].update(AXRole="AXTextField", AXFocused=True, AXEnabled=True, editable=True)
            ax.nodes["button"]["AXFocused"] = False
            ax.nodes["application"]["AXFocusedUIElement"] = "text"
        elif failure == "label":
            ax.nodes["button"]["AXTitle"] = "Replaced"
        elif failure == "root":
            ax.nodes["application"]["AXWindows"] = []
        elif failure == "removed":
            ax.nodes["group"]["AXChildren"] = []
        return -25206 if failure == "native_error" else 0

    ax.AXUIElementSetAttributeValue.side_effect = setter
    pause = Mock()
    with pytest.raises(RuntimeError):
        request_editor_focus(tree, obs, pause=pause)
    ax.AXUIElementSetAttributeValue.assert_called_once()
    assert pause.call_count <= 3


def test_focus_verification_deadline_rejects_a_late_positive_result(monkeypatch):
    ax, tree, obs = editor_focus_fixture()
    now = [0.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    snapshot = tree.snapshot

    def late_snapshot(*args, **kwargs):
        result = snapshot(*args, **kwargs)
        now[0] += 1.01
        return result

    monkeypatch.setattr(tree, "snapshot", late_snapshot)
    with pytest.raises(RuntimeError, match="did not acquire verified"):
        request_editor_focus(tree, obs)
    ax.AXUIElementSetAttributeValue.assert_called_once()


def test_focus_verification_includes_final_scope_check_in_deadline(monkeypatch):
    ax, tree, obs = editor_focus_fixture()
    now = [0.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])

    def check_scope():
        if guard.call_count == 6:
            now[0] += 1.01

    guard = Mock(side_effect=check_scope)
    with pytest.raises(RuntimeError, match="did not acquire verified"):
        request_editor_focus(tree, obs, check_scope=guard)
    ax.AXUIElementSetAttributeValue.assert_called_once()


def test_unsupported_focus_does_not_remove_normal_click_and_keyboard_capabilities():
    ax, tree, obs = editor_focus_fixture()
    ax.nodes["button"].update(focusable=False, AXFocused=True)
    ax.nodes["application"]["AXFocusedUIElement"] = "button"
    ax.hit = "button"
    elements, status = tree.snapshot(NS(processIdentifier=lambda: 100), obs)
    assert status == "available"
    assert not elements[0]["focusable"] and elements[0]["actionable"] and elements[0]["typing_ready"]
    tree.resolve(elements[0]["id"], obs)
    tree.validate_typing(obs)


@pytest.mark.parametrize("supported", [True, False])
def test_legacy_worker_dispatch_consumes_editor_focus_observation_on_success_and_failure(supported, monkeypatch):
    ax, tree, obs = editor_focus_fixture()
    ax.nodes["button"]["focusable"] = supported
    driver = typing_driver(tree, obs)
    driver.check_focus.return_value = NS(processIdentifier=lambda: 100)
    driver._inputs = Mock(return_value=NS(pause=Mock()))
    driver.focus = Mock(side_effect=AssertionError("must not activate an app"))
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    if supported:
        driver.act(Action(kind="focus", element_id=obs.elements[0]["id"]), {"generation": 1})
        ax.AXUIElementSetAttributeValue.assert_called_once()
    else:
        with pytest.raises(RuntimeError, match="does not support"):
            driver.act(Action(kind="focus", element_id=obs.elements[0]["id"]), {"generation": 1})
        ax.AXUIElementSetAttributeValue.assert_not_called()
    assert driver.last is None and not tree.references
    driver.post_event.assert_not_called()
    driver.focus.assert_not_called()


def container_focus(role="AXScrollArea"):
    ax, tree, observation = typing_snapshot()
    ax.nodes["container"] = {
        "AXRole": role,
        "AXParent": "window",
        "AXWindow": "window",
        "AXChildren": ["button"],
    }
    ax.nodes["window"]["AXChildren"] = ["container", "text"]
    ax.nodes["button"]["AXParent"] = "container"
    ax.nodes["application"]["AXFocusedUIElement"] = "container"
    return ax, tree, observation


@pytest.mark.parametrize("role", sorted(module.FOCUS_CONTAINER_ROLES))
def test_container_focus_binds_explicitly_focused_editable_descendant(role):
    ax, tree, obs = container_focus(role)
    # A valid typing target need not support physical center hit testing.
    ax.hit = "text"
    elements, status = tree.snapshot(NS(processIdentifier=lambda: 100), obs)
    field = next(e for e in elements if e["editable"])
    assert status == "available" and field["focused"] and field["typing_ready"]
    assert not field["actionable"]
    tree.validate_typing(obs)
    driver = typing_driver(tree, replace(obs, elements=elements))
    driver.act(Action(kind="type", text="abc"), {"generation": 1})
    assert driver.post_event.call_count == 6


def test_exact_root_can_report_container_focus():
    ax, tree, obs = typing_snapshot(focused="window")
    assert obs.elements[0]["typing_ready"]
    tree.validate_typing(obs)


@pytest.mark.parametrize(
    "change",
    [
        "sibling",
        "foreign_pid",
        "foreign_window",
        "secure",
        "hidden",
        "not_focused",
        "readonly",
        "disabled",
        "competing_field",
        "text_container",
        "failed_read",
        "truncated",
    ],
)
def test_container_focus_does_not_infer_missing_or_conflicting_ownership(change, monkeypatch):
    ax, tree, obs = container_focus()
    if change == "sibling":
        ax.nodes["button"]["AXParent"] = "window"
    elif change == "foreign_pid":
        ax.nodes["container"]["pid"] = 101
    elif change == "foreign_window":
        ax.nodes["container"]["AXWindow"] = "private"
    elif change == "secure":
        ax.nodes["container"]["AXProtectedContent"] = True
    elif change == "hidden":
        ax.nodes["container"]["AXHidden"] = True
    elif change == "not_focused":
        ax.nodes["button"]["AXFocused"] = False
    elif change == "readonly":
        ax.nodes["button"]["editable"] = False
    elif change == "disabled":
        ax.nodes["button"]["AXEnabled"] = False
    elif change == "competing_field":
        ax.nodes["other"] = dict(ax.nodes["button"])
        ax.nodes["container"]["AXChildren"].append("other")
    elif change == "text_container":
        ax.nodes["container"]["AXRole"] = "AXTextArea"
    elif change == "failed_read":
        original = ax.AXUIElementCopyAttributeValue
        ax.AXUIElementCopyAttributeValue = lambda e, n, u: (
            (-25200, None) if (e, n) == ("application", "AXFocusedUIElement") else original(e, n, u)
        )
    else:
        monkeypatch.setattr(module, "MAX_ELEMENTS", 1)
    elements, _ = tree.snapshot(NS(processIdentifier=lambda: 100), obs)
    assert not any(e.get("typing_ready") for e in elements)
    with pytest.raises(RuntimeError, match="no observed editable"):
        tree.validate_typing(obs)


@pytest.mark.parametrize("change", ["container", "role", "parent", "field_focus", "hidden", "protected", "readonly"])
def test_container_focus_is_fenced_between_key_pairs(change):
    ax, tree, obs = container_focus()
    elements, _ = tree.snapshot(NS(processIdentifier=lambda: 100), obs)
    driver = typing_driver(tree, replace(obs, elements=elements))
    events = []

    def change_after_pair(event):
        events.append(event)
        if len(events) != 2:
            return
        if change == "container":
            ax.nodes["application"]["AXFocusedUIElement"] = "window"
        elif change == "role":
            ax.nodes["container"]["AXRole"] = "AXGroup"
        elif change == "parent":
            ax.nodes["button"]["AXParent"] = "window"
        elif change == "field_focus":
            ax.nodes["button"]["AXFocused"] = False
        elif change == "hidden":
            ax.nodes["container"]["AXHidden"] = True
        elif change == "protected":
            ax.nodes["container"]["AXProtectedContent"] = True
        else:
            ax.nodes["button"]["editable"] = False

    driver.post_event.side_effect = change_after_pair
    with pytest.raises(RuntimeError):
        driver.act(Action(kind="type", text="abc"), {"generation": 1})
    assert len(events) == 2


def test_typing_ready_distinguishes_raw_focus_from_validated_focus():
    _, _, obs = typing_snapshot(focused="text")
    assert obs.elements[0]["focused"] and obs.elements[0]["editable"]
    assert not obs.elements[0]["typing_ready"]
    _, _, obs = typing_snapshot()
    assert obs.elements[0]["typing_ready"]


@pytest.mark.parametrize("focus,editable", [("text", True), ("button", False)])
def test_typing_rejects_noneditable_or_static_focus_before_input(focus, editable):
    _, tree, obs = typing_snapshot(focused=focus, editable=editable)
    driver = typing_driver(tree, obs)
    with pytest.raises(RuntimeError, match="no observed editable"):
        driver.act(Action(kind="type", text="x"), {"generation": 1})
    driver.post_event.assert_not_called()


@pytest.mark.parametrize("change", ["different_focus", "protected", "protected_ancestor", "disabled", "readonly"])
def test_typing_revalidates_focus_and_protection_before_input(change):
    ax, tree, obs = typing_snapshot()
    driver = typing_driver(tree, obs)
    if change == "different_focus":
        ax.nodes["application"]["AXFocusedUIElement"] = "text"
    elif change == "protected":
        ax.nodes["button"]["AXProtectedContent"] = True
    elif change == "protected_ancestor":
        ax.nodes["group"] = {"AXRole": "AXGroup", "AXProtectedContent": True, "AXParent": "window"}
        ax.nodes["button"]["AXParent"] = "group"
    elif change == "disabled":
        ax.nodes["button"]["AXEnabled"] = False
    else:
        ax.nodes["button"]["editable"] = False
    with pytest.raises(RuntimeError):
        driver.act(Action(kind="type", text="x"), {"generation": 1})
    driver.post_event.assert_not_called()


def test_typing_fences_remaining_characters_when_focus_changes_mid_string():
    ax, tree, obs = typing_snapshot()
    driver = typing_driver(tree, obs)
    events = []

    def change_focus(event):
        events.append(event)
        if len(events) == 2:
            ax.nodes["application"]["AXFocusedUIElement"] = "text"

    driver.post_event.side_effect = change_focus
    with pytest.raises(RuntimeError, match="focus changed"):
        driver.act(Action(kind="type", text="abc"), {"generation": 1})
    assert len(events) == 2  # finish the existing down/up pair; do not type b or c


def test_reused_field_editor_reparented_to_another_control_is_rejected():
    ax, tree, obs = typing_snapshot()
    ax.nodes["editor"] = {"AXRole": "AXTextArea", "AXParent": "button", "AXWindow": "window", "pid": 100}
    ax.nodes["application"]["AXFocusedUIElement"] = "editor"
    tree.snapshot(NS(processIdentifier=lambda: 100), obs)
    tree.validate_typing(obs)
    ax.nodes["other"] = {**ax.nodes["button"]}
    ax.nodes["editor"]["AXParent"] = "other"
    driver = typing_driver(tree, obs)
    with pytest.raises(RuntimeError, match="another control"):
        driver.act(Action(kind="type", text="x"), {"generation": 1})
    driver.post_event.assert_not_called()


def test_cancellation_or_focus_loss_during_typing_validation_prevents_key_pair():
    _, tree, obs = typing_snapshot()
    driver = typing_driver(tree, obs)
    driver.check_focus.side_effect = [None, RuntimeError("native executor is stopping")]
    with pytest.raises(RuntimeError, match="stopping"):
        driver.post_key(0, text="x", require_editable=True)
    driver.post_event.assert_not_called()


def test_normal_text_area_typing_and_copy_shortcut_remain_supported(monkeypatch):
    _, tree, obs = typing_snapshot()
    driver = typing_driver(tree, obs)
    monkeypatch.setattr("openjiuwen.harness.tools.desktop.worker.time.sleep", lambda _: None)
    driver.act(Action(kind="type", text="Hi"), {"generation": 1})
    assert driver.post_event.call_count == 4 and driver.last is None
    _, tree, obs = typing_snapshot(focused="text")
    driver = typing_driver(tree, obs)
    driver.act(Action(kind="shortcut", keys=["cmd", "c"]), {"generation": 1})
    assert driver.post_event.call_count == 2  # copy does not require an editable field


@pytest.mark.parametrize("keys", [["cmd", "v"], ["backspace"]])
@pytest.mark.parametrize("changed", [False, True])
def test_text_mutation_shortcuts_require_unchanged_editable_focus(monkeypatch, keys, changed):
    ax, tree, obs = typing_snapshot()
    driver = typing_driver(tree, obs)
    monkeypatch.setattr("openjiuwen.harness.tools.desktop.worker.time.sleep", lambda _: None)
    if changed:
        ax.nodes["application"]["AXFocusedUIElement"] = "text"
        with pytest.raises(RuntimeError, match="focus changed"):
            driver.act(Action(kind="shortcut", keys=keys), {"generation": 1})
        driver.post_event.assert_not_called()
    else:
        driver.act(Action(kind="shortcut", keys=keys), {"generation": 1})
        assert driver.post_event.call_count == 2 and driver.last is None


def test_unavailable_accessibility_retains_baseline_typing_guard(monkeypatch):
    _, tree, obs = typing_snapshot(focused="text")
    driver = typing_driver(tree, replace(obs, elements=[], accessibility_status="unavailable"))
    monkeypatch.setattr("openjiuwen.harness.tools.desktop.worker.time.sleep", lambda _: None)
    driver.act(Action(kind="type", text="x"), {"generation": 1})
    driver.check_focus.assert_called()
    assert driver.post_event.call_count == 2


@pytest.mark.parametrize("role", ["AXCheckBox", "AXRadioButton"])
def test_toggle_state_change_rejects_stale_click(role):
    ax = FakeAX()
    ax.nodes["button"].update(AXRole=role, AXValue=0)
    tree = AccessibilityTree(ax)
    obs = Observation("", 1400, 960, (10, 20, 700, 480), "test", 12, 1)
    elements, _ = tree.snapshot(NS(processIdentifier=lambda: 100), obs)
    assert tree.resolve(elements[0]["id"], obs) == (80, 75)
    ax.nodes["button"]["AXValue"] = 1
    with pytest.raises(RuntimeError, match="toggle value changed"):
        tree.resolve(elements[0]["id"], obs)


def test_transient_attribute_errors_do_not_expose_unverified_text():
    ax = FakeAX()
    original = ax.AXUIElementCopyAttributeValue

    def interrupted(element, name, unused):
        return (-25204, None) if element == "button" and name == "AXSubrole" else original(element, name, unused)

    ax.AXUIElementCopyAttributeValue = interrupted
    tree = AccessibilityTree(ax)
    obs = Observation("", 700, 480, (10, 20, 700, 480), "test", 12, 1)
    elements, status = tree.snapshot(NS(processIdentifier=lambda: 100), obs)
    assert elements == [] and status == "unavailable" and not tree.references
    assert ("button", "AXValue") not in ax.reads


def test_message_timeout_is_set_on_each_reference_before_attribute_read():
    ax = FakeAX()
    events = []
    original = ax.AXUIElementCopyAttributeValue
    ax.AXUIElementSetMessagingTimeout = lambda element, timeout: events.append((element, "timeout")) or 0

    def read(element, name, unused):
        assert events[-1] == (element, "timeout")
        events.append((element, name))
        return original(element, name, unused)

    ax.AXUIElementCopyAttributeValue = read
    tree = AccessibilityTree(ax)
    obs = Observation("", 700, 480, (10, 20, 700, 480), "test", 12, 1)
    elements, status = tree.snapshot(NS(processIdentifier=lambda: 100), obs)
    assert elements and status == "available"


def test_optional_description_failure_keeps_native_editable_target_usable():
    ax, tree, obs = typing_snapshot()
    ax.nodes["button"].pop("AXTitle")
    original = ax.AXUIElementCopyAttributeValue

    def missing_description(element, name, unused):
        return (-25200, None) if element == "button" and name == "AXDescription" else original(element, name, unused)

    ax.AXUIElementCopyAttributeValue = missing_description
    elements, status = tree.snapshot(NS(processIdentifier=lambda: 100), obs)
    assert status == "available" and elements[0]["label"] == "" and elements[0]["editable"]
    assert tree.resolve(elements[0]["id"], obs) == (80, 75)
    tree.validate_typing(obs)


def test_previously_observed_description_disappearing_still_rejects_target():
    ax, tree, obs = typing_snapshot()
    ax.nodes["button"].pop("AXTitle")
    ax.nodes["button"]["AXDescription"] = "Editor"
    elements, status = tree.snapshot(NS(processIdentifier=lambda: 100), obs)
    assert status == "available" and elements[0]["label"] == "Editor"
    original = ax.AXUIElementCopyAttributeValue

    def missing_description(element, name, unused):
        return (-25200, None) if element == "button" and name == "AXDescription" else original(element, name, unused)

    ax.AXUIElementCopyAttributeValue = missing_description
    with pytest.raises(RuntimeError, match="changed"):
        tree.resolve(elements[0]["id"], obs)
    with pytest.raises(RuntimeError, match="changed"):
        tree.validate_typing(obs)


@pytest.mark.parametrize(
    "attribute", ["AXRole", "AXSubrole", "AXProtectedContent", "AXPosition", "AXEnabled", "AXWindow"]
)
def test_generic_failure_on_critical_state_still_discards_projection(attribute):
    ax, tree, obs = typing_snapshot()
    original = ax.AXUIElementCopyAttributeValue

    def critical_failure(element, name, unused):
        return (-25200, None) if element == "button" and name == attribute else original(element, name, unused)

    ax.AXUIElementCopyAttributeValue = critical_failure
    elements, status = tree.snapshot(NS(processIdentifier=lambda: 100), obs)
    assert status == "unavailable" and elements == [] and not tree.references


def test_missing_enabled_writable_text_area_is_supported_and_revalidated():
    ax, tree, obs = typing_snapshot()
    ax.nodes["button"].pop("AXEnabled")
    elements, status = tree.snapshot(NS(processIdentifier=lambda: 100), obs)
    assert status == "available"
    assert elements[0]["enabled"] and elements[0]["editable"] and elements[0]["actionable"]
    assert tree.resolve(elements[0]["id"], obs) == (80, 75)
    tree.validate_typing(obs)
    ax.nodes["button"]["editable"] = False
    with pytest.raises(RuntimeError, match="disabled"):
        tree.resolve(elements[0]["id"], obs)
    with pytest.raises(RuntimeError, match="disabled"):
        tree.validate_typing(obs)


def test_missing_enabled_readonly_text_area_is_not_actionable():
    ax, tree, obs = typing_snapshot(editable=False)
    ax.nodes["button"].pop("AXEnabled")
    elements, status = tree.snapshot(NS(processIdentifier=lambda: 100), obs)
    assert status == "available"
    assert not elements[0]["enabled"] and not elements[0]["actionable"] and not elements[0]["editable"]
    with pytest.raises(RuntimeError, match="stale or unknown"):
        tree.resolve(elements[0]["id"], obs)
    with pytest.raises(RuntimeError, match="no observed editable"):
        tree.validate_typing(obs)


def test_explicit_disabled_text_area_never_uses_writability_override():
    ax, tree, obs = typing_snapshot()
    ax.nodes["button"]["AXEnabled"] = False
    ax.AXUIElementIsAttributeSettable = Mock(side_effect=AssertionError("explicit disabled wins"))
    elements, status = tree.snapshot(NS(processIdentifier=lambda: 100), obs)
    assert status == "available" and not elements[0]["actionable"]
    ax.AXUIElementIsAttributeSettable.assert_not_called()


def test_missing_enabled_button_never_uses_text_writability_override():
    ax, tree, obs = projected()
    ax.nodes["button"].pop("AXEnabled")
    ax.AXUIElementIsAttributeSettable = Mock(side_effect=AssertionError("button needs explicit enabled"))
    elements, status = tree.snapshot(NS(processIdentifier=lambda: 100), obs)
    assert status == "available" and not elements[0]["actionable"]
    ax.AXUIElementIsAttributeSettable.assert_not_called()


@pytest.mark.parametrize("error", [-25200, -25204, -25205, -25212])
def test_missing_enabled_writability_query_errors_never_enable_input(error):
    ax, tree, obs = typing_snapshot()
    ax.nodes["button"].pop("AXEnabled")
    ax.AXUIElementIsAttributeSettable = lambda *_: (error, True)
    elements, status = tree.snapshot(NS(processIdentifier=lambda: 100), obs)
    if error in {-25200, -25204}:
        assert status == "unavailable" and elements == [] and not tree.references
    else:
        assert status == "available" and not elements[0]["actionable"]
    with pytest.raises(RuntimeError, match="no observed editable"):
        tree.validate_typing(obs)


def test_generic_enabled_read_error_does_not_trigger_writability_fallback():
    ax, tree, obs = typing_snapshot()
    original = ax.AXUIElementCopyAttributeValue

    def failed_enabled(element, name, unused):
        return (-25200, None) if element == "button" and name == "AXEnabled" else original(element, name, unused)

    ax.AXUIElementCopyAttributeValue = failed_enabled
    ax.AXUIElementIsAttributeSettable = Mock(side_effect=AssertionError("cannot infer from failed enabled read"))
    elements, status = tree.snapshot(NS(processIdentifier=lambda: 100), obs)
    assert status == "unavailable" and elements == []
    ax.AXUIElementIsAttributeSettable.assert_not_called()
