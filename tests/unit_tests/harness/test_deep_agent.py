# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for DeepAgent public APIs."""
# pylint: disable=protected-access
from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, call, patch

import pytest

from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.tool import Tool, ToolCard, McpServerConfig
from openjiuwen.core.foundation.tool.schema import ToolInfo
from openjiuwen.core.runner.resources_manager.base import Ok
from openjiuwen.core.session.stream.base import StreamMode
from openjiuwen.core.single_agent.agents.react_agent import ReActAgentConfig
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentCallbackEvent,
    AgentRail,
)
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness import create_deep_agent, Workspace
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.rails.filesystem_rail import FileSystemRail
from openjiuwen.harness.schema.config import DeepAgentConfig, SubAgentConfig
from openjiuwen.harness.subagents import (
    build_code_agent_config,
    build_research_agent_config,
    create_code_agent,
)
from openjiuwen.harness.subagents.code_agent import (
    CODE_AGENT_FACTORY_NAME,
    DEFAULT_CODE_AGENT_SYSTEM_PROMPT,
)
from openjiuwen.harness.subagents.research_agent import (
    DEFAULT_RESEARCH_AGENT_SYSTEM_PROMPT,
    RESEARCH_AGENT_FACTORY_NAME,
)
from openjiuwen.harness.task_loop.task_loop_event_handler import TaskLoopEventHandler
from openjiuwen.harness.task_loop.loop_coordinator import LoopCoordinator
from openjiuwen.harness.tools import WebFreeSearchTool


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
        self.config = ReActAgentConfig()
        self.prompt_builder = None
        self.system_prompt_builder = None

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

    async def write_invoke_result_to_stream(
        self,
        result: Dict[str, Any],
        session: Optional[Any] = None,
    ) -> None:
        from openjiuwen.core.session.stream.base import OutputSchema
        if session is not None:
            await session.write_stream(OutputSchema(
                type="answer",
                index=0,
                payload={
                    "output": result.get("output", ""),
                    "result_type": result.get("result_type", ""),
                },
            ))

    def configure(self, config: ReActAgentConfig) -> None:
        self.config = config


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
        auto_create_workspace=False,
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
            auto_create_workspace=False,
        )

        assert isinstance(agent, DeepAgent)
        assert agent.ability_manager.get(tool.card.name) is tool.card
        assert Runner.resource_mgr.get_tool(tool.card.id) is not None
    finally:
        Runner.resource_mgr.remove_tool(tool.card.id)


def test_create_deep_agent_skips_free_search_when_all_free_engines_disabled(monkeypatch) -> None:
    monkeypatch.setenv("FREE_SEARCH_DDG_ENABLED", "false")
    monkeypatch.setenv("FREE_SEARCH_BING_ENABLED", "false")
    tool = WebFreeSearchTool(language="cn", agent_id="disabled")

    agent = create_deep_agent(
        model=_create_dummy_model(),
        tools=[tool],
        auto_create_workspace=False,
    )

    assert agent.ability_manager.get("free_search") is None
    assert Runner.resource_mgr.get_tool(tool.card.id) is None


def test_deep_agent_hot_reload_removes_and_restores_free_search(monkeypatch) -> None:
    monkeypatch.setenv("FREE_SEARCH_DDG_ENABLED", "true")
    monkeypatch.setenv("FREE_SEARCH_BING_ENABLED", "false")
    tool = WebFreeSearchTool(language="cn", agent_id="hot_reload")

    agent = create_deep_agent(
        model=_create_dummy_model(),
        tools=[tool],
        auto_create_workspace=False,
    )

    try:
        assert agent.ability_manager.get("free_search") is tool.card
        assert Runner.resource_mgr.get_tool(tool.card.id) is not None

        monkeypatch.setenv("FREE_SEARCH_DDG_ENABLED", "false")
        monkeypatch.setenv("FREE_SEARCH_BING_ENABLED", "false")
        agent.configure(DeepAgentConfig(tools=[tool.card]))

        assert agent.ability_manager.get("free_search") is None
        assert Runner.resource_mgr.get_tool(tool.card.id) is None

        monkeypatch.setenv("FREE_SEARCH_DDG_ENABLED", "true")
        agent.configure(DeepAgentConfig(tools=[tool.card], language="cn"))

        assert agent.ability_manager.get("free_search") is tool.card
        assert Runner.resource_mgr.get_tool(tool.card.id) is not None
    finally:
        if Runner.resource_mgr.get_tool(tool.card.id) is not None:
            Runner.resource_mgr.remove_tool(tool.card.id)


