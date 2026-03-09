# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for ReActAgent interruption/resume logic.

Covers:
- _is_interrupted: WorkflowOutput.INPUT_REQUIRED and list[__interaction__] formats
- _after_execute_tool_call: builds InterruptionState on first interrupted result
- session state management: save/load/clear
- invoke() interrupt path and resume path (including multi-pending)
"""
import os
import unittest
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch, MagicMock

import pytest

from openjiuwen.core.foundation.llm import AssistantMessage, UsageMetadata, ToolCall, ToolMessage
from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.runner import Runner

from tests.unit_tests.fixtures.mock_llm import MockLLMModel, create_text_response, create_tool_call_response


@dataclass
class _FakeInteractionItem:
    type: str = "__interaction__"
    payload: Any = None


def _make_agent(agent_id: str = "test_interrupt_agent") -> ReActAgent:
    card = AgentCard(id=agent_id)
    agent = ReActAgent(card=card)
    config = ReActAgentConfig()
    config.configure_model_client(
        provider="OpenAI",
        api_key="sk-fake",
        api_base="https://api.openai.com/v1",
        model_name="gpt-3.5-turbo",
        verify_ssl=False,
    )
    config.configure_prompt_template([{"role": "system", "content": "You are a helpful assistant."}])
    agent.configure(config)
    return agent


def _tool_msg(tool_call_id: str = "c1") -> ToolMessage:
    return ToolMessage(tool_call_id=tool_call_id, content="tool result")


# ---------------------------------------------------------------------------
# _is_interrupted
# ---------------------------------------------------------------------------

class TestIsInterrupted(unittest.TestCase):

    def setUp(self):
        self.agent = _make_agent("agent_is_interrupted")

    def test_detects_workflow_input_required(self):
        from openjiuwen.core.workflow import WorkflowOutput, WorkflowExecutionState
        wf = WorkflowOutput(result=None, state=WorkflowExecutionState.INPUT_REQUIRED)
        self.assertTrue(self.agent._is_interrupted(wf))
        wf_done = WorkflowOutput(result=None, state=WorkflowExecutionState.COMPLETED)
        self.assertFalse(self.agent._is_interrupted(wf_done))

    def test_detects_list_with_interaction_item(self):
        self.assertTrue(self.agent._is_interrupted([_FakeInteractionItem()]))
        self.assertFalse(self.agent._is_interrupted([MagicMock(type="normal")]))
        self.assertFalse(self.agent._is_interrupted("plain string"))


# ---------------------------------------------------------------------------
# _after_execute_tool_call
# ---------------------------------------------------------------------------

class TestAfterExecuteToolCall(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        os.environ.setdefault("LLM_SSL_VERIFY", "false")
        await Runner.start()
        self.agent = _make_agent("agent_after_exec")

    async def asyncTearDown(self):
        await Runner.stop()

    @pytest.mark.asyncio
    async def test_no_interrupt_returns_none(self):
        ai_msg = AssistantMessage(content="", tool_calls=[
            ToolCall(id="c1", type="function", name="tool_a", arguments='{}'),
        ])
        state = self.agent._after_execute_tool_call(
            [("plain result", _tool_msg("c1"))], ai_msg.tool_calls, ai_msg, iteration=0
        )
        self.assertIsNone(state)

    @pytest.mark.asyncio
    async def test_builds_state_for_first_interrupted_tool(self):
        from openjiuwen.core.workflow import WorkflowOutput, WorkflowExecutionState
        ai_msg = AssistantMessage(content="", tool_calls=[
            ToolCall(id="c1", type="function", name="tool_a", arguments='{}'),
            ToolCall(id="c2", type="function", name="tool_b", arguments='{}'),
        ])
        interrupted = WorkflowOutput(result=None, state=WorkflowExecutionState.INPUT_REQUIRED)
        # c2 is interrupted; pending_workflow_id should be "tool_b" (fallback = tool_call.name)
        state = self.agent._after_execute_tool_call(
            [("ok", _tool_msg("c1")), (interrupted, _tool_msg("c2"))],
            ai_msg.tool_calls, ai_msg, iteration=1,
        )
        self.assertIsNotNone(state)
        self.assertEqual(state.iteration, 1)
        self.assertIn("tool_b", state.interrupted_workflows)
        self.assertEqual(state.pending_workflow_id, "tool_b")
        self.assertEqual(state.interrupted_workflows["tool_b"].tool_call.id, "c2")


# ---------------------------------------------------------------------------
# Session state management
# ---------------------------------------------------------------------------

class TestSessionStateManagement(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        os.environ.setdefault("LLM_SSL_VERIFY", "false")
        await Runner.start()
        self.agent = _make_agent("agent_session_state")

    async def asyncTearDown(self):
        await Runner.stop()

    @pytest.mark.asyncio
    async def test_save_load_clear_cycle(self):
        """save → load returns state; clear → load returns None; None session is no-op."""
        session = create_agent_session(
            session_id="sess_state_001",
            card=AgentCard(id="agent_session_state"),
        )
        await session.pre_run(inputs={"query": "test"})

        fake_state = MagicMock()
        fake_state.ai_message = AssistantMessage(content="test")

        self.agent._save_interruption_state(fake_state, session)
        loaded = self.agent._load_interruption_state(session)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.ai_message, fake_state.ai_message)

        self.agent._clear_interruption_state(session)
        self.assertIsNone(self.agent._load_interruption_state(session))

        # None session must be a no-op
        self.agent._save_interruption_state(fake_state, None)
        self.assertIsNone(self.agent._load_interruption_state(None))


# ---------------------------------------------------------------------------
# invoke() interrupt / resume
# ---------------------------------------------------------------------------

class TestInvokeInterruptResume(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        os.environ.setdefault("LLM_SSL_VERIFY", "false")
        await Runner.start()

    async def asyncTearDown(self):
        await Runner.stop()

    @pytest.mark.asyncio
    async def test_invoke_interrupt_then_resume(self):
        """First invoke interrupts; second invoke resumes and returns answer."""
        from openjiuwen.core.workflow import WorkflowOutput, WorkflowExecutionState

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response("my_workflow", '{}', "c1"),
            create_text_response("Resume complete!"),
        ])
        interrupted_output = WorkflowOutput(result=None, state=WorkflowExecutionState.INPUT_REQUIRED)
        call_count = {"n": 0}

        async def fake_execute(ctx, tool_call, session):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return [(interrupted_output, _tool_msg("c1"))]
            return [("workflow completed", _tool_msg("c1"))]

        with patch("openjiuwen.core.foundation.llm.model.Model.stream", side_effect=mock_llm.stream), \
             patch("openjiuwen.core.foundation.llm.model.Model.invoke", side_effect=mock_llm.invoke):
            agent = _make_agent("agent_invoke_resume")
            agent.ability_manager.execute = fake_execute
            session = create_agent_session(
                session_id="sess_resume_001",
                card=AgentCard(id="agent_invoke_resume"),
            )
            await session.pre_run(inputs={"query": "start"})

            result1 = await agent.invoke({"query": "start"}, session=session)
            self.assertEqual(result1.get("result_type"), "interrupt", result1)

            result2 = await agent.invoke({"query": "user feedback"}, session=session)
            self.assertEqual(result2.get("result_type"), "answer", result2)
            self.assertIn("Resume complete!", result2.get("output", ""))

    @pytest.mark.asyncio
    async def test_multi_pending_collects_feedback_one_by_one(self):
        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            AssistantMessage(
                content="",
                tool_calls=[
                    ToolCall(id="c1", type="function", name="wf_a", arguments='{}'),
                    ToolCall(id="c2", type="function", name="wf_b", arguments='{}'),
                ],
                usage_metadata=UsageMetadata(model_name="mock", finish_reason="tool_calls"),
            ),
            create_text_response("Both workflows done!"),
        ])

        from openjiuwen.core.workflow import WorkflowOutput, WorkflowExecutionState
        interrupted_output = WorkflowOutput(result=None, state=WorkflowExecutionState.INPUT_REQUIRED)
        execute_calls = []

        async def fake_execute(ctx, tool_call, session):
            execute_calls.append([tc.id for tc in tool_call])
            if len(execute_calls) == 1:
                return [(interrupted_output, _tool_msg("c1")), (interrupted_output, _tool_msg("c2"))]
            return [("wf_a done", _tool_msg("c1")), ("wf_b done", _tool_msg("c2"))]

        with patch("openjiuwen.core.foundation.llm.model.Model.stream", side_effect=mock_llm.stream), \
             patch("openjiuwen.core.foundation.llm.model.Model.invoke", side_effect=mock_llm.invoke):
            agent = _make_agent("agent_multi_pending")
            agent.ability_manager.execute = fake_execute
            session = create_agent_session(
                session_id="sess_multi_001",
                card=AgentCard(id="agent_multi_pending"),
            )
            await session.pre_run(inputs={"query": "start"})

            result1 = await agent.invoke({"query": "start"}, session=session)
            self.assertEqual(result1.get("result_type"), "interrupt", result1)

            result2 = await agent.invoke({"query": "feedback for c1"}, session=session)
            self.assertEqual(result2.get("result_type"), "interrupt", result2)

            result3 = await agent.invoke({"query": "feedback for c2"}, session=session)
            self.assertEqual(result3.get("result_type"), "answer", result3)
            self.assertIn("Both workflows done!", result3.get("output", ""))

            self.assertEqual(len(execute_calls), 2)
            self.assertIn("c1", execute_calls[1])
            self.assertIn("c2", execute_calls[1])


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
