# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import pytest

from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.runner import Runner

from tests.system_tests.agent.react_agent.interrupt.test_base import (
    AgentWithToolsConfig,
    create_agent_with_tools,
    assert_interrupt_result,
    assert_answer_result,
    ActionTool,
    confirm_interrupt,
    API_KEY,
    API_BASE,
)


@pytest.mark.asyncio
@pytest.mark.skipif(not API_KEY or not API_BASE, reason="API_KEY and API_BASE required")
async def test_recovery_with_wrong_tool_call_id():
    """Exception: recover with wrong tool_call_id, then correct

    Flow: trigger interrupt -> wrong ID (system stays in interrupt) -> correct ID -> complete
    """
    await Runner.start()
    try:
        action_tool = ActionTool("action")
        agent, _, trace_rail = await create_agent_with_tools(
            AgentWithToolsConfig(
                tools=[action_tool],
                session_id_prefix="wrong_id_test",
                rail_tool_names=["action"],
                trace_tool_names=["action"],
            )
        )

        result1 = await Runner.run_agent(
            agent=agent,
            inputs={"query": "Please execute test operation", "conversation_id": "495"},
        )
        interrupt_ids, _ = assert_interrupt_result(result1, expected_count=1)
        correct_tool_call_id = interrupt_ids[0]
        wrong_tool_call_id = "wrong_id_12345"

        interactive_input_wrong = InteractiveInput()
        interactive_input_wrong.update(wrong_tool_call_id, {"approved": True, "feedback": "Wrong ID"})

        result2 = await Runner.run_agent(
            agent=agent,
            inputs={"query": interactive_input_wrong, "conversation_id": "495"},
        )

        assert result2.get("result_type") == "interrupt"
        remaining_ids = result2.get("interrupt_ids", [])
        assert correct_tool_call_id in remaining_ids, (f"Correct ID {correct_tool_call_id} "
                                                       f"should remain in {remaining_ids}")

        result3 = await Runner.run_agent(
            agent=agent,
            inputs={"query": confirm_interrupt(correct_tool_call_id), "conversation_id": "495"},
        )
        assert_answer_result(result3)
        assert trace_rail.get_execution_count(
            "action") == 1, f"Expected action execution count=1, got {trace_rail.get_execution_count('action')}"
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.skipif(not API_KEY or not API_BASE, reason="API_KEY and API_BASE required")
async def test_empty_interactive_input_recovery():
    """Exception: recover with empty InteractiveInput, then correct

    Flow: trigger interrupt -> empty input (still interrupt) -> confirm -> complete
    """
    await Runner.start()
    try:
        action_tool = ActionTool("action")
        agent, _, trace_rail = await create_agent_with_tools(
            AgentWithToolsConfig(
                tools=[action_tool],
                session_id_prefix="empty_input_test",
                rail_tool_names=["action"],
                trace_tool_names=["action"],
            )
        )

        result1 = await Runner.run_agent(
            agent=agent,
            inputs={"query": "Please execute test operation", "conversation_id": "495"},
        )
        interrupt_ids, _ = assert_interrupt_result(result1, expected_count=1)

        empty_input = InteractiveInput()

        result2 = await Runner.run_agent(
            agent=agent,
            inputs={"query": empty_input, "conversation_id": "495"},
        )

        assert result2.get("result_type") == "interrupt"

        remaining_ids = result2.get("interrupt_ids", [])
        assert len(remaining_ids) == 1, f"Expected 1 remaining interrupt, got {len(remaining_ids)}"

        result3 = await Runner.run_agent(
            agent=agent,
            inputs={"query": confirm_interrupt(remaining_ids[0]), "conversation_id": "495"},
        )
        assert_answer_result(result3)
        assert trace_rail.get_execution_count(
            "action") == 1, f"Expected action execution count=1, got {trace_rail.get_execution_count('action')}"
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.skipif(not API_KEY or not API_BASE, reason="API_KEY and API_BASE required")
async def test_session_switch_recovery():
    """Exception: recover in wrong session, then correct session

    Flow: trigger interrupt in session A -> wrong session B (no execution) -> correct session A -> complete
    """
    await Runner.start()
    try:
        action_tool = ActionTool("action")

        agent1, _, _ = await create_agent_with_tools(
            AgentWithToolsConfig(
                tools=[action_tool],
                session_id_prefix="session_a",
                rail_tool_names=["action"],
            )
        )
        agent2, _, trace_rail2 = await create_agent_with_tools(
            AgentWithToolsConfig(
                tools=[action_tool],
                session_id_prefix="session_b",
                rail_tool_names=["action"],
                trace_tool_names=["action"],
            )
        )

        result1 = await Runner.run_agent(
            agent=agent1,
            inputs={"query": "Please execute test operation", "conversation_id": "495_a"},
        )
        interrupt_ids, _ = assert_interrupt_result(result1, expected_count=1)
        tool_call_id = interrupt_ids[0]

        result2 = await Runner.run_agent(
            agent=agent2,
            inputs={"query": confirm_interrupt(tool_call_id), "conversation_id": "495_b"},
        )

        assert trace_rail2.get_execution_count(
            "action") == 0, (f"Expected action execution count=0 in session B, got "
                             f"{trace_rail2.get_execution_count('action')}")

        result3 = await Runner.run_agent(
            agent=agent1,
            inputs={"query": confirm_interrupt(tool_call_id), "conversation_id": "495_a"},
        )
        assert_answer_result(result3)
    finally:
        await Runner.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
