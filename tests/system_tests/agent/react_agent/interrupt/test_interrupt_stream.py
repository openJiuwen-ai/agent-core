# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import pytest

from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.runner import Runner
from openjiuwen.core.common.constants.constant import INTERACTION

from tests.system_tests.agent.react_agent.interrupt.test_base import (
    create_simple_agent,
    create_agent_with_tools,
    AgentWithToolsConfig,
    ReadTool,
    get_filepath_from_state,
    API_KEY,
    API_BASE,
)


@pytest.mark.asyncio
@pytest.mark.skipif(not API_KEY or not API_BASE, reason="API_KEY and API_BASE required")
async def test_hitl_rail_stream_interrupt_detected():
    """Stream mode: verify interrupt can be detected during streaming

    Flow: stream read tool -> interrupt detected -> tool not executed
    """
    await Runner.start()
    try:
        agent, _, read_tool, _, _ = await create_simple_agent(
            session_id_prefix="rail_stream",
        )

        outputs = []
        interrupt_detected = False
        tool_call_id = None

        async for output in Runner.run_agent_streaming(
                agent=agent,
                inputs={"query": "Call read tool, read /tmp/test.txt", "conversation_id": "493"},
        ):
            outputs.append(output)
            if output.type == INTERACTION:
                interrupt_detected = True
                tool_call_id = output.payload.id

        assert interrupt_detected, "Should detect interrupt"
        assert tool_call_id is not None, "Should get tool_call_id"
        assert read_tool.invoke_count == 0, f"Expected read invoke_count=0, got {read_tool.invoke_count}"
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.skipif(not API_KEY or not API_BASE, reason="API_KEY and API_BASE required")
async def test_hitl_rail_stream_agree_with_autoconfirm():
    """Stream mode: confirm with auto_confirm, subsequent calls auto-pass

    Flow: stream read -> confirm with auto_confirm=True -> 2nd stream read auto-passes
    """
    await Runner.start()
    try:
        agent, _, read_tool, _, _ = await create_simple_agent(
            session_id_prefix="rail_stream_autoconfirm",
            system_prompt="You are an assistant. When the user requests to read a file, you must call the read tool.",
        )

        outputs1 = []
        interrupt_detected = False
        tool_call_id = None

        async for output in Runner.run_agent_streaming(
                agent=agent,
                inputs={"query": "Please read /tmp/test1.txt", "conversation_id": "493"},
        ):
            outputs1.append(output)
            if output.type == INTERACTION:
                interrupt_detected = True
                tool_call_id = output.payload.id

        if not interrupt_detected:
            pytest.skip("LLM did not call tool, skipping auto_confirm test")

        assert tool_call_id is not None
        assert read_tool.invoke_count == 0

        interactive_input = InteractiveInput()
        interactive_input.update(tool_call_id, {
            "approved": True,
            "feedback": "Confirm and auto confirm",
            "auto_confirm": True
        })

        outputs2 = []
        second_interrupt_detected = False

        async for output in Runner.run_agent_streaming(
                agent=agent,
                inputs={"query": interactive_input, "conversation_id": "493"},
        ):
            outputs2.append(output)
            if output.type == INTERACTION:
                second_interrupt_detected = True

        assert not second_interrupt_detected, "Should not interrupt after confirm"
        assert read_tool.invoke_count == 1, f"Expected read invoke_count=1, got {read_tool.invoke_count}"

        outputs3 = []
        third_interrupt_detected = False

        async for output in Runner.run_agent_streaming(
                agent=agent,
                inputs={"query": "Please read /tmp/test2.txt", "conversation_id": "493"},
        ):
            outputs3.append(output)
            if output.type == INTERACTION:
                third_interrupt_detected = True

        assert not third_interrupt_detected, "Should auto-confirm, no interrupt expected"
        assert read_tool.invoke_count == 2, f"Expected read invoke_count=2, got {read_tool.invoke_count}"
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.skipif(not API_KEY or not API_BASE, reason="API_KEY and API_BASE required")
async def test_hitl_rail_stream_reject():
    """Stream mode: reject execution, tool not executed

    Flow: stream read -> reject -> tool not executed
    """
    await Runner.start()
    try:
        agent, _, read_tool, _, _ = await create_simple_agent(
            session_id_prefix="rail_stream_reject",
        )

        outputs1 = []
        interrupt_detected = False
        tool_call_id = None

        async for output in Runner.run_agent_streaming(
                agent=agent,
                inputs={"query": "Call read tool, read /tmp/test.txt", "conversation_id": "493"},
        ):
            outputs1.append(output)
            if output.type == INTERACTION:
                interrupt_detected = True
                tool_call_id = output.payload.id

        assert interrupt_detected, "Should detect interrupt"
        assert tool_call_id is not None
        assert read_tool.invoke_count == 0

        interactive_input = InteractiveInput()
        interactive_input.update(tool_call_id, {
            "approved": False,
            "feedback": "Reject this operation"
        })

        outputs2 = []
        second_interrupt_detected = False

        async for output in Runner.run_agent_streaming(
                agent=agent,
                inputs={"query": interactive_input, "conversation_id": "493"},
        ):
            outputs2.append(output)
            if output.type == INTERACTION:
                second_interrupt_detected = True

        assert not second_interrupt_detected, "Should not interrupt after reject"
        assert read_tool.invoke_count == 0, f"Expected read invoke_count=0, got {read_tool.invoke_count}"
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.skipif(not API_KEY or not API_BASE, reason="API_KEY and API_BASE required")
async def test_hitl_rail_stream_concurrent_tools_all_confirmed():
    """Stream mode: 2 concurrent read tools intercepted, stream outputs all interrupts

    Flow: stream 2 reads intercepted -> all 2 interrupts output -> confirm both -> complete
    """
    await Runner.start()
    try:
        read_tool = ReadTool()

        agent, _, _ = await create_agent_with_tools(
            AgentWithToolsConfig(
                tools=[read_tool],
                session_id_prefix="stream_concurrent_all_confirmed",
                system_prompt="You are an assistant. When the user requests to read files, "
                              "please call the read tool concurrently to read file a.txt "
                              "and file b.txt at the same time.",
                rail_tool_names=["read"],
                trace_tool_names=["read"],
            )
        )

        outputs1 = []
        interrupt_ids = []
        state_list = []

        async for output in Runner.run_agent_streaming(
                agent=agent,
                inputs={"query": "Please read file a.txt and file b.txt simultaneously", "conversation_id": "498"},
        ):
            outputs1.append(output)
            if output.type == INTERACTION:
                interrupt_ids.append(output.payload.id)
                state_list.append(output)

        assert len(interrupt_ids) == 2, f"Stream should output all 2 interrupts, got {len(interrupt_ids)}"

        interactive_input = InteractiveInput()
        for interrupt_id in interrupt_ids:
            interactive_input.update(interrupt_id, {"approved": True, "feedback": "Confirm"})

        outputs2 = []
        second_interrupt_detected = False

        async for output in Runner.run_agent_streaming(
                agent=agent,
                inputs={"query": interactive_input, "conversation_id": "498"},
        ):
            outputs2.append(output)
            if output.type == INTERACTION:
                second_interrupt_detected = True

        assert not second_interrupt_detected, "Should not interrupt after confirming both"
        assert read_tool.invoke_count == 2, f"Expected read invoke_count=2, got {read_tool.invoke_count}"
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.skipif(not API_KEY or not API_BASE, reason="API_KEY and API_BASE required")
async def test_hitl_rail_stream_concurrent_tools_partial_reject():
    """Stream mode: 2 concurrent read tools intercepted, stream outputs all interrupts, reject one

    Flow: stream 2 reads intercepted -> all 2 interrupts output -> reject b.txt, confirm a.txt -> complete
    """
    await Runner.start()
    try:
        read_tool = ReadTool()

        agent, _, _ = await create_agent_with_tools(
            AgentWithToolsConfig(
                tools=[read_tool],
                session_id_prefix="stream_concurrent_partial_reject",
                system_prompt="You are an assistant. When the user requests to read files, "
                              "please call the read tool concurrently to read file a.txt "
                              "and file b.txt at the same time.",
                rail_tool_names=["read"],
                trace_tool_names=["read"],
            )
        )

        outputs1 = []
        interrupt_ids = []
        state_list = []

        async for output in Runner.run_agent_streaming(
                agent=agent,
                inputs={"query": "Please read file a.txt and file b.txt simultaneously", "conversation_id": "498"},
        ):
            outputs1.append(output)
            if output.type == INTERACTION:
                interrupt_ids.append(output.payload.id)
                state_list.append(output)

        assert len(interrupt_ids) == 2, f"Stream should output all 2 interrupts, got {len(interrupt_ids)}"

        a_txt_id = None
        b_txt_id = None

        for i, tool_call_id in enumerate(interrupt_ids):
            filepath = get_filepath_from_state(state_list[i])
            if filepath == "a.txt":
                a_txt_id = tool_call_id
            elif filepath == "b.txt":
                b_txt_id = tool_call_id

        interactive_input = InteractiveInput()
        if a_txt_id:
            interactive_input.update(a_txt_id, {"approved": True, "feedback": "Confirm a.txt"})
        if b_txt_id:
            interactive_input.update(b_txt_id, {"approved": False, "feedback": "Reject b.txt"})

        outputs2 = []
        second_interrupt_detected = False

        async for output in Runner.run_agent_streaming(
                agent=agent,
                inputs={"query": interactive_input, "conversation_id": "498"},
        ):
            outputs2.append(output)
            if output.type == INTERACTION:
                second_interrupt_detected = True

        assert not second_interrupt_detected, "Should not interrupt after partial reject"
        assert read_tool.invoke_count == 1, f"Expected read invoke_count=1 (only a.txt), got {read_tool.invoke_count}"
    finally:
        await Runner.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
