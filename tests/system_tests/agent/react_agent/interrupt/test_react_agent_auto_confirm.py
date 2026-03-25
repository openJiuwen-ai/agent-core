# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import pytest

from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.runner import Runner

from tests.system_tests.agent.react_agent.interrupt.test_base import (
    AgentWithToolsConfig,
    create_simple_agent,
    create_agent_with_tools,
    assert_interrupt_result,
    assert_answer_result,
    ReadTool,
    ActionTool,
    confirm_interrupt,
    API_KEY,
    API_BASE,
)


@pytest.mark.asyncio
@pytest.mark.skipif(not API_KEY or not API_BASE, reason="API_KEY and API_BASE required")
async def test_hitl_rail_auto_confirm():
    """auto_confirm feature: confirm once, subsequent same-name tools auto-pass

    Flow: read intercepted -> confirm with auto_confirm=True -> 2nd read auto-passes -> complete
    """
    await Runner.start()
    try:
        agent, _, read_tool, _, trace_rail = await create_simple_agent(
            session_id_prefix="rail_auto_confirm",
            system_prompt="You are an assistant. When the user requests to read a file, you must call the read tool.",
        )

        result1 = await Runner.run_agent(
            agent=agent,
            inputs={"query": "Please read /tmp/test1.txt", "conversation_id": "497"},
        )

        if result1.get("result_type") != "interrupt":
            pytest.skip("LLM did not call tool, skipping auto_confirm test")

        interrupt_ids, _ = assert_interrupt_result(result1, expected_count=1)
        tool_call_id = interrupt_ids[0]

        interactive_input = InteractiveInput()
        interactive_input.update(tool_call_id, {
            "approved": True,
            "feedback": "Confirm, auto-pass subsequently",
            "auto_confirm": True
        })

        result2 = await Runner.run_agent(
            agent=agent,
            inputs={"query": interactive_input, "conversation_id": "497"},
        )
        assert_answer_result(result2)
        assert read_tool.invoke_count == 1, f"Expected read invoke_count=1, got {read_tool.invoke_count}"

        result3 = await Runner.run_agent(
            agent=agent,
            inputs={"query": "Please read /tmp/test2.txt", "conversation_id": "497"},
        )

        assert_answer_result(result3)
        assert read_tool.invoke_count == 2, f"Expected read invoke_count=2, got {read_tool.invoke_count}"
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.skipif(not API_KEY or not API_BASE, reason="API_KEY and API_BASE required")
async def test_hitl_rail_same_tool_multiple_calls():
    """Same tool called multiple times in one iteration, each independently confirmed

    Flow: 3 concurrent action calls intercepted -> confirm first -> confirm second -> confirm third -> complete
    """
    await Runner.start()
    try:
        action_tool = ActionTool("multi_action")
        agent, _, trace_rail = await create_agent_with_tools(
            AgentWithToolsConfig(
                tools=[action_tool],
                session_id_prefix="multi_call",
                system_prompt="You are an assistant. When the user requests to execute multiple operations, "
                              "please call multi_action concurrently multiple times, "
                              "using different action parameters for each call.",
                rail_tool_names=["multi_action"],
                trace_tool_names=["multi_action"],
            )
        )

        result1 = await Runner.run_agent(
            agent=agent,
            inputs={"query": "Please execute action1, action2, and action3 simultaneously", "conversation_id": "497"},
        )

        assert result1.get("result_type") == "interrupt"
        interrupt_ids = result1.get("interrupt_ids", [])
        assert len(interrupt_ids) == 3, f"Expected 3 interrupts, got {len(interrupt_ids)}"

        confirmed_count = 0
        current_result = result1

        while current_result.get("result_type") == "interrupt":
            current_interrupt_ids = current_result.get("interrupt_ids", [])
            if len(current_interrupt_ids) == 0:
                break

            tool_call_id = current_interrupt_ids[0]
            confirmed_count += 1
            current_result = await Runner.run_agent(
                agent=agent,
                inputs={"query": confirm_interrupt(tool_call_id), "conversation_id": "497"},
            )

        assert_answer_result(current_result)
        assert action_tool.invoke_count == confirmed_count, (f"Expected action invoke_count={confirmed_count}, "
                                                             f"got {action_tool.invoke_count}")
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.skipif(not API_KEY or not API_BASE, reason="API_KEY and API_BASE required")
async def test_hitl_rail_confirm_one_auto_pass_others():
    """Confirm one tool with auto_confirm, other same-name tools auto-pass

    Flow: 3 concurrent read calls intercepted -> confirm first with auto_confirm -> others auto-pass -> complete
    """
    await Runner.start()
    try:
        read_tool = ReadTool()
        agent, _, trace_rail = await create_agent_with_tools(
            AgentWithToolsConfig(
                tools=[read_tool],
                session_id_prefix="auto_pass",
                system_prompt="You are an assistant. When the user requests to read multiple files, "
                              "please call the read tool concurrently multiple times, "
                              "using different filepath parameters for each call.",
                rail_tool_names=["read"],
                trace_tool_names=["read"],
            )
        )

        result1 = await Runner.run_agent(
            agent=agent,
            inputs={"query": "Please read /tmp/file1.txt, /tmp/file2.txt, and /tmp/file3.txt simultaneously",
                    "conversation_id": "497"},
        )

        assert result1.get("result_type") == "interrupt"
        interrupt_ids = result1.get("interrupt_ids", [])
        assert len(interrupt_ids) == 3, f"Expected 3 interrupts, got {len(interrupt_ids)}"

        first_tool_call_id = interrupt_ids[0]
        interactive_input = InteractiveInput()
        interactive_input.update(first_tool_call_id, {
            "approved": True,
            "feedback": "Confirm reading file",
            "auto_confirm": True
        })

        result2 = await Runner.run_agent(
            agent=agent,
            inputs={"query": interactive_input, "conversation_id": "497"},
        )

        assert_answer_result(result2)
        assert read_tool.invoke_count == 3, f"Expected read invoke_count=3, got {read_tool.invoke_count}"
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.skipif(not API_KEY or not API_BASE, reason="API_KEY and API_BASE required")
async def test_hitl_rail_clear_session_after_reject():
    """Clear session after reject and auto_confirm, then verify interrupt is triggered again
    """
    await Runner.start()
    try:
        agent, _, read_tool, _, trace_rail = await create_simple_agent(
            system_prompt="You are an assistant. When the user requests to read a file, you must call the read tool.",
        )

        session_id = "497"

        result1 = await Runner.run_agent(
            agent=agent,
            inputs={"query": "Please read /tmp/test1.txt", "conversation_id": session_id},
        )
        interrupt_ids, _ = assert_interrupt_result(result1, expected_count=1)
        tool_call_id = interrupt_ids[0]

        interactive_input = InteractiveInput()
        interactive_input.update(tool_call_id, {
            "approved": True,
            "feedback": "Confirm, auto-pass subsequently",
            "auto_confirm": True
        })

        result2 = await Runner.run_agent(
            agent=agent,
            inputs={"query": interactive_input, "conversation_id": session_id},
        )

        assert_answer_result(result2)
        assert read_tool.invoke_count == 1, f"Expected read invoke_count=1, got {read_tool.invoke_count}"

        result3 = await Runner.run_agent(
            agent=agent,
            inputs={"query": "Please read /tmp/test2.txt", "conversation_id": session_id},
        )

        assert_answer_result(result3)
        assert read_tool.invoke_count == 2, f"Expected read invoke_count=2, got {read_tool.invoke_count}"

        await agent.clear_session(session_id)

        result4 = await Runner.run_agent(
            agent=agent,
            inputs={"query": "Please read /tmp/test3.txt", "conversation_id": session_id},
        )

        assert result4.get(
            "result_type") == "interrupt", (f"Expected interrupt after clearing session, "
                                            f"got {result4.get('result_type')}")
        interrupt_ids = result4.get("interrupt_ids", [])
        assert len(interrupt_ids) == 1, f"Expected 1 interrupt after clearing session, got {len(interrupt_ids)}"
    finally:
        await Runner.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
