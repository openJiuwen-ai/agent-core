# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Regression tests for empty no-tool turns in ReActAgent.

A model turn that has no assistant text and no tool_calls is not a valid
ReAct answer even when reasoning_content is present. After one same-round
retry with an [EMPTY_RESPONSE] notice, it must end with an explicit error
instead of a silent empty ``answer`` (2026-09-11 team stall: reasoning-only
empty turn left the claimed board task IN_PROGRESS until the relay 300s
stall watchdog killed the stream).
"""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, UserMessage
from openjiuwen.core.foundation.llm.schema.message import UsageMetadata

from openjiuwen.core.single_agent.agents.react_agent import (
    EMPTY_RESPONSE_MAX_RETRIES,
    EMPTY_RESPONSE_MSG,
    ReActAgent,
    ReActAgentConfig,
    SOFT_STOP_FALLBACK_STEERING,
    SOFT_STOP_MAX_CONTINUES,
    SOFT_STOP_NEEDS_CONTINUE_KEY,
    SOFT_STOP_RECOVERY_FAILED_MSG,
)
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

from tests.unit_tests.fixtures.mock_llm import MockLLMModel

EMPTY_RESPONSE_NOTICE = "[EMPTY_RESPONSE]"


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

    async def test_reasoning_only_retries_once_then_succeeds(self):
        """首次 reasoning-only 空帧同轮重试一次；第二次有正文则成功。"""
        agent, _, context = self._make_agent_with_mocks()

        result, mock_llm, _ = await self._invoke(
            agent,
            [_reasoning_only_empty(), _text_answer("recovered")],
        )

        self.assertEqual(result["result_type"], "answer")
        self.assertEqual(result["output"], "recovered")
        self.assertEqual(mock_llm.call_count, EMPTY_RESPONSE_MAX_RETRIES + 1)

        added = [c[0][0] for c in context.add_messages.call_args_list if c[0]]
        notice_idxs = [
            i for i, msg in enumerate(added)
            if isinstance(msg, UserMessage)
            and EMPTY_RESPONSE_NOTICE in (msg.content or "")
        ]
        self.assertTrue(notice_idxs, "EMPTY_RESPONSE notice required before retry")

    async def test_reasoning_only_exhausts_as_error(self):
        """连续空帧耗尽同轮预算后必须显式 error，不得静默空 answer。"""
        agent, _, context = self._make_agent_with_mocks()

        result, mock_llm, _ = await self._invoke(
            agent,
            [_reasoning_only_empty(), _reasoning_only_empty()],
        )

        self.assertEqual(result["result_type"], "error")
        self.assertEqual(result["output"], EMPTY_RESPONSE_MSG)
        self.assertEqual(result["finish_reason"], "stop")
        self.assertTrue(result["reasoning_present"])
        self.assertEqual(mock_llm.call_count, EMPTY_RESPONSE_MAX_RETRIES + 1)

        added = [c[0][0] for c in context.add_messages.call_args_list if c[0]]
        assistant_idxs = [
            i for i, msg in enumerate(added) if isinstance(msg, AssistantMessage)
        ]
        notice_idxs = [
            i for i, msg in enumerate(added)
            if isinstance(msg, UserMessage)
            and EMPTY_RESPONSE_NOTICE in (msg.content or "")
        ]
        self.assertTrue(assistant_idxs, "empty AssistantMessage must be committed")
        self.assertTrue(notice_idxs, "EMPTY_RESPONSE reject notice required")
        self.assertGreater(
            notice_idxs[0],
            assistant_idxs[0],
            "reject notice must follow the empty AssistantMessage",
        )
        self.assertIn("no content", added[notice_idxs[0]].content)
        self.assertIn("tool_calls", added[notice_idxs[0]].content)

    async def test_empty_on_last_iteration_keeps_empty_response_error(self):
        """最后一格 iteration 空帧不得 continue 漂成 max-iterations。

        continue 在无下一轮时会落入 for-else，误报
        ``Max iterations reached without completion``，并丢失
        finish_reason / reasoning_present；此处必须仍走 OA.05000090。
        """
        agent, _, _ = self._make_agent_with_mocks(max_iterations=1)

        result, mock_llm, _ = await self._invoke(agent, [_reasoning_only_empty()])

        self.assertEqual(result["result_type"], "error")
        self.assertEqual(result["output"], EMPTY_RESPONSE_MSG)
        self.assertEqual(result["finish_reason"], "stop")
        self.assertTrue(result["reasoning_present"])
        self.assertEqual(mock_llm.call_count, 1)
        self.assertNotEqual(
            result["output"],
            "Max iterations reached without completion",
        )

    async def test_fully_empty_turn_is_error(self):
        """content 与 reasoning 均空：耗尽同轮重试后仍为 error。"""
        agent, _, _ = self._make_agent_with_mocks()

        result, mock_llm, _ = await self._invoke(
            agent,
            [_fully_empty(), _fully_empty()],
        )

        self.assertEqual(result["result_type"], "error")
        self.assertEqual(result["output"], EMPTY_RESPONSE_MSG)
        self.assertFalse(result["reasoning_present"])
        self.assertEqual(mock_llm.call_count, EMPTY_RESPONSE_MAX_RETRIES + 1)

    async def test_whitespace_only_content_is_error(self):
        """纯空白正文等价于空正文；耗尽同轮重试后 error。"""
        blank = AssistantMessage(
            content="   \n\t",
            finish_reason="stop",
            reasoning_content="thinking",
            usage_metadata=_usage(),
        )
        agent, _, _ = self._make_agent_with_mocks()

        result, mock_llm, _ = await self._invoke(agent, [blank, blank])

        self.assertEqual(result["result_type"], "error")
        self.assertTrue(result["reasoning_present"])
        self.assertEqual(mock_llm.call_count, EMPTY_RESPONSE_MAX_RETRIES + 1)

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

    async def test_soft_stop_empty_continues_once_then_succeeds(self):
        """RAS-style soft-stop empty partial: continue once, then accept answer."""
        agent, _, context = self._make_agent_with_mocks(max_iterations=5)
        calls = {"n": 0}

        async def _fake_call_model(ctx, _context, _tools):
            calls["n"] += 1
            if calls["n"] == 1:
                ctx.extra[SOFT_STOP_NEEDS_CONTINUE_KEY] = True
                return _reasoning_only_empty()
            return _text_answer("recovered after soft-stop")

        session = _mock_session()
        with patch.object(agent, "_call_model", side_effect=_fake_call_model):
            result = await agent.invoke({"query": "work"}, session=session)

        self.assertEqual(result["result_type"], "answer")
        self.assertEqual(result["output"], "recovered after soft-stop")
        self.assertEqual(calls["n"], 2)
        # Fallback steering injected when queue has no pending messages.
        added = [c[0][0] for c in context.add_messages.call_args_list if c[0]]
        steering = [
            m for m in added
            if isinstance(m, UserMessage)
            and "[STEERING]" in (m.content or "")
            and SOFT_STOP_FALLBACK_STEERING[:40] in (m.content or "")
        ]
        self.assertTrue(steering, "fallback steering required when queue empty")

    async def test_soft_stop_empty_exhausts_as_explicit_error(self):
        """Second soft-stop empty after N=1 continue must hard-fail (no silent success)."""
        agent, _, _ = self._make_agent_with_mocks(max_iterations=5)
        calls = {"n": 0}

        async def _fake_call_model(ctx, _context, _tools):
            calls["n"] += 1
            ctx.extra[SOFT_STOP_NEEDS_CONTINUE_KEY] = True
            return _reasoning_only_empty()

        session = _mock_session()
        with patch.object(agent, "_call_model", side_effect=_fake_call_model):
            result = await agent.invoke({"query": "work"}, session=session)

        self.assertEqual(result["result_type"], "error")
        self.assertEqual(result["output"], SOFT_STOP_RECOVERY_FAILED_MSG)
        self.assertTrue(result.get("soft_stop_recovery_exhausted"))
        self.assertNotEqual(result["output"], EMPTY_RESPONSE_MSG)
        # initial empty + one continue empty → stop (N=SOFT_STOP_MAX_CONTINUES)
        self.assertEqual(calls["n"], SOFT_STOP_MAX_CONTINUES + 1)

    async def test_soft_stop_uses_pending_steering_without_duplicate_fallback(self):
        """When steering is already queued, continue without injecting fallback text."""
        agent, _, context = self._make_agent_with_mocks(max_iterations=5)
        calls = {"n": 0}

        async def _fake_call_model(ctx, _context, _tools):
            calls["n"] += 1
            if calls["n"] == 1:
                ctx.extra[SOFT_STOP_NEEDS_CONTINUE_KEY] = True
                ctx.bind_steering_queue(__import__("asyncio").Queue())
                ctx.push_steering("already-queued recovery steer")
                return _reasoning_only_empty()
            return _text_answer("ok")

        session = _mock_session()
        with patch.object(agent, "_call_model", side_effect=_fake_call_model):
            result = await agent.invoke({"query": "work"}, session=session)

        self.assertEqual(result["result_type"], "answer")
        self.assertEqual(calls["n"], 2)
        added = [c[0][0] for c in context.add_messages.call_args_list if c[0]]
        fallback = [
            m for m in added
            if isinstance(m, UserMessage)
            and SOFT_STOP_FALLBACK_STEERING[:40] in (m.content or "")
        ]
        self.assertFalse(fallback, "must not duplicate fallback when queue has steering")

    async def test_empty_retry_budget_independent_of_soft_stop(self):
        """普通空响应重试预算与 soft-stop 续跑互相独立。"""
        agent, _, _ = self._make_agent_with_mocks(max_iterations=8)
        calls = {"n": 0}

        async def _fake_call_model(ctx, _context, _tools):
            calls["n"] += 1
            # 1st: soft-stop empty → soft-stop continue
            # 2nd: ordinary empty → EMPTY_RESPONSE retry
            # 3rd: text answer
            if calls["n"] == 1:
                ctx.extra[SOFT_STOP_NEEDS_CONTINUE_KEY] = True
                return _reasoning_only_empty()
            if calls["n"] == 2:
                return _reasoning_only_empty()
            return _text_answer("ok after both budgets")

        session = _mock_session()
        with patch.object(agent, "_call_model", side_effect=_fake_call_model):
            result = await agent.invoke({"query": "work"}, session=session)

        self.assertEqual(result["result_type"], "answer")
        self.assertEqual(result["output"], "ok after both budgets")
        self.assertEqual(calls["n"], 3)


if __name__ == "__main__":
    unittest.main()