def test_create_deep_agent_reuses_same_tool_instance_across_agents() -> None:
    tool = DummyTool("shared_tool_instance", tool_id="shared_tool_instance_id")

    try:
        first_agent = create_deep_agent(
            model=_create_dummy_model(),
            tools=[tool],
            auto_create_workspace=False,
        )
        second_agent = create_deep_agent(
            model=_create_dummy_model(),
            tools=[tool],
            auto_create_workspace=False,
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
            auto_create_workspace=False,
        )

        with pytest.raises(ValueError, match="different tool instance"):
            create_deep_agent(
                model=_create_dummy_model(),
                tools=[second_tool],
                auto_create_workspace=False,
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
            auto_create_workspace=False,
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
            auto_create_workspace=False,
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
            auto_create_workspace=False,
            enable_task_loop=False,
        )
        agent.set_react_agent(FakeReactAgent(), initialized=False)

        with pytest.raises(Exception, match="different config"):
            await agent.invoke("factory_call")


def test_create_deep_agent_with_custom_card() -> None:
    custom_card = AgentCard(name="custom_deep", description="custom")
    agent = create_deep_agent(
        model=_create_dummy_model(),
        card=custom_card,
        auto_create_workspace=False,
    )

    assert isinstance(agent, DeepAgent)
    assert agent.card is custom_card


def test_create_deep_agent_auto_add_task_planning_rail() -> None:
    """Test that TaskPlanningRail is auto-added when enable_task_loop=True."""
    agent = create_deep_agent(
        model=_create_dummy_model(),
        auto_create_workspace=False,
        enable_task_planning=True,
    )

    pending_rails = agent._pending_rails
    assert len(pending_rails) > 0

    rail_types = [type(rail).__name__ for rail in pending_rails if rail is not None]
    assert "TaskPlanningRail" in rail_types


@pytest.mark.asyncio
async def test_hot_reconfigure_preserves_task_tool_from_subagent_rail() -> None:
    tool = _build_tool_card("factory_tool")
    subagent = SubAgentConfig(
        agent_card=AgentCard(name="browser_agent", description="browser subagent"),
        system_prompt="browser prompt",
        model=_create_dummy_model(),
    )

    agent = create_deep_agent(
        model=_create_dummy_model(),
        tools=[tool],
        subagents=[subagent],
        auto_create_workspace=False,
        enable_task_loop=False,
    )
    fake_react = FakeReactAgent()
    agent.set_react_agent(fake_react, initialized=False)

    await agent.invoke("initialize subagent rail")
    assert agent.ability_manager.get("task_tool") is not None

    agent.configure(
        DeepAgentConfig(
            model=_create_dummy_model(),
            tools=[tool],
            subagents=[subagent],
            rails=[],
            enable_task_loop=False,
            system_prompt="updated prompt",
        )
    )

    assert agent.ability_manager.get("task_tool") is not None


def test_create_deep_agent_auto_add_skill_rail(tmp_path) -> None:
    """Test that SkillUseRail is auto-added when skills parameter is provided."""
    skills = ["name", "test_skill", "description", "test"]
    workspace_root = tmp_path / "team_member_workspace"
    agent = create_deep_agent(
        model=_create_dummy_model(),
        skills=skills,
        workspace=Workspace(root_path=str(workspace_root)),
    )

    pending_rails = agent._pending_rails
    assert len(pending_rails) > 0

    non_null_rails = [rail for rail in pending_rails if rail is not None]
    rail_types = [type(rail).__name__ for rail in non_null_rails]
    assert "SkillUseRail" in rail_types

    skill_rail = next(rail for rail in non_null_rails if type(rail).__name__ == "SkillUseRail")
    assert isinstance(skill_rail.skills_dir, list)
    assert Path(skill_rail.skills_dir[0]) == workspace_root / "skills"
    # ``skills`` enables the default SkillUseRail but should not be copied into
    # SkillUseRail.enabled_skills, which would incorrectly filter available skills.
    assert skill_rail.enabled_skills == set()


