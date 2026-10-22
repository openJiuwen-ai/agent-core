"""Optimized path regressions: real native guard logic, injected OS/input only."""

from dataclasses import asdict

import pytest

from openjiuwen.harness.tools.desktop import worker
from openjiuwen.harness.tools.desktop.native_batch import run_batch
from openjiuwen.harness.tools.desktop.schema import Action
from tests.unit_tests.harness.tools.desktop.performance_fixture import PerformanceRig


@pytest.fixture
def rig():
    worker.STOPPING.clear()
    value = PerformanceRig()
    value.observe()
    yield value
    worker.STOPPING.clear()


def run(rig, actions, **kwargs):
    return run_batch(
        rig.d, [Action(**a) for a in actions], asdict(rig.d.last), max_steps=20, max_observations=21, **kwargs
    )


def test_text_batch_skips_intermediate_rasters_but_preserves_fresh_ax_and_final_pixels(rig):
    result = run(rig, [dict(kind="type", text=v) for v in ["One\n", "Two\n", "Three"]])
    assert result["status"] == "action_completed" and result["completed"] == 3
    assert rig.value == "One\nTwo\nThree"
    observations = [e for e in result["events"] if e["event"] == "observation"]
    assert [e["performance"]["image_mode"] for e in observations] == ["metadata", "metadata", "full"]
    assert len({e["generation"] for e in observations}) == 3
    assert result["observation"]["png_b64"]
    assert len(rig.captures) == 6  # Three approved windows, initial + final, no intermediate capture.


def test_clicks_keep_pixel_checks_and_rebind_fresh_ids(rig):
    ids = [e["id"] for e in rig.d.last.elements]
    result = run(rig, [dict(kind="click", element_id=ids[i]) for i in [1, 2, 1]])
    assert result["completed"] == 3
    assert [a.element_id for a in rig.actions] == ["1:1", "2:2", "3:1"]
    assert [e["performance"]["image_mode"] for e in result["events"] if e["event"] == "observation"] == [
        "pixels",
        "pixels",
        "full",
    ]


@pytest.mark.parametrize("initially_actionable,late_click", [(False, False), (True, False), (True, True)])
def test_hit_state_change_keeps_keyboard_batch_but_revalidates_pending_click(rig, initially_actionable, late_click):
    rig.controls[1]["actionable"] = initially_actionable
    rig.observe()
    initial = rig.d.last.elements[1]["id"]
    rig.after_action = lambda _: rig.controls[1].update(actionable=not initially_actionable)
    following = dict(kind="click", element_id=initial) if late_click else dict(kind="type", text="second")
    result = run(rig, [dict(kind="type", text="first"), following])
    assert result["status"] == "action_completed" and result["completed"] == (1 if late_click else 2)
    assert len(rig.actions) == (1 if late_click else 2)
    if late_click:
        assert "bound accessibility control changed" in result["reason"]


@pytest.mark.parametrize("change", ["geometry", "control", "missing", "focus", "locked", "stop"])
def test_change_between_actions_stops_without_replaying(rig, change):
    initial = rig.d.last.elements[1]["id"]

    def changed(_):
        if change == "geometry":
            rig.rows[0]["kCGWindowBounds"]["X"] += 5
        elif change == "control":
            rig.label = "Different"
        elif change == "missing":
            rig.enabled = False
        elif change == "focus":
            rig.front = 20
        elif change == "locked":
            rig.locked = True
        else:
            worker.STOPPING.set()

    rig.after_action = changed
    result = run(rig, [dict(kind="type", text="first"), dict(kind="click", element_id=initial)])
    assert len(rig.actions) == 1
    assert result["completed"] <= 1
    assert result["reason"]
    if change in {"locked", "stop"}:
        assert result["status"] == "failed" and rig.released
    else:
        assert result["observation"]["png_b64"]


