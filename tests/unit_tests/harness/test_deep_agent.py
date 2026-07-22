# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for DeepAgent public APIs."""

# pylint: disable=protected-access
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, call, patch

import pytest

from openjiuwen.core.context_engine import ContextEngineConfig
from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    Model,
    ModelClientConfig,
    ModelRequestConfig,
    UsageMetadata,
    UserMessage,
)
from openjiuwen.core.foundation.tool import McpServerConfig, Tool, ToolCard
from openjiuwen.core.foundation.tool.schema import ToolInfo
from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.resources_manager.base import Ok
from openjiuwen.core.session.agent import Session
from openjiuwen.core.session.stream.base import OutputSchema, StreamMode
from openjiuwen.core.single_agent.agents.react_agent import ReActAgentConfig
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentCallbackEvent,
    AgentRail,
)
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness import Workspace, create_deep_agent
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.rails.sys_operation_rail import SysOperationRail
from openjiuwen.harness.schema.config import (
    DeepAgentConfig,
    SubAgentConfig,
    VisionModelConfig,
)
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
from openjiuwen.harness.task_loop.loop_coordinator import LoopCoordinator
from openjiuwen.harness.task_loop.task_loop_event_executor import DEEP_TASK_TYPE
from openjiuwen.harness.task_loop.task_loop_event_handler import TaskLoopEventHandler
from openjiuwen.harness.tools import WebFreeSearchTool
from openjiuwen.harness.tools.subagent.session_tools import SessionToolkit


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


@pytest.fixture(autouse=True)
def _mock_image_modality_probe(monkeypatch):
    probe = AsyncMock(return_value=True)
    monkeypatch.setattr("openjiuwen.harness.deep_agent.probe_image_support", probe)
    return probe


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
            await session.write_stream(
                OutputSchema(
                    type="answer",
                    index=0,
                    payload={
                        "output": result.get("output", ""),
                        "result_type": result.get("result_type", ""),
                    },
                )
            )

    def configure(self, config: ReActAgentConfig) -> None:
        self.config = config


class SlowReactAgent(FakeReactAgent):
    """Fake ReActAgent that stays in invoke until cancelled."""

    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def invoke(
        self,
        inputs: Dict[str, Any],
        session: Optional[Any] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        self.invoke_calls.append({"inputs": inputs, "session": session})
        self.started.set()
        try:
            await asyncio.sleep(30.0)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        return {
            "output": "late",
            "result_type": "answer",
        }


class CountingRail(AgentRail):
    def __init__(self) -> None:
        super().__init__()
        self.before_invoke_count = 0
        self.after_invoke_count = 0
        self.before_tool_call_count = 0
        self.after_invoke_result: Optional[Dict[str, Any]] = None

    def init(self, agent):
        rail_tool = _build_tool_card("rail_tool")
        agent.ability_manager.add(rail_tool)

    def uninit(self, agent):
        agent.ability_manager.remove("rail_tool")

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        _ = ctx
        self.before_invoke_count += 1

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        self.after_invoke_count += 1
        self.after_invoke_result = getattr(ctx.inputs, "result", None)

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        _ = ctx
        self.before_tool_call_count += 1


class CapturingRail(AgentRail):
    def __init__(self) -> None:
        super().__init__()
        self.enable_read_image_multimodal: Optional[bool] = None

    def init(self, agent) -> None:
        self.enable_read_image_multimodal = agent.deep_config.enable_read_image_multimodal


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


@pytest.mark.asyncio
async def test_ensure_initialized_resolves_read_image_multimodal_before_rails(
    _mock_image_modality_probe,
) -> None:
    llm = _create_dummy_model()
    _mock_image_modality_probe.return_value = False
    rail = CapturingRail()
    agent = DeepAgent(AgentCard(name="deep", description="test")).configure(
        DeepAgentConfig(
            model=llm,
            enable_task_loop=False,
            auto_create_workspace=False,
        )
    )
    agent.set_react_agent(FakeReactAgent(), initialized=False)
    agent.add_rail(rail)

    await agent.ensure_initialized()

    assert agent.deep_config.enable_read_image_multimodal is False
    assert rail.enable_read_image_multimodal is False
    _mock_image_modality_probe.assert_awaited_once_with(llm)


def test_configure_set_react_agent_and_is_initialized() -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test"))

    configured = agent.configure(DeepAgentConfig(enable_task_loop=False, max_iterations=3))
    assert configured is agent
    assert agent.is_initialized is False

    fake_react = FakeReactAgent()
    set_result = agent.set_react_agent(fake_react, initialized=True)
    assert set_result is agent
    assert agent.is_initialized is True

    assert agent.loop_coordinator is None


def test_prompt_attachment_reminder_is_in_initial_and_hot_reloaded_system_prompt() -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test")).configure(
        DeepAgentConfig(
            enable_task_loop=False,
            language="en",
            system_prompt="initial identity",
        )
    )

    assert agent.system_prompt_builder is not None
    assert agent.system_prompt_builder.get_section(SectionName.PROMPT_ATTACHMENTS) is not None
    initial_prompt = agent._react_agent.config.prompt_template[0]["content"]
    assert "initial identity" in initial_prompt
    assert "<prompt-attachment>" in initial_prompt

    agent.configure(
        DeepAgentConfig(
            enable_task_loop=False,
            language="en",
            system_prompt="updated identity",
        )
    )

    assert agent.system_prompt_builder.get_section(SectionName.PROMPT_ATTACHMENTS) is not None
    reloaded_prompt = agent._react_agent.config.prompt_template[0]["content"]
    assert "updated identity" in reloaded_prompt
    assert "<prompt-attachment>" in reloaded_prompt


