# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
import os

import pytest

from openjiuwen.core.runner import Runner
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.rails.interrupt.confirm_rail import ConfirmInterruptRail
from tests.system_tests.agent.react_agent.interrupt.test_base import (
    AgentWithToolsConfig,
    create_agent_with_tools,
    assert_interrupt_result,
    assert_answer_result,
    ReadTool,
    NestedAgentConfig,
    create_nested_agent,
    API_KEY,
    API_BASE,
)


class FineGrainedConfirmRail(ConfirmInterruptRail):
    """Fine-grained confirmation Rail - uses simple keys based on file names.

    Example:
        - read a.txt -> auto_confirm_key = "read_a"
        - read b.txt -> auto_confirm_key = "read_b"
    """

    def _get_auto_confirm_key(self, tool_call) -> str:
        """Generate fine-grained auto-confirm key based on tool arguments."""
        if tool_call.name == "read":
            args = json.loads(tool_call.arguments) if isinstance(tool_call.arguments, str) else tool_call.arguments
            filepath = args.get("filepath", "")
            if filepath:
                filename = os.path.basename(filepath)
                name_without_ext = filename.rsplit('.', 1)[0] if '.' in filename else filename
                return tool_call.name + "_" + name_without_ext
            return tool_call.name
        return tool_call.name


@pytest.mark.asyncio
@pytest.mark.skipif(not API_KEY or not API_BASE, reason="API_KEY and API_BASE required")
async def test_single_agent_fine_grained_auto_confirm():
    """Single-layer Agent fine-grained auto-confirm test.

    Verifies auto-confirm key based on tool arguments:
    1. read a.txt -> interrupt -> confirm (auto_confirm=True, key: a)
    2. read a.txt -> auto-pass (key matches)
    3. read b.txt -> interrupt (key: b does not match)
    """
    await Runner.start()
    try:
        read_tool = ReadTool()
        agent, session, trace_rail = await create_agent_with_tools(
            AgentWithToolsConfig(
                tools=[read_tool],
                session_id_prefix="single_fg",
                system_prompt="You are an assistant. When the user requests to read a file, "
                              "call the read tool with the exact filepath provided.",
                rail_tool_names=[],
                trace_tool_names=["read"],
            )
        )

        fine_grained_rail = FineGrainedConfirmRail(tool_names=["read"])
        await agent.register_rail(fine_grained_rail)

        result1 = await Runner.run_agent(
            agent=agent,
            inputs={"query": "Please read /tmp/a.txt", "conversation_id": "test_single_fg"},
        )

        if result1.get("result_type") != "interrupt":
            pytest.skip("LLM did not call tool, skipping test")

        interrupt_ids, _ = assert_interrupt_result(result1, expected_count=1)
        tool_call_id = interrupt_ids[0]

        interactive_input = InteractiveInput()
        interactive_input.update(tool_call_id, {
            "approved": True,
            "feedback": "Confirm reading a.txt",
            "auto_confirm": True
        })

        result2 = await Runner.run_agent(
            agent=agent,
            inputs={"query": interactive_input, "conversation_id": "test_single_fg"},
        )
        assert_answer_result(result2)
        assert read_tool.invoke_count == 1

        result3 = await Runner.run_agent(
            agent=agent,
            inputs={"query": "Please read /tmp/a.txt again", "conversation_id": "test_single_fg"},
        )
        assert_answer_result(result3)
        assert read_tool.invoke_count == 2

        result4 = await Runner.run_agent(
            agent=agent,
            inputs={"query": "Please read /tmp/b.txt", "conversation_id": "test_single_fg"},
        )
        assert result4.get("result_type") == "interrupt", \
            f"Expected interrupt for b.txt (different key), got {result4.get('result_type')}"

    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.skipif(not API_KEY or not API_BASE, reason="API_KEY and API_BASE required")