def test_incomplete_ax_falls_back_to_pixels_and_never_reuses_last_image(rig):
    rig.ax_status = "unavailable"
    result = run(rig, [dict(kind="type", text="a"), dict(kind="type", text="b")])
    assert result["completed"] == 2
    observations = [e for e in result["events"] if e["event"] == "observation"]
    assert all(e["performance"]["image_mode"] != "metadata" for e in observations)


def test_ineffective_click_limit_is_preserved(rig):
    rig.after_action = lambda _: setattr(rig, "value", "")
    button = rig.d.last.elements[1]["id"]
    result = run(rig, [dict(kind="click", element_id=button)] * 4)
    assert result["status"] == "stalled" and result["completed"] == 3
    assert rig.released is False  # Host closes/reaps the worker after the stalled receipt.


def test_readiness_uses_metadata_polls_and_returns_final_image(rig):
    rig.enabled = False
    rig.observe()

    def enable(action):
        if action.kind == "wait":
            rig.enabled = True

    rig.after_action = enable
    result = run(
        rig,
        [dict(kind="type", text="ready")],
        wait_for=dict(signal="control_enabled", label="Apply", timeout_seconds=1),
    )
    assert result["wait_for"]["met"] and result["wait_for"]["polls"] == 1
    assert result["completed"] == 1 and result["steps"] == 2
    assert result["observation"]["png_b64"]


def test_stale_call_and_invalid_late_action_rejected_before_input(rig):
    stale = asdict(rig.d.last)
    rig.observe()
    with pytest.raises(RuntimeError, match="stale"):
        run_batch(rig.d, [Action(kind="type", text="x")], stale, max_steps=2, max_observations=3)
    with pytest.raises(ValueError, match="unsupported"):
        run(rig, [dict(kind="type", text="x"), dict(kind="launch", target="editor")])
    assert not rig.actions


def test_scene_changed_before_input_refreshes_without_replaying_or_losing_grants(rig):
    before = rig.d.last
    grants = set(rig.d.navigation.catalog.shared)
    rig.rows[0]["kCGWindowBounds"]["X"] += 5
    result = run(rig, [dict(kind="type", text="never typed"), dict(kind="type", text="nor this")])
    assert result["status"] == "action_completed" and result["completed"] == result["steps"] == 0
    assert result["observations"] == 1 and result["observation"]["png_b64"]
    assert result["observation"]["generation"] > before.generation and not rig.actions
    assert set(rig.d.navigation.catalog.shared) == grants
    assert "observe again only if none was returned" in result["reason"]
    following = run(rig, [dict(kind="type", text="fresh decision")])
    assert following["completed"] == 1 and rig.value == "fresh decision"


def test_scene_changed_during_input_is_still_fatal(rig):
    from openjiuwen.harness.tools.desktop.macos_navigation import ObservationChanged

    def changed(_):
        raise ObservationChanged("changed after input started")

    rig.after_action = changed
    result = run(rig, [dict(kind="type", text="first"), dict(kind="type", text="never")])
    assert result["status"] == "failed" and len(rig.actions) == 1 and rig.released


def test_release_failure_preserves_completed_progress(rig):
    def failure(_):
        if len(rig.actions) == 2:
            raise RuntimeError("input failed")

    rig.after_action = failure
    rig.d.release_inputs = lambda: (_ for _ in ()).throw(RuntimeError("release failed"))
    result = run(rig, [dict(kind="type", text=v) for v in ["first", "second", "third"]])
    assert result["status"] == "failed" and result["completed"] == 1
    assert "input failed" in result["reason"] and "release failed" in result["reason"]


def test_pixel_signal_after_metadata_steps_has_a_fresh_before_image(rig):
    result = run(
        rig,
        [dict(kind="type", text=v) for v in ["a", "b", "c", "d"]],
        wait_for=dict(signal="screen_change", timeout_seconds=0),
    )
    assert result["wait_for"]["met"] and result["wait_for"]["polls"] == 0
    observations = [e for e in result["events"] if e["event"] == "observation"]
    assert [e["performance"]["image_mode"] for e in observations] == ["metadata", "metadata", "pixels", "full"]
