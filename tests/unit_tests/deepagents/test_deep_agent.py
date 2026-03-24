# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for DeepAgent public APIs."""
# pylint: disable=protected-access
from __future__ import annotations

from typing import Any, AsyncIterator, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, call, patch

import pytest

from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.tool import Tool, ToolCard, McpServerConfig
from openjiuwen.core.foundation.tool.schema import ToolInfo
from openjiuwen.core.runner.resources_manager.base import Ok
from openjiuwen.core.session.stream.base import StreamMode
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentCallbackEvent,
    AgentRail,
)
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.deepagents import create_deep_agent
from openjiuwen.deepagents.deep_agent import DeepAgent
from openjiuwen.deepagents.rails.filesystem_rail import FileSystemRail
from openjiuwen.deepagents.schema.config import DeepAgentConfig
from openjiuwen.deepagents.subagents import create_code_agent
from openjiuwen.deepagents.task_loop.task_loop_event_handler import TaskLoopEventHandler
from openjiuwen.deepagents.task_loop.loop_coordinator import LoopCoordinator
from openjiuwen.deepagents.tools.task_tool import create_task_tool


def _create_dummy_model() -> Model:
    """Create a dummy Model instance for testing."""
    model_client_config = ModelClientConfig(
        client_provider="OpenAI",
        api_key="test-key",
        api_base="http://test-base",
        verify_ssl=False,
    )
    model_config = ModelRequestConfig(model="test-model")
    return Model(model_client_config=model_client_config, model_config=model_config)



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
        **kwargs: Any,
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


