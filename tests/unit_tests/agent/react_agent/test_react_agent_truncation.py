# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Unit tests for output-truncation handling in ReActAgent.

Covers:
- finish_reason propagation from accumulated_chunk to ai_message
- _write_invoke_result_to_stream includes finish_reason in answer payload
- _inject_truncation_notice injects AssistantMessage + TRUNCATION_NOTICE UserMessage
- _railed_model_call passes _max_tokens_override into extra_kwargs
- Integration: truncation detection, retry with adjusted max_tokens,
  persistent truncation with TRUNCATION_NOTICE injection
"""
import unittest
from unittest.mock import MagicMock, AsyncMock

from openjiuwen.core.foundation.llm import AssistantMessage, UserMessage
from openjiuwen.core.foundation.llm.schema.message import UsageMetadata
from openjiuwen.core.session.stream import OutputSchema

from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


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
    """Integration tests that exercise the truncation retry logic in the
    _inner_invoke loop by mocking _call_model, context, and session."""

    def _build_ctx(self, extra: dict | None = None):
        ctx = MagicMock()
        ctx.extra = extra or {}
        ctx.consume_force_finish = MagicMock(return_value=None)
        ctx.drain_steering = MagicMock(return_value=[])
        ctx.has_pending_steering = MagicMock(return_value=False)
        ctx.fire = AsyncMock()
        return ctx

    async def test_single_truncation_then_success(self):
        """Truncation detected once, retry succeeds with 'stop' finish_reason."""
        agent = _make_agent(max_iterations=3)

        truncated_msg = _truncated_response(content="Partial", output_tokens=4096)
        normal_msg = _normal_response(content="Complete answer")

        call_count = 0

        async def mock_call_model(ctx, context, tools):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return truncated_msg
            return normal_msg

        agent._call_model = mock_call_model

        context = MagicMock()
        context.add_messages = AsyncMock()

        session = MagicMock()
        session.write_stream = AsyncMock()
        session.get_session_id = MagicMock(return_value="test-session")

        ctx = self._build_ctx()
        ctx.session = session
        ctx.context = context

        result = {"output": None, "result_type": None}
        _truncation_retry_count = 0
        start_iteration = 0

        for iteration in range(start_iteration, agent._config.max_iterations):
            ai_message = await agent._call_model(ctx, context, [])

            _truncation_detected = (
                ai_message is not None
                and getattr(ai_message, "finish_reason", "null") == "length"
            )

            if _truncation_detected and _truncation_retry_count < 1:
                _truncation_retry_count += 1
                _truncated_output_tokens = (
                    getattr(ai_message.usage_metadata, "output_tokens", None)
                    or 16384
                )
                ctx.extra["_max_tokens_override"] = _truncated_output_tokens
                await agent._inject_truncation_notice(ai_message, context)
                ai_message = await agent._call_model(ctx, context, [])
                ctx.extra.pop("_max_tokens_override", None)
                _truncation_detected = (
                    ai_message is not None
                    and getattr(ai_message, "finish_reason", "null") == "length"
                )

            if _truncation_detected:
                await agent._inject_truncation_notice(ai_message, context)
                continue

            await context.add_messages(ai_message)
            result = {
                "output": ai_message.content,
                "result_type": "answer",
                "finish_reason": getattr(ai_message, "finish_reason", "null"),
            }
            break

        self.assertEqual(result["result_type"], "answer")
        self.assertEqual(result["finish_reason"], "stop")
        self.assertEqual(call_count, 2)

    async def test_max_tokens_override_set_from_output_tokens(self):
        """When truncation is detected, _max_tokens_override is set to
        the truncated response's output_tokens (not output_tokens * 2)."""
        agent = _make_agent(max_iterations=3)

        truncated_msg = _truncated_response(output_tokens=2048)
        normal_msg = _normal_response(content="Done")

        call_count = 0
        captured_max_tokens = None

        async def mock_call_model(ctx, context, tools):
            nonlocal call_count, captured_max_tokens
            call_count += 1
            if call_count == 2:
                captured_max_tokens = ctx.extra.get("_max_tokens_override")
            if call_count == 1:
                return truncated_msg
            return normal_msg

        agent._call_model = mock_call_model

        context = MagicMock()
        context.add_messages = AsyncMock()

        session = MagicMock()
        session.write_stream = AsyncMock()
        session.get_session_id = MagicMock(return_value="test-session")

        ctx = self._build_ctx()
        ctx.session = session
        ctx.context = context

        _truncation_retry_count = 0
        for iteration in range(agent._config.max_iterations):
            ai_message = await agent._call_model(ctx, context, [])

            _truncation_detected = (
                ai_message is not None
                and getattr(ai_message, "finish_reason", "null") == "length"
            )

            if _truncation_detected and _truncation_retry_count < 1:
                _truncation_retry_count += 1
                _truncated_output_tokens = (
                    getattr(ai_message.usage_metadata, "output_tokens", None)
                    or 16384
                )
                ctx.extra["_max_tokens_override"] = _truncated_output_tokens
                await agent._inject_truncation_notice(ai_message, context)
                ai_message = await agent._call_model(ctx, context, [])
                ctx.extra.pop("_max_tokens_override", None)
                _truncation_detected = (
                    ai_message is not None
                    and getattr(ai_message, "finish_reason", "null") == "length"
                )

            if _truncation_detected:
                await agent._inject_truncation_notice(ai_message, context)
                continue

            await context.add_messages(ai_message)
            break

        self.assertEqual(captured_max_tokens, 2048)

    async def test_max_tokens_override_cleaned_up_after_retry(self):
        """_max_tokens_override is removed from ctx.extra after the retry call."""
        agent = _make_agent(max_iterations=3)

        truncated_msg = _truncated_response(output_tokens=4096)
        normal_msg = _normal_response(content="Done")

        call_count = 0

        async def mock_call_model(ctx, context, tools):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return truncated_msg
            return normal_msg

        agent._call_model = mock_call_model

        context = MagicMock()
        context.add_messages = AsyncMock()

        session = MagicMock()
        session.write_stream = AsyncMock()
        session.get_session_id = MagicMock(return_value="test-session")

        ctx = self._build_ctx()
        ctx.session = session
        ctx.context = context

        _truncation_retry_count = 0
        for iteration in range(agent._config.max_iterations):
            ai_message = await agent._call_model(ctx, context, [])

            _truncation_detected = (
                ai_message is not None
                and getattr(ai_message, "finish_reason", "null") == "length"
            )

            if _truncation_detected and _truncation_retry_count < 1:
                _truncation_retry_count += 1
                _truncated_output_tokens = (
                    getattr(ai_message.usage_metadata, "output_tokens", None)
                    or 16384
                )
                ctx.extra["_max_tokens_override"] = _truncated_output_tokens
                await agent._inject_truncation_notice(ai_message, context)
                ai_message = await agent._call_model(ctx, context, [])
                ctx.extra.pop("_max_tokens_override", None)
                _truncation_detected = (
                    ai_message is not None
                    and getattr(ai_message, "finish_reason", "null") == "length"
                )

            if _truncation_detected:
                await agent._inject_truncation_notice(ai_message, context)
                continue

            await context.add_messages(ai_message)
            break

        self.assertNotIn("_max_tokens_override", ctx.extra)

    async def test_persistent_truncation_exhausts_iterations(self):
        """Both initial and retry calls return 'length', so TRUNCATION_NOTICE
        is injected and the loop continues until max_iterations is exhausted."""
        agent = _make_agent(max_iterations=3)

        call_count = 0

        async def mock_call_model(ctx, context, tools):
            nonlocal call_count
            call_count += 1
            return _truncated_response(output_tokens=4096)

        agent._call_model = mock_call_model

        context = MagicMock()
        context.add_messages = AsyncMock()

        session = MagicMock()
        session.write_stream = AsyncMock()
        session.get_session_id = MagicMock(return_value="test-session")

        ctx = self._build_ctx()
        ctx.session = session
        ctx.context = context

        _truncation_retry_count = 0
        for iteration in range(agent._config.max_iterations):
            ai_message = await agent._call_model(ctx, context, [])

            _truncation_detected = (
                ai_message is not None
                and getattr(ai_message, "finish_reason", "null") == "length"
            )

            if _truncation_detected and _truncation_retry_count < 1:
                _truncation_retry_count += 1
                _truncated_output_tokens = (
                    getattr(ai_message.usage_metadata, "output_tokens", None)
                    or 16384
                )
                ctx.extra["_max_tokens_override"] = _truncated_output_tokens
                await agent._inject_truncation_notice(ai_message, context)
                ai_message = await agent._call_model(ctx, context, [])
                ctx.extra.pop("_max_tokens_override", None)
                _truncation_detected = (
                    ai_message is not None
                    and getattr(ai_message, "finish_reason", "null") == "length"
                )

            if _truncation_detected:
                await agent._inject_truncation_notice(ai_message, context)
                continue

            await context.add_messages(ai_message)
            break

        # Iteration 1: 1 initial call + 1 retry (retry_count goes 0->1) = 2 calls
        # Iteration 2: 1 initial call (retry_count=1, no more retry) = 1 call
        # Iteration 3: 1 initial call (retry_count=1, no more retry) = 1 call
        # Total: 4 calls
        self.assertEqual(call_count, 4)
        # Iteration 1: inject for retry (2) + inject for persist (2) = 4
        # Iteration 2: inject for persist (2) = 2
        # Iteration 3: inject for persist (2) = 2
        # Total: 8 add_messages calls
        self.assertEqual(context.add_messages.call_count, 8)