def test_create_deep_agent_does_not_add_skill_rail_when_skills_empty(tmp_path) -> None:
    workspace_root = tmp_path / "team_member_workspace"
    agent = create_deep_agent(
        model=_create_dummy_model(),
        skills=[],
        workspace=Workspace(root_path=str(workspace_root)),
    )

    non_null_rails = [rail for rail in agent._pending_rails if rail is not None]
    rail_types = [type(rail).__name__ for rail in non_null_rails]
    assert "SkillUseRail" not in rail_types


def test_create_deep_agent_auto_add_skill_rail_when_skill_discovery_enabled(tmp_path) -> None:
    workspace_root = tmp_path / "team_member_workspace"
    agent = create_deep_agent(
        model=_create_dummy_model(),
        skills=[],
        workspace=Workspace(root_path=str(workspace_root)),
        enable_skill_discovery=True,
    )

    non_null_rails = [rail for rail in agent._pending_rails if rail is not None]
    rail_types = [type(rail).__name__ for rail in non_null_rails]
    assert "SkillUseRail" in rail_types

    skill_rail = next(rail for rail in non_null_rails if type(rail).__name__ == "SkillUseRail")
    assert isinstance(skill_rail.skills_dir, list)
    assert Path(skill_rail.skills_dir[0]) == workspace_root / "skills"
    assert skill_rail.enabled_skills == set()


def test_create_deep_agent_no_duplicate_task_planning_rail() -> None:
    """Test that TaskPlanningRail is not duplicated when manually provided."""
    from openjiuwen.harness.rails import TaskPlanningRail

    manual_rail = TaskPlanningRail()
    agent = create_deep_agent(
        model=_create_dummy_model(),
        auto_create_workspace=False,
        enable_task_loop=True,
        rails=[manual_rail],
    )

    pending_rails = agent._pending_rails
    task_planning_count = sum(1 for rail in pending_rails if isinstance(rail, TaskPlanningRail))
    assert task_planning_count == 1, f"Expected 1 TaskPlanningRail, but found {task_planning_count}"


def test_create_deep_agent_no_duplicate_skill_rail() -> None:
    """Test that SkillUseRail is not duplicated when manually provided."""
    from openjiuwen.harness.rails import SkillUseRail

    manual_rail = SkillUseRail(skills_dir="./", skill_mode="all")
    skills = [{"name": "test_skill", "description": "test"}]
    agent = create_deep_agent(
        model=_create_dummy_model(),
        skills=skills,
        rails=[manual_rail],
        auto_create_workspace=False,
    )

    pending_rails = agent._pending_rails
    skill_rail_count = sum(1 for rail in pending_rails if isinstance(rail, SkillUseRail))
    assert skill_rail_count == 1, f"Expected 1 SkillUseRail, but found {skill_rail_count}"


def test_create_deep_agent_subclass_skill_rail_not_duplicated() -> None:
    """Subclass of SkillUseRail should suppress the default SkillUseRail fallback."""
    from openjiuwen.harness.rails import SkillUseRail

    class _CustomSkillRail(SkillUseRail):
        pass

    custom_rail = _CustomSkillRail(skills_dir="./", skill_mode="all")
    skills = ["some_skill"]
    agent = create_deep_agent(
        model=_create_dummy_model(),
        skills=skills,
        rails=[custom_rail],
        auto_create_workspace=False,
    )

    skill_rail_count = sum(1 for r in agent._pending_rails if isinstance(r, SkillUseRail))
    assert skill_rail_count == 1, (
        f"Subclass should suppress default SkillUseRail, but found {skill_rail_count}"
    )


def test_create_deep_agent_subclass_task_planning_rail_not_duplicated() -> None:
    """Subclass of TaskPlanningRail should suppress the default TaskPlanningRail fallback."""
    from openjiuwen.harness.rails import TaskPlanningRail

    class _CustomTaskPlanningRail(TaskPlanningRail):
        pass

    custom_rail = _CustomTaskPlanningRail()
    agent = create_deep_agent(
        model=_create_dummy_model(),
        auto_create_workspace=False,
        enable_task_planning=True,
        rails=[custom_rail],
    )

    task_plan_count = sum(1 for r in agent._pending_rails if isinstance(r, TaskPlanningRail))
    assert task_plan_count == 1, (
        f"Subclass should suppress default TaskPlanningRail, but found {task_plan_count}"
    )


