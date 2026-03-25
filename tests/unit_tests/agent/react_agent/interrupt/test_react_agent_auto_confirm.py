# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Test HITL rail auto-confirm feature."""

import os
from unittest.mock import patch

import pytest

from openjiuwen.core.foundation.llm import AssistantMessage, UsageMetadata, ToolCall
from openjiuwen.core.runner import Runner

from tests.unit_tests.agent.react_agent.interrupt.test_base import (
    ActionTool,
    AgentWithToolsConfig,
    assert_answer_result,
    assert_interrupt_result,
    confirm_interrupt,
    create_agent_with_tools,
    create_simple_agent,
    ReadTool,
)
from tests.unit_tests.fixtures.mock_llm import (
    create_text_response,
    create_tool_call_response,
    MockLLMModel,
)


@pytest.mark.asyncio
async def test_hitl_rail_auto_confirm():
    """auto_confirm feature: confirm once, subsequent same-name tools auto-pass

    Flow: read intercepted -> confirm with auto_confirm=True -> 2nd read auto-passes -> complete
    """
    os.environ.setdefault("LLM_SSL_VERIFY", "false")
    await Runner.start()
    try:
        agent, _, read_tool, _, trace_rail = await create_simple_agent(
            session_id_prefix="rail_auto_confirm",
            system_prompt="You are an assistant. When the user requests to read a file, you must call the read tool.",
        )

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response("read", '{"filepath": "/tmp/test1.txt"}'),
            create_text_response("File 1 read"),
            create_tool_call_response("read", '{"filepath": "/tmp/test2.txt"}'),
            create_text_response("File 2 read"),
        ])

        with patch("openjiuwen.core.foundation.llm.model.Model.stream", side_effect=mock_llm.stream), \
             patch("openjiuwen.core.foundation.llm.model.Model.invoke", side_effect=mock_llm.invoke):
            result1 = await Runner.run_agent(
                agent=agent,
                inputs={"query": "Please read /tmp/test1.txt", "conversation_id": "497"},
            )

            if result1.get("result_type") != "interrupt":
                pytest.skip("LLM did not call tool, skipping auto_confirm test")

            interrupt_ids, _ = assert_interrupt_result(result1, expected_count=1)
            tool_call_id = interrupt_ids[0]

            interactive_input = confirm_interrupt(tool_call_id, auto_confirm=True)

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
async def test_hitl_rail_same_tool_multiple_calls():
    """Same tool called multiple times in one iteration, each independently confirmed

    Flow: 3 concurrent action calls intercepted -> confirm first -> confirm second -> confirm third -> complete
    """
    os.environ.setdefault("LLM_SSL_VERIFY", "false")
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

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            AssistantMessage(
                content="",
                tool_calls=[
                    ToolCall(id="c1", type="function", name="multi_action", arguments='{"action": "action1"}'),
                    ToolCall(id="c2", type="function", name="multi_action", arguments='{"action": "action2"}'),
                    ToolCall(id="c3", type="function", name="multi_action", arguments='{"action": "action3"}'),
                ],
                usage_metadata=UsageMetadata(model_name="mock"),
            ),
            create_text_response("All actions completed"),
        ])

        with patch("openjiuwen.core.foundation.llm.model.Model.stream", side_effect=mock_llm.stream), \
             patch("openjiuwen.core.foundation.llm.model.Model.invoke", side_effect=mock_llm.invoke):
            result1 = await Runner.run_agent(
                agent=agent,
                inputs={"query": "Please execute action1, action2, and action3 simultaneously",
                        "conversation_id": "497"},
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
async def test_hitl_rail_confirm_one_auto_pass_others():
    """Confirm one tool with auto_confirm, other same-name tools auto-pass

    Flow: 3 concurrent read calls intercepted -> confirm first with auto_confirm -> others auto-pass -> complete
    """
    os.environ.setdefault("LLM_SSL_VERIFY", "false")
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

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            AssistantMessage(
                content="",
                tool_calls=[
                    ToolCall(id="c1", type="function", name="read", arguments='{"filepath": "/tmp/file1.txt"}'),
                    ToolCall(id="c2", type="function", name="read", arguments='{"filepath": "/tmp/file2.txt"}'),
                    ToolCall(id="c3", type="function", name="read", arguments='{"filepath": "/tmp/file3.txt"}'),
                ],
                usage_metadata=UsageMetadata(model_name="mock", finish_reason="tool_calls"),
            ),
            create_text_response("All files read"),
        ])

        with patch("openjiuwen.core.foundation.llm.model.Model.stream", side_effect=mock_llm.stream), \
             patch("openjiuwen.core.foundation.llm.model.Model.invoke", side_effect=mock_llm.invoke):
            result1 = await Runner.run_agent(
                agent=agent,
                inputs={"query": "Please read /tmp/file1.txt, /tmp/file2.txt, and /tmp/file3.txt simultaneously",
                        "conversation_id": "497"},
            )

            assert result1.get("result_type") == "interrupt"
            interrupt_ids = result1.get("interrupt_ids", [])
            assert len(interrupt_ids) == 3, f"Expected 3 interrupts, got {len(interrupt_ids)}"

            first_tool_call_id = interrupt_ids[0]
            interactive_input = confirm_interrupt(first_tool_call_id, auto_confirm=True)

            result2 = await Runner.run_agent(
                agent=agent,
                inputs={"query": interactive_input, "conversation_id": "497"},
            )

            assert_answer_result(result2)
            assert read_tool.invoke_count == 3, f"Expected read invoke_count=3, got {read_tool.invoke_count}"
    finally:
        await Runner.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
