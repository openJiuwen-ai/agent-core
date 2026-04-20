# -*- coding: utf-8 -*-
"""Unit tests for trajectory module: RLRail, TrajectoryCollector."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.agent_evolving.agent_rl.offline.runtime.collector import (
    TrajectoryCollector,
)
from openjiuwen.agent_evolving.agent_rl.rl_rail import RLRail
from openjiuwen.agent_evolving.trajectory import InMemoryTrajectoryStore
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    InvokeInputs,
    ModelCallInputs,
)


def _ctx(inputs) -> MagicMock:
    ctx = MagicMock(spec=AgentCallbackContext)
    ctx.inputs = inputs
    ctx.agent = None
    return ctx


@pytest.mark.asyncio
async def test_rl_rail_uses_evolution_rail_flow():
    """Test RLRail works with EvolutionRail's base class flow."""
    store = InMemoryTrajectoryStore()
    rail = RLRail(session_id="test-session", case_id="case-123", trajectory_store=store)

    invoke_inputs = InvokeInputs(query="hi", conversation_id="test-session")
    await rail.before_invoke(_ctx(invoke_inputs))

    before = ModelCallInputs(
        messages=[{"role": "user", "content": "test query"}],
        tools=[{"name": "test_tool", "description": "test tool"}],
    )
    await rail.before_model_call(_ctx(before))

    mock_response = MagicMock()
    mock_response.content = "test response"
    mock_response.tool_calls = []
    del mock_response.model_dump

    after = ModelCallInputs(
        messages=[{"role": "user", "content": "test query"}],
        tools=[{"name": "test_tool", "description": "test tool"}],
        response=mock_response,
    )
    await rail.after_model_call(_ctx(after))

    await rail.after_invoke(_ctx(invoke_inputs))

    trajectories = store.query()
    assert len(trajectories) == 1
    assert trajectories[0].session_id == "test-session"
    step0 = trajectories[0].steps[0]
    assert step0.meta.get("turn_id") == 0
    assert step0.meta.get("case_id") == "case-123"


@pytest.mark.asyncio
async def test_rl_rail_with_tool_calls():
    """Test RLRail handles tool calls correctly."""
    store = InMemoryTrajectoryStore()
    rail = RLRail(trajectory_store=store)

    invoke_inputs = InvokeInputs(query="q", conversation_id="test")
    await rail.before_invoke(_ctx(invoke_inputs))

    response_dict = {
        "role": "assistant",
        "content": "Thinking...",
        "tool_calls": [
            {
                "id": "tc1",
                "type": "function",
                "function": {
                    "name": "test_tool",
                    "arguments": '{"param": "value"}',
                },
            }
        ],
    }

    after = ModelCallInputs(
        messages=[{"role": "user", "content": "test query"}],
        response=response_dict,
    )
    await rail.after_model_call(_ctx(after))
    await rail.after_invoke(_ctx(invoke_inputs))

    trajectories = store.query()
    assert len(trajectories) == 1
    response = trajectories[0].steps[0].detail.response
    assert response["tool_calls"][0]["function"]["name"] == "test_tool"


@pytest.mark.asyncio
async def test_trajectory_collector_basic():
    """Test TrajectoryCollector registers rail; mock invoke does not emit after_invoke."""
    mock_agent = MagicMock()
    mock_agent.register_rail = AsyncMock()
    mock_agent.unregister_rail = AsyncMock()
    mock_agent.invoke = AsyncMock()

    collector = TrajectoryCollector()
    result = await collector.collect(mock_agent, {"query": "test"})

    assert result is None
    mock_agent.register_rail.assert_called_once()
    mock_agent.invoke.assert_called_once()


@pytest.mark.asyncio
async def test_trajectory_collector_raises_for_unsupported_agent():
    """Test TrajectoryCollector rejects agents without register_rail."""
    class PlainAgent:
        pass

    collector = TrajectoryCollector()
    with pytest.raises(ValueError, match="register_rail"):
        await collector.collect(PlainAgent(), {"query": "test"})


@pytest.mark.asyncio
async def test_trajectory_collector_partial_on_exception():
    """Test TrajectoryCollector returns trajectory when mock simulates full rail flow."""
    rail_holder = {}

    async def mock_register_rail(rail):
        rail_holder["rail"] = rail

    async def mock_invoke(inputs, session=None):
        rail = rail_holder["rail"]
        invoke_inputs = InvokeInputs(query="test", conversation_id="test")
        await rail.before_invoke(_ctx(invoke_inputs))

        after = ModelCallInputs(
            messages=[{"role": "user", "content": "q"}],
            response=MagicMock(content="partial", tool_calls=[]),
        )
        del after.response.model_dump
        await rail.after_model_call(_ctx(after))
        await rail.after_invoke(_ctx(invoke_inputs))

        raise RuntimeError("something went wrong")

    mock_agent = MagicMock()
    mock_agent.register_rail = mock_register_rail
    mock_agent.unregister_rail = AsyncMock()
    mock_agent.invoke = mock_invoke

    collector = TrajectoryCollector()
    result = await collector.collect(mock_agent, {"query": "test"})

    assert result is not None
    assert hasattr(result, "steps")
    assert len(result.steps) == 1
