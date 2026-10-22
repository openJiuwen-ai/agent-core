"""Batch safety and progress through the same public tool as individual actions."""

import asyncio
from dataclasses import replace

import pytest

from openjiuwen.core.session.agent import Session
from openjiuwen.harness.tools.desktop.direct_protocol import decode_call, input_schema
from tests.unit_tests.harness.tools.desktop.test_direct import observe, setup


def call(token, actions, **kwargs):
    return {"operation": "batch", "observation_id": token, "actions": actions, **kwargs}


@pytest.mark.asyncio
async def test_batch_captures_between_primitives_and_returns_only_final_image():
    tool, state, desktop, lease = setup()
    first = await observe(tool)
    result = await tool.invoke(
        call(
            first.data["observation_id"],
            [
                {"operation": "click", "x": 20, "y": 30},
                {"operation": "type", "text": "exact text"},
                {"operation": "wait", "seconds": 0.1},
            ],
            release_after=True,
        ),
        session=Session("owner"),
    )
    assert result.success and result.data["batch"]["completed"] == 3
    assert len(desktop.actions) == 3 and state.observations == 4
    assert len(result.data["multimodal"]) == 1
    assert lease.released and desktop.closed


@pytest.mark.asyncio
async def test_invalid_late_action_rejects_whole_batch():
    tool, state, desktop, _ = setup()
    token = (await observe(tool)).data["observation_id"]
    result = await tool.invoke(
        call(token, [{"operation": "type", "text": "never typed"}, {"operation": "click", "x": 10, "y": 10}]),
        session=Session("owner"),
    )
    assert not result.success and not desktop.actions and state.token == token
    await tool.close()


@pytest.mark.asyncio
async def test_budget_rejects_before_any_input_and_stale_token_is_not_filled():
    tool, state, desktop, _ = setup(max_steps=1)
    token = (await observe(tool)).data["observation_id"]
    for supplied in ("stale", token):
        result = await tool.invoke(
            call(supplied, [{"operation": "type", "text": "a"}, {"operation": "type", "text": "b"}]),
            session=Session("owner"),
        )
        assert not result.success and not desktop.actions
    await tool.close()


@pytest.mark.asyncio
async def test_geometry_change_returns_partial_progress_without_replaying():
    tool, state, desktop, _ = setup()
    token = (await observe(tool)).data["observation_id"]
    original = desktop.observe

    async def changed():
        return replace(await original(), bounds=(10, 10, 100, 100))

    desktop.observe = changed
    result = await tool.invoke(
        call(token, [{"operation": "type", "text": "first"}, {"operation": "type", "text": "second"}]),
        session=Session("owner"),
    )
    assert result.success and result.data["batch"]["completed"] == 1
    assert result.data["batch"]["remaining"] == 1 and result.data["observation_id"] != token
    assert len(desktop.actions) == 1
    await tool.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_capture", [False, True])
async def test_preinput_stale_refresh_preserves_session_or_closes_if_capture_fails(fail_capture):
    from openjiuwen.harness.tools.desktop.errors import InputNotStarted

    tool, state, desktop, lease = setup()
    token = (await observe(tool)).data["observation_id"]

    async def stale(_action, _observation):
        desktop.fail_capture = fail_capture
        raise InputNotStarted("screen changed before input")

    desktop.act = stale
    result = await tool.invoke(
        call(token, [{"operation": "type", "text": "never"}, {"operation": "type", "text": "also never"}]),
        session=Session("owner"),
    )
    assert not desktop.actions and result.data["batch"]["completed"] == 0
    assert state.closed is fail_capture and lease.released is fail_capture
    if not fail_capture:
        assert result.data["observation_id"] != token and result.data["action_applied"] is False
        assert result.data["batch"]["remaining"] == 2
    await tool.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("initially_actionable,late_click", [(False, False), (True, False), (True, True)])
async def test_hit_state_change_keeps_keyboard_batch_but_revalidates_pending_click(initially_actionable, late_click):
    tool, state, desktop, _ = setup()
    original = desktop.observe

    async def controls():
        observation = await original()
        return replace(
            observation,
            elements=[
                {
                    "id": str(observation.generation),
                    "role": "AXButton",
                    "label": "Apply",
                    "bounds": [10, 10, 20, 20],
                    "enabled": True,
                    "actionable": initially_actionable if observation.generation == 1 else not initially_actionable,
                }
            ],
        )

    desktop.observe = controls
    token = (await observe(tool)).data["observation_id"]
    following = {"operation": "click", "element_id": "1"} if late_click else {"operation": "type", "text": "second"}
    result = await tool.invoke(
        call(token, [{"operation": "type", "text": "first"}, following]), session=Session("owner")
    )
    assert result.success and result.data["batch"]["completed"] == (1 if late_click else 2)
    assert len(desktop.actions) == (1 if late_click else 2)
    if late_click:
        assert "bound accessibility control changed" in result.data["batch"]["stop_reason"]
    await tool.close()


@pytest.mark.asyncio
async def test_cancellation_during_batch_releases_worker_and_lease():
    tool, state, desktop, lease = setup()
    token = (await observe(tool)).data["observation_id"]
    desktop.block_action = True
    running = asyncio.create_task(
        tool.invoke(call(token, [{"operation": "type", "text": "x"}]), session=Session("owner"))
    )
    await desktop.entered.wait()
    await tool.cancel_session("owner")
    assert running.cancelled() and lease.released and desktop.closed


