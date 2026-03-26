# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import pytest

from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.deepagents.rails.interrupt.confirm_rail import ConfirmInterruptRail

from tests.system_tests.agent.react_agent.interrupt.test_base import (
    AgentWithToolsConfig,
    create_agent_with_tools,
    assert_answer_result,
    ReadTool,
    ActionTool,
    TraceRail,
    get_filepath_from_state,
    confirm_interrupt,
    reject_interrupt,
    API_KEY,
    API_BASE,
    MODEL_PROVIDER,
    MODEL_NAME,
)


@pytest.mark.asyncio
@pytest.mark.skipif(not API_KEY or not API_BASE, reason="API_KEY and API_BASE required")
async def test_hitl_rail_concurrent_tools_all_confirmed():
    """2 concurrent read tools intercepted, confirm both sequentially

    Flow: 2 reads intercepted -> confirm first (1 remaining) -> confirm second -> complete
    """
    await Runner.start()
    try:
        read_tool = ReadTool()

        agent, _, trace_rail = await create_agent_with_tools(
            AgentWithToolsConfig(
                tools=[read_tool],
                session_id_prefix="concurrent_all_confirmed",
                system_prompt="You are an assistant. When the user requests to read files, "
                              "please call the read tool concurrently to read file a.txt "
                              "and file b.txt at the same time.",
                rail_tool_names=["read"],
                trace_tool_names=["read"],
            )
        )

        result = await Runner.run_agent(
            agent=agent,
            inputs={"query": "Please read file a.txt and file b.txt simultaneously", "conversation_id": "498"},
        )

        assert result.get("result_type") == "interrupt"
        interrupt_ids = result.get("interrupt_ids", [])
        assert len(interrupt_ids) == 2, f"Expected 2 interrupts, got {len(interrupt_ids)}"

        result2 = await Runner.run_agent(
            agent=agent,
            inputs={"query": confirm_interrupt(interrupt_ids[0]), "conversation_id": "498"},
        )

        assert result2.get("result_type") == "interrupt"
        remaining_interrupt_ids = result2.get("interrupt_ids", [])
        assert len(remaining_interrupt_ids) == 1, f"Expected 1 remaining interrupt, got {len(remaining_interrupt_ids)}"

        result3 = await Runner.run_agent(
            agent=agent,
            inputs={"query": confirm_interrupt(remaining_interrupt_ids[0]), "conversation_id": "498"},
        )

        assert_answer_result(result3)
        assert read_tool.invoke_count == 2, f"Expected read invoke_count=2, got {read_tool.invoke_count}"
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.skipif(not API_KEY or not API_BASE, reason="API_KEY and API_BASE required")
async def test_hitl_rail_concurrent_tools_partial_reject_one_round():
    """2 concurrent read tools intercepted, reject one and confirm the other in one round

    Flow: 2 reads intercepted -> reject b.txt, confirm a.txt -> complete (auto-executes confirmed)
    """
    await Runner.start()
    try:
        read_tool = ReadTool()

        agent, _, _ = await create_agent_with_tools(
            AgentWithToolsConfig(
                tools=[read_tool],
                session_id_prefix="partial_reject_one_round",
                system_prompt="You are an assistant. When the user requests to read files, "
                              "please call the read tool concurrently to read file a.txt "
                              "and file b.txt at the same time.",
                rail_tool_names=["read"],
                trace_tool_names=["read"],
            )
        )

        result = await Runner.run_agent(
            agent=agent,
            inputs={"query": "Please read file a.txt and file b.txt simultaneously", "conversation_id": "498"},
        )

        assert result.get("result_type") == "interrupt"
        interrupt_ids = result.get("interrupt_ids", [])
        state_list = result.get("state", [])
        assert len(interrupt_ids) == 2, f"Expected 2 interrupts, got {len(interrupt_ids)}"
        assert len(state_list) == 2

        interactive_input = InteractiveInput()

        for i, tool_call_id in enumerate(interrupt_ids):
            filepath = get_filepath_from_state(state_list[i])

            if filepath == "b.txt":
                interactive_input.update(tool_call_id, {"approved": False, "feedback": "Reject reading b.txt"})
            else:
                interactive_input.update(tool_call_id, {"approved": True, "feedback": "Confirm read"})
        result2 = await Runner.run_agent(
            agent=agent,
            inputs={"query": interactive_input, "conversation_id": "498"},
        )
        assert_answer_result(result2)
        assert read_tool.invoke_count == 1, f"Expected read invoke_count=1 (only a.txt), got {read_tool.invoke_count}"
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.skipif(not API_KEY or not API_BASE, reason="API_KEY and API_BASE required")
async def test_hitl_rail_concurrent_tools_partial_reject_two_rounds():
    """2 concurrent read tools intercepted, reject one, then confirm the other

    Flow: 2 reads intercepted -> reject b.txt (a.txt still interrupt) -> confirm a.txt -> complete
    """
    await Runner.start()
    try:
        read_tool = ReadTool()

        agent = ReActAgent(card=AgentCard(id="partial_reject_two_rounds_agent"))
        config = ReActAgentConfig()
        config.configure_model_client(
            provider=MODEL_PROVIDER,
            api_key=API_KEY,
            api_base=API_BASE,
            model_name=MODEL_NAME,
        )
        config.configure_prompt_template([
            {"role": "system",
             "content": "You are an assistant. When the user requests to read files, "
                        "please call the read tool concurrently to read file a.txt and file b.txt at the same time."}
        ])
        agent.configure(config)

        Runner.resource_mgr.add_tool(read_tool)
        agent.ability_manager.add(read_tool.card)

        rail = ConfirmInterruptRail(tool_names=["read"])
        await agent.register_rail(rail)

        trace_rail = TraceRail()
        await agent.register_rail(trace_rail)

        result = await Runner.run_agent(
            agent=agent,
            inputs={"query": "Please read file a.txt and file b.txt simultaneously", "conversation_id": "498"},
        )

        assert result.get("result_type") == "interrupt"
        interrupt_ids = result.get("interrupt_ids", [])
        state_list = result.get("state", [])
        assert len(interrupt_ids) == 2, f"Expected 2 interrupts, got {len(interrupt_ids)}"
        assert len(state_list) == 2

        a_txt_tool_call_id = None
        b_txt_tool_call_id = None

        for i, tool_call_id in enumerate(interrupt_ids):
            filepath = get_filepath_from_state(state_list[i])

            if filepath == "b.txt":
                b_txt_tool_call_id = tool_call_id
            elif filepath == "a.txt":
                a_txt_tool_call_id = tool_call_id

        assert b_txt_tool_call_id is not None, "b.txt should be intercepted"
        assert a_txt_tool_call_id is not None, "a.txt should be intercepted"

        result2 = await Runner.run_agent(
            agent=agent,
            inputs={"query": reject_interrupt(b_txt_tool_call_id, "Reject reading b.txt"),
                    "conversation_id": "498"},
        )

        assert result2.get("result_type") == "interrupt"
        remaining_interrupt_ids = result2.get("interrupt_ids", [])
        assert a_txt_tool_call_id in remaining_interrupt_ids, "a.txt should still be interrupted"
        assert len(remaining_interrupt_ids) == 1, f"Expected 1 remaining interrupt, got {len(remaining_interrupt_ids)}"

        result3 = await Runner.run_agent(
            agent=agent,
            inputs={"query": confirm_interrupt(a_txt_tool_call_id), "conversation_id": "498"},
        )
        assert_answer_result(result3)
        assert read_tool.invoke_count == 1, f"Expected read invoke_count=1 (only a.txt), got {read_tool.invoke_count}"
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.skipif(not API_KEY or not API_BASE, reason="API_KEY and API_BASE required")
async def test_hitl_rail_concurrent_tools_one_pass_one_interrupt():
    """One tool passes directly, one is intercepted during concurrent execution

    Flow: read+action concurrent -> only read intercepted -> confirm read (action auto-passes) -> complete
    """
    await Runner.start()
    try:
        read_tool = ReadTool()
        action_tool = ActionTool("action")

        agent = ReActAgent(card=AgentCard(id="one_pass_one_interrupt_agent"))
        config = ReActAgentConfig()
        config.configure_model_client(
            provider=MODEL_PROVIDER,
            api_key=API_KEY,
            api_base=API_BASE,
            model_name=MODEL_NAME,
        )
        config.configure_prompt_template([
            {"role": "system",
             "content": "You are an assistant. Please call read and action tools concurrently to read file a.txt "
                        "and execute action operation."}
        ])
        agent.configure(config)

        Runner.resource_mgr.add_tool(read_tool)
        Runner.resource_mgr.add_tool(action_tool)
        agent.ability_manager.add(read_tool.card)
        agent.ability_manager.add(action_tool.card)

        rail = ConfirmInterruptRail(tool_names=["read"])
        await agent.register_rail(rail)

        trace_rail = TraceRail()
        await agent.register_rail(trace_rail)

        result = await Runner.run_agent(
            agent=agent,
            inputs={"query": "Please read file a.txt and execute action operation simultaneously",
                    "conversation_id": "498"},
        )

        assert result.get("result_type") == "interrupt"
        interrupt_ids = result.get("interrupt_ids", [])
        state_list = result.get("state", [])
        assert len(interrupt_ids) == 1, f"Expected 1 interrupt, got {len(interrupt_ids)}"

        tool_name_in_state = ""
        if state_list:
            payload = state_list[0].payload.value if hasattr(state_list[0], 'payload') else None
            if payload and hasattr(payload, 'tool_name'):
                tool_name_in_state = payload.tool_name
        assert tool_name_in_state == "read", f"Expected tool_name 'read', got '{tool_name_in_state}'"

        interactive_input = InteractiveInput()
        interactive_input.update(interrupt_ids[0], {"approved": True, "feedback": "Confirm read"})

        result2 = await Runner.run_agent(
            agent=agent,
            inputs={"query": interactive_input, "conversation_id": "498"},
        )
        assert_answer_result(result2)
        assert trace_rail.get_execution_count(
            "action") == 1, f"Expected action execution count=1, got {trace_rail.get_execution_count('action')}"
        assert read_tool.invoke_count == 1, f"Expected read invoke_count=1, got {read_tool.invoke_count}"
    finally:
        await Runner.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
