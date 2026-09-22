# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Regression tests for empty no-tool turns in ReActAgent.

A model turn that has no assistant text and no tool_calls is not a valid
ReAct answer even when reasoning_content is present. It must end the round
with an explicit error instead of a silent empty ``answer`` (2026-09-11 team
stall: reasoning-only empty turn left the claimed board task IN_PROGRESS
until the relay 300s stall watchdog killed the stream).
"""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall
from openjiuwen.core.foundation.llm.schema.message import UsageMetadata

from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

from tests.unit_tests.fixtures.mock_llm import MockLLMModel

EMPTY_RESPONSE_MSG = "模型未返回有效内容（空响应），请重试或检查上下文。"


def _make_agent(max_iterations: int = 5) -> ReActAgent:
    card = AgentCard(
        name="test-empty-turn-agent",
        description="test agent for empty no-tool turns",
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


def _usage(output_tokens: int = 100) -> UsageMetadata:
    return UsageMetadata(
        model_name="mock-model",
        input_tokens=100,
        output_tokens=output_tokens,
        total_tokens=100 + output_tokens,
    )


def _reasoning_only_empty() -> AssistantMessage:
    """事故形态：content 空、tool_calls 空、reasoning_content 非空。"""
    return AssistantMessage(
        content="",
        tool_calls=None,
        reasoning_content="long reasoning without any actionable output",
        finish_reason="stop",
        usage_metadata=_usage(output_tokens=10889),
    )


def _fully_empty() -> AssistantMessage:
    return AssistantMessage(
        content="",
        tool_calls=None,
        reasoning_content=None,
        finish_reason="stop",
        usage_metadata=_usage(),
    )


def _text_answer(content: str = "Done") -> AssistantMessage:
    return AssistantMessage(
        content=content,
        finish_reason="stop",
        usage_metadata=_usage(output_tokens=20),
    )


def _length_empty() -> AssistantMessage:
    return AssistantMessage(
        content="",
        finish_reason="length",
        usage_metadata=_usage(output_tokens=4096),
    )


def _tool_call_answer(name: str = "view_task") -> AssistantMessage:
    return AssistantMessage(
        content="",
        tool_calls=[
            ToolCall(
                id="call_1",
                type="function",
                name=name,
                arguments="{}",
            )
        ],
        finish_reason="tool_calls",
        usage_metadata=_usage(output_tokens=30),
    )


class TestEmptyNoToolTurn(unittest.IsolatedAsyncioTestCase):
    def _make_agent_with_mocks(self, max_iterations: int = 5):
        agent = _make_agent(max_iterations=max_iterations)
        context_engine, context = _mock_context_engine()
        agent.context_engine = context_engine
        return agent, context_engine, context

    async def _invoke(self, agent, responses):
        mock_llm = MockLLMModel()
        mock_llm.set_responses(responses)
        session = _mock_session()
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            result = await agent.invoke({"query": "continue"}, session=session)
        return result, mock_llm, session

    async def test_reasoning_only_turn_is_error(self):
        """核心断言：仅 reasoning 的空帧必须显式 error，不得静默空 answer。"""
        agent, _, _ = self._make_agent_with_mocks()

        result, mock_llm, _ = await self._invoke(agent, [_reasoning_only_empty()])

        self.assertEqual(result["result_type"], "error")
        self.assertEqual(result["output"], EMPTY_RESPONSE_MSG)
        self.assertEqual(result["finish_reason"], "stop")
        self.assertTrue(result["reasoning_present"])
        # 当前分支不做空回复同轮重试：首次空帧即收口为 error。
        self.assertEqual(mock_llm.call_count, 1)

    async def test_fully_empty_turn_is_error(self):
        """content 与 reasoning 均空：保持原有 error 行为，仅诊断字段不同。"""
        agent, _, _ = self._make_agent_with_mocks()

        result, _, _ = await self._invoke(agent, [_fully_empty()])

        self.assertEqual(result["result_type"], "error")
        self.assertEqual(result["output"], EMPTY_RESPONSE_MSG)
        self.assertFalse(result["reasoning_present"])

    async def test_whitespace_only_content_is_error(self):
        """纯空白正文等价于空正文。"""
        agent, _, _ = self._make_agent_with_mocks()

        result, _, _ = await self._invoke(agent, [
            AssistantMessage(content="   \n\t", finish_reason="stop",
                             reasoning_content="thinking", usage_metadata=_usage()),
        ])

        self.assertEqual(result["result_type"], "error")
        self.assertTrue(result["reasoning_present"])

    async def test_normal_text_answer_unaffected(self):
        agent, _, _ = self._make_agent_with_mocks()

        result, mock_llm, _ = await self._invoke(agent, [_text_answer("hello")])

        self.assertEqual(result["result_type"], "answer")
        self.assertEqual(result["output"], "hello")
        self.assertNotIn("reasoning_present", result)
        self.assertEqual(mock_llm.call_count, 1)

    async def test_empty_content_with_tool_calls_is_not_error(self):
        """有 tool_calls 时 content 为空是正常 ReAct 步骤，不得判为空回复错误。"""
        agent, _, _ = self._make_agent_with_mocks(max_iterations=3)

        async def _fake_execute(ctx, tool_calls, session, context):
            return [{"tool_name": "view_task", "tool_result": "ok"}] * len(tool_calls)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([_tool_call_answer(), _text_answer("listed")])
        session = _mock_session()

        with patch.object(agent, "_get_llm", return_value=mock_llm), \
                patch.object(agent, "_execute_tool_call", side_effect=_fake_execute), \
                patch.object(agent, "_after_execute_tool_call_for_hitl",
                             return_value=(None, None)), \
                patch.object(agent, "_after_execute_tool_call", return_value=None):
            result = await agent.invoke({"query": "work"}, session=session)

        self.assertEqual(result["result_type"], "answer")
        self.assertEqual(result["output"], "listed")
        self.assertNotEqual(result["output"], EMPTY_RESPONSE_MSG)

    async def test_length_turn_uses_truncation_retry_not_empty_error(self):
        """finish_reason=length 必须走截断续跑，不被空回复判定吞掉。"""
        agent, _, _ = self._make_agent_with_mocks(max_iterations=3)

        result, mock_llm, session = await self._invoke(
            agent,
            [_length_empty(), _text_answer("continued after retry")],
        )

        self.assertEqual(result["result_type"], "answer")
        self.assertEqual(result["output"], "continued after retry")
        self.assertEqual(mock_llm.call_count, 2)
        truncation_schemas = [
            c[0][0]
            for c in session.write_stream.call_args_list
            if getattr(c[0][0], "type", None) == "truncation_retry"
        ]
        self.assertTrue(truncation_schemas)


if __name__ == "__main__":
    unittest.main()