class DummyTool(Tool):
    def __init__(self, name: str, tool_id: Optional[str] = None) -> None:
        super().__init__(ToolCard(id=tool_id or name, name=name, description=f"{name} tool"))

    async def invoke(self, inputs: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
        _ = kwargs
        return {"inputs": inputs}

    async def stream(self, inputs: Dict[str, Any], **kwargs: Any) -> AsyncIterator[Dict[str, Any]]:
        _ = kwargs
        yield {"inputs": inputs}


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
    await Runner.start()
    try:
        agent = DeepAgent(
            AgentCard(name="deep", description="test")
        ).configure(
            DeepAgentConfig(enable_task_loop=True)
        )
        fake_react = FakeReactAgent()
        agent.set_react_agent(fake_react, initialized=True)

        chunks = []
        async for chunk in Runner.run_agent_streaming(
            agent, {"query": "loop_input"}
        ):
            chunks.append(chunk)

        from openjiuwen.core.session.stream.base import OutputSchema
        assert len(chunks) >= 1
        # Chunks should be OutputSchema instances (token-level streaming)
        assert isinstance(chunks[-1], OutputSchema)
        # Last chunk should be an answer with the round result
        answer_chunks = [c for c in chunks if c.type == "answer"]
        assert len(answer_chunks) >= 1
        assert answer_chunks[0].payload["output"] == "echo:loop_input"
    finally:
        await Runner.stop()


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
        model=_create_dummy_model(),
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


def test_create_deep_agent_registers_tool_instances() -> None:
    tool = DummyTool("factory_tool_instance")

    try:
        agent = create_deep_agent(
            model=_create_dummy_model(),
            tools=[tool],
        )

        assert isinstance(agent, DeepAgent)
        assert agent.ability_manager.get(tool.card.name) is tool.card
        assert Runner.resource_mgr.get_tool(tool.card.id) is not None
    finally:
        Runner.resource_mgr.remove_tool(tool.card.id)


def test_create_deep_agent_reuses_same_tool_instance_across_agents() -> None:
    tool = DummyTool("shared_tool_instance", tool_id="shared_tool_instance_id")

    try:
        first_agent = create_deep_agent(
            model=_create_dummy_model(),
            tools=[tool],
        )
        second_agent = create_deep_agent(
            model=_create_dummy_model(),
            tools=[tool],
        )

        assert isinstance(first_agent, DeepAgent)
        assert isinstance(second_agent, DeepAgent)
        assert second_agent.ability_manager.get(tool.card.name) is tool.card
    finally:
        Runner.resource_mgr.remove_tool(tool.card.id)


def test_create_deep_agent_rejects_conflicting_tool_instances_with_same_id() -> None:
    first_tool = DummyTool("tool_a", tool_id="shared_tool_id")
    second_tool = DummyTool("tool_b", tool_id="shared_tool_id")

    try:
        create_deep_agent(
            model=_create_dummy_model(),
            tools=[first_tool],
        )

        with pytest.raises(ValueError, match="different tool instance"):
            create_deep_agent(
                model=_create_dummy_model(),
                tools=[second_tool],
            )
    finally:
        Runner.resource_mgr.remove_tool(first_tool.card.id)


@pytest.mark.asyncio
async def test_create_deep_agent_registers_mcps_on_first_invoke() -> None:
    mcp_config = McpServerConfig(
        server_name="test_mcp_server",
        server_id="mcp_server_001",
        server_path="http://127.0.0.1:8930/mcp",
        client_type="streamable-http",
    )
    mcp_tool = ToolInfo(
        name="mcp_lookup",
        description="lookup through mcp",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    )

    with patch.object(
        Runner.resource_mgr,
        "add_mcp_server",
        new=AsyncMock(return_value=Ok(mcp_config.server_id)),
    ) as mock_add_mcp_server, patch.object(
        Runner.resource_mgr,
        "get_mcp_tool_infos",
        new=AsyncMock(return_value=[mcp_tool]),
    ) as mock_get_mcp_tool_infos:
        agent = create_deep_agent(
            model=_create_dummy_model(),
            mcps=[mcp_config],
            enable_task_loop=False,
        )
        fake_react = FakeReactAgent()
        agent.set_react_agent(fake_react, initialized=False)

        assert mock_add_mcp_server.await_count == 0

        result = await agent.invoke("factory_call")

        assert result["output"] == "echo:factory_call"
        mock_add_mcp_server.assert_awaited_once_with(
            mcp_config,
            tag=agent.card.id,
        )
        assert agent.ability_manager.get(mcp_config.server_name) is mcp_config

        tool_infos = await agent.ability_manager.list_tool_info()
        assert any(tool_info.name == "mcp_lookup" for tool_info in tool_infos)
        mcp_tool_card = agent.ability_manager.get("mcp_lookup")
        assert isinstance(mcp_tool_card, ToolCard)
        assert mcp_tool_card.input_params == mcp_tool.parameters
        mock_get_mcp_tool_infos.assert_awaited()


@pytest.mark.asyncio
async def test_create_deep_agent_reuses_registered_mcps_with_same_config() -> None:
    mcp_config = McpServerConfig(
        server_name="test_mcp_server",
        server_id="mcp_server_001",
        server_path="http://127.0.0.1:8930/mcp",
        client_type="streamable-http",
    )
    mcp_tool = ToolInfo(
        name="mcp_lookup",
        description="lookup through mcp",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    )
    mcp_tool_id = f"{mcp_config.server_id}.{mcp_config.server_name}.{mcp_tool.name}"

    with patch.object(
        Runner.resource_mgr,
        "get_mcp_server_config",
        return_value=mcp_config,
    ), patch.object(
        Runner.resource_mgr,
        "add_mcp_server",
        new=AsyncMock(),
    ) as mock_add_mcp_server, patch.object(
        Runner.resource_mgr,
        "get_mcp_tool_ids",
        return_value=[mcp_tool_id],
    ), patch.object(
        Runner.resource_mgr,
        "add_resource_tag",
        return_value=Ok(["deep_agent_id"]),
    ) as mock_add_resource_tag, patch.object(
        Runner.resource_mgr,
        "get_mcp_tool_infos",
        new=AsyncMock(return_value=[mcp_tool]),
    ):
        agent = create_deep_agent(
            model=_create_dummy_model(),
            mcps=[mcp_config],
            enable_task_loop=False,
        )
        fake_react = FakeReactAgent()
        agent.set_react_agent(fake_react, initialized=False)

        result = await agent.invoke("factory_call")

        assert result["output"] == "echo:factory_call"
        mock_add_mcp_server.assert_not_awaited()
        mock_add_resource_tag.assert_has_calls(
            [
                call(mcp_config.server_id, agent.card.id),
                call(mcp_tool_id, agent.card.id),
            ]
        )
        assert agent.ability_manager.get(mcp_config.server_name) is mcp_config


@pytest.mark.asyncio
async def test_create_deep_agent_rejects_conflicting_registered_mcp_config() -> None:
    mcp_config = McpServerConfig(
        server_name="test_mcp_server",
        server_id="mcp_server_001",
        server_path="http://127.0.0.1:8930/mcp",
        client_type="streamable-http",
    )
    conflicting_config = mcp_config.model_copy(update={"server_path": "http://127.0.0.1:8940/mcp"})

    with patch.object(
        Runner.resource_mgr,
        "get_mcp_server_config",
        return_value=conflicting_config,
    ):
        agent = create_deep_agent(
            model=_create_dummy_model(),
            mcps=[mcp_config],
            enable_task_loop=False,
        )
        agent.set_react_agent(FakeReactAgent(), initialized=False)

        with pytest.raises(Exception, match="different config"):
            await agent.invoke("factory_call")


def test_create_deep_agent_with_custom_card() -> None:
    custom_card = AgentCard(name="custom_deep", description="custom")
    agent = create_deep_agent(model=_create_dummy_model(), card=custom_card)

    assert isinstance(agent, DeepAgent)
    assert agent.card is custom_card


def test_create_deep_agent_auto_add_task_planning_rail() -> None:
    """Test that TaskPlanningRail is auto-added when enable_task_loop=True."""
    agent = create_deep_agent(
        model=_create_dummy_model(),
        enable_task_loop=True,
    )

    pending_rails = agent._pending_rails
    assert len(pending_rails) > 0

    rail_types = [type(rail).__name__ for rail in pending_rails if rail is not None]
    assert "TaskPlanningRail" in rail_types


def test_create_deep_agent_auto_add_skill_rail() -> None:
    """Test that SkillRail is auto-added when skills parameter is provided."""
    skills = ["name", "test_skill", "description", "test"]
    agent = create_deep_agent(
        model=_create_dummy_model(),
        skills=skills,
    )

    pending_rails = agent._pending_rails
    assert len(pending_rails) > 0

    rail_types = [type(rail).__name__ for rail in [r for r in pending_rails if r is not None]]
    assert "SkillRail" in rail_types


def test_create_deep_agent_no_duplicate_task_planning_rail() -> None:
    """Test that TaskPlanningRail is not duplicated when manually provided."""
    from openjiuwen.deepagents.rails import TaskPlanningRail

    manual_rail = TaskPlanningRail()
    agent = create_deep_agent(
        model=_create_dummy_model(),
        enable_task_loop=True,
        rails=[manual_rail],
    )

    pending_rails = agent._pending_rails
    task_planning_count = sum(1 for rail in pending_rails if isinstance(rail, TaskPlanningRail))
    assert task_planning_count == 1, f"Expected 1 TaskPlanningRail, but found {task_planning_count}"


def test_create_deep_agent_no_duplicate_skill_rail() -> None:
    """Test that SkillRail is not duplicated when manually provided."""
    from openjiuwen.deepagents.rails import SkillRail

    manual_rail = SkillRail(skills_dir="./", skill_mode="all")
    skills = [{"name": "test_skill", "description": "test"}]
    agent = create_deep_agent(
        model=_create_dummy_model(),
        skills=skills,
        rails=[manual_rail],
    )

    pending_rails = agent._pending_rails
    skill_rail_count = sum(1 for rail in pending_rails if isinstance(rail, SkillRail))
    assert skill_rail_count == 1, f"Expected 1 SkillRail, but found {skill_rail_count}"


def test_create_code_agent_injects_default_code_tool_and_fs_rail() -> None:
    agent = create_code_agent(model=_create_dummy_model())

    assert isinstance(agent, DeepAgent)
    assert agent.card.name == "code_agent"
    assert agent.ability_manager.get("code") is not None
    assert any(isinstance(rail, FileSystemRail) for rail in agent._pending_rails)


def test_create_code_agent_explicit_tools_and_rails_override_defaults() -> None:
    custom_tool = _build_tool_card("custom_tool")
    custom_rail = CountingRail()

    agent = create_code_agent(
        model=_create_dummy_model(),
        tools=[custom_tool],
        rails=[custom_rail],
    )

    assert agent.ability_manager.get("custom_tool") is custom_tool
    assert agent.ability_manager.get("code") is None
    assert any(isinstance(rail, CountingRail) for rail in agent._pending_rails)
    assert not any(isinstance(rail, FileSystemRail) for rail in agent._pending_rails)


def test_create_code_agent_accepts_explicit_mcps() -> None:
    mcp_config = McpServerConfig(
        server_name="wrapper_mcp",
        server_id="wrapper_mcp_001",
        server_path="http://127.0.0.1:8930/mcp",
    )

    agent = create_code_agent(
        model=_create_dummy_model(),
        mcps=[mcp_config],
    )

    assert agent.deep_config is not None
    assert agent.deep_config.mcps == [mcp_config]
