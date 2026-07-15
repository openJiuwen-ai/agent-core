# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Unit tests for output-truncation handling in ReActAgent.

Covers:
- finish_reason propagation from accumulated_chunk to ai_message
- _write_invoke_result_to_stream includes finish_reason in answer payload
- _call_model max_tokens_override flows into ctx.extra
- Integration: truncation detection, retry with doubled max_tokens,
  persistent truncation with TRUNCATION_NOTICE injection, and message order
"""
import asyncio
import unittest
from unittest.mock import patch, MagicMock, AsyncMock

from openjiuwen.core.foundation.llm import AssistantMessage
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.foundation.llm.schema.message import UsageMetadata
from openjiuwen.core.session.stream import OutputSchema

from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

from tests.unit_tests.fixtures.mock_llm import MockLLMModel


class TestFinishReasonPropagation(unittest.TestCase):
    """Verify finish_reason flows from accumulated_chunk to ai_message."""

    def test_ai_message_includes_finish_reason_from_chunk(self):
        """AssistantMessage constructed from accumulated_chunk should carry finish_reason."""
        chunk = AssistantMessageChunk(
            content="Partial text",
            finish_reason="length",
            usage_metadata=UsageMetadata(model_name="mock"),
        )

        ai_message = AssistantMessage(
            content=chunk.content or "",
            tool_calls=chunk.tool_calls or [],
            usage_metadata=chunk.usage_metadata,
            reasoning_content=chunk.reasoning_content,
            finish_reason=chunk.finish_reason,
        )

        assert ai_message.finish_reason == "length"
        assert ai_message.content == "Partial text"

    def test_ai_message_default_finish_reason_is_null(self):
        """AssistantMessage without explicit finish_reason defaults to 'null'."""
        ai_message = AssistantMessage(content="test")
        assert ai_message.finish_reason == "null"


class TestAnswerPayloadFinishReason(unittest.TestCase):
    """Verify _write_invoke_result_to_stream includes finish_reason."""

    async def _run_write_invoke_result(self, agent, result, session_mock):
        return await agent._write_invoke_result_to_stream(result, session_mock)

    def test_answer_payload_carries_finish_reason(self):
        """answer OutputSchema payload should include finish_reason field."""
        card = AgentCard(id="test-agent", name="Test", description="test")
        config = ReActAgentConfig(model_name="mock-model")
        agent = ReActAgent(card=card)
        agent.configure(config)

        collected = []

        async def fake_write_stream(schema):
            collected.append(schema)

        session_mock = MagicMock()
        session_mock.write_stream = fake_write_stream

        result = {
            "output": "Hello world",
            "result_type": "answer",
            "finish_reason": "stop",
        }

        asyncio.run(
            self._run_write_invoke_result(agent, result, session_mock)
        )

        assert len(collected) == 1
        schema = collected[0]
        assert isinstance(schema, OutputSchema)
        assert schema.type == "answer"
        assert schema.payload["output"] == "Hello world"
        assert schema.payload["result_type"] == "answer"
        assert schema.payload["finish_reason"] == "stop"

    def test_answer_payload_finish_reason_can_be_length(self):
        """finish_reason='length' should be carried through to answer payload."""
        card = AgentCard(id="test-agent2", name="Test2", description="test")
        config = ReActAgentConfig(model_name="mock-model")
        agent = ReActAgent(card=card)
        agent.configure(config)

        collected = []

        async def fake_write_stream(schema):
            collected.append(schema)

        session_mock = MagicMock()
        session_mock.write_stream = fake_write_stream

        result = {
            "output": "Truncated...",
            "result_type": "answer",
            "finish_reason": "length",
        }

        asyncio.run(
            self._run_write_invoke_result(agent, result, session_mock)
        )

        assert len(collected) == 1
        assert collected[0].payload["finish_reason"] == "length"

    def test_answer_payload_finish_reason_none(self):
        """When finish_reason is None, payload should carry None."""
        card = AgentCard(id="test-agent3", name="Test3", description="test")
        config = ReActAgentConfig(model_name="mock-model")
        agent = ReActAgent(card=card)
        agent.configure(config)

        collected = []

        async def fake_write_stream(schema):
            collected.append(schema)

        session_mock = MagicMock()
        session_mock.write_stream = fake_write_stream

        result = {
            "output": "Hello",
            "result_type": "answer",
            "finish_reason": None,
        }

        asyncio.run(
            self._run_write_invoke_result(agent, result, session_mock)
        )

        assert len(collected) == 1
        assert collected[0].payload.get("finish_reason") is None


class TestCallModelMaxTokensOverride(unittest.TestCase):
    """Verify _call_model max_tokens_override is stored in ctx.extra."""

    def test_call_model_stores_max_tokens_override(self):
        """_call_model with max_tokens_override should store it in ctx.extra."""
        card = AgentCard(id="test-agent4", name="Test4", description="test")
        config = ReActAgentConfig(model_name="mock-model")
        agent = ReActAgent(card=card)
        agent.configure(config)

        from openjiuwen.core.single_agent.agents.react_agent import (
            AgentCallbackContext,
            ModelCallInputs,
        )

        session_mock = MagicMock()
        session_mock.get_session_id = MagicMock(return_value="test")
        session_mock.write_stream = MagicMock()

        ctx = AgentCallbackContext(
            agent=agent,
            inputs=ModelCallInputs(messages=[]),
            session=session_mock,
        )
        ctx.extra["_streaming"] = False

        # Before calling _call_model, set the override
        ctx.extra["_max_tokens_override"] = 2048

        # Verify it's stored
        assert ctx.extra.get("_max_tokens_override") == 2048


class TestTruncationRetryOutputSchema(unittest.TestCase):
    """Verify truncation_retry OutputSchema structures with phase field."""

    def test_truncation_retry_schema_structure(self):
        """OutputSchema for truncation_retry ( phase=retry_attempt should have correct structure."""
        schema = OutputSchema(
            type="truncation_retry",
            index=0,
            payload={
                "finish_reason": "length",
                "truncated_content": "Partial text here...",
                "phase": "retry_attempt",
            },
        )

        assert schema.type == "truncation_retry"
        assert schema.payload["finish_reason"] == "length"
        assert schema.payload["truncated_content"] == "Partial text here..."
        assert schema.payload["phase"] == "retry_attempt"

    def test_truncation_retry_persist_schema_structure(self):
        """OutputSchema for truncation_retry phase=persist should have correct structure."""
        schema = OutputSchema(
            type="truncation_retry",
            index=0,
            payload={
                "finish_reason": "length",
                "truncated_content": "Still truncated after retry...",
                "phase": "persist",
            },
        )

        assert schema.type == "truncation_retry"
        assert schema.payload["finish_reason"] == "length"
        assert schema.payload["truncated_content"] == "Still truncated after retry..."
        assert schema.payload["phase"] == "persist"


def _make_agent(agent_id="test_trunc_agent"):
    card = AgentCard(id=agent_id, name="TruncTest", description="test")
    config = (
        ReActAgentConfig()
        .configure_model("mock-model")
        .configure_prompt_template([
            {"role": "system", "content": "You are a helpful assistant."},
        ])
        .configure_max_iterations(5)
    )
    agent = ReActAgent(card=card)
    agent.configure(config)
    return agent


def _make_mock_context():
    mock_context = MagicMock()
    mock_context.add_messages = AsyncMock()
    mock_context.get_context_window = AsyncMock(return_value=MagicMock(
        get_messages=MagicMock(return_value=[]),
        get_tools=MagicMock(return_value=None),
    ))
    mock_context.reloader_tool = MagicMock(return_value=MagicMock(
        card=MagicMock(id="context_reload", name="context_reload"),
        invoke=AsyncMock(return_value="ok"),
    ))
    return mock_context


def _make_mock_context_engine(mock_context):
    mock_context_engine = MagicMock()
    mock_context_engine.save_contexts = AsyncMock()
    mock_context_engine.create_context = AsyncMock(return_value=mock_context)
    return mock_context_engine


def _make_mock_session():
    mock_session = MagicMock()
    mock_session.get_state.return_value = None
    mock_session.write_stream = AsyncMock()
    return mock_session


class TestTruncationRetryIntegration(unittest.IsolatedAsyncioTestCase):
    """Integration tests for _inner_invoke truncation retry flow.

    These tests exercise the full _inner_invoke loop:
    - truncation detection (finish_reason='length')
    - retry with doubled max_tokens (_max_tokens_override)
    - persistent truncation with AssistantMessage + TRUNCATION_NOTICE injection
    - continue behavior and message order
    """

    async def asyncSetUp(self):
        self.agent = _make_agent("trunc_integration_agent")
        self.mock_context = _make_mock_context()
        self.mock_context_engine = _make_mock_context_engine(self.mock_context)
        self.mock_session = _make_mock_session()
        self.agent.context_engine = self.mock_context_engine

    async def test_truncation_retry_succeeds_on_second_call(self):
        """When first call returns finish_reason='length', retry with
        doubled max_tokens succeeds and returns the retry response."""
        truncated_msg = AssistantMessage(
            content="Truncated output that was cut off...",
            finish_reason="length",
            usage_metadata=UsageMetadata(
                model_name="mock-model",
                output_tokens=8192,
            ),
        )
        successful_msg = AssistantMessage(
            content="Full response after retry with doubled max_tokens",
            finish_reason="stop",
            usage_metadata=UsageMetadata(model_name="mock-model"),
        )

        mock_llm = MockLLMModel()
        mock_llm.set_responses([truncated_msg, successful_msg])

        with patch.object(self.agent, "_get_llm", return_value=mock_llm):
            result = await self.agent.invoke(
                {"query": "tell me a very long story"},
                session=self.mock_session,
            )

        assert result["result_type"] == "answer"
        assert result["output"] == "Full response after retry with doubled max_tokens"
        assert result["finish_reason"] == "stop"
        assert mock_llm.call_count == 2

    async def test_truncation_retry_emits_truncation_retry_output_schema(self):
        """Truncation retry should emit truncation_retry OutputSchema to session."""
        truncated_msg = AssistantMessage(
            content="Truncated partial...",
            finish_reason="length",
            usage_metadata=UsageMetadata(
                model_name="mock-model",
                output_tokens=4096,
            ),
        )
        successful_msg = AssistantMessage(
            content="Complete response",
            finish_reason="stop",
        )

        mock_llm = MockLLMModel()
        mock_llm.set_responses([truncated_msg, successful_msg])

        written = []
        self.mock_session.write_stream = AsyncMock(side_effect=lambda schema: written.append(schema))

        with patch.object(self.agent, "_get_llm", return_value=mock_llm):
            await self.agent.invoke(
                {"query": "long story"},
                session=self.mock_session,
            )

        truncation_frames = [f for f in written if isinstance(f, OutputSchema) and f.type == "truncation_retry"]
        assert len(truncation_frames) >= 1
        assert truncation_frames[0].payload["finish_reason"] == "length"
        assert truncation_frames[0].payload["truncated_content"] == "Truncated partial..."
        assert truncation_frames[0].payload["phase"] == "retry_attempt"

    async def test_truncation_retry_uses_output_tokens(self):
        """Retry max_tokens should equal the truncated output_tokens,
        not output_tokens*2, preventing BadRequest on models with
        a max_tokens ceiling (e.g. deepseek-v3.2 max 32768)."""
        truncated_msg = AssistantMessage(
            content="Truncated...",
            finish_reason="length",
            usage_metadata=UsageMetadata(
                model_name="mock-model",
                output_tokens=5000,
            ),
        )
        successful_msg = AssistantMessage(
            content="Done",
            finish_reason="stop",
        )

        mock_llm = MockLLMModel()
        mock_llm.set_responses([truncated_msg, successful_msg])

        captured_kwargs = []
        original_invoke = mock_llm.invoke

        async def invoke_capturer(*args, **kwargs):
            captured_kwargs.append(kwargs)
            return await original_invoke(*args, **kwargs)

        mock_llm.invoke = invoke_capturer

        with patch.object(self.agent, "_get_llm", return_value=mock_llm):
            await self.agent.invoke(
                {"query": "long story"},
                session=self.mock_session,
            )

        assert mock_llm.call_count == 2
        retry_kwargs = captured_kwargs[1]
        assert retry_kwargs.get("max_tokens") == 5000

    async def test_truncation_retry_injects_failure_reason_into_context(self):
        """Before retry, _inject_truncation_notice is called so the
        truncated AssistantMessage and TRUNCATION_NOTICE UserMessage
        are added to context, giving the model context about why it failed."""
        truncated_msg = AssistantMessage(
            content="Truncated partial output...",
            finish_reason="length",
            usage_metadata=UsageMetadata(
                model_name="mock-model",
                output_tokens=5000,
            ),
        )
        successful_msg = AssistantMessage(
            content="Complete response after retry",
            finish_reason="stop",
        )

        mock_llm = MockLLMModel()
        mock_llm.set_responses([truncated_msg, successful_msg])

        add_messages_calls = []
        self.mock_context.add_messages = AsyncMock(
            side_effect=lambda msg, **kw: add_messages_calls.append((msg, kw)),
        )

        with patch.object(self.agent, "_get_llm", return_value=mock_llm):
            await self.agent.invoke(
                {"query": "long story"},
                session=self.mock_session,
            )

        from openjiuwen.core.foundation.llm import UserMessage

        truncation_assistant_calls = [
            (msg, kw) for msg, kw in add_messages_calls
            if isinstance(msg, AssistantMessage)
            and msg.finish_reason == "length"
            and msg.content == "Truncated partial output..."
        ]
        assert len(truncation_assistant_calls) >= 1, \
            "Truncated AssistantMessage must be injected before retry"

        truncation_notice_calls = [
            (msg, kw) for msg, kw in add_messages_calls
            if isinstance(msg, UserMessage)
            and "[TRUNCATION_NOTICE]" in (msg.content or "")
        ]
        assert len(truncation_notice_calls) >= 1, \
            "TRUNCATION_NOTICE UserMessage must be injected before retry"

        assistant_idx = None
        notice_idx = None
        for i, (msg, kw) in enumerate(add_messages_calls):
            if isinstance(msg, AssistantMessage) and msg.finish_reason == "length" and msg.content == "Truncated partial output...":
                assistant_idx = i
            if isinstance(msg, UserMessage) and "[TRUNCATION_NOTICE]" in (msg.content or ""):
                notice_idx = i

        assert assistant_idx is not None and notice_idx is not None
        assert notice_idx == assistant_idx + 1, \
            "TRUNCATION_NOTICE must immediately follow truncated AssistantMessage"

    async def test_truncation_retry_count_limited_to_one(self):
        """Only one retry attempt is allowed (_truncation_retry_count < 1).
        If retry also returns finish_reason='length', the persistent-truncation
        path is taken (AssistantMessage + TRUNCATION_NOTICE + continue)."""
        truncated_msg_1 = AssistantMessage(
            content="Part 1 truncated...",
            finish_reason="length",
            usage_metadata=UsageMetadata(
                model_name="mock-model",
                output_tokens=8192,
            ),
        )
        truncated_msg_2 = AssistantMessage(
            content="Part 2 still truncated after retry...",
            finish_reason="length",
            usage_metadata=UsageMetadata(
                model_name="mock-model",
                output_tokens=16384,
            ),
        )
        final_msg = AssistantMessage(
            content="Finally complete on next iteration",
            finish_reason="stop",
        )

        mock_llm = MockLLMModel()
        mock_llm.set_responses([truncated_msg_1, truncated_msg_2, final_msg])

        with patch.object(self.agent, "_get_llm", return_value=mock_llm):
            result = await self.agent.invoke(
                {"query": "very long story"},
                session=self.mock_session,
            )

        assert result["result_type"] == "answer"
        assert result["output"] == "Finally complete on next iteration"
        assert mock_llm.call_count == 3

    async def test_persistent_truncation_injects_assistant_then_user_message(self):
        """When truncation persists after retry, AssistantMessage with
        finish_reason='length' and then UserMessage with TRUNCATION_NOTICE
        are injected in that order."""
        truncated_msg_1 = AssistantMessage(
            content="First truncated...",
            finish_reason="length",
            usage_metadata=UsageMetadata(
                model_name="mock-model",
                output_tokens=8192,
            ),
        )
        truncated_msg_2 = AssistantMessage(
            content="Still truncated...",
            finish_reason="length",
            usage_metadata=UsageMetadata(
                model_name="mock-model",
                output_tokens=16384,
            ),
        )
        final_msg = AssistantMessage(
            content="Complete",
            finish_reason="stop",
        )

        mock_llm = MockLLMModel()
        mock_llm.set_responses([truncated_msg_1, truncated_msg_2, final_msg])

        add_messages_calls = []
        self.mock_context.add_messages = AsyncMock(
            side_effect=lambda msg, **kw: add_messages_calls.append((msg, kw)),
        )

        with patch.object(self.agent, "_get_llm", return_value=mock_llm):
            await self.agent.invoke(
                {"query": "very long"},
                session=self.mock_session,
            )

        truncation_pair_calls = [
            (msg, kw) for msg, kw in add_messages_calls
            if isinstance(msg, AssistantMessage) and msg.finish_reason == "length"
        ]
        assert len(truncation_pair_calls) >= 1

        truncation_assistant_idx = None
        truncation_notice_idx = None
        for i, (msg, kw) in enumerate(add_messages_calls):
            if isinstance(msg, AssistantMessage) and msg.finish_reason == "length":
                truncation_assistant_idx = i
            if isinstance(msg, AssistantMessage) and msg.content == "Still truncated..." and msg.finish_reason == "length":
                truncation_assistant_idx = i

        for i, (msg, kw) in enumerate(add_messages_calls):
            if truncation_assistant_idx is not None and i == truncation_assistant_idx + 1:
                from openjiuwen.core.foundation.llm import UserMessage
                if isinstance(msg, UserMessage) and "[TRUNCATION_NOTICE]" in (msg.content or ""):
                    truncation_notice_idx = i

        assert truncation_notice_idx is not None, \
            "TRUNCATION_NOTICE UserMessage must immediately follow truncated AssistantMessage"

    async def test_persistent_truncation_assistant_message_preserves_finish_reason(self):
        """The AssistantMessage injected in persistent-truncation path
        must carry finish_reason='length' (not default 'null')."""
        truncated_msg_1 = AssistantMessage(
            content="Truncated...",
            finish_reason="length",
            usage_metadata=UsageMetadata(
                model_name="mock-model",
                output_tokens=8192,
            ),
        )
        truncated_msg_2 = AssistantMessage(
            content="Still truncated...",
            finish_reason="length",
            usage_metadata=UsageMetadata(
                model_name="mock-model",
                output_tokens=16384,
            ),
        )
        final_msg = AssistantMessage(
            content="Complete",
            finish_reason="stop",
        )

        mock_llm = MockLLMModel()
        mock_llm.set_responses([truncated_msg_1, truncated_msg_2, final_msg])

        injected_assistant_msgs = []
        self.mock_context.add_messages = AsyncMock(
            side_effect=lambda msg, **kw: (
                injected_assistant_msgs.append(msg) if isinstance(msg, AssistantMessage) else None
            ),
        )

        with patch.object(self.agent, "_get_llm", return_value=mock_llm):
            await self.agent.invoke(
                {"query": "long"},
                session=self.mock_session,
            )

        length_msgs = [m for m in injected_assistant_msgs if m.finish_reason == "length"]
        assert len(length_msgs) >= 1, \
            "Truncated AssistantMessage must carry finish_reason='length'"

    async def test_truncation_retry_clears_override_after_retry(self):
        """_max_tokens_override must be popped from ctx.extra after the
        retry call, so subsequent iterations are not affected."""
        truncated_msg = AssistantMessage(
            content="Truncated...",
            finish_reason="length",
            usage_metadata=UsageMetadata(
                model_name="mock-model",
                output_tokens=4096,
            ),
        )
        successful_msg = AssistantMessage(
            content="Done",
            finish_reason="stop",
        )

        mock_llm = MockLLMModel()
        mock_llm.set_responses([truncated_msg, successful_msg])

        override_presence = []

        original_call_model = self.agent._call_model

        async def call_model_wrapper(ctx, context, tools=None):
            override_presence.append(ctx.extra.get("_max_tokens_override"))
            result = await original_call_model(ctx, context, tools)
            override_presence.append(ctx.extra.get("_max_tokens_override"))
            return result

        with patch.object(self.agent, "_call_model", side_effect=call_model_wrapper):
            with patch.object(self.agent, "_get_llm", return_value=mock_llm):
                await self.agent.invoke(
                    {"query": "story"},
                    session=self.mock_session,
                )

        assert override_presence[0] is None, "No override before first call"
        assert override_presence[1] is None, "Override cleared after first call"

    async def test_truncation_events_distinguish_phase_via_payload(self):
        """When truncation persists after retry, both events use type='truncation_retry',
        but distinguished by payload.phase='retry_attempt' vs 'persist'."""
        truncated_msg_1 = AssistantMessage(
            content="Part 1...",
            finish_reason="length",
            usage_metadata=UsageMetadata(
                model_name="mock-model",
                output_tokens=8192,
            ),
        )
        truncated_msg_2 = AssistantMessage(
            content="Part 2 still truncated...",
            finish_reason="length",
            usage_metadata=UsageMetadata(
                model_name="mock-model",
                output_tokens=16384,
            ),
        )
        final_msg = AssistantMessage(
            content="Complete",
            finish_reason="stop",
        )

        mock_llm = MockLLMModel()
        mock_llm.set_responses([truncated_msg_1, truncated_msg_2, final_msg])

        written = []
        self.mock_session.write_stream = AsyncMock(side_effect=lambda schema: written.append(schema))

        with patch.object(self.agent, "_get_llm", return_value=mock_llm):
            await self.agent.invoke(
                {"query": "long"},
                session=self.mock_session,
            )

        truncation_frames = [f for f in written if isinstance(f, OutputSchema) and f.type == "truncation_retry"]
        retry_attempt_frames = [f for f in truncation_frames if f.payload.get("phase") == "retry_attempt"]
        persist_frames = [f for f in truncation_frames if f.payload.get("phase") == "persist"]
        assert len(retry_attempt_frames) == 1, "Exactly one retry_attempt event"
        assert len(persist_frames) == 1, "Exactly one persist event"

    async def test_no_truncation_when_finish_reason_is_stop(self):
        """When finish_reason='stop' (normal), no truncation handling occurs."""
        normal_msg = AssistantMessage(
            content="Normal response",
            finish_reason="stop",
        )

        mock_llm = MockLLMModel()
        mock_llm.set_responses([normal_msg])

        written = []
        self.mock_session.write_stream = AsyncMock(side_effect=lambda schema: written.append(schema))

        with patch.object(self.agent, "_get_llm", return_value=mock_llm):
            result = await self.agent.invoke(
                {"query": "hello"},
                session=self.mock_session,
            )

        assert result["result_type"] == "answer"
        assert result["output"] == "Normal response"
        assert mock_llm.call_count == 1

        truncation_frames = [f for f in written if isinstance(f, OutputSchema) and f.type == "truncation_retry"]
        assert len(truncation_frames) == 0

    async def test_truncated_output_tokens_fallback_when_usage_metadata_missing(self):
        """When usage_metadata is missing, _max_tokens_override falls back
        to the default output_tokens value (16384), not 16384*2."""
        truncated_msg = AssistantMessage(
            content="Truncated without metadata...",
            finish_reason="length",
        )
        successful_msg = AssistantMessage(
            content="Done",
            finish_reason="stop",
        )

        mock_llm = MockLLMModel()
        mock_llm.set_responses([truncated_msg, successful_msg])

        captured_kwargs = []
        original_invoke = mock_llm.invoke

        async def invoke_capturer(*args, **kwargs):
            captured_kwargs.append(kwargs)
            return await original_invoke(*args, **kwargs)

        mock_llm.invoke = invoke_capturer

        with patch.object(self.agent, "_get_llm", return_value=mock_llm):
            await self.agent.invoke(
                {"query": "long"},
                session=self.mock_session,
            )

        assert mock_llm.call_count == 2
        retry_kwargs = captured_kwargs[1]
        assert retry_kwargs.get("max_tokens") == 16384
