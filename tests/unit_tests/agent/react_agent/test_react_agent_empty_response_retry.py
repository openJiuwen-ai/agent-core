# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Unit tests for empty no-tool response retry in ReActAgent.

When the model returns no assistant text and no tool_calls (including
reasoning-only turns), ReActAgent injects EMPTY_RESPONSE_NOTICE and retries
once inside the same invoke, instead of treating the turn as a final answer.
"""
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

from openjiuwen.core.foundation.llm import AssistantMessage, UserMessage, ToolCall
from openjiuwen.core.foundation.llm.schema.message import UsageMetadata
from openjiuwen.core.session.stream import OutputSchema

from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

from tests.unit_tests.fixtures.mock_llm import MockLLMModel


def _make_agent(max_iterations: int = 5) -> ReActAgent:
    card = AgentCard(
        name="test-empty-response-agent",
        description="test agent for empty-response retry",
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


def _reasoning_only_empty() -> AssistantMessage:
    return AssistantMessage(
        content="",
        tool_calls=None,
        reasoning_content="long reasoning without action",
        finish_reason="stop",
        usage_metadata=UsageMetadata(
            model_name="mock-model",
            input_tokens=100,
            output_tokens=10889,
            total_tokens=10989,
        ),
    )


def _text_answer(content: str = "Done") -> AssistantMessage:
    return AssistantMessage(
        content=content,
        finish_reason="stop",
        usage_metadata=UsageMetadata(
            model_name="mock-model",
            input_tokens=100,
            output_tokens=20,
            total_tokens=120,
        ),
    )


def _tool_call_answer(name: str = "member_complete_task") -> AssistantMessage:
    return AssistantMessage(
        content="",
        tool_calls=[
            ToolCall(
                id="call_1",
                type="function",
                name=name,
                arguments='{"task_id": "task-arch"}',
            )
        ],
        finish_reason="tool_calls",
        usage_metadata=UsageMetadata(
            model_name="mock-model",
            input_tokens=100,
            output_tokens=30,
            total_tokens=130,
        ),
    )


class TestInjectEmptyResponseNotice(unittest.IsolatedAsyncioTestCase):
    async def test_injects_user_notice_only(self):
        agent = _make_agent()
        context = MagicMock()
        context.add_messages = AsyncMock()

        await agent._inject_empty_response_notice(context)

        self.assertEqual(context.add_messages.call_count, 1)
        user_msg = context.add_messages.call_args[0][0]
        self.assertIsInstance(user_msg, UserMessage)
        self.assertIn("[EMPTY_RESPONSE_NOTICE]", user_msg.content)
        self.assertIn("tool", user_msg.content.lower())
        self.assertNotIn("member_complete_task", user_msg.content)


class TestEmptyResponseRetryIntegration(unittest.IsolatedAsyncioTestCase):
    def _make_agent_with_mocks(self, max_iterations: int = 5):
        agent = _make_agent(max_iterations=max_iterations)
        context_engine, context = _mock_context_engine()
        agent.context_engine = context_engine
        return agent, context_engine, context

    def _retry_schemas(self, session) -> list:
        return [
            c[0][0]
            for c in session.write_stream.call_args_list
            if isinstance(c[0][0], OutputSchema) and c[0][0].type == "empty_response_retry"
        ]

    async def test_reasoning_only_then_text_answer(self):
        """Reasoning-only empty turn retries once and accepts the next answer."""
        agent, _, context = self._make_agent_with_mocks(max_iterations=3)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            _reasoning_only_empty(),
            _text_answer("设计规格已写完"),
        ])
        mock_session = _mock_session()

        with patch.object(agent, "_get_llm", return_value=mock_llm):
            result = await agent.invoke(
                {"query": "continue task-arch"},
                session=mock_session,
            )

        self.assertEqual(result["result_type"], "answer")
        self.assertEqual(result["output"], "设计规格已写完")
        self.assertEqual(mock_llm.call_count, 2)
        self.assertEqual(len(self._retry_schemas(mock_session)), 1)

        notice_calls = [
            c for c in context.add_messages.call_args_list
            if c[0] and isinstance(c[0][0], UserMessage)
            and "[EMPTY_RESPONSE_NOTICE]" in (c[0][0].content or "")
        ]
        self.assertEqual(len(notice_calls), 1)

    async def test_reasoning_only_then_tool_call(self):
        """Retry may recover into a tool call instead of plain text."""
        agent, _, _ = self._make_agent_with_mocks(max_iterations=3)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            _reasoning_only_empty(),
            _tool_call_answer("view_task"),
            _text_answer("claimed task listed"),
        ])
        mock_session = _mock_session()

        # Avoid real tool execution: treat tool path as force-finish via rail
        # by mocking _execute_tool_call to return empty and then a final answer
        # on the subsequent model call after tools (simpler: mock execute to
        # leave context and let next model return text).
        async def _fake_execute(ctx, tool_calls, session, context):
            return [{"tool_name": "view_task", "tool_result": "ok"}] * len(tool_calls)

        with patch.object(agent, "_get_llm", return_value=mock_llm), \
                patch.object(agent, "_execute_tool_call", side_effect=_fake_execute), \
                patch.object(agent, "_after_execute_tool_call_for_hitl", return_value=(None, None)), \
                patch.object(agent, "_after_execute_tool_call", return_value=None):
            result = await agent.invoke(
                {"query": "continue task-arch"},
                session=mock_session,
            )

        self.assertEqual(result["result_type"], "answer")
        self.assertEqual(result["output"], "claimed task listed")
        self.assertGreaterEqual(mock_llm.call_count, 2)
        self.assertEqual(len(self._retry_schemas(mock_session)), 1)

    async def test_empty_twice_exits_without_infinite_loop(self):
        """Second empty no-tool turn exits as answer/error; only one retry."""
        agent, _, _ = self._make_agent_with_mocks(max_iterations=5)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            _reasoning_only_empty(),
            _reasoning_only_empty(),
        ])
        mock_session = _mock_session()

        with patch.object(agent, "_get_llm", return_value=mock_llm):
            result = await agent.invoke(
                {"query": "continue"},
                session=mock_session,
            )

        self.assertEqual(result["result_type"], "answer")
        self.assertEqual(mock_llm.call_count, 2)
        self.assertEqual(len(self._retry_schemas(mock_session)), 1)

    async def test_normal_text_answer_does_not_retry(self):
        agent, _, _ = self._make_agent_with_mocks(max_iterations=3)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([_text_answer("hello")])
        mock_session = _mock_session()

        with patch.object(agent, "_get_llm", return_value=mock_llm):
            result = await agent.invoke(
                {"query": "hi"},
                session=mock_session,
            )

        self.assertEqual(result["result_type"], "answer")
        self.assertEqual(result["output"], "hello")
        self.assertEqual(mock_llm.call_count, 1)
        self.assertEqual(len(self._retry_schemas(mock_session)), 0)

    async def test_empty_content_with_tools_does_not_retry(self):
        """Empty content but with tool_calls is normal ReAct, not empty retry."""
        agent, _, _ = self._make_agent_with_mocks(max_iterations=3)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            _tool_call_answer("view_task"),
            _text_answer("ok"),
        ])
        mock_session = _mock_session()

        async def _fake_execute(ctx, tool_calls, session, context):
            return [{"tool_name": "view_task", "tool_result": "ok"}] * len(tool_calls)

        with patch.object(agent, "_get_llm", return_value=mock_llm), \
                patch.object(agent, "_execute_tool_call", side_effect=_fake_execute), \
                patch.object(agent, "_after_execute_tool_call_for_hitl", return_value=(None, None)), \
                patch.object(agent, "_after_execute_tool_call", return_value=None):
            result = await agent.invoke(
                {"query": "work"},
                session=mock_session,
            )

        self.assertEqual(result["result_type"], "answer")
        self.assertEqual(mock_llm.call_count, 2)
        self.assertEqual(len(self._retry_schemas(mock_session)), 0)


if __name__ == "__main__":
    unittest.main()
