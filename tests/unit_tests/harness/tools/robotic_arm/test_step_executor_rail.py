#!/usr/bin/env python
# coding: utf-8
"""Tests for StepExecutorRail: construction validation and running execution
after report_plan, all via the inner ReActAgent's own ctx.extra (no cross-layer
bridging)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, InvokeInputs, ToolCallInputs
from openjiuwen.harness.tools.robotic_arm.config import RoboticArmRuntimeSettings
from openjiuwen.harness.tools.robotic_arm.rails.step_executor_rail import StepExecutorRail


def _make_tool_call_ctx(*, tool_name: str = "report_plan", extra: dict | None = None) -> AgentCallbackContext:
    ctx = AgentCallbackContext(agent=MagicMock(), inputs=ToolCallInputs(tool_name=tool_name), extra=extra or {})
    ctx.bind_steering_queue(asyncio.Queue())
    return ctx


def test_direct_handle_is_used_as_is() -> None:
    executor = MagicMock()
    settings = RoboticArmRuntimeSettings(step_executor=executor, health_check=False)

    rail = StepExecutorRail(settings)

    assert rail._step_executor is executor


def test_missing_step_executor_raises() -> None:
    settings = RoboticArmRuntimeSettings()

    with pytest.raises(ValueError, match="step_executor or step_executor_model"):
        StepExecutorRail(settings)


def test_health_check_failure_does_not_raise() -> None:
    executor = MagicMock(capture=MagicMock(side_effect=RuntimeError("camera offline")))
    settings = RoboticArmRuntimeSettings(step_executor=executor, health_check=True)

    rail = StepExecutorRail(settings)  # must not raise

    assert rail._step_executor is executor
    executor.capture.assert_called_once()


def test_health_check_disabled_skips_capture() -> None:
    executor = MagicMock()
    settings = RoboticArmRuntimeSettings(step_executor=executor, health_check=False)

    StepExecutorRail(settings)

    executor.capture.assert_not_called()


@pytest.mark.asyncio
async def test_ignores_non_tool_call_events() -> None:
    executor = MagicMock()
    rail = StepExecutorRail(RoboticArmRuntimeSettings(step_executor=executor, health_check=False))
    ctx = AgentCallbackContext(agent=MagicMock(), inputs=InvokeInputs(query="go"), extra={})

    await rail.after_tool_call(ctx)

    executor.execute.assert_not_called()


@pytest.mark.asyncio
async def test_ignores_other_tool_calls() -> None:
    executor = MagicMock()
    rail = StepExecutorRail(RoboticArmRuntimeSettings(step_executor=executor, health_check=False))
    ctx = _make_tool_call_ctx(tool_name="some_other_tool", extra={"last_plan_sub_tasks": [{"status": "in_progress"}]})

    await rail.after_tool_call(ctx)

    executor.execute.assert_not_called()


@pytest.mark.asyncio
async def test_no_sub_tasks_is_a_noop() -> None:
    executor = MagicMock()
    rail = StepExecutorRail(RoboticArmRuntimeSettings(step_executor=executor, health_check=False))

    await rail.after_tool_call(_make_tool_call_ctx(extra={}))

    executor.execute.assert_not_called()


@pytest.mark.asyncio
async def test_no_in_progress_task_is_a_noop() -> None:
    executor = MagicMock()
    rail = StepExecutorRail(RoboticArmRuntimeSettings(step_executor=executor, health_check=False))
    ctx = _make_tool_call_ctx(extra={"last_plan_sub_tasks": [{"id": "s1", "status": "done"}]})

    await rail.after_tool_call(ctx)

    executor.execute.assert_not_called()


@pytest.mark.asyncio
async def test_executes_in_progress_task_with_raw_model_coordinates() -> None:
    executor = MagicMock(execute=MagicMock(return_value="Success: picked up the cup"))
    rail = StepExecutorRail(RoboticArmRuntimeSettings(step_executor=executor, health_check=False))
    frame = object()
    sub_task = {
        "id": "s1",
        "description": "pick up the cup",
        "status": "in_progress",
        "start_x": 500,
        "start_y": 500,
    }
    ctx = _make_tool_call_ctx(extra={"last_plan_sub_tasks": [sub_task], "vlm_raw_frame": frame})

    await rail.after_tool_call(ctx)

    executor.execute.assert_called_once()
    called_frame, called_task = executor.execute.call_args.args
    assert called_frame is frame
    assert called_task is sub_task
    assert called_task["start_x"] == 500
    assert called_task["start_y"] == 500

    messages = ctx.drain_steering()
    assert any("Success: picked up the cup" in m for m in messages)


@pytest.mark.asyncio
async def test_executor_exception_is_reported_not_raised() -> None:
    executor = MagicMock(execute=MagicMock(side_effect=RuntimeError("gripper jam")))
    rail = StepExecutorRail(RoboticArmRuntimeSettings(step_executor=executor, health_check=False))
    ctx = _make_tool_call_ctx(
        extra={
            "last_plan_sub_tasks": [{"id": "s1", "status": "in_progress"}],
            "vlm_raw_frame": object(),
        }
    )

    await rail.after_tool_call(ctx)

    messages = ctx.drain_steering()
    assert any("StepExecutionFailed" in m and "gripper jam" in m for m in messages)
