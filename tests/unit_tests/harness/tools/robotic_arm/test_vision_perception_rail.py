#!/usr/bin/env python
# coding: utf-8
"""Tests for VisionPerceptionRail: photo capture, observation injection, and
deriving the pinned goal from message history (no outer-layer before_invoke)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image

from openjiuwen.core.foundation.llm import UserMessage
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, InvokeInputs, ModelCallInputs
from openjiuwen.harness.tools.robotic_arm.config import RoboticArmRuntimeSettings
from openjiuwen.harness.tools.robotic_arm.rails.vision_perception_rail import VisionPerceptionRail


def _make_model_call_ctx(*, context=None, messages=None) -> AgentCallbackContext:
    return AgentCallbackContext(
        agent=MagicMock(),
        inputs=ModelCallInputs(messages=messages or []),
        extra={},
        context=context,
    )


class _FakeModelContext:
    def __init__(self, messages: list) -> None:
        self._messages = messages

    def get_messages(self):
        return self._messages

    def set_messages(self, messages):
        self._messages = messages

    def pop_messages(self, n: int):
        popped, self._messages = self._messages[-n:], self._messages[:-n]
        return popped

    async def add_messages(self, msg):
        self._messages.append(msg)


def test_ignores_non_model_call_events() -> None:
    rail = VisionPerceptionRail(RoboticArmRuntimeSettings(step_executor=MagicMock()))
    ctx = AgentCallbackContext(agent=MagicMock(), inputs=InvokeInputs(query="go"), extra={})

    import asyncio

    asyncio.run(rail.before_model_call(ctx))

    assert "pinned_user_goal" not in ctx.extra


@pytest.mark.asyncio
async def test_pinned_goal_derived_from_first_user_message() -> None:
    executor = MagicMock(capture=MagicMock(side_effect=RuntimeError("no camera in this test")))
    rail = VisionPerceptionRail(RoboticArmRuntimeSettings(step_executor=executor))
    fake_context = _FakeModelContext([UserMessage(content="pick up the cup and pour water")])
    ctx = _make_model_call_ctx(context=fake_context)

    await rail.before_model_call(ctx)

    assert ctx.extra["pinned_user_goal"] == "pick up the cup and pour water"


@pytest.mark.asyncio
async def test_pinned_goal_is_cached_not_rederived() -> None:
    executor = MagicMock(capture=MagicMock(side_effect=RuntimeError("no camera in this test")))
    rail = VisionPerceptionRail(RoboticArmRuntimeSettings(step_executor=executor))
    fake_context = _FakeModelContext([UserMessage(content="original goal")])
    ctx = _make_model_call_ctx(context=fake_context)
    ctx.extra["pinned_user_goal"] = "already cached"

    await rail.before_model_call(ctx)

    assert ctx.extra["pinned_user_goal"] == "already cached"


@pytest.mark.asyncio
async def test_missing_step_executor_skips_capture_without_raising() -> None:
    rail = VisionPerceptionRail(RoboticArmRuntimeSettings(step_executor=MagicMock()))
    rail._step_executor = None
    fake_context = _FakeModelContext([UserMessage(content="goal")])
    ctx = _make_model_call_ctx(context=fake_context)

    await rail.before_model_call(ctx)  # must not raise

    assert "vlm_raw_frame" not in ctx.extra


@pytest.mark.asyncio
async def test_successful_capture_injects_observation_with_image() -> None:
    frame = Image.new("RGB", (640, 480), color=(1, 2, 3))
    executor = MagicMock(capture=MagicMock(return_value=frame))
    rail = VisionPerceptionRail(RoboticArmRuntimeSettings(step_executor=executor))
    fake_context = _FakeModelContext([UserMessage(content="pick up the cup")])
    ctx = _make_model_call_ctx(context=fake_context, messages=[UserMessage(content="pick up the cup")])

    await rail.before_model_call(ctx)

    assert ctx.extra["vlm_raw_frame"] is frame
    last_msg = fake_context.get_messages()[-1]
    assert any(block.get("type") == "image_url" for block in last_msg.content)
    assert any("[Task Goal] pick up the cup" in block.get("text", "") for block in last_msg.content if "text" in block)


@pytest.mark.asyncio
async def test_on_frame_captured_callback_invoked_with_image() -> None:
    frame = Image.new("RGB", (640, 480), color=(1, 2, 3))
    executor = MagicMock(capture=MagicMock(return_value=frame))
    on_frame_captured = AsyncMock()
    rail = VisionPerceptionRail(RoboticArmRuntimeSettings(step_executor=executor, on_frame_captured=on_frame_captured))
    fake_context = _FakeModelContext([UserMessage(content="pick up the cup")])
    ctx = _make_model_call_ctx(context=fake_context, messages=[UserMessage(content="pick up the cup")])

    await rail.before_model_call(ctx)

    on_frame_captured.assert_awaited_once()
    (payload,), _ = on_frame_captured.call_args
    assert payload["width"] == 640
    assert payload["height"] == 480
    assert isinstance(payload["image_base64"], str) and payload["image_base64"]


@pytest.mark.asyncio
async def test_on_frame_captured_callback_exception_does_not_raise() -> None:
    frame = Image.new("RGB", (640, 480), color=(1, 2, 3))
    executor = MagicMock(capture=MagicMock(return_value=frame))
    on_frame_captured = AsyncMock(side_effect=RuntimeError("stream closed"))
    rail = VisionPerceptionRail(RoboticArmRuntimeSettings(step_executor=executor, on_frame_captured=on_frame_captured))
    fake_context = _FakeModelContext([UserMessage(content="pick up the cup")])
    ctx = _make_model_call_ctx(context=fake_context, messages=[UserMessage(content="pick up the cup")])

    await rail.before_model_call(ctx)  # must not raise

    on_frame_captured.assert_awaited_once()
    assert ctx.extra["vlm_raw_frame"] is frame


@pytest.mark.asyncio
async def test_capture_failure_does_not_inject_observation() -> None:
    executor = MagicMock(capture=MagicMock(side_effect=RuntimeError("camera disconnected")))
    rail = VisionPerceptionRail(RoboticArmRuntimeSettings(step_executor=executor))
    fake_context = _FakeModelContext([UserMessage(content="goal")])
    ctx = _make_model_call_ctx(context=fake_context, messages=[UserMessage(content="goal")])

    await rail.before_model_call(ctx)

    assert "vlm_raw_frame" not in ctx.extra
    assert len(fake_context.get_messages()) == 1
