# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import pytest

from openjiuwen.core.runner import Runner
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from tests.system_tests.agent.react_agent.interrupt.test_base import (
    NestedAgentConfig,
    ReadTool,
    assert_interrupt_result,
    assert_answer_result,
    get_tool_name_from_state,
    confirm_interrupt,
    create_nested_agent,
    API_KEY,
    API_BASE,
)


@pytest.mark.asyncio
@pytest.mark.skipif(not API_KEY or not API_BASE, reason="API_KEY and API_BASE required")
async def test_3layer_agent_interrupt():
    """3-layer agent nested interrupt test - single read

    Flow: MainAgent -> SubAgent1 -> SubAgent2 -> read (interrupt) -> confirm -> complete

    Structure:
        main_agent
          └── sub_agent_1
                └── sub_agent_2
                      └── read (call_xxx) ← interrupt

    Verify:
    1. Interrupt bubbles from innermost to outermost agent
    2. interrupt_ids contains innermost tool_call_id
    3. Recovery executes correctly
    """
    await Runner.start()
    try:
        read_tool = ReadTool()
        Runner.resource_mgr.add_tool(read_tool)

        sub_agent_2 = await create_nested_agent(
            NestedAgentConfig(
                agent_id="sub_agent_2",
                agent_name="sub_agent_2",
                system_prompt="You are innermost agent. Call read tool when user requests file read.",
                tools=[read_tool],
                rail_tool_names=["read"],
            )
        )
        sub_agent_2_card = AgentCard(
            id="sub_agent_2",
            name="sub_agent_2",
            description="Innermost agent for file read tasks",
            input_params={
                "type": "object",
                "properties": {
                    "query": {"description": "Task description", "type": "string"},
                },
                "required": ["query"],
            },
        )
        Runner.resource_mgr.add_agent(sub_agent_2_card, agent=lambda: sub_agent_2)

        sub_agent_1 = await create_nested_agent(
            NestedAgentConfig(
                agent_id="sub_agent_1",
                agent_name="sub_agent_1",
                system_prompt="You are middle agent. Call sub_agent_2 tool for file read tasks.",
                sub_agent_cards=[sub_agent_2_card],
            )
        )
        sub_agent_1_card = AgentCard(
            id="sub_agent_1",
            name="sub_agent_1",
            description="Middle agent coordinating sub-tasks",
            input_params={
                "type": "object",
                "properties": {
                    "query": {"description": "Task description", "type": "string"},
                },
                "required": ["query"],
            },
        )
        Runner.resource_mgr.add_agent(sub_agent_1_card, agent=lambda: sub_agent_1)

        main_agent = await create_nested_agent(
            NestedAgentConfig(
                agent_id="main_agent",
                agent_name="main_agent",
                system_prompt="You are main agent. Call sub_agent_1 tool for tasks.",
                sub_agent_cards=[sub_agent_1_card],
            )
        )

        result1 = await Runner.run_agent(
            agent=main_agent,
            inputs={"query": "Please read file /tmp/test.txt", "conversation_id": "494"},
        )

        interrupt_ids, state_list = assert_interrupt_result(result1, expected_count=1)
        inner_tool_call_id = interrupt_ids[0]
        tool_name = get_tool_name_from_state(state_list[0])
        assert tool_name == "read", f"Expected tool_name 'read', got '{tool_name}'"

        result2 = await Runner.run_agent(
            agent=main_agent,
            inputs={"query": confirm_interrupt(inner_tool_call_id), "conversation_id": "494"},
        )

        assert_answer_result(result2)
        assert read_tool.invoke_count == 1, f"Expected read invoke_count=1, got {read_tool.invoke_count}"

    finally:
        Runner.resource_mgr.remove_agent(agent_id="sub_agent_2")
        Runner.resource_mgr.remove_agent(agent_id="sub_agent_1")
        Runner.resource_mgr.remove_agent(agent_id="main_agent")
        Runner.resource_mgr.remove_tool(tool_id=read_tool.card.id)
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.skipif(not API_KEY or not API_BASE, reason="API_KEY and API_BASE required")
async def test_3layer_agent_parallel_interrupt():
    """3-layer agent nested interrupt - SubAgent2 internally makes 2 concurrent read calls

    Flow: SubAgent2 internally calls read twice concurrently -> 2 interrupts -> confirm one -> confirm other -> complete

    Structure:
        main_agent
          └── sub_agent_1_parallel (call_xxx)
                └── sub_agent_2_parallel (call_yyy)
                      ├── read (call_aaa) ← interrupt
                      └── read (call_bbb) ← interrupt

    Resume flow:
        main_agent resume -> sub_agent_1_parallel resume -> sub_agent_2_parallel resume
          -> both reads resume concurrently

    Verify:
    1. 2 concurrent interrupts from SubAgent2's internal tool calls bubble up
    2. Both interrupt IDs are distinct innermost tool_call_ids
    3. Recovery executes both reads correctly
    """
    await Runner.start()
    try:
        read_tool = ReadTool()
        Runner.resource_mgr.add_tool(read_tool)

        sub_agent_2 = await create_nested_agent(
            NestedAgentConfig(
                agent_id="sub_agent_2_parallel",
                agent_name="sub_agent_2_parallel",
                system_prompt="You are innermost agent. When asked to read multiple files, "
                              "call read tool concurrently for each file in a single response.",
                tools=[read_tool],
                rail_tool_names=["read"],
            )
        )
        sub_agent_2_card = AgentCard(
            id="sub_agent_2_parallel",
            name="sub_agent_2_parallel",
            description="Innermost agent for file read tasks",
            input_params={
                "type": "object",
                "properties": {
                    "query": {"description": "Task description", "type": "string"},
                },
                "required": ["query"],
            },
        )
        Runner.resource_mgr.add_agent(sub_agent_2_card, agent=lambda: sub_agent_2)

        sub_agent_1 = await create_nested_agent(
            NestedAgentConfig(
                agent_id="sub_agent_1_parallel",
                agent_name="sub_agent_1_parallel",
                system_prompt="You are middle agent. Call sub_agent_2_parallel tool once for file read tasks.",
                sub_agent_cards=[sub_agent_2_card],
            )
        )
        sub_agent_1_card = AgentCard(
            id="sub_agent_1_parallel",
            name="sub_agent_1_parallel",
            description="Middle agent coordinating sub-tasks",
            input_params={
                "type": "object",
                "properties": {
                    "query": {"description": "Task description", "type": "string"},
                },
                "required": ["query"],
            },
        )
        Runner.resource_mgr.add_agent(sub_agent_1_card, agent=lambda: sub_agent_1)

        main_agent = await create_nested_agent(
            NestedAgentConfig(
                agent_id="main_agent_parallel",
                agent_name="main_agent_parallel",
                system_prompt="You are main agent. Call sub_agent_1_parallel tool for tasks.",
                sub_agent_cards=[sub_agent_1_card],
            )
        )

        result1 = await Runner.run_agent(
            agent=main_agent,
            inputs={"query": "Please read files /tmp/a.txt and /tmp/b.txt", "conversation_id": "494"},
        )

        interrupt_ids, state_list = assert_interrupt_result(result1, expected_count=2)
        assert len(interrupt_ids) == 2, f"Expected 2 interrupts, got {len(interrupt_ids)}"

        for state in state_list:
            tool_name = get_tool_name_from_state(state)
            assert tool_name == "read", f"Expected tool_name 'read', got '{tool_name}'"

        result2 = await Runner.run_agent(
            agent=main_agent,
            inputs={"query": confirm_interrupt(interrupt_ids[0]), "conversation_id": "494"},
        )

        assert result2.get("result_type") == "interrupt"
        remaining_ids = result2.get("interrupt_ids", [])
        assert len(remaining_ids) == 1, f"Expected 1 remaining interrupt, got {len(remaining_ids)}"

        result3 = await Runner.run_agent(
            agent=main_agent,
            inputs={"query": confirm_interrupt(remaining_ids[0]), "conversation_id": "494"},
        )

        assert_answer_result(result3)
        assert read_tool.invoke_count == 2, f"Expected read invoke_count=2, got {read_tool.invoke_count}"

    finally:
        Runner.resource_mgr.remove_agent(agent_id="sub_agent_2_parallel")
        Runner.resource_mgr.remove_agent(agent_id="sub_agent_1_parallel")
        Runner.resource_mgr.remove_agent(agent_id="main_agent_parallel")
        Runner.resource_mgr.remove_tool(tool_id=read_tool.card.id)
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.skipif(not API_KEY or not API_BASE, reason="API_KEY and API_BASE required")
async def test_3layer_agent_auto_confirm_clear_session():
    """3-layer agent nested interrupt - auto_confirm and clear session

    Flow: MainAgent -> SubAgent1 -> SubAgent2 -> read (interrupt) -> confirm with auto_confirm -> 
          2nd read auto-passes -> clear session -> 3rd read triggers interrupt again

    Structure:
        main_agent
          └── sub_agent_1
                └── sub_agent_2
                      └── read (call_xxx) ← interrupt

    Verify:
    1. Interrupt bubbles from innermost to outermost agent
    2. interrupt_ids contains innermost tool_call_id
    3. After confirming with auto_confirm, 2nd read auto-passes
    4. After clearing session, 3rd read triggers interrupt again
    """
    await Runner.start()
    try:
        read_tool = ReadTool()
        Runner.resource_mgr.add_tool(read_tool)

        sub_agent_2 = await create_nested_agent(
            NestedAgentConfig(
                agent_id="sub_agent_2",
                agent_name="sub_agent_2",
                system_prompt="You are innermost agent. Call read tool when user requests file read.",
                tools=[read_tool],
                rail_tool_names=["read"],
            )
        )
        sub_agent_2_card = AgentCard(
            id="sub_agent_2",
            name="sub_agent_2",
            description="Innermost agent for file read tasks",
            input_params={
                "type": "object",
                "properties": {
                    "query": {"description": "Task description", "type": "string"},
                },
                "required": ["query"],
            },
        )
        Runner.resource_mgr.add_agent(sub_agent_2_card, agent=lambda: sub_agent_2)

        sub_agent_1 = await create_nested_agent(
            NestedAgentConfig(
                agent_id="sub_agent_1",
                agent_name="sub_agent_1",
                system_prompt="You are middle agent. Call sub_agent_2 tool for file read tasks.",
                sub_agent_cards=[sub_agent_2_card],
            )
        )
        sub_agent_1_card = AgentCard(
            id="sub_agent_1",
            name="sub_agent_1",
            description="Middle agent coordinating sub-tasks",
            input_params={
                "type": "object",
                "properties": {
                    "query": {"description": "Task description", "type": "string"},
                },
                "required": ["query"],
            },
        )
        Runner.resource_mgr.add_agent(sub_agent_1_card, agent=lambda: sub_agent_1)

        main_agent = await create_nested_agent(
            NestedAgentConfig(
                agent_id="main_agent",
                agent_name="main_agent",
                system_prompt="You are main agent. Call sub_agent_1 tool for tasks.",
                sub_agent_cards=[sub_agent_1_card],
            )
        )

        session_id = "494"

        result1 = await Runner.run_agent(
            agent=main_agent,
            inputs={"query": "Please read file /tmp/test1.txt", "conversation_id": session_id},
        )

        interrupt_ids, state_list = assert_interrupt_result(result1, expected_count=1)
        inner_tool_call_id = interrupt_ids[0]
        tool_name = get_tool_name_from_state(state_list[0])
        assert tool_name == "read", f"Expected tool_name 'read', got '{tool_name}'"

        interactive_input = InteractiveInput()
        interactive_input.update(inner_tool_call_id, {
            "approved": True,
            "feedback": "Confirm, auto-pass subsequently",
            "auto_confirm": True
        })

        result2 = await Runner.run_agent(
            agent=main_agent,
            inputs={"query": interactive_input, "conversation_id": session_id},
        )

        assert_answer_result(result2)
        assert read_tool.invoke_count == 1, f"Expected read invoke_count=1, got {read_tool.invoke_count}"

        result3 = await Runner.run_agent(
            agent=main_agent,
            inputs={"query": "Please read file /tmp/test2.txt", "conversation_id": session_id},
        )

        assert_answer_result(result3)
        assert read_tool.invoke_count == 2, f"Expected read invoke_count=2, got {read_tool.invoke_count}"

        await main_agent.clear_session(session_id)

        result4 = await Runner.run_agent(
            agent=main_agent,
            inputs={"query": "Please read file /tmp/test3.txt", "conversation_id": session_id},
        )

        assert result4.get(
            "result_type") == "interrupt", (f"Expected interrupt after clearing session, "
                                            f"got {result4.get('result_type')}")
        interrupt_ids = result4.get("interrupt_ids", [])
        assert len(interrupt_ids) == 1, f"Expected 1 interrupt after clearing session, got {len(interrupt_ids)}"

    finally:
        Runner.resource_mgr.remove_agent(agent_id="sub_agent_2")
        Runner.resource_mgr.remove_agent(agent_id="sub_agent_1")
        Runner.resource_mgr.remove_agent(agent_id="main_agent")
        Runner.resource_mgr.remove_tool(tool_id=read_tool.card.id)
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.skipif(not API_KEY or not API_BASE, reason="API_KEY and API_BASE required")
async def test_3layer_agent_subagent_parallel_interrupt():
    """3-layer agent nested interrupt - SubAgent1 concurrently calls SubAgent2 twice

    Flow: SubAgent1 calls 2 SubAgent2 instances concurrently -> each SubAgent2 calls read once -> 2 interrupts

    Structure:
        main_agent
          └── sub_agent_1_parallel (call_sub1)
                ├── sub_agent_2_parallel (call_sub2_A)
                │      └── read (call_aaa) ← interrupt_A
                └── sub_agent_2_parallel (call_sub2_B)
                       └── read (call_bbb) ← interrupt_B

    Resume flow:
        main_agent resume -> sub_agent_1_parallel resume
          -> both sub_agent_2_parallel instances resume concurrently
            -> each read resumes

    Note: Each SubAgent2 call uses unique session_id (tool_call.id) to prevent state collision.

    Verify:
    1. 2 concurrent interrupts from different SubAgent2 instances bubble up
    2. Each SubAgent2 has isolated session state
    3. Recovery executes both reads correctly
    """
    await Runner.start()
    try:
        read_tool = ReadTool()
        Runner.resource_mgr.add_tool(read_tool)

        sub_agent_2 = await create_nested_agent(
            NestedAgentConfig(
                agent_id="sub_agent_2_parallel",
                agent_name="sub_agent_2_parallel",
                system_prompt="You are innermost agent. Call read tool when user requests file read.",
                tools=[read_tool],
                rail_tool_names=["read"],
            )
        )
        sub_agent_2_card = AgentCard(
            id="sub_agent_2_parallel",
            name="sub_agent_2_parallel",
            description="Innermost agent for file read tasks",
            input_params={
                "type": "object",
                "properties": {
                    "query": {"description": "Task description", "type": "string"},
                },
                "required": ["query"],
            },
        )
        Runner.resource_mgr.add_agent(sub_agent_2_card, agent=lambda: sub_agent_2)

        sub_agent_1 = await create_nested_agent(
            NestedAgentConfig(
                agent_id="sub_agent_1_parallel",
                agent_name="sub_agent_1_parallel",
                system_prompt="You are middle agent. When asked to read multiple files, "
                              "call sub_agent_2_parallel tool concurrently for each file in a single response.",
                sub_agent_cards=[sub_agent_2_card],
            )
        )
        sub_agent_1_card = AgentCard(
            id="sub_agent_1_parallel",
            name="sub_agent_1_parallel",
            description="Middle agent coordinating sub-tasks",
            input_params={
                "type": "object",
                "properties": {
                    "query": {"description": "Task description", "type": "string"},
                },
                "required": ["query"],
            },
        )
        Runner.resource_mgr.add_agent(sub_agent_1_card, agent=lambda: sub_agent_1)

        main_agent = await create_nested_agent(
            NestedAgentConfig(
                agent_id="main_agent_parallel",
                agent_name="main_agent_parallel",
                system_prompt="You are main agent. Call sub_agent_1_parallel tool for tasks.",
                sub_agent_cards=[sub_agent_1_card],
            )
        )

        result1 = await Runner.run_agent(
            agent=main_agent,
            inputs={"query": "Please read files /tmp/a.txt and /tmp/b.txt", "conversation_id": "494"},
        )

        interrupt_ids, state_list = assert_interrupt_result(result1, expected_count=2)
        assert len(interrupt_ids) == 2, f"Expected 2 interrupts, got {len(interrupt_ids)}"

        for state in state_list:
            tool_name = get_tool_name_from_state(state)
            assert tool_name == "read", f"Expected tool_name 'read', got '{tool_name}'"

        result2 = await Runner.run_agent(
            agent=main_agent,
            inputs={"query": confirm_interrupt(interrupt_ids[0]), "conversation_id": "494"},
        )

        assert result2.get("result_type") == "interrupt"
        remaining_ids = result2.get("interrupt_ids", [])
        assert len(remaining_ids) == 1, f"Expected 1 remaining interrupt, got {len(remaining_ids)}"

        result3 = await Runner.run_agent(
            agent=main_agent,
            inputs={"query": confirm_interrupt(remaining_ids[0]), "conversation_id": "494"},
        )

        assert_answer_result(result3)
        assert read_tool.invoke_count == 2, f"Expected read invoke_count=2, got {read_tool.invoke_count}"

    finally:
        Runner.resource_mgr.remove_agent(agent_id="sub_agent_2_parallel")
        Runner.resource_mgr.remove_agent(agent_id="sub_agent_1_parallel")
        Runner.resource_mgr.remove_agent(agent_id="main_agent_parallel")
        Runner.resource_mgr.remove_tool(tool_id=read_tool.card.id)
        await Runner.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