async def test_3layer_agent_fine_grained_auto_confirm():
    """3-layer Agent fine-grained auto-confirm concurrent test.

    Verifies fine-grained auto-confirm in 3-layer agent structure:
    1. read a.txt, read b.txt concurrently -> 2 interrupts
    2. Confirm read a.txt (auto_confirm=True, key: a)
    3. Confirm read b.txt (auto_confirm=False, key: b)
    4. read a.txt -> auto-pass (key: a is confirmed)
    """
    await Runner.start()
    try:
        read_tool = ReadTool()
        Runner.resource_mgr.add_tool(read_tool)

        sub_agent_2 = await create_nested_agent(
            NestedAgentConfig(
                agent_id="sub_agent_2_fg",
                agent_name="sub_agent_2_fg",
                system_prompt="You are innermost agent. When asked to read multiple files, "
                              "call read tool concurrently for each file in a single response.",
                tools=[read_tool],
                rail_tool_names=[],
            )
        )
        sub_agent_2_card = AgentCard(
            id="sub_agent_2_fg",
            name="sub_agent_2_fg",
            description="Innermost agent for file read",
            input_params={
                "type": "object",
                "properties": {
                    "query": {"description": "Task description", "type": "string"},
                },
                "required": ["query"],
            },
        )
        Runner.resource_mgr.add_agent(sub_agent_2_card, agent=lambda: sub_agent_2)

        fine_grained_rail = FineGrainedConfirmRail(tool_names=["read"])
        await sub_agent_2.register_rail(fine_grained_rail)

        sub_agent_1 = await create_nested_agent(
            NestedAgentConfig(
                agent_id="sub_agent_1_fg",
                agent_name="sub_agent_1_fg",
                system_prompt="You are middle agent. Call sub_agent_2_fg tool once for file read tasks.",
                sub_agent_cards=[sub_agent_2_card],
            )
        )
        sub_agent_1_card = AgentCard(
            id="sub_agent_1_fg",
            name="sub_agent_1_fg",
            description="Middle agent",
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
                agent_id="main_agent_fg",
                agent_name="main_agent_fg",
                system_prompt="You are main agent. Call sub_agent_1_fg tool for tasks.",
                sub_agent_cards=[sub_agent_1_card],
            )
        )

        result1 = await Runner.run_agent(
            agent=main_agent,
            inputs={"query": "Please read files /tmp/a.txt and /tmp/b.txt", "conversation_id": "test_3layer_fg"},
        )

        interrupt_ids, state_list = assert_interrupt_result(result1, expected_count=2)
        assert len(interrupt_ids) == 2

        interactive_input = InteractiveInput()
        interactive_input.update(interrupt_ids[0], {
            "approved": True,
            "feedback": "Confirm reading a.txt",
            "auto_confirm": True
        })

        result2 = await Runner.run_agent(
            agent=main_agent,
            inputs={"query": interactive_input, "conversation_id": "test_3layer_fg"},
        )

        assert result2.get("result_type") == "interrupt"
        remaining_ids = result2.get("interrupt_ids", [])
        assert len(remaining_ids) == 1

        interactive_input2 = InteractiveInput()
        interactive_input2.update(remaining_ids[0], {
            "approved": True,
            "feedback": "Confirm reading b.txt",
            "auto_confirm": False
        })

        result3 = await Runner.run_agent(
            agent=main_agent,
            inputs={"query": interactive_input2, "conversation_id": "test_3layer_fg"},
        )
        assert_answer_result(result3)
        assert read_tool.invoke_count == 2

        result4 = await Runner.run_agent(
            agent=main_agent,
            inputs={"query": "Please read file /tmp/a.txt again", "conversation_id": "test_3layer_fg"},
        )
        assert_answer_result(result4)
        assert read_tool.invoke_count == 3

    finally:
        Runner.resource_mgr.remove_agent(agent_id="sub_agent_2_fg")
        Runner.resource_mgr.remove_agent(agent_id="sub_agent_1_fg")
        Runner.resource_mgr.remove_agent(agent_id="main_agent_fg")
        Runner.resource_mgr.remove_tool(tool_id=read_tool.card.id)
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.skipif(not API_KEY or not API_BASE, reason="API_KEY and API_BASE required")
async def test_3layer_agent_fine_grained_auto_confirm_clear_session():
    """3-layer Agent fine-grained auto-confirm + clear session test.

    Verifies that auto_confirm state is cleared after clearing session:
    1. read a.txt -> interrupt -> confirm (auto_confirm=True, key: a)
    2. read a.txt -> auto-pass
    3. Clear session
    4. read a.txt -> interrupt again (auto_confirm state cleared)
    """
    await Runner.start()
    try:
        read_tool = ReadTool()
        Runner.resource_mgr.add_tool(read_tool)

        sub_agent_2 = await create_nested_agent(
            NestedAgentConfig(
                agent_id="sub_agent_2_clear",
                agent_name="sub_agent_2_clear",
                system_prompt="You are innermost agent. Call read tool when user requests file read.",
                tools=[read_tool],
                rail_tool_names=[],
            )
        )
        sub_agent_2_card = AgentCard(
            id="sub_agent_2_clear",
            name="sub_agent_2_clear",
            description="Innermost agent",
            input_params={
                "type": "object",
                "properties": {
                    "query": {"description": "Task description", "type": "string"},
                },
                "required": ["query"],
            },
        )
        Runner.resource_mgr.add_agent(sub_agent_2_card, agent=lambda: sub_agent_2)

        fine_grained_rail = FineGrainedConfirmRail(tool_names=["read"])
        await sub_agent_2.register_rail(fine_grained_rail)

        sub_agent_1 = await create_nested_agent(
            NestedAgentConfig(
                agent_id="sub_agent_1_clear",
                agent_name="sub_agent_1_clear",
                system_prompt="You are middle agent. Call sub_agent_2_clear tool for file read tasks.",
                sub_agent_cards=[sub_agent_2_card],
            )
        )
        sub_agent_1_card = AgentCard(
            id="sub_agent_1_clear",
            name="sub_agent_1_clear",
            description="Middle agent",
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
                agent_id="main_agent_clear",
                agent_name="main_agent_clear",
                system_prompt="You are main agent. Call sub_agent_1_clear tool for tasks.",
                sub_agent_cards=[sub_agent_1_card],
            )
        )

        fine_grained_rail = FineGrainedConfirmRail(tool_names=["read"])
        await main_agent.register_rail(fine_grained_rail)

        session_id = "test_clear_session"

        result1 = await Runner.run_agent(
            agent=main_agent,
            inputs={"query": "Please read file /tmp/a.txt", "conversation_id": session_id},
        )

        interrupt_ids, _ = assert_interrupt_result(result1, expected_count=1)
        tool_call_id = interrupt_ids[0]

        interactive_input = InteractiveInput()
        interactive_input.update(tool_call_id, {
            "approved": True,
            "feedback": "Confirm reading a.txt",
            "auto_confirm": True
        })

        result2 = await Runner.run_agent(
            agent=main_agent,
            inputs={"query": interactive_input, "conversation_id": session_id},
        )
        assert_answer_result(result2)
        assert read_tool.invoke_count == 1

        result3 = await Runner.run_agent(
            agent=main_agent,
            inputs={"query": "Please read file /tmp/a.txt again", "conversation_id": session_id},
        )
        assert_answer_result(result3)
        assert read_tool.invoke_count == 2

        await main_agent.clear_session(session_id)

        result4 = await Runner.run_agent(
            agent=main_agent,
            inputs={"query": "Please read file /tmp/a.txt once more", "conversation_id": session_id},
        )
        assert result4.get("result_type") == "interrupt", \
            f"Expected interrupt after clearing session, got {result4.get('result_type')}"

    finally:
        Runner.resource_mgr.remove_agent(agent_id="sub_agent_2_clear")
        Runner.resource_mgr.remove_agent(agent_id="sub_agent_1_clear")
        Runner.resource_mgr.remove_agent(agent_id="main_agent_clear")
        Runner.resource_mgr.remove_tool(tool_id=read_tool.card.id)
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.skipif(not API_KEY or not API_BASE, reason="API_KEY and API_BASE required")
async def test_fine_grained_auto_confirm_merge_keys():
    """Verify auto_confirm records are merged and appended, not overwritten.

    Scenario:
    1. read a.txt -> interrupt -> confirm (auto_confirm=True, key: a)
    2. read b.txt -> interrupt -> confirm (auto_confirm=True, key: b)
    3. read a.txt -> auto-pass (verify key: a is still valid)
    4. read b.txt -> auto-pass (verify key: b is still valid)
    """
    await Runner.start()
    try:
        read_tool = ReadTool()
        Runner.resource_mgr.add_tool(read_tool)

        sub_agent_2 = await create_nested_agent(
            NestedAgentConfig(
                agent_id="sub_agent_2_merge",
                agent_name="sub_agent_2_merge",
                system_prompt="You are innermost agent. Call read tool when user requests file read.",
                tools=[read_tool],
                rail_tool_names=[],
            )
        )
        sub_agent_2_card = AgentCard(
            id="sub_agent_2_merge",
            name="sub_agent_2_merge",
            description="Innermost agent for file read",
            input_params={
                "type": "object",
                "properties": {
                    "query": {"description": "Task description", "type": "string"},
                },
                "required": ["query"],
            },
        )
        Runner.resource_mgr.add_agent(sub_agent_2_card, agent=lambda: sub_agent_2)

        fine_grained_rail = FineGrainedConfirmRail(tool_names=["read"])
        await sub_agent_2.register_rail(fine_grained_rail)

        sub_agent_1 = await create_nested_agent(
            NestedAgentConfig(
                agent_id="sub_agent_1_merge",
                agent_name="sub_agent_1_merge",
                system_prompt="You are middle agent. Call sub_agent_2_merge tool for file read tasks.",
                sub_agent_cards=[sub_agent_2_card],
            )
        )
        sub_agent_1_card = AgentCard(
            id="sub_agent_1_merge",
            name="sub_agent_1_merge",
            description="Middle agent",
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
                agent_id="main_agent_merge",
                agent_name="main_agent_merge",
                system_prompt="You are main agent. Call sub_agent_1_merge tool for tasks.",
                sub_agent_cards=[sub_agent_1_card],
            )
        )

        session_id = "test_merge_keys"

        # Step 1: read a.txt -> interrupt -> confirm (auto_confirm=True, key: a)
        result1 = await Runner.run_agent(
            agent=main_agent,
            inputs={"query": "Please read file /tmp/a.txt", "conversation_id": session_id},
        )
        interrupt_ids1, _ = assert_interrupt_result(result1, expected_count=1)

        interactive_input1 = InteractiveInput()
        interactive_input1.update(interrupt_ids1[0], {
            "approved": True,
            "feedback": "Confirm reading a.txt",
            "auto_confirm": True
        })

        result2 = await Runner.run_agent(
            agent=main_agent,
            inputs={"query": interactive_input1, "conversation_id": session_id},
        )
        assert_answer_result(result2)
        assert read_tool.invoke_count == 1

        # Step 2: read b.txt -> interrupt -> confirm (auto_confirm=True, key: b)
        result3 = await Runner.run_agent(
            agent=main_agent,
            inputs={"query": "Please read file /tmp/b.txt", "conversation_id": session_id},
        )
        interrupt_ids2, _ = assert_interrupt_result(result3, expected_count=1)

        interactive_input2 = InteractiveInput()
        interactive_input2.update(interrupt_ids2[0], {
            "approved": True,
            "feedback": "Confirm reading b.txt",
            "auto_confirm": True
        })

        result4 = await Runner.run_agent(
            agent=main_agent,
            inputs={"query": interactive_input2, "conversation_id": session_id},
        )
        assert_answer_result(result4)
        assert read_tool.invoke_count == 2

        # Step 3: read a.txt -> auto-pass (verify key: a is still valid)
        result5 = await Runner.run_agent(
            agent=main_agent,
            inputs={"query": "Please read file /tmp/a.txt again", "conversation_id": session_id},
        )
        assert_answer_result(result5)
        assert read_tool.invoke_count == 3

        # Step 4: read b.txt -> auto-pass (verify key: b is still valid)
        result6 = await Runner.run_agent(
            agent=main_agent,
            inputs={"query": "Please read file /tmp/b.txt again", "conversation_id": session_id},
        )
        assert_answer_result(result6)
        assert read_tool.invoke_count == 4

    finally:
        Runner.resource_mgr.remove_agent(agent_id="sub_agent_2_merge")
        Runner.resource_mgr.remove_agent(agent_id="sub_agent_1_merge")
        Runner.resource_mgr.remove_agent(agent_id="main_agent_merge")
        Runner.resource_mgr.remove_tool(tool_id=read_tool.card.id)
        await Runner.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
