"""Focus recovery through the public tool, with synthetic windows and no native UI."""

from dataclasses import asdict

import pytest

from openjiuwen.core.session.agent import Session
from openjiuwen.harness.tools.desktop.direct import DirectDesktopSession
from openjiuwen.harness.tools.desktop.direct_tool import DirectComputerUseTool
from openjiuwen.harness.tools.desktop.errors import InputNotStarted
from openjiuwen.harness.tools.desktop.schema import Action
from tests.unit_tests.harness.tools.desktop.test_direct import Lease
from tests.unit_tests.harness.tools.desktop.test_tool_performance import Driver
from tests.unit_tests.harness.tools.desktop.test_window_navigation import NativeRig, row


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["single", "serial", "optimized"])
@pytest.mark.parametrize("focus", ["foreign", "missing", "private"])
@pytest.mark.parametrize("crop", [False, True])
async def test_focus_loss_keeps_session_and_crop_until_explicit_refocus(mode, focus, crop):
    driver, lease = Driver(), Lease()
    rig = driver.rig
    rig.d.settings = rig.d.settings.model_copy(
        update={"active_window_screenshots": crop, "optimized_batches": mode == "optimized"}
    )

    async def act(action, observation):
        rig.act(action, observation)

    driver.act = act
    state = DirectDesktopSession(rig.d.settings, driver_factory=lambda _: driver, lease_factory=lambda: lease)
    tool = DirectComputerUseTool(rig.d.settings, session_factory=lambda: state)
    owner = Session("focus-regression")
    try:
        first = await tool.invoke({"operation": "observe"}, session=owner)
        initial = first.data["observation"]
        selected = initial["context"]["active_window_id"]
        grants = rig.d.navigation.catalog.shared
        if focus == "private":
            rig.rows.insert(0, row(99, bounds=(200, 180, 150, 100)))
            rig.focused[10] = 99
        else:
            rig.front = 999 if focus == "foreign" else None
        token = first.data["observation_id"]
        # First attempt finds a changed scene; the next sees stable missing focus.
        for _ in range(2):
            action = {"operation": "type", "text": "must never be entered"}
            call = action if mode == "single" else {"operation": "batch", "actions": [action]}
            result = await tool.invoke({**call, "observation_id": token}, session=owner)
            assert not state.closed and not lease.released
            assert not rig.actions and not rig.raised and not rig.posts
            assert result.data["observation_id"] != token
            if mode == "single":
                assert result.data["action_applied"] is False
            else:
                assert result.data["batch"]["completed"] == 0
            observation = result.data["observation"]
            assert observation["context"]["active_window_id"] is None
            assert observation["context"]["view_window_id"] == (selected if crop else None)
            assert observation["bounds"] == initial["bounds"]
            assert rig.d.navigation.catalog.shared == grants
            assert 99 not in rig.captures
            token = result.data["observation_id"]
        assert "focus" in result.data["reason"]
        focused = await tool.invoke(
            {"operation": "focus", "window_id": selected, "observation_id": token}, session=owner
        )
        assert focused.success and focused.data["observation"]["context"]["active_window_id"] == selected
        finished = await tool.invoke(
            {
                "operation": "type",
                "text": "new decision",
                "observation_id": focused.data["observation_id"],
                "release_after": True,
            },
            session=owner,
        )
        assert finished.success and rig.value == "new decision"
        assert len(rig.actions) == 1 and rig.raised == [1]
        assert state.closed and driver.closed and lease.released
    finally:
        await tool.close()


@pytest.mark.parametrize("change", ["disappeared", "revoked", "shared_overlay"])
def test_retained_crop_cannot_hide_a_shared_dialog_or_keep_a_retired_grant(change):
    rig = NativeRig()
    rig.d.settings = rig.d.settings.model_copy(update={"active_window_screenshots": True})
    previous = rig.observe()
    old_handle = previous.context["view_window_id"]
    rig.front = 999
    if change == "disappeared":
        rig.rows = [r for r in rig.rows if r["kCGWindowNumber"] != 1]
    elif change == "revoked":
        rig.d.navigation.sharing.roots["editor"].discard((10, 1))
    else:
        rig.rows.insert(0, row(5, bounds=(40, 45, 90, 80)))
        rig.roles[5] = "AXSheet"
        rig.sheets[1] = [5]
    rig.captures.clear()
    current = rig.observe()
    assert current.context["active_window_id"] is None and current.context["view_window_id"] is None
    if change == "shared_overlay":
        assert 5 in rig.captures
    else:
        assert old_handle not in {w["window_id"] for w in current.context["windows"]}
        assert 1 not in rig.captures


def test_focus_loss_after_input_started_is_not_a_recoverable_rejection():
    rig = NativeRig()
    observed = rig.observe()

    def perform(_):
        rig.posts.append("partial input")
        rig.front = 999
        rig.d.navigation.expected = rig.d.navigation.refresh()
        rig.d.navigation.check()

    rig.d._perform_action = perform
    with pytest.raises(RuntimeError, match="focus a permitted window") as error:
        rig.act(Action(kind="type", text="partly sent"), observed)
    assert not isinstance(error.value, InputNotStarted)
    assert rig.posts == ["partial input"] and rig.d.last is None


def test_worker_settling_returns_latest_pixels_token_and_aggregate_timing(monkeypatch):
    rig = NativeRig()
    rig.d.settings = rig.d.settings.model_copy(
        update={"active_window_screenshots": True, "observation_settle_seconds": 0.25}
    )
    clock = [100.0]
    monkeypatch.setattr("openjiuwen.harness.tools.desktop.macos_navigation.time.monotonic", lambda: clock[0])
    rig.d._inputs().pause = lambda delay: clock.__setitem__(0, clock[0] + delay)
    capture = rig.capture

    def moving(native_id):
        clock[0] += 0.02
        image = capture(native_id)
        if len(rig.captures) == 1:
            image.paste("green", (0, 0, image.width, image.height))
        return image

    rig.d.navigation.capture_window = moving
    result = rig.d.observe()
    assert result == asdict(rig.d.last)
    assert result["generation"] == 3 and len(rig.captures) == 3
    assert result["performance"]["settled"] is True
    assert result["performance"]["settle_samples"] == result["performance"]["window_captures"] == 3
    assert result["performance"]["total_seconds"] == pytest.approx(0.16)
    assert result["performance"]["capture_compose_seconds"] == pytest.approx(0.06)
    assert not rig.posts and not rig.raised
