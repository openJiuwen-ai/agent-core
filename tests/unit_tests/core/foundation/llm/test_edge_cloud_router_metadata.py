from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.application.llm_agent.llm_controller import LLMController
from openjiuwen.core.context_engine.base import ContextWindow
from openjiuwen.core.foundation.llm.schema.message import UsageMetadata
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


class _StreamingReActAgent(ReActAgent):
    async def call_model_for_test(self, *, ctx: AgentCallbackContext, context):
        return await self._call_model(ctx=ctx, context=context, tools=None)


class _MetadataStreamingModel:
    def supports_kv_cache_release(self) -> bool:
        return False

    async def stream(self, *args, **kwargs) -> AsyncIterator[AssistantMessageChunk]:
        yield AssistantMessageChunk(
            content="hello",
            metadata={"edge_cloud_router": {"target": "cloud", "policy_origin": "cloud"}},
        )
        yield AssistantMessageChunk(content=" world", usage_metadata=UsageMetadata(total_tokens=2))


@pytest.mark.asyncio
async def test_react_stream_aggregation_preserves_router_metadata() -> None:
    config = ReActAgentConfig()
    config.configure_model_client(
        provider="OpenAI",
        api_key="test-key",
        api_base="http://model.invalid/v1",
        model_name="test-model",
    )
    config.configure_context_engine(max_context_message_num=100)
    agent = _StreamingReActAgent(card=AgentCard(name="test", id="test")).configure(config)
    agent.set_llm(_MetadataStreamingModel())

    session = MagicMock(spec=Session)
    session.get_session_id.return_value = "session-1"
    session.write_stream = AsyncMock()
    model_context = MagicMock()
    model_context.get_messages.return_value = []
    model_context.session_id.return_value = "session-1"
    model_context.get_context_window = AsyncMock(
        return_value=ContextWindow(system_messages=[], context_messages=[], tools=[])
    )
    model_context.detect_context_window_change.return_value = None
    ctx = AgentCallbackContext(
        agent=agent,
        session=session,
        inputs={},
        extra={"_streaming": True},
    )
    ctx.context = model_context

    response = await agent.call_model_for_test(ctx=ctx, context=model_context)

    assert response.content == "hello world"
    assert response.metadata["edge_cloud_router"] == {
        "target": "cloud",
        "policy_origin": "cloud",
    }


@pytest.mark.asyncio
async def test_react_context_save_preserves_router_metadata() -> None:
    config = ReActAgentConfig()
    config.configure_model_client(
        provider="OpenAI",
        api_key="test-key",
        api_base="http://model.invalid/v1",
        model_name="test-model",
    )
    config.configure_context_engine(max_context_message_num=100)
    agent = _StreamingReActAgent(card=AgentCard(name="test", id="test")).configure(config)
    agent.set_llm(_MetadataStreamingModel())

    session = MagicMock(spec=Session)
    session.get_session_id.return_value = "session-1"
    session.write_stream = AsyncMock()
    model_context = MagicMock()
    model_context.add_messages = AsyncMock()
    model_context.get_messages.return_value = []
    model_context.session_id.return_value = "session-1"
    model_context.get_context_window = AsyncMock(
        return_value=ContextWindow(system_messages=[], context_messages=[], tools=[])
    )
    model_context.detect_context_window_change.return_value = None
    agent._init_context = AsyncMock(return_value=model_context)
    agent._update_skill_prompt_builder_section = AsyncMock()
    agent.ability_manager.list_tool_info = AsyncMock(return_value=[])
    agent.context_engine.save_contexts = AsyncMock()

    result = await agent._inner_invoke(
        session=session,
        inputs={"query": "hello"},
        query="hello",
        need_cleanup=False,
        conversation_id="session-1",
        _streaming=True,
    )

    saved_message = model_context.add_messages.await_args_list[-1].args[0]
    assert result == {"output": "hello world", "result_type": "answer"}
    assert saved_message.metadata["edge_cloud_router"] == {
        "target": "cloud",
        "policy_origin": "cloud",
    }


@pytest.mark.asyncio
async def test_llm_controller_stream_aggregation_preserves_router_metadata() -> None:
    controller = object.__new__(LLMController)
    session = MagicMock(spec=Session)
    session.write_stream = AsyncMock()

    response = await controller._call_llm_get_output(
        _MetadataStreamingModel(),
        "test-model",
        [],
        [],
        session,
    )

    assert response.content == "hello world"
    assert response.metadata["edge_cloud_router"] == {
        "target": "cloud",
        "policy_origin": "cloud",
    }
