# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Test 3-layer agent nested interrupt."""

import os
from unittest.mock import patch

import pytest

from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

from tests.unit_tests.agent.react_agent.interrupt.test_base import (
    assert_answer_result,
    assert_interrupt_result,
    confirm_interrupt,
    create_nested_agent,
    get_tool_name_from_state,
    NestedAgentConfig,
    ReadTool,
)
from tests.unit_tests.fixtures.mock_llm import (
    create_text_response,
    create_tool_call_response,
    MockLLMModel,
)


@pytest.mark.asyncio
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
    os.environ.setdefault("LLM_SSL_VERIFY", "false")
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

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response("sub_agent_1", '{"query": "read file"}'),
            create_tool_call_response("sub_agent_2", '{"query": "read file"}'),
            create_tool_call_response("read", '{"filepath": "/tmp/test.txt"}'),
            create_text_response("File read complete"),
        ])

        with patch("openjiuwen.core.foundation.llm.model.Model.stream", side_effect=mock_llm.stream), \
             patch("openjiuwen.core.foundation.llm.model.Model.invoke", side_effect=mock_llm.invoke):
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


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