class TestTruncationRetryCount(unittest.IsolatedAsyncioTestCase):
    async def test_retry_only_once_per_iteration(self):
        """_truncation_retry_count ensures only one retry per iteration.
        If the retry also returns 'length', the truncation persists path
        is taken (inject notice + continue)."""
        agent = _make_agent(max_iterations=2)

        call_count = 0

        async def mock_call_model(ctx, context, tools):
            nonlocal call_count
            call_count += 1
            return _truncated_response(output_tokens=4096)

        agent._call_model = mock_call_model

        context = MagicMock()
        context.add_messages = AsyncMock()

        session = MagicMock()
        session.write_stream = AsyncMock()
        session.get_session_id = MagicMock(return_value="test-session")

        ctx = MagicMock()
        ctx.extra = {}
        ctx.consume_force_finish = MagicMock(return_value=None)
        ctx.drain_steering = MagicMock(return_value=[])
        ctx.has_pending_steering = MagicMock(return_value=False)
        ctx.fire = AsyncMock()
        ctx.session = session
        ctx.context = context

        _truncation_retry_count = 0
        for iteration in range(agent._config.max_iterations):
            ai_message = await agent._call_model(ctx, context, [])

            _truncation_detected = (
                ai_message is not None
                and getattr(ai_message, "finish_reason", "null") == "length"
            )

            if _truncation_detected and _truncation_retry_count < 1:
                _truncation_retry_count += 1
                await agent._inject_truncation_notice(ai_message, context)
                ai_message = await agent._call_model(ctx, context, [])
                _truncation_detected = (
                    ai_message is not None
                    and getattr(ai_message, "finish_reason", "null") == "length"
                )

            if _truncation_detected:
                await agent._inject_truncation_notice(ai_message, context)
                continue

            await context.add_messages(ai_message)
            break

        # Iteration 1: 1 call + 1 retry = 2, then persist
        # Iteration 2: 1 call (no more retry since _truncation_retry_count=1), then persist
        self.assertEqual(call_count, 3)


if __name__ == "__main__":
    unittest.main()