@pytest.mark.asyncio
async def test_add_rail_lazy_register_on_first_invoke() -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test")).configure(DeepAgentConfig(enable_task_loop=False))
    fake_react = FakeReactAgent()
    agent.set_react_agent(fake_react, initialized=False)

    rail = CountingRail()
    assert agent.add_rail(rail) is agent

    result = await agent.invoke({"query": "hello", "conversation_id": "c1"})

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
    agent = DeepAgent(AgentCard(name="deep", description="test")).configure(DeepAgentConfig(enable_task_loop=False))
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
async def test_outer_rails_are_isolated_between_same_card_id_agents() -> None:
    card = AgentCard(
        id="shared-deep-agent-card",
        name="deep",
        description="test",
    )
    agents: List[DeepAgent] = []
    rails: List[CountingRail] = []

    for _ in range(3):
        agent = DeepAgent(card).configure(DeepAgentConfig(enable_task_loop=False))
        agent.set_react_agent(FakeReactAgent(), initialized=False)
        rail = CountingRail()
        agent.add_rail(rail)
        await agent._ensure_initialized()
        agents.append(agent)
        rails.append(rail)

    try:
        assert len(
            {agent.agent_callback_manager._get_agent_event(AgentCallbackEvent.BEFORE_INVOKE) for agent in agents}
        ) == len(agents)

        result = await agents[0].invoke({"query": "hello"})

        assert result["output"] == "echo:hello"
        assert [rail.before_invoke_count for rail in rails] == [1, 0, 0]
        assert [rail.after_invoke_count for rail in rails] == [1, 0, 0]
    finally:
        for agent in agents:
            await agent.agent_callback_manager.clear()


def test_find_rails_by_type_returns_matching_rails() -> None:
    """find_rails_by_type locates queued rails by type without exposing internals."""
    agent = DeepAgent(AgentCard(name="deep", description="test")).configure(DeepAgentConfig(enable_task_loop=False))
    counting = CountingRail()
    agent.add_rail(counting)

    assert agent.find_rails_by_type((CountingRail,)) == [counting]
    assert agent.find_rails_by_type((SysOperationRail,)) == []
    assert agent.find_rails_by_type(()) == []


@pytest.mark.asyncio
async def test_invoke_runtime_error_when_not_configured() -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test"))

    with pytest.raises(Exception, match="DeepAgent not configured"):
        await agent.invoke({"query": "hello"})


