# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for DeepAgent public APIs."""
from __future__ import annotations

from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import pytest

from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.session.stream.base import StreamMode
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentCallbackEvent,
    AgentRail,
)
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.deepagents import create_deep_agent
from openjiuwen.deepagents.deep_agent import DeepAgent
from openjiuwen.deepagents.schema.config import DeepAgentConfig
from openjiuwen.deepagents.task_loop.task_loop_event_handler import TaskLoopEventHandler
from openjiuwen.deepagents.task_loop.loop_coordinator import LoopCoordinator


class DummyModel:
    """Minimal model stub for create_deep_agent unit tests."""

    def __init__(self) -> None:
        self.model_client_config = None
        self.model_config = None



class FakeInnerCallbackManager:
    def __init__(self) -> None:
        self.unregister_calls: List[Tuple[AgentRail, Any]] = []

    async def unregister_rail(self, rail: AgentRail, agent: Any) -> None:
        self.unregister_calls.append((rail, agent))


class FakeReactAgent:
    def __init__(self) -> None:
        self.invoke_calls: List[Dict[str, Any]] = []
        self.stream_calls: List[Dict[str, Any]] = []
        self.registered_callbacks: List[Tuple[AgentCallbackEvent, Any, int]] = []
        self.agent_callback_manager = FakeInnerCallbackManager()

    async def register_callback(
        self,
        event: AgentCallbackEvent,
        callback: Any,
        priority: int,
    ) -> None:
        self.registered_callbacks.append((event, callback, priority))

    async def invoke(
        self,
        inputs: Dict[str, Any],
        session: Optional[Any] = None,
    ) -> Dict[str, Any]:
        self.invoke_calls.append({"inputs": inputs, "session": session})
        return {
            "output": f"echo:{inputs['query']}",
            "result_type": "answer",
        }

    async def stream(
        self,
        inputs: Dict[str, Any],
        session: Optional[Any] = None,
        stream_modes: Optional[List[StreamMode]] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        self.stream_calls.append(
            {
                "inputs": inputs,
                "session": session,
                "stream_modes": stream_modes,
            }
        )
        yield {"chunk": 1, "query": inputs["query"]}
        yield {"chunk": 2, "query": inputs["query"]}


class CountingRail(AgentRail):
    def __init__(self) -> None:
        super().__init__()
        self.before_invoke_count = 0
        self.after_invoke_count = 0
        self.before_tool_call_count = 0

    def init(self, agent):
        rail_tool = _build_tool_card("rail_tool")
        agent.ability_manager.add(rail_tool)

    def uninit(self, agent):
        agent.ability_manager.remove("rail_tool")

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        _ = ctx
        self.before_invoke_count += 1

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        _ = ctx
        self.after_invoke_count += 1

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        _ = ctx
        self.before_tool_call_count += 1


def _build_tool_card(name: str) -> ToolCard:
    return ToolCard(name=name, description=f"{name} tool")


def test_configure_set_react_agent_and_is_initialized() -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test"))

    configured = agent.configure(
        DeepAgentConfig(enable_task_loop=False, max_iterations=3)
    )
    assert configured is agent
    assert agent.is_initialized is False

    fake_react = FakeReactAgent()
    set_result = agent.set_react_agent(fake_react, initialized=True)
    assert set_result is agent
    assert agent.is_initialized is True

    assert agent.loop_coordinator is None


@pytest.mark.asyncio
async def test_add_rail_lazy_register_on_first_invoke() -> None:
    agent = DeepAgent(
        AgentCard(name="deep", description="test")
    ).configure(
        DeepAgentConfig(enable_task_loop=False)
    )
    fake_react = FakeReactAgent()
    agent.set_react_agent(fake_react, initialized=False)

    rail = CountingRail()
    assert agent.add_rail(rail) is agent

    result = await agent.invoke(
        {"query": "hello", "conversation_id": "c1"}
    )

    assert result["output"] == "echo:hello"
    assert rail.before_invoke_count == 1
    assert rail.after_invoke_count == 1
    assert agent.is_initialized is True
    assert fake_react.invoke_calls[0]["inputs"] == {
        "query": "hello",
        "conversation_id": "c1",
    }

    bridged_events = [item[0] for item in fake_react.registered_callbacks]
    assert AgentCallbackEvent.BEFORE_TOOL_CALL in bridged_events
    assert AgentCallbackEvent.BEFORE_INVOKE not in bridged_events


@pytest.mark.asyncio
async def test_register_and_unregister_rail() -> None:
    agent = DeepAgent(
        AgentCard(name="deep", description="test")
    ).configure(
        DeepAgentConfig(enable_task_loop=False)
    )
    fake_react = FakeReactAgent()
    agent.set_react_agent(fake_react, initialized=True)

    rail = CountingRail()
    await agent.register_rail(rail)
    await agent.invoke("round1")

    assert rail.before_invoke_count == 1
    assert rail.after_invoke_count == 1

    await agent.unregister_rail(rail)
    await agent.invoke("round2")

    assert rail.before_invoke_count == 1
    assert rail.after_invoke_count == 1
    assert len(fake_react.agent_callback_manager.unregister_calls) == 1
    assert fake_react.agent_callback_manager.unregister_calls[0][0] is rail


@pytest.mark.asyncio
async def test_invoke_runtime_error_when_not_configured() -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test"))

    with pytest.raises(Exception, match="DeepAgent not configured"):
        await agent.invoke({"query": "hello"})


@pytest.mark.asyncio
async def test_invoke_invalid_input_type_error() -> None:
    agent = DeepAgent(
        AgentCard(name="deep", description="test")
    ).configure(
        DeepAgentConfig(enable_task_loop=False)
    )
    agent.set_react_agent(FakeReactAgent(), initialized=True)

    with pytest.raises(Exception, match="Input must be dict"):
        await agent.invoke(123)


@pytest.mark.asyncio
async def test_invoke_task_loop_requires_session() -> None:
    agent = DeepAgent(
        AgentCard(name="deep", description="test")
    ).configure(
        DeepAgentConfig(enable_task_loop=True)
    )
    agent.set_react_agent(FakeReactAgent(), initialized=True)

    with pytest.raises(
        Exception, match="session is required"
    ):
        await agent.invoke("no_session")


@pytest.mark.asyncio
async def test_invoke_task_loop_delegates_to_event_queue() -> None:
    agent = DeepAgent(
        AgentCard(name="deep", description="test")
    ).configure(
        DeepAgentConfig(enable_task_loop=True)
    )
    fake_react = FakeReactAgent()
    agent.set_react_agent(fake_react, initialized=True)

    session = Session(session_id="s1")
    result = await agent.invoke("loop_input", session=session)

    assert result["output"] == "echo:loop_input"
    # _loop_ctx is cleaned up after invoke completes
    assert agent.loop_coordinator is None


@pytest.mark.asyncio
async def test_stream_single_round_branch() -> None:
    agent = DeepAgent(
        AgentCard(name="deep", description="test")
    ).configure(
        DeepAgentConfig(enable_task_loop=False)
    )
    fake_react = FakeReactAgent()
    agent.set_react_agent(fake_react, initialized=True)

    chunks = [chunk async for chunk in agent.stream("stream_input")]

    assert [chunk["chunk"] for chunk in chunks] == [1, 2]
    assert fake_react.stream_calls[0]["inputs"] == {"query": "stream_input"}


@pytest.mark.asyncio
async def test_stream_task_loop_yields_result() -> None:
    agent = DeepAgent(
        AgentCard(name="deep", description="test")
    ).configure(
        DeepAgentConfig(enable_task_loop=True)
    )
    fake_react = FakeReactAgent()
    agent.set_react_agent(fake_react, initialized=True)

    session = Session(session_id="s1")
    chunks = [
        chunk async for chunk
        in agent.stream("loop_input", session=session)
    ]

    assert len(chunks) >= 1
    assert chunks[0]["output"] == "echo:loop_input"


@pytest.mark.asyncio
async def test_follow_up_steer_noop_without_queue() -> None:
    agent = DeepAgent(
        AgentCard(name="deep", description="test")
    ).configure(
        DeepAgentConfig(enable_task_loop=False)
    )

    # No event_queue → these are safe no-ops
    await agent.follow_up("continue", task_id="task_1")
    await agent.steer("change strategy")
    assert agent.loop_coordinator is None


@pytest.mark.asyncio
async def test_abort_sets_coordinator_flag() -> None:
    agent = DeepAgent(
        AgentCard(name="deep", description="test")
    ).configure(
        DeepAgentConfig(enable_task_loop=True)
    )
    fake_react = FakeReactAgent()
    agent.set_react_agent(fake_react, initialized=True)

    # Manually set up loop state to simulate mid-loop
    coordinator = LoopCoordinator()
    coordinator.reset()
    handler = TaskLoopEventHandler(agent)

    class FakeController:
        """Minimal Controller stub."""
        def __init__(self) -> None:
            self.event_handler = handler
            self.event_queue = None

        async def stop(self) -> None:
            pass

    agent._loop_coordinator = coordinator
    agent._loop_controller = FakeController()
    agent._loop_session = None

    handler.prepare_round()

    await agent.abort()
    assert coordinator.is_aborted is True

    # Future should be resolved with abort error
    fut_result = await handler.wait_completion(
        timeout=1.0
    )
    assert fut_result == {"error": "aborted"}


@pytest.mark.asyncio
async def test_create_deep_agent_factory_public_api() -> None:
    rail = CountingRail()
    tool = _build_tool_card("factory_tool")
    subagent = AgentCard(name="subagent_a", description="sub")

    agent = create_deep_agent(
        model=DummyModel(),
        system_prompt="factory prompt",
        tools=[tool],
        subagents=[subagent],
        rails=[rail],
        enable_task_loop=False,
        max_iterations=4,
    )

    assert isinstance(agent, DeepAgent)
    assert agent.card.name == "deep_agent"
    assert agent.ability_manager.get("factory_tool") is tool

    fake_react = FakeReactAgent()
    agent.set_react_agent(fake_react, initialized=False)
    result = await agent.invoke("factory_call")

    assert result["output"] == "echo:factory_call"
    assert rail.before_invoke_count == 1
    assert rail.after_invoke_count == 1


def test_create_deep_agent_with_custom_card() -> None:
    custom_card = AgentCard(name="custom_deep", description="custom")
    agent = create_deep_agent(model=DummyModel(), card=custom_card)

    assert isinstance(agent, DeepAgent)
    assert agent.card is custom_card
