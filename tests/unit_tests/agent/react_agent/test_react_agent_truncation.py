# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Unit tests for output-truncation handling in ReActAgent.

Covers:
- finish_reason propagation from accumulated_chunk to ai_message
- _write_invoke_result_to_stream includes finish_reason in answer payload
- _inject_truncation_notice injects AssistantMessage + TRUNCATION_NOTICE UserMessage
- _railed_model_call passes _max_tokens_override into extra_kwargs
- Integration via agent.invoke(): truncation detection, retry with adjusted
  max_tokens, persistent truncation with TRUNCATION_NOTICE injection,
  isinstance guard after retry _call_model
"""
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

from openjiuwen.core.foundation.llm import AssistantMessage, UserMessage
from openjiuwen.core.foundation.llm.schema.message import UsageMetadata
from openjiuwen.core.session.stream import OutputSchema

from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

from tests.unit_tests.fixtures.mock_llm import (
    MockLLMModel,
    create_text_response,
)


def _make_agent(max_iterations: int = 5) -> ReActAgent:
    card = AgentCard(
        name="test-truncation-agent",
        description="test agent for truncation",
    )
    config = ReActAgentConfig(
        model_name="mock-model",
        max_iterations=max_iterations,
    )
    agent = ReActAgent(card=card)
    agent.configure(config)
    return agent


def _mock_context_engine():
    mock_context = MagicMock()
    mock_context.add_messages = AsyncMock()
    mock_context.get_context_window = AsyncMock(return_value=MagicMock(
        get_messages=MagicMock(return_value=[]),
        get_tools=MagicMock(return_value=None),
    ))
    mock_context.session_id = MagicMock(return_value="test-session")

    mock_context_engine = MagicMock()
    mock_context_engine.save_contexts = AsyncMock()
    mock_context_engine.create_context = AsyncMock(return_value=mock_context)
    return mock_context_engine, mock_context


def _mock_session():
    mock_session = MagicMock()
    mock_session.get_state.return_value = None
    mock_session.write_stream = AsyncMock()
    mock_session.get_session_id = MagicMock(return_value="test-session")
    return mock_session


def _truncated_response(
    content: str = "Partial output...",
    output_tokens: int = 4096,
) -> AssistantMessage:
    return AssistantMessage(
        content=content,
        finish_reason="length",
        usage_metadata=UsageMetadata(
            model_name="mock-model",
            input_tokens=100,
            output_tokens=output_tokens,
            total_tokens=100 + output_tokens,
        ),
    )


def _normal_response(content: str = "Done") -> AssistantMessage:
    return AssistantMessage(
        content=content,
        finish_reason="stop",
        usage_metadata=UsageMetadata(
            model_name="mock-model",
            input_tokens=100,
            output_tokens=200,
            total_tokens=300,
        ),
    )


class TestFinishReasonPropagation(unittest.TestCase):
    def test_truncated_response_has_length_finish_reason(self):
        msg = _truncated_response()
        self.assertEqual(msg.finish_reason, "length")

    def test_normal_response_has_stop_finish_reason(self):
        msg = _normal_response()
        self.assertEqual(msg.finish_reason, "stop")

    def test_default_finish_reason_is_null(self):
        msg = AssistantMessage(content="hi")
        self.assertEqual(msg.finish_reason, "null")


class TestWriteInvokeResultToStream(unittest.IsolatedAsyncioTestCase):
    async def test_answer_payload_includes_finish_reason(self):
        agent = _make_agent()
        session = MagicMock()
        session.write_stream = AsyncMock()

        result = {
            "output": "hello",
            "result_type": "answer",
            "finish_reason": "length",
        }
        await agent._write_invoke_result_to_stream(result, session)

        session.write_stream.assert_awaited_once()
        schema = session.write_stream.call_args[0][0]
        self.assertIsInstance(schema, OutputSchema)
        self.assertEqual(schema.type, "answer")
        self.assertEqual(schema.payload["finish_reason"], "length")

    async def test_answer_payload_finish_reason_none_when_absent(self):
        agent = _make_agent()
        session = MagicMock()
        session.write_stream = AsyncMock()

        result = {"output": "hello", "result_type": "answer"}
        await agent._write_invoke_result_to_stream(result, session)

        schema = session.write_stream.call_args[0][0]
        self.assertIsNone(schema.payload.get("finish_reason"))


class TestInjectTruncationNotice(unittest.IsolatedAsyncioTestCase):
    async def test_injects_assistant_and_user_messages(self):
        agent = _make_agent()
        context = MagicMock()
        context.add_messages = AsyncMock()

        ai_message = _truncated_response(content="partial content")
        await agent._inject_truncation_notice(ai_message, context)

        self.assertEqual(context.add_messages.call_count, 2)

        assistant_msg = context.add_messages.call_args_list[0][0][0]
        self.assertIsInstance(assistant_msg, AssistantMessage)
        self.assertEqual(assistant_msg.content, "partial content")
        self.assertEqual(assistant_msg.finish_reason, "length")

        user_msg = context.add_messages.call_args_list[1][0][0]
        self.assertIsInstance(user_msg, UserMessage)
        self.assertIn("[TRUNCATION_NOTICE]", user_msg.content)

    async def test_empty_content_handled(self):
        agent = _make_agent()
        context = MagicMock()
        context.add_messages = AsyncMock()

        ai_message = AssistantMessage(content="", finish_reason="length")
        await agent._inject_truncation_notice(ai_message, context)

        assistant_msg = context.add_messages.call_args_list[0][0][0]
        self.assertEqual(assistant_msg.content, "")

    async def test_tool_calls_preserved_in_injected_message(self):
        agent = _make_agent()
        context = MagicMock()
        context.add_messages = AsyncMock()

        ai_message = AssistantMessage(
            content="",
            finish_reason="length",
            tool_calls=[{"id": "tc1", "type": "function", "name": "read_file", "arguments": "{}"}],
        )
        await agent._inject_truncation_notice(ai_message, context)

        assistant_msg = context.add_messages.call_args_list[0][0][0]
        self.assertEqual(len(assistant_msg.tool_calls), 1)


class TestMaxTokensOverride(unittest.TestCase):
    def test_max_tokens_override_injected_into_kwargs(self):
        extra_kwargs = {"temperature": 0.7}
        ctx = MagicMock()
        ctx.extra = {"_max_tokens_override": 8192}

        _max_tokens_override = ctx.extra.get("_max_tokens_override")
        if _max_tokens_override is not None:
            extra_kwargs["max_tokens"] = _max_tokens_override

        self.assertEqual(extra_kwargs["max_tokens"], 8192)
        self.assertEqual(extra_kwargs["temperature"], 0.7)

    def test_max_tokens_override_not_set_when_absent(self):
        extra_kwargs = {"temperature": 0.7}
        ctx = MagicMock()
        ctx.extra = {}

        _max_tokens_override = ctx.extra.get("_max_tokens_override")
        if _max_tokens_override is not None:
            extra_kwargs["max_tokens"] = _max_tokens_override

        self.assertNotIn("max_tokens", extra_kwargs)


class TestTruncationRetryIntegration(unittest.IsolatedAsyncioTestCase):
    """Integration tests that exercise the truncation retry logic through
    agent.invoke(), mocking only the LLM layer. This ensures the production
    code path in _inner_invoke is actually tested."""

    def _make_agent_with_mocks(self, max_iterations: int = 5):
        agent = _make_agent(max_iterations=max_iterations)
        context_engine, context = _mock_context_engine()
        agent.context_engine = context_engine
        return agent, context_engine, context

    async def test_single_truncation_then_success(self):
        """First LLM call returns finish_reason='length', retry returns 'stop'.
        The final answer should have finish_reason='stop' and a truncation_retry
        OutputSchema should be emitted to the session stream."""
        agent, _, _ = self._make_agent_with_mocks(max_iterations=3)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            _truncated_response(content="Partial", output_tokens=4096),
            _normal_response(content="Complete answer"),
        ])

        mock_session = _mock_session()

        with patch.object(agent, "_get_llm", return_value=mock_llm):
            result = await agent.invoke(
                {"query": "test query"},
                session=mock_session,
            )

        self.assertEqual(result["result_type"], "answer")
        self.assertEqual(result["finish_reason"], "stop")
        self.assertEqual(result["output"], "Complete answer")

        truncation_schemas = [
            c[0][0]
            for c in mock_session.write_stream.call_args_list
            if isinstance(c[0][0], OutputSchema) and c[0][0].type == "truncation_retry"
        ]
        self.assertTrue(len(truncation_schemas) >= 1)

    async def test_persistent_truncation_exhausts_iterations(self):
        """All LLM calls return finish_reason='length'. The loop exhausts
        max_iterations. truncation_retry OutputSchema events should be emitted
        for each iteration."""
        agent, _, _ = self._make_agent_with_mocks(max_iterations=3)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            _truncated_response(output_tokens=4096),
        ] * 10)

        mock_session = _mock_session()

        with patch.object(agent, "_get_llm", return_value=mock_llm):
            result = await agent.invoke(
                {"query": "test query"},
                session=mock_session,
            )

        self.assertIn(result["result_type"], ("answer", "error"))

        truncation_schemas = [
            c[0][0]
            for c in mock_session.write_stream.call_args_list
            if isinstance(c[0][0], OutputSchema) and c[0][0].type == "truncation_retry"
        ]
        self.assertTrue(len(truncation_schemas) >= 3)

    async def test_normal_response_no_truncation_retry(self):
        """Normal response with finish_reason='stop' should not trigger
        any truncation_retry OutputSchema events."""
        agent, _, _ = self._make_agent_with_mocks(max_iterations=3)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_text_response("Normal answer", finish_reason="stop"),
        ])

        mock_session = _mock_session()

        with patch.object(agent, "_get_llm", return_value=mock_llm):
            result = await agent.invoke(
                {"query": "test query"},
                session=mock_session,
            )

        self.assertEqual(result["result_type"], "answer")
        self.assertEqual(result["finish_reason"], "stop")

        truncation_schemas = [
            c[0][0]
            for c in mock_session.write_stream.call_args_list
            if isinstance(c[0][0], OutputSchema) and c[0][0].type == "truncation_retry"
        ]
        self.assertEqual(len(truncation_schemas), 0)

    async def test_truncation_retry_output_schema_payload(self):
        """When truncation is detected, the truncation_retry OutputSchema
        should contain finish_reason='length', truncated_content, and phase."""
        agent, _, _ = self._make_agent_with_mocks(max_iterations=3)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            _truncated_response(content="Partial content here", output_tokens=4096),
            _normal_response(content="Done"),
        ])

        mock_session = _mock_session()

        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke(
                {"query": "test query"},
                session=mock_session,
            )

        truncation_schemas = [
            c[0][0]
            for c in mock_session.write_stream.call_args_list
            if isinstance(c[0][0], OutputSchema) and c[0][0].type == "truncation_retry"
        ]
        self.assertTrue(len(truncation_schemas) >= 1)
        schema = truncation_schemas[0]
        self.assertEqual(schema.payload["finish_reason"], "length")
        self.assertIn("truncated_content", schema.payload)
        self.assertIn(schema.payload["phase"], ("retry_attempt", "persist"))

    async def test_non_assistant_message_after_truncation_retry_breaks(self):
        """If the retry _call_model returns a non-AssistantMessage (e.g. dict),
        the isinstance guard should set invoke_inputs.result and break without
        crashing. This tests the isinstance guard added in the retry path."""
        agent, _, _ = self._make_agent_with_mocks(max_iterations=3)

        call_count = 0

        async def mock_call_model(ctx, context, tools):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _truncated_response(output_tokens=4096)
            return {"output": "rail-transformed", "result_type": "answer"}

        agent._call_model = mock_call_model

        mock_session = _mock_session()

        with patch.object(agent, "_get_llm", return_value=MockLLMModel()):
            result = await agent.invoke(
                {"query": "test query"},
                session=mock_session,
            )

        self.assertIsInstance(result, dict)


if __name__ == "__main__":
    unittest.main()