def test_batch_disabled_and_nested_terminal_rejected():
    assert "batch" not in input_schema(max_batch_actions=1)["properties"]["operation"]["enum"]
    for actions in (
        [{"operation": "done"}],
        [{"operation": "launch", "target": "notes"}],
        [{"operation": "type", "text": "x", "release_after": True}],
    ):
        with pytest.raises(ValueError):
            decode_call(call("token", actions))
    with pytest.raises(ValueError):
        decode_call(call("token", [{"operation": "type", "text": "x"}]), max_batch_actions=1)


@pytest.mark.asyncio
async def test_late_id_click_binds_control_identity_and_uses_fresh_id():
    tool, state, desktop, _ = setup()
    original = desktop.observe

    async def controls():
        observation = await original()
        return replace(
            observation,
            elements=[
                {
                    "id": str(observation.generation),
                    "role": "AXButton",
                    "label": "Save",
                    "bounds": [10, 10, 20, 20],
                    "enabled": True,
                    "actionable": True,
                }
            ],
        )

    desktop.observe = controls
    token = (await observe(tool)).data["observation_id"]
    initial_id = state.observation.elements[0]["id"]
    result = await tool.invoke(
        call(token, [{"operation": "type", "text": "x"}, {"operation": "click", "element_id": initial_id}]),
        session=Session("owner"),
    )
    assert result.success and result.data["batch"]["completed"] == 2
    assert desktop.actions[1].element_id != initial_id
    assert desktop.actions[1].element_id == "2"
    await tool.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_target", [False, True])
async def test_dynamic_display_and_unrelated_label_keep_batch_but_changed_target_stops(changed_target):
    tool, state, desktop, _ = setup()
    original = desktop.observe

    async def controls():
        observation = await original()
        changed = observation.generation > 1
        return replace(
            observation,
            elements=[
                {
                    "id": str(observation.generation),
                    "role": "AXButton",
                    "label": "Changed" if changed and changed_target else "Save",
                    "bounds": [10, 10, 20, 20],
                    "enabled": True,
                    "actionable": True,
                },
                {
                    "id": "other",
                    "role": "AXButton",
                    "label": "C" if changed else "AC",
                    "bounds": [50, 10, 20, 20],
                    "enabled": True,
                    "actionable": True,
                },
                {
                    "id": "display",
                    "role": "AXStaticText",
                    "label": str(observation.generation),
                    "bounds": [10, 50, 10 * observation.generation, 20],
                },
            ],
        )

    desktop.observe = controls
    token = (await observe(tool)).data["observation_id"]
    result = await tool.invoke(
        call(
            token,
            [
                {"operation": "type", "text": "x"},
                {"operation": "click", "element_id": "1"},
            ],
        ),
        session=Session("owner"),
    )
    assert result.success and result.data["batch"]["completed"] == (1 if changed_target else 2)
    assert len(desktop.actions) == (1 if changed_target else 2)
    await tool.close()


@pytest.mark.asyncio
async def test_integrated_signal_wait_polls_locally_and_counts_native_budget():
    tool, state, desktop, _ = setup(max_steps=5)
    original = desktop.observe

    async def changing():
        observation = await original()
        return replace(observation, png_b64="changed" if observation.generation >= 3 else observation.png_b64)

    desktop.observe = changing
    token = (await observe(tool)).data["observation_id"]
    result = await tool.invoke(
        call(token, [{"operation": "type", "text": "x"}], wait_for={"signal": "screen_change", "timeout_seconds": 1}),
        session=Session("owner"),
    )
    assert result.success and result.data["wait_for"]["met"]
    assert result.data["wait_for"]["polls"] == 1
    assert result.data["batch"]["completed"] == 1 and state.steps == 2
    assert [a.kind for a in desktop.actions] == ["type", "wait"]
    assert len(result.data["multimodal"]) == 1
    await tool.close()


@pytest.mark.asyncio
async def test_unmet_signal_returns_explicit_timeout_without_replaying_input():
    tool, state, desktop, _ = setup()
    token = (await observe(tool)).data["observation_id"]
    result = await tool.invoke(
        call(
            token,
            [{"operation": "type", "text": "x"}],
            wait_for={"signal": "control_enabled", "label": "Save", "timeout_seconds": 0},
        ),
        session=Session("owner"),
    )
    assert result.success and not result.data["wait_for"]["met"]
    assert result.data["wait_for"]["reason"] == "signal timeout"
    assert len(desktop.actions) == 1
    await tool.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("use_id", [True, False])
async def test_screenshot_scale_change_rebinds_ax_id_but_stops_pixel_click(use_id):
    tool, state, desktop, _ = setup()
    original = desktop.observe

    async def controls():
        observation = await original()
        height = 100 if observation.generation == 1 else 90
        return replace(
            observation,
            height=height,
            elements=[
                {
                    "id": str(observation.generation),
                    "role": "AXButton",
                    "label": "Save",
                    "bounds": [10, height / 10, 20, height / 5],
                    "enabled": True,
                    "actionable": True,
                }
            ],
        )

    desktop.observe = controls
    token = (await observe(tool)).data["observation_id"]
    click = {"operation": "click", **({"element_id": "1"} if use_id else {"x": 20, "y": 20})}
    result = await tool.invoke(call(token, [{"operation": "type", "text": "x"}, click]), session=Session("owner"))
    assert result.success
    assert result.data["batch"]["completed"] == (2 if use_id else 1)
    assert len(desktop.actions) == (2 if use_id else 1)
    if use_id:
        assert desktop.actions[1].element_id == "2"
    await tool.close()