def test_create_code_agent_injects_default_code_tool_and_fs_rail() -> None:
    agent = create_code_agent(model=_create_dummy_model())

    assert isinstance(agent, DeepAgent)
    assert agent.card.name == "code_agent"
    assert any(isinstance(rail, FileSystemRail) for rail in agent._pending_rails)


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


def test_build_code_agent_config_uses_code_factory() -> None:
    spec = build_code_agent_config(_create_dummy_model(), language="en")

    assert isinstance(spec, SubAgentConfig)
    assert spec.agent_card.name == "code_agent"
    assert spec.system_prompt == DEFAULT_CODE_AGENT_SYSTEM_PROMPT["en"]
    assert spec.factory_name == CODE_AGENT_FACTORY_NAME
    assert spec.tools is None
    assert spec.rails is None


def test_build_research_agent_config_uses_research_factory() -> None:
    spec = build_research_agent_config(_create_dummy_model(), language="en")

    assert isinstance(spec, SubAgentConfig)
    assert spec.agent_card.name == "research_agent"
    assert spec.system_prompt == DEFAULT_RESEARCH_AGENT_SYSTEM_PROMPT["en"]
    assert spec.factory_name == RESEARCH_AGENT_FACTORY_NAME
    assert spec.tools is None
    assert spec.rails is None


def test_create_subagent_uses_code_agent_factory(tmp_path) -> None:
    workspace_root = tmp_path / "parent_workspace"
    parent = create_deep_agent(
        model=_create_dummy_model(),
        card=AgentCard(name="parent", description="parent"),
        system_prompt="parent prompt",
        workspace=Workspace(root_path=str(workspace_root)),
        subagents=[build_code_agent_config(_create_dummy_model(), language="en")],
    )
    factory_result = object()

    with patch(
        "openjiuwen.harness.subagents.code_agent.create_code_agent",
        return_value=factory_result,
    ) as mock_create_code_agent:
        sub = parent.create_subagent("code_agent", "sub_session_id")

    assert sub is factory_result
    mock_create_code_agent.assert_called_once()
    call_kwargs = mock_create_code_agent.call_args.kwargs
    assert call_kwargs["card"].name == "code_agent"
    assert call_kwargs["tools"] is None
    assert call_kwargs["rails"] is None
    assert call_kwargs["workspace"].root_path.endswith("/sub_session_id")


def test_create_subagent_uses_research_agent_factory(tmp_path) -> None:
    workspace_root = tmp_path / "parent_workspace"
    parent = create_deep_agent(
        model=_create_dummy_model(),
        card=AgentCard(name="parent", description="parent"),
        system_prompt="parent prompt",
        workspace=Workspace(root_path=str(workspace_root)),
        subagents=[build_research_agent_config(_create_dummy_model(), language="en")],
    )
    factory_result = object()

    with patch(
        "openjiuwen.harness.subagents.research_agent.create_research_agent",
        return_value=factory_result,
    ) as mock_create_research_agent:
        sub = parent.create_subagent("research_agent", "sub_session_id")

    assert sub is factory_result
    mock_create_research_agent.assert_called_once()
    call_kwargs = mock_create_research_agent.call_args.kwargs
    assert call_kwargs["card"].name == "research_agent"
    assert call_kwargs["tools"] is None
    assert call_kwargs["rails"] is None
    assert call_kwargs["workspace"].root_path.endswith("/sub_session_id")


@pytest.mark.asyncio
async def test_create_deep_agent_with_restrict_to_work_dir_enabled(tmp_path) -> None:
    """Test that restrict_to_work_dir=False results in no sandbox."""
    agent = create_deep_agent(
        model=_create_dummy_model(),
        workspace=Workspace(root_path=str(tmp_path)),
        restrict_to_work_dir=False,
    )

    assert agent.deep_config is not None
    assert agent.deep_config.sys_operation is not None

    sys_op = Runner.resource_mgr.get_sys_operation(f"{agent.card.name}_{agent.card.id}")
    assert sys_op is not None
    assert sys_op._run_config.sandbox_root is None
    assert sys_op._run_config.restrict_to_sandbox is False