@pytest.mark.asyncio
async def test_invoke_invalid_input_type_error() -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test")).configure(DeepAgentConfig(enable_task_loop=False))
    agent.set_react_agent(FakeReactAgent(), initialized=True)

    with pytest.raises(Exception, match="Input must be dict"):
        await agent.invoke(123)


@pytest.mark.asyncio
async def test_invoke_task_loop_requires_session() -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test")).configure(DeepAgentConfig(enable_task_loop=True))
    agent.set_react_agent(FakeReactAgent(), initialized=True)

    with pytest.raises(Exception, match="session is required"):
        await agent.invoke("no_session")


@pytest.mark.asyncio
async def test_invoke_task_loop_delegates_to_event_queue() -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test")).configure(DeepAgentConfig(enable_task_loop=True))
    fake_react = FakeReactAgent()
    agent.set_react_agent(fake_react, initialized=True)

    session = Session(session_id="s1")
    result = await agent.invoke("loop_input", session=session)

    assert result["output"] == "echo:loop_input"
    # _loop_ctx is cleaned up after invoke completes
    assert agent.loop_coordinator is None


@pytest.mark.asyncio
async def test_stream_single_round_branch() -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test")).configure(DeepAgentConfig(enable_task_loop=False))
    fake_react = FakeReactAgent()
    agent.set_react_agent(fake_react, initialized=True)

    chunks = [chunk async for chunk in agent.stream("stream_input")]

    assert [chunk["chunk"] for chunk in chunks] == [1, 2]
    assert fake_react.stream_calls[0]["inputs"] == {"query": "stream_input"}


@pytest.mark.asyncio
async def test_stream_sets_result_before_after_invoke() -> None:
    class AnswerStreamingReactAgent(FakeReactAgent):
        async def stream(
            self,
            inputs: Dict[str, Any],
            session: Optional[Any] = None,
            stream_modes: Optional[List[StreamMode]] = None,
        ) -> AsyncIterator[OutputSchema]:
            self.stream_calls.append(
                {
                    "inputs": inputs,
                    "session": session,
                    "stream_modes": stream_modes,
                }
            )
            yield OutputSchema(
                type="llm_output",
                index=0,
                payload={"content": "hello ", "result_type": "answer"},
            )
            yield OutputSchema(
                type="llm_output",
                index=1,
                payload={"content": "world", "result_type": "answer"},
            )
            yield OutputSchema(
                type="answer",
                index=0,
                payload={"output": "hello world", "result_type": "answer"},
            )

    agent = DeepAgent(AgentCard(name="deep", description="test")).configure(DeepAgentConfig(enable_task_loop=False))
    rail = CountingRail()
    agent.add_rail(rail)
    agent.set_react_agent(AnswerStreamingReactAgent(), initialized=False)

    chunks = [chunk async for chunk in agent.stream("stream_input")]

    assert [chunk.type for chunk in chunks] == ["llm_output", "llm_output", "answer"]
    assert rail.after_invoke_count == 1
    assert rail.after_invoke_result == {
        "output": "hello world",
        "result_type": "answer",
    }


@pytest.mark.asyncio
async def test_stream_task_loop_yields_result() -> None:
    await Runner.start()
    try:
        agent = DeepAgent(AgentCard(name="deep", description="test")).configure(DeepAgentConfig(enable_task_loop=True))
        fake_react = FakeReactAgent()
        agent.set_react_agent(fake_react, initialized=True)

        chunks = []
        async for chunk in Runner.run_agent_streaming(agent, {"query": "loop_input"}):
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
    agent = DeepAgent(AgentCard(name="deep", description="test")).configure(DeepAgentConfig(enable_task_loop=False))

    # No event_queue → these are safe no-ops
    await agent.follow_up("continue", task_id="task_1")
    await agent.steer("change strategy")
    assert agent.loop_coordinator is None


