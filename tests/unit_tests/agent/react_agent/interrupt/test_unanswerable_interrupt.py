# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Rounds that arrive into an interrupt no caller can answer.

A confirmation is raised for a request that came in over a transport with no way
to send one back -- a plain chat relay, say. The prompt is unanswerable there, so
every later message resumes the interrupt, raises it again and ends the round
with neither output nor error. These cover the way out of that.
"""

import os
from unittest.mock import patch

import pytest

from openjiuwen.core.runner import Runner
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.single_agent.interrupt.state import INTERRUPTION_KEY

from tests.unit_tests.agent.react_agent.interrupt.test_base import (
    ActionTool,
    AgentWithToolsConfig,
    assert_answer_result,
    assert_interrupt_result,
    confirm_interrupt,
    create_agent_with_tools,
)
from tests.unit_tests.fixtures.mock_llm import (
    create_text_response,
    create_tool_call_response,
    MockLLMModel,
)


@pytest.mark.asyncio
async def test_plain_message_into_interrupted_session_is_answered():
    """A plain message withdraws the unanswerable interrupt and gets a reply.

    Flow: trigger interrupt -> plain chat message -> answer, tool not executed.
    """
    os.environ.setdefault("LLM_SSL_VERIFY", "false")
    await Runner.start()
    try:
        action_tool = ActionTool("action")
        agent, _, trace_rail = await create_agent_with_tools(
            AgentWithToolsConfig(
                tools=[action_tool],
                session_id_prefix="plain_message_resume",
                rail_tool_names=["action"],
                trace_tool_names=["action"],
            )
        )

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response("action", '{"action": "test"}'),
            create_text_response("The confirmation is still pending; here is what you asked."),
        ])

        with patch("openjiuwen.core.foundation.llm.model.Model.stream", side_effect=mock_llm.stream), \
                patch("openjiuwen.core.foundation.llm.model.Model.invoke", side_effect=mock_llm.invoke):
            first = await Runner.run_agent(
                agent=agent,
                inputs={"query": "Please execute test operation", "conversation_id": "unanswerable_1"},
            )
            assert_interrupt_result(first, expected_count=1)

            second = await Runner.run_agent(
                agent=agent,
                inputs={"query": "what time is it?", "conversation_id": "unanswerable_1"},
            )

        assert_answer_result(second)
        assert second.get("output"), "the round must produce assistant content, not silence"
        assert trace_rail.get_execution_count("action") == 0, (
            "the unconfirmed tool must not run just because the interrupt was withdrawn"
        )
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_withdrawn_interrupt_does_not_capture_later_rounds():
    """The session is usable again: a third message runs a normal round too."""
    os.environ.setdefault("LLM_SSL_VERIFY", "false")
    await Runner.start()
    try:
        action_tool = ActionTool("action")
        agent, session, _ = await create_agent_with_tools(
            AgentWithToolsConfig(
                tools=[action_tool],
                session_id_prefix="withdrawn_not_sticky",
                rail_tool_names=["action"],
            )
        )

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response("action", '{"action": "test"}'),
            create_text_response("First reply"),
            create_text_response("Second reply"),
        ])

        with patch("openjiuwen.core.foundation.llm.model.Model.stream", side_effect=mock_llm.stream), \
                patch("openjiuwen.core.foundation.llm.model.Model.invoke", side_effect=mock_llm.invoke):
            # Rounds share one session object so the stored interrupt state is
            # readable from the test rather than only from inside the agent.
            await Runner.run_agent(
                agent=agent,
                inputs={"query": "Please execute test operation"},
                session=session,
            )
            assert session.get_state(INTERRUPTION_KEY) is not None, (
                "the first round must leave a pending interrupt for the rest to withdraw"
            )
            second = await Runner.run_agent(
                agent=agent,
                inputs={"query": "never mind that"},
                session=session,
            )
            third = await Runner.run_agent(
                agent=agent,
                inputs={"query": "and one more thing"},
                session=session,
            )

        assert_answer_result(second)
        assert_answer_result(third)
        assert session.get_state(INTERRUPTION_KEY) is None, (
            "no interrupt state may survive a withdrawal"
        )
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_structured_answer_still_holds_the_interrupt_open():
    """An answer that misses its request keeps waiting rather than withdrawing.

    A caller sending InteractiveInput can answer interrupts, so an answer that
    resolves nothing -- a stale or mistyped request id -- is a retry, not a sign
    that the prompt is unanswerable.
    """
    os.environ.setdefault("LLM_SSL_VERIFY", "false")
    await Runner.start()
    try:
        action_tool = ActionTool("action")
        agent, _, trace_rail = await create_agent_with_tools(
            AgentWithToolsConfig(
                tools=[action_tool],
                session_id_prefix="structured_answer_holds",
                rail_tool_names=["action"],
                trace_tool_names=["action"],
            )
        )

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response("action", '{"action": "test"}'),
            create_text_response("Action completed"),
        ])

        with patch("openjiuwen.core.foundation.llm.model.Model.stream", side_effect=mock_llm.stream), \
                patch("openjiuwen.core.foundation.llm.model.Model.invoke", side_effect=mock_llm.invoke):
            first = await Runner.run_agent(
                agent=agent,
                inputs={"query": "Please execute test operation", "conversation_id": "unanswerable_3"},
            )
            interrupt_ids, _ = assert_interrupt_result(first, expected_count=1)
            pending_id = interrupt_ids[0]

            stale = InteractiveInput()
            stale.update("stale-request-id", {"approved": True, "feedback": "Confirm"})
            second = await Runner.run_agent(
                agent=agent,
                inputs={"query": stale, "conversation_id": "unanswerable_3"},
            )
            assert second.get("result_type") == "interrupt"
            assert pending_id in second.get("interrupt_ids", [])

            third = await Runner.run_agent(
                agent=agent,
                inputs={"query": confirm_interrupt(pending_id), "conversation_id": "unanswerable_3"},
            )

        assert_answer_result(third)
        assert trace_rail.get_execution_count("action") == 1
    finally:
        await Runner.stop()
