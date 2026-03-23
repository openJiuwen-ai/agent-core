# -*- coding: utf-8 -*-
"""Unit tests for trajectory module: TrajectoryCollectionRail, TrajectoryCollector."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.dev_tools.agentrl.agent_runtime.trajectory import (
    TrajectoryCollector,
    TrajectoryCollectionRail,
)
from openjiuwen.dev_tools.agentrl.coordinator.schemas import Rollout
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs


@pytest.mark.asyncio
async def test_trajectory_collection_rail_basic():
    """TrajectoryCollectionRail collects one Rollout per LLM turn."""
    rail = TrajectoryCollectionRail()

    mock_ctx_before = MagicMock(spec=AgentCallbackContext)
    mock_ctx_before.inputs = MagicMock(
        messages=[{"role": "user", "content": "test query"}]
    )
    mock_ctx_before.inputs.tools = [{"name": "test_tool", "description": "test tool"}]
    mock_ctx_before.agent = None

    await rail.before_model_call(mock_ctx_before)

    mock_response = MagicMock()
    mock_response.content = "test response"
    mock_response.tool_calls = []
    # Responses without model_dump use the fallback path
    del mock_response.model_dump

    mock_ctx_after = MagicMock(spec=AgentCallbackContext)
    mock_ctx_after.inputs = MagicMock(response=mock_response)

    await rail.after_model_call(mock_ctx_after)

    rollouts = rail.get_rollouts()
    assert len(rollouts) == 1
    assert rollouts[0].turn_id == 0
    assert rollouts[0].input_prompt["message"] == [{"role": "user", "content": "test query"}]
    assert rollouts[0].output_response["role"] == "assistant"
    assert rollouts[0].output_response["content"] == "test response"

    rail.clear()
    assert len(rail.get_rollouts()) == 0


@pytest.mark.asyncio
async def test_trajectory_collection_rail_with_tool_calls():
    """TrajectoryCollectionRail captures tool calls in the LLM response."""
    rail = TrajectoryCollectionRail()

    mock_ctx_before = MagicMock(spec=AgentCallbackContext)
    mock_ctx_before.inputs = MagicMock(
        messages=[{"role": "user", "content": "test query"}]
    )
    mock_ctx_before.inputs.tools = []
    mock_ctx_before.agent = None

    await rail.before_model_call(mock_ctx_before)

    mock_tool_call = MagicMock()
    mock_tool_call.id = "tc1"
    mock_tool_call.type = "function"
    mock_tool_call.name = "test_tool"
    mock_tool_call.arguments = '{"param": "value"}'

    mock_response = MagicMock()
    mock_response.content = "Thinking..."
    mock_response.tool_calls = [mock_tool_call]
    del mock_response.model_dump

    mock_ctx_after = MagicMock(spec=AgentCallbackContext)
    mock_ctx_after.inputs = MagicMock(response=mock_response)

    await rail.after_model_call(mock_ctx_after)

    rollouts = rail.get_rollouts()
    assert len(rollouts) == 1
    assert "tool_calls" in rollouts[0].output_response
    assert rollouts[0].output_response["tool_calls"][0]["function"]["name"] == "test_tool"


@pytest.mark.asyncio
async def test_after_tool_call_captures_result():
    """after_tool_call stores actual tool result; before_model_call uses it to patch messages."""
    rail = TrajectoryCollectionRail()

    # Simulate after_tool_call capturing "52.5"
    mock_tc = MagicMock()
    mock_tc.id = "call-123"

    tc_inputs = MagicMock(spec=ToolCallInputs)
    tc_inputs.tool_call = mock_tc
    tc_inputs.tool_result = "52.5"
    tc_inputs.tool_msg = None

    mock_ctx_tool = MagicMock(spec=AgentCallbackContext)
    mock_ctx_tool.inputs = tc_inputs

    await rail.after_tool_call(mock_ctx_tool)

    # Now before_model_call sees a tool message with wrong content
    bad_tool_msg = {
        "role": "tool",
        "content": "(tool returned non-serializable value)",
        "tool_call_id": "call-123",
    }
    mock_ctx_before = MagicMock(spec=AgentCallbackContext)
    mock_ctx_before.inputs = MagicMock(messages=[bad_tool_msg])
    mock_ctx_before.inputs.tools = None
    mock_ctx_before.agent = None

    await rail.before_model_call(mock_ctx_before)

    mock_response = MagicMock()
    mock_response.content = "ok"
    mock_response.tool_calls = []
    del mock_response.model_dump

    mock_ctx_after = MagicMock(spec=AgentCallbackContext)
    mock_ctx_after.inputs = MagicMock(response=mock_response)

    await rail.after_model_call(mock_ctx_after)

    rollouts = rail.get_rollouts()
    assert len(rollouts) == 1
    assert rollouts[0].input_prompt["message"][0]["content"] == "52.5"


@pytest.mark.asyncio
async def test_trajectory_collector_basic():
    """TrajectoryCollector registers the rail and collects rollouts."""
    mock_agent = MagicMock()
    mock_agent.register_rail = AsyncMock()
    mock_agent.unregister_rail = AsyncMock()
    mock_agent.invoke = AsyncMock()

    collector = TrajectoryCollector()
    result = await collector.collect(mock_agent, {"query": "test"})

    assert isinstance(result, list)
    mock_agent.register_rail.assert_called_once()
    mock_agent.invoke.assert_called_once_with({"query": "test"})


@pytest.mark.asyncio
async def test_trajectory_collector_raises_for_unsupported_agent():
    """TrajectoryCollector raises ValueError if agent lacks register_rail."""

    class PlainAgent:
        pass

    collector = TrajectoryCollector()
    with pytest.raises(ValueError, match="register_rail"):
        await collector.collect(PlainAgent(), {"query": "test"})


@pytest.mark.asyncio
async def test_trajectory_collector_partial_on_exception():
    """TrajectoryCollector returns partial rollouts even when agent raises."""
    rail_holder = {}

    async def mock_register_rail(rail):
        rail_holder["rail"] = rail

    async def mock_invoke(inputs):
        # Simulate one LLM turn before crashing
        rail = rail_holder["rail"]
        ctx = MagicMock(spec=AgentCallbackContext)
        ctx.inputs = MagicMock(messages=[{"role": "user", "content": "q"}])
        ctx.inputs.tools = None
        ctx.agent = None
        await rail.before_model_call(ctx)

        resp = MagicMock()
        resp.content = "partial"
        resp.tool_calls = []
        del resp.model_dump
        ctx2 = MagicMock(spec=AgentCallbackContext)
        ctx2.inputs = MagicMock(response=resp)
        await rail.after_model_call(ctx2)

        raise RuntimeError("something went wrong")

    mock_agent = MagicMock()
    mock_agent.register_rail = mock_register_rail
    mock_agent.unregister_rail = AsyncMock()
    mock_agent.invoke = mock_invoke

    collector = TrajectoryCollector()
    result = await collector.collect(mock_agent, {"query": "test"})

    # Should get the one turn that completed before the crash
    assert len(result) == 1
    assert result[0].output_response["content"] == "partial"