@pytest.mark.asyncio
async def test_get_context_usage_prefers_model_usage_metadata() -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test")).configure(
        DeepAgentConfig(
            enable_task_loop=False,
            context_engine_config=ContextEngineConfig(
                context_window_tokens=1000,
            ),
        )
    )

    session = Session(session_id="ctx_usage")
    context = await agent.react_agent.context_engine.create_context(session=session)
    await context.add_messages(
        [
            UserMessage(content="hello"),
            AssistantMessage(
                content="world",
                usage_metadata=UsageMetadata(total_tokens=250),
            ),
        ]
    )

    usage = agent.get_context_usage(session_id="ctx_usage")

    assert usage["session_id"] == "ctx_usage"
    assert usage["total_tokens"] == 250
    assert usage["context_window_tokens"] == 1000
    assert usage["usage_ratio"] == 0.25
    assert usage["usage_percent"] == 25.0
    assert usage["stats"]["total_tokens"] == 250


@pytest.mark.asyncio
async def test_get_current_context_returns_messages() -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test")).configure(DeepAgentConfig(enable_task_loop=False))

    session = Session(session_id="ctx_messages")
    context = await agent.react_agent.context_engine.create_context(session=session)
    await context.add_messages(UserMessage(content="current"))

    messages = agent.get_current_context(session_id="ctx_messages")

    assert len(messages) == 1
    assert messages[0].content == "current"


@pytest.mark.asyncio
async def test_create_new_context_engine_returns_session_id_and_keeps_existing_context() -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test")).configure(DeepAgentConfig(enable_task_loop=False))

    old_engine = agent.react_agent.context_engine
    session = Session(session_id="old_ctx")
    await old_engine.create_context(session=session)

    new_session_id = await agent.create_new_context_engine("new_ctx")

    assert new_session_id == "new_ctx"
    assert agent.react_agent.context_engine is old_engine
    assert old_engine.get_context(session_id="old_ctx") is not None
    assert agent.react_agent.context_engine.get_context(session_id="new_ctx") is not None


@pytest.mark.asyncio
async def test_create_new_context_engine_seeds_messages() -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test")).configure(DeepAgentConfig(enable_task_loop=False))

    await agent.create_new_context_engine(
        "seeded_ctx",
        messages=["seed prompt"],
    )
    context = agent.react_agent.context_engine.get_context(session_id="seeded_ctx")

    messages = context.get_messages()
    assert len(messages) == 1
    assert messages[0].role == "system"
    assert messages[0].content == "seed prompt"


@pytest.mark.asyncio
async def test_new_context_engine_accepts_messages() -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test")).configure(DeepAgentConfig(enable_task_loop=False))

    session_id = await agent.new_context_engine(
        session_id="alias_ctx",
        messages=["alias prompt"],
    )

    context = agent.react_agent.context_engine.get_context(session_id="alias_ctx")
    assert session_id == "alias_ctx"
    assert context.get_messages()[0].content == "alias prompt"


@pytest.mark.asyncio
async def test_abort_sets_coordinator_flag() -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test")).configure(DeepAgentConfig(enable_task_loop=True))
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
    fut_result = await handler.wait_completion(timeout=1.0)
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


@pytest.mark.asyncio
async def test_create_deep_agent_auto_registers_complete_vision_tools(
    _mock_image_modality_probe,
) -> None:
    agent = create_deep_agent(
        model=_create_dummy_model(),
        vision_model_config=VisionModelConfig(
            api_key="vision-key",
            base_url="https://vision.example/v1",
            model="vision-model",
        ),
        auto_create_workspace=False,
    )

    try:
        assert agent.ability_manager.get("image_ocr") is not None
        assert agent.ability_manager.get("visual_question_answering") is not None
        assert agent.deep_config.enable_read_image_multimodal is False
        await agent.ensure_initialized()
        _mock_image_modality_probe.assert_not_awaited()
    finally:
        agent.ability_manager.teardown_tools()


@pytest.mark.asyncio
async def test_create_deep_agent_skips_incomplete_vision_tools(
    _mock_image_modality_probe,
) -> None:
    agent = create_deep_agent(
        model=_create_dummy_model(),
        vision_model_config=VisionModelConfig(),
        auto_create_workspace=False,
    )

    assert agent.ability_manager.get("image_ocr") is None
    assert agent.ability_manager.get("visual_question_answering") is None
    assert agent.deep_config.enable_read_image_multimodal is None

    _mock_image_modality_probe.return_value = True
    await agent.ensure_initialized()

    assert agent.deep_config.enable_read_image_multimodal is True
    _mock_image_modality_probe.assert_awaited_once_with(agent.deep_config.model)


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


def test_create_deep_agent_qualifies_conflicting_tool_ids_per_agent() -> None:
    # Two distinct (stateful) instances share a bare id; per-agent id
    # qualification at registration keeps them distinct instead of raising.
    first_tool = DummyTool("tool_a", tool_id="shared_tool_id")
    second_tool = DummyTool("tool_b", tool_id="shared_tool_id")

    first_agent = create_deep_agent(
        model=_create_dummy_model(),
        tools=[first_tool],
        auto_create_workspace=False,
    )
    second_agent = create_deep_agent(
        model=_create_dummy_model(),
        tools=[second_tool],
        auto_create_workspace=False,
    )

    try:
        assert first_agent.ability_manager.get("tool_a") is first_tool.card
        assert second_agent.ability_manager.get("tool_b") is second_tool.card
        assert first_tool.card.id != second_tool.card.id
        assert first_tool.card.id.endswith(first_agent.card.id)
        assert second_tool.card.id.endswith(second_agent.card.id)
    finally:
        Runner.resource_mgr.remove_tool(first_tool.card.id)
        Runner.resource_mgr.remove_tool(second_tool.card.id)


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
    mcp_tool_raw_name = mcp_tool.name

    with (
        patch.object(
            Runner.resource_mgr,
            "add_mcp_server",
            new=AsyncMock(return_value=Ok(mcp_config.server_id)),
        ) as mock_add_mcp_server,
        patch.object(
            Runner.resource_mgr,
            "get_mcp_tool_infos",
            new=AsyncMock(return_value=[mcp_tool]),
        ) as mock_get_mcp_tool_infos,
    ):
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
        expected_mcp_tool_name = f"mcp_{mcp_config.server_name}_{mcp_tool_raw_name}"
        assert any(tool_info.name == expected_mcp_tool_name for tool_info in tool_infos)
        mcp_tool_card = agent.ability_manager.get(expected_mcp_tool_name)
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

    with (
        patch.object(
            Runner.resource_mgr,
            "get_mcp_server_config",
            return_value=mcp_config,
        ),
        patch.object(
            Runner.resource_mgr,
            "add_mcp_server",
            new=AsyncMock(),
        ) as mock_add_mcp_server,
        patch.object(
            Runner.resource_mgr,
            "get_mcp_tool_ids",
            return_value=[mcp_tool_id],
        ),
        patch.object(
            Runner.resource_mgr,
            "add_resource_tag",
            return_value=Ok(["deep_agent_id"]),
        ) as mock_add_resource_tag,
        patch.object(
            Runner.resource_mgr,
            "get_mcp_tool_infos",
            new=AsyncMock(return_value=[mcp_tool]),
        ),
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
    assert skill_rail_count == 1, f"Subclass should suppress default SkillUseRail, but found {skill_rail_count}"


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
    assert task_plan_count == 1, f"Subclass should suppress default TaskPlanningRail, but found {task_plan_count}"


def test_create_code_agent_injects_default_code_tool_and_fs_rail() -> None:
    agent = create_code_agent(model=_create_dummy_model())

    assert isinstance(agent, DeepAgent)
    assert agent.card.name == "code_agent"
    assert any(isinstance(rail, SysOperationRail) for rail in agent._pending_rails)


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
    assert Path(call_kwargs["workspace"].root_path).name == "sub_session_id"


def test_create_subagent_forwards_browser_capabilities_to_factory(tmp_path) -> None:
    browser_spec = SubAgentConfig(
        agent_card=AgentCard(name="browser_agent", description="browser"),
        system_prompt="browser prompt",
        factory_name="browser_agent",
    )
    parent = create_deep_agent(
        model=_create_dummy_model(),
        card=AgentCard(name="parent", description="parent"),
        system_prompt="parent prompt",
        workspace=Workspace(root_path=str(tmp_path / "parent_workspace")),
        subagents=[browser_spec],
    )
    factory_result = object()

    with patch(
        "openjiuwen.harness.subagents.browser_agent.create_browser_agent",
        return_value=factory_result,
    ) as mock_create_browser_agent:
        subagent = parent.create_subagent(
            "browser_agent",
            "browser_session",
            browser_capabilities=["pdf", "vision"],
        )

    assert subagent is factory_result
    assert mock_create_browser_agent.call_args.kwargs["browser_capabilities"] == ["pdf", "vision"]


def test_create_subagent_passes_configured_runtime_fields(tmp_path) -> None:
    workspace_root = tmp_path / "parent_workspace"
    subagent_config = SubAgentConfig(
        agent_card=AgentCard(name="reviewer", description="reviewer"),
        system_prompt="Review strictly.",
        factory_name=CODE_AGENT_FACTORY_NAME,
        factory_kwargs={"sandbox": True},
        enable_task_loop=True,
        max_iterations=5,
        enable_plan_mode=True,
        parallel_tool_calls=False,
        restrict_to_work_dir=True,
        prompt_mode="concise",
        language="en",
    )
    parent = create_deep_agent(
        model=_create_dummy_model(),
        card=AgentCard(name="parent", description="parent"),
        system_prompt="parent prompt",
        workspace=Workspace(root_path=str(workspace_root)),
        restrict_to_work_dir=False,
        subagents=[subagent_config],
    )
    factory_result = object()

    with patch(
        "openjiuwen.harness.subagents.code_agent.create_code_agent",
        return_value=factory_result,
    ) as mock_create_code_agent:
        sub = parent.create_subagent("reviewer", "sub_session_id")

    assert sub is factory_result
    call_kwargs = mock_create_code_agent.call_args.kwargs
    assert call_kwargs["enable_task_loop"] is True
    assert call_kwargs["max_iterations"] == 5
    assert call_kwargs["enable_plan_mode"] is True
    assert call_kwargs["parallel_tool_calls"] is False
    assert call_kwargs["restrict_to_work_dir"] is True
    assert call_kwargs["prompt_mode"] == "concise"
    assert call_kwargs["language"] == "en"
    assert call_kwargs["sandbox"] is True


def test_create_subagent_keeps_parent_work_dir_restriction_when_stricter(tmp_path) -> None:
    workspace_root = tmp_path / "parent_workspace"
    subagent_config = SubAgentConfig(
        agent_card=AgentCard(name="reviewer", description="reviewer"),
        system_prompt="Review strictly.",
        factory_name=CODE_AGENT_FACTORY_NAME,
        restrict_to_work_dir=False,
    )
    parent = create_deep_agent(
        model=_create_dummy_model(),
        card=AgentCard(name="parent", description="parent"),
        system_prompt="parent prompt",
        workspace=Workspace(root_path=str(workspace_root)),
        restrict_to_work_dir=True,
        subagents=[subagent_config],
    )
    factory_result = object()

    with patch(
        "openjiuwen.harness.subagents.code_agent.create_code_agent",
        return_value=factory_result,
    ) as mock_create_code_agent:
        sub = parent.create_subagent("reviewer", "sub_session_id")

    assert sub is factory_result
    assert mock_create_code_agent.call_args.kwargs["restrict_to_work_dir"] is True


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
    assert Path(call_kwargs["workspace"].root_path).name == "sub_session_id"


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
    assert sys_op._run_config.shell_allowlist is None
    assert sys_op._run_config.restrict_to_sandbox is False


@pytest.mark.asyncio
async def test_stream_cancel_waits_for_cleanup() -> None:
    """Cancelled stream should wait for cleanup before returning."""
    await Runner.start()
    try:
        agent = DeepAgent(AgentCard(name="deep", description="test")).configure(DeepAgentConfig(enable_task_loop=True))
        fake_react = FakeReactAgent()
        agent.set_react_agent(fake_react, initialized=True)

        # Start streaming task
        async def _collect():
            chunks = []
            async for chunk in Runner.run_agent_streaming(agent, {"query": "test"}):
                chunks.append(chunk)
            return chunks

        stream_task = asyncio.create_task(_collect())
        await asyncio.sleep(0.2)  # Let stream start

        # Cancel and wait
        stream_task.cancel()
        try:
            await stream_task
        except asyncio.CancelledError:
            pass

        # Cleanup should be complete
        assert agent._bound_session_id is None
        assert agent._loop_controller is None
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_stream_cancel_cancels_deep_task_when_controller_is_kept_alive() -> None:
    """Client disconnect should stop the current ReAct task without killing session-spawn tasks."""
    await Runner.start()
    try:
        agent = DeepAgent(AgentCard(name="deep", description="test")).configure(DeepAgentConfig(enable_task_loop=True))
        slow_react = SlowReactAgent()
        agent.set_react_agent(slow_react, initialized=True)

        toolkit = SessionToolkit()
        toolkit.upsert_running(
            "spawn-running",
            "sub-session",
            "keep controller alive",
        )
        agent.set_session_toolkit(toolkit)

        async def _collect() -> None:
            async for _chunk in Runner.run_agent_streaming(
                agent,
                {"query": "slow"},
                session="issue959",
            ):
                pass

        stream_task = asyncio.create_task(_collect())
        await asyncio.wait_for(slow_react.started.wait(), timeout=5.0)

        controller = agent.loop_controller
        assert controller is not None
        scheduler = controller.task_scheduler
        assert scheduler is not None

        stream_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(stream_task, timeout=5.0)

        await asyncio.wait_for(slow_react.cancelled.wait(), timeout=5.0)

        running_tasks = getattr(scheduler, "_running_tasks")
        assert running_tasks == {}
        tasks = await scheduler.task_manager.get_task()
        deep_tasks = [task for task in tasks if task.task_type == DEEP_TASK_TYPE]
        assert deep_tasks
        assert all(task.status.value == "canceled" for task in deep_tasks)
        assert toolkit.get("spawn-running").status == "running"
    finally:
        controller = agent.loop_controller if "agent" in locals() else None
        if controller is not None:
            await controller.stop()
        await Runner.stop()


def test_create_subagent_inherits_parent_restrict_to_work_dir(tmp_path) -> None:
    """子代理的 restrict_to_work_dir 不能低于父代理的约束级别。

    当父代理设置了 restrict_to_work_dir=True（默认），即使子代理 SubAgentConfig
    中指定 restrict_to_work_dir=False（如 explore_agent），创建出的子代理也必须
    继承父代理的 restrict_to_sandbox=True，防止通过子代理绕过沙箱限制。
    """
    from openjiuwen.harness.subagents.explore_agent import build_explore_agent_config

    workspace_root = tmp_path / "parent_workspace"
    parent = create_deep_agent(
        model=_create_dummy_model(),
        card=AgentCard(name="parent", description="parent"),
        workspace=Workspace(root_path=str(workspace_root)),
        subagents=[build_explore_agent_config(model=_create_dummy_model())],
        restrict_to_work_dir=True,
    )
    assert parent.deep_config.restrict_to_work_dir is True

    # build_explore_agent_config 没有 factory_name，走 create_deep_agent 分支
    with patch("openjiuwen.harness.factory.create_deep_agent", return_value=object()) as mock_create:
        parent.create_subagent("explore_agent", "sub_session_id")

    mock_create.assert_called_once()
    call_kwargs = mock_create.call_args.kwargs
    # 子代理 spec 的 restrict_to_work_dir=False，但父代理为 True，结果应为 True
    assert call_kwargs["restrict_to_work_dir"] is True


def test_create_subagent_respects_subagent_restrict_when_parent_unrestricted(tmp_path) -> None:
    """父代理不限制时，子代理自身的 restrict_to_work_dir=True 仍然生效。"""
    from openjiuwen.harness.subagents.explore_agent import build_explore_agent_config

    workspace_root = tmp_path / "parent_workspace"
    explore_spec = build_explore_agent_config(model=_create_dummy_model())
    explore_spec.restrict_to_work_dir = True

    parent = create_deep_agent(
        model=_create_dummy_model(),
        card=AgentCard(name="parent", description="parent"),
        workspace=Workspace(root_path=str(workspace_root)),
        subagents=[explore_spec],
        restrict_to_work_dir=False,
    )
    assert parent.deep_config.restrict_to_work_dir is False

    with patch("openjiuwen.harness.factory.create_deep_agent", return_value=object()) as mock_create:
        parent.create_subagent("explore_agent", "sub_session_id")

    call_kwargs = mock_create.call_args.kwargs
    # 子代理自己要求限制，即使父代理不限制，结果也应为 True
    assert call_kwargs["restrict_to_work_dir"] is True


def test_create_subagent_unrestricted_when_both_unrestricted(tmp_path) -> None:
    """父子代理均不限制时，子代理可以无沙箱运行（CLI 宽松场景）。"""
    from openjiuwen.harness.subagents.explore_agent import build_explore_agent_config

    workspace_root = tmp_path / "parent_workspace"
    parent = create_deep_agent(
        model=_create_dummy_model(),
        card=AgentCard(name="parent", description="parent"),
        workspace=Workspace(root_path=str(workspace_root)),
        subagents=[build_explore_agent_config(model=_create_dummy_model())],
        restrict_to_work_dir=False,
    )

    with patch("openjiuwen.harness.factory.create_deep_agent", return_value=object()) as mock_create:
        parent.create_subagent("explore_agent", "sub_session_id")

    call_kwargs = mock_create.call_args.kwargs
    # 父子均不限制，保持 False
    assert call_kwargs["restrict_to_work_dir"] is False


@pytest.mark.asyncio
async def test_create_subagent_writes_relative_files_to_inherited_artifact_root(
    tmp_path: Path,
) -> None:
    """Subagent keeps an isolated workspace but cwd is the parent's artifact_root."""
    from openjiuwen.core.sys_operation.cwd import get_cwd, get_workspace, init_cwd
    from openjiuwen.harness.schema.config import SubAgentConfig

    parent_ws = tmp_path / "parent_ws"
    artifact_root = tmp_path / "projects" / "sess-1"
    parent_ws.mkdir()
    artifact_root.mkdir(parents=True)
    init_cwd(str(artifact_root), workspace=str(artifact_root))

    parent = DeepAgent(AgentCard(name="parent", description="test")).configure(
        DeepAgentConfig(
            model=_create_dummy_model(),
            workspace=Workspace(root_path=str(parent_ws)),
            auto_create_workspace=False,
            enable_task_loop=False,
            add_general_purpose_agent=False,
            subagents=[
                SubAgentConfig(
                    agent_card=AgentCard(name="worker", description="worker"),
                    system_prompt="do work",
                )
            ],
        )
    )
    parent.set_react_agent(FakeReactAgent(), initialized=True)

    sub = parent.create_subagent("worker", "sub_sess")
    assert sub._inherited_artifact_root == str(artifact_root.resolve())

    await sub.ensure_initialized()
    assert Path(get_cwd()).resolve() == artifact_root.resolve()
    assert "sub_agents" in str(Path(get_workspace()).resolve())
    assert Path(get_workspace()).resolve() != artifact_root.resolve()

    # After a sibling has polluted ambient workspace, the next create still
    # inherits cwd (shared artifact root), not the sibling sub_agents path.
    sub2 = parent.create_subagent("worker", "sub_sess_2")
    assert sub2._inherited_artifact_root == str(artifact_root.resolve())
    assert "sub_agents" not in sub2._inherited_artifact_root
