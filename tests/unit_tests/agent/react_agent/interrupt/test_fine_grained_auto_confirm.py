# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Test fine-grained auto-confirm feature."""

import json
import os
from typing import Optional
from unittest.mock import patch

import pytest

from openjiuwen.core.foundation.llm import AssistantMessage, UsageMetadata, ToolCall
from openjiuwen.core.runner import Runner
from openjiuwen.core.session import InteractiveInput
from openjiuwen.deepagents.rails.interrupt.confirm_rail import ConfirmInterruptRail

from tests.unit_tests.agent.react_agent.interrupt.test_base import (
    AgentWithToolsConfig,
    assert_answer_result,
    assert_interrupt_result,
    confirm_interrupt,
    create_agent_with_tools,
    ReadTool,
    WriteTool,
)
from tests.unit_tests.fixtures.mock_llm import (
    create_text_response,
    create_tool_call_response,
    MockLLMModel,
)


class FineGrainedConfirmRail(ConfirmInterruptRail):
    """Fine-grained confirmation Rail - uses keys based on tool name and arguments.

    Example:
        - read a.txt -> auto_confirm_key = "read_a"
        - read b.txt -> auto_confirm_key = "read_b"
        - write c.txt -> auto_confirm_key = "write_c"
    """

    def _get_auto_confirm_key(self, tool_call) -> str:
        if tool_call is None:
            return ""
        args = json.loads(tool_call.arguments) if isinstance(tool_call.arguments, str) else tool_call.arguments
        if tool_call.name == "read":
            filepath = args.get("filepath", "")
            if filepath:
                filename = os.path.basename(filepath)
                name_without_ext = filename.rsplit('.', 1)[0] if '.' in filename else filename
                return f"read_{name_without_ext}"
            return tool_call.name
        elif tool_call.name == "write":
            filepath = args.get("filepath", "")
            if filepath:
                filename = os.path.basename(filepath)
                name_without_ext = filename.rsplit('.', 1)[0] if '.' in filename else filename
                return f"write_{name_without_ext}"
            return tool_call.name
        return tool_call.name


@pytest.mark.asyncio
async def test_fine_grained_auto_confirm_single_agent():
    """Fine-grained auto-confirm: confirm read a.txt, read b.txt should still interrupt

    Flow:
        1. read a.txt -> interrupt -> confirm with auto_confirm=True (key: read_a)
        2. read a.txt -> auto-pass (key matches)
        3. read b.txt -> interrupt (key: read_b does not match)
    """
    os.environ.setdefault("LLM_SSL_VERIFY", "false")
    await Runner.start()
    try:
        read_tool = ReadTool()
        agent, _, trace_rail = await create_agent_with_tools(
            AgentWithToolsConfig(
                tools=[read_tool],
                session_id_prefix="fg_single",
                system_prompt="You are an assistant. When the user requests to read a file, "
                              "you must call the read tool with the exact filepath provided.",
                rail_tool_names=[],
                trace_tool_names=["read"],
            )
        )

        fine_grained_rail = FineGrainedConfirmRail(tool_names=["read"])
        await agent.register_rail(fine_grained_rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response("read", '{"filepath": "/tmp/a.txt"}'),
            create_text_response("File a.txt read"),
            create_tool_call_response("read", '{"filepath": "/tmp/a.txt"}'),
            create_text_response("File a.txt read again"),
            create_tool_call_response("read", '{"filepath": "/tmp/b.txt"}'),
            create_text_response("File b.txt read"),
        ])

        with patch("openjiuwen.core.foundation.llm.model.Model.stream", side_effect=mock_llm.stream), \
             patch("openjiuwen.core.foundation.llm.model.Model.invoke", side_effect=mock_llm.invoke):
            result1 = await Runner.run_agent(
                agent=agent,
                inputs={"query": "Please read /tmp/a.txt", "conversation_id": "fg_single"},
            )

            if result1.get("result_type") != "interrupt":
                pytest.skip("LLM did not call tool, skipping test")

            interrupt_ids, _ = assert_interrupt_result(result1, expected_count=1)
            tool_call_id = interrupt_ids[0]

            interactive_input = confirm_interrupt(tool_call_id, auto_confirm=True)

            result2 = await Runner.run_agent(
                agent=agent,
                inputs={"query": interactive_input, "conversation_id": "fg_single"},
            )
            assert_answer_result(result2)
            assert read_tool.invoke_count == 1

            result3 = await Runner.run_agent(
                agent=agent,
                inputs={"query": "Please read /tmp/a.txt again", "conversation_id": "fg_single"},
            )
            assert_answer_result(result3)
            assert read_tool.invoke_count == 2

            result4 = await Runner.run_agent(
                agent=agent,
                inputs={"query": "Please read /tmp/b.txt", "conversation_id": "fg_single"},
            )
            assert result4.get("result_type") == "interrupt", \
                f"Expected interrupt for b.txt (different key), got {result4.get('result_type')}"

    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_fine_grained_auto_confirm_merge_keys():
    """Verify auto_confirm records are merged and appended, not overwritten.

    Flow:
        1. read a.txt -> interrupt -> confirm with auto_confirm=True (key: read_a)
        2. read b.txt -> interrupt -> confirm with auto_confirm=True (key: read_b)
        3. read a.txt -> auto-pass (key: read_a still valid)
        4. read b.txt -> auto-pass (key: read_b still valid)
    """
    os.environ.setdefault("LLM_SSL_VERIFY", "false")
    await Runner.start()
    try:
        read_tool = ReadTool()
        agent, _, trace_rail = await create_agent_with_tools(
            AgentWithToolsConfig(
                tools=[read_tool],
                session_id_prefix="fg_merge",
                system_prompt="You are an assistant. When the user requests to read a file, "
                              "you must call the read tool.",
                rail_tool_names=[],
                trace_tool_names=["read"],
            )
        )

        fine_grained_rail = FineGrainedConfirmRail(tool_names=["read"])
        await agent.register_rail(fine_grained_rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response("read", '{"filepath": "/tmp/a.txt"}'),
            create_text_response("File a.txt read"),
            create_tool_call_response("read", '{"filepath": "/tmp/b.txt"}'),
            create_text_response("File b.txt read"),
            create_tool_call_response("read", '{"filepath": "/tmp/a.txt"}'),
            create_text_response("File a.txt read again"),
            create_tool_call_response("read", '{"filepath": "/tmp/b.txt"}'),
            create_text_response("File b.txt read again"),
        ])

        with patch("openjiuwen.core.foundation.llm.model.Model.stream", side_effect=mock_llm.stream), \
             patch("openjiuwen.core.foundation.llm.model.Model.invoke", side_effect=mock_llm.invoke):
            result1 = await Runner.run_agent(
                agent=agent,
                inputs={"query": "Please read /tmp/a.txt", "conversation_id": "fg_merge"},
            )

            if result1.get("result_type") != "interrupt":
                pytest.skip("LLM did not call tool, skipping test")

            interrupt_ids1, _ = assert_interrupt_result(result1, expected_count=1)
            interactive_input1 = confirm_interrupt(interrupt_ids1[0], auto_confirm=True)

            result2 = await Runner.run_agent(
                agent=agent,
                inputs={"query": interactive_input1, "conversation_id": "fg_merge"},
            )
            assert_answer_result(result2)
            assert read_tool.invoke_count == 1

            result3 = await Runner.run_agent(
                agent=agent,
                inputs={"query": "Please read /tmp/b.txt", "conversation_id": "fg_merge"},
            )
            interrupt_ids2, _ = assert_interrupt_result(result3, expected_count=1)
            interactive_input2 = confirm_interrupt(interrupt_ids2[0], auto_confirm=True)

            result4 = await Runner.run_agent(
                agent=agent,
                inputs={"query": interactive_input2, "conversation_id": "fg_merge"},
            )
            assert_answer_result(result4)
            assert read_tool.invoke_count == 2

            result5 = await Runner.run_agent(
                agent=agent,
                inputs={"query": "Please read /tmp/a.txt again", "conversation_id": "fg_merge"},
            )
            assert_answer_result(result5)
            assert read_tool.invoke_count == 3

            result6 = await Runner.run_agent(
                agent=agent,
                inputs={"query": "Please read /tmp/b.txt again", "conversation_id": "fg_merge"},
            )
            assert_answer_result(result6)
            assert read_tool.invoke_count == 4

    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_fine_grained_auto_confirm_different_tools():
    """Verify auto_confirm keys for different tools are independent.

    Flow:
        1. read a.txt -> interrupt -> confirm with auto_confirm=True (key: read_a)
        2. write a.txt -> interrupt -> confirm with auto_confirm=True (key: write_a)
        3. read a.txt -> auto-pass (key: read_a matches)
        4. write a.txt -> auto-pass (key: write_a matches)
    """
    os.environ.setdefault("LLM_SSL_VERIFY", "false")
    await Runner.start()
    try:
        read_tool = ReadTool()
        write_tool = WriteTool()
        agent, _, trace_rail = await create_agent_with_tools(
            AgentWithToolsConfig(
                tools=[read_tool, write_tool],
                session_id_prefix="fg_diff_tools",
                system_prompt="You are an assistant. When the user requests to read or write a file, "
                              "you must call the corresponding tool.",
                rail_tool_names=[],
                trace_tool_names=["read", "write"],
            )
        )

        fine_grained_rail = FineGrainedConfirmRail(tool_names=["read", "write"])
        await agent.register_rail(fine_grained_rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response("read", '{"filepath": "/tmp/a.txt"}'),
            create_text_response("File a.txt read"),
            create_tool_call_response("write", '{"filepath": "/tmp/a.txt", "content": "hello"}'),
            create_text_response("File a.txt written"),
            create_tool_call_response("read", '{"filepath": "/tmp/a.txt"}'),
            create_text_response("File a.txt read again"),
            create_tool_call_response("write", '{"filepath": "/tmp/a.txt", "content": "world"}'),
            create_text_response("File a.txt written again"),
        ])

        with patch("openjiuwen.core.foundation.llm.model.Model.stream", side_effect=mock_llm.stream), \
             patch("openjiuwen.core.foundation.llm.model.Model.invoke", side_effect=mock_llm.invoke):
            result1 = await Runner.run_agent(
                agent=agent,
                inputs={"query": "Please read /tmp/a.txt", "conversation_id": "fg_diff_tools"},
            )

            if result1.get("result_type") != "interrupt":
                pytest.skip("LLM did not call tool, skipping test")

            interrupt_ids1, _ = assert_interrupt_result(result1, expected_count=1)
            interactive_input1 = confirm_interrupt(interrupt_ids1[0], auto_confirm=True)

            result2 = await Runner.run_agent(
                agent=agent,
                inputs={"query": interactive_input1, "conversation_id": "fg_diff_tools"},
            )
            assert_answer_result(result2)
            assert read_tool.invoke_count == 1

            result3 = await Runner.run_agent(
                agent=agent,
                inputs={"query": "Please write 'hello' to /tmp/a.txt", "conversation_id": "fg_diff_tools"},
            )
            interrupt_ids2, _ = assert_interrupt_result(result3, expected_count=1)
            interactive_input2 = confirm_interrupt(interrupt_ids2[0], auto_confirm=True)

            result4 = await Runner.run_agent(
                agent=agent,
                inputs={"query": interactive_input2, "conversation_id": "fg_diff_tools"},
            )
            assert_answer_result(result4)
            assert write_tool.invoke_count == 1

            result5 = await Runner.run_agent(
                agent=agent,
                inputs={"query": "Please read /tmp/a.txt again", "conversation_id": "fg_diff_tools"},
            )
            assert_answer_result(result5)
            assert read_tool.invoke_count == 2

            result6 = await Runner.run_agent(
                agent=agent,
                inputs={"query": "Please write 'world' to /tmp/a.txt", "conversation_id": "fg_diff_tools"},
            )
            assert_answer_result(result6)
            assert write_tool.invoke_count == 2

    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_fine_grained_auto_confirm_concurrent_tools():
    """Verify fine-grained auto-confirm with concurrent tool calls.

    Flow:
        1. read a.txt, read b.txt concurrently -> 2 interrupts
        2. Confirm read a.txt with auto_confirm=True (key: read_a)
        3. Confirm read b.txt with auto_confirm=False (key: read_b)
        4. read a.txt, read b.txt concurrently -> read_a auto-passes, read_b interrupts
    """
    os.environ.setdefault("LLM_SSL_VERIFY", "false")
    await Runner.start()
    try:
        read_tool = ReadTool()
        agent, _, trace_rail = await create_agent_with_tools(
            AgentWithToolsConfig(
                tools=[read_tool],
                session_id_prefix="fg_concurrent",
                system_prompt="You are an assistant. When asked to read multiple files, "
                              "call read tool concurrently for each file.",
                rail_tool_names=[],
                trace_tool_names=["read"],
            )
        )

        fine_grained_rail = FineGrainedConfirmRail(tool_names=["read"])
        await agent.register_rail(fine_grained_rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            AssistantMessage(
                content="",
                tool_calls=[
                    ToolCall(id="c1", type="function", name="read", arguments='{"filepath": "/tmp/a.txt"}'),
                    ToolCall(id="c2", type="function", name="read", arguments='{"filepath": "/tmp/b.txt"}'),
                ],
                usage_metadata=UsageMetadata(model_name="mock", finish_reason="tool_calls"),
            ),
            create_text_response("Files read"),
            AssistantMessage(
                content="",
                tool_calls=[
                    ToolCall(id="c3", type="function", name="read", arguments='{"filepath": "/tmp/a.txt"}'),
                    ToolCall(id="c4", type="function", name="read", arguments='{"filepath": "/tmp/b.txt"}'),
                ],
                usage_metadata=UsageMetadata(model_name="mock", finish_reason="tool_calls"),
            ),
            create_text_response("Files read again"),
        ])

        with patch("openjiuwen.core.foundation.llm.model.Model.stream", side_effect=mock_llm.stream), \
             patch("openjiuwen.core.foundation.llm.model.Model.invoke", side_effect=mock_llm.invoke):
            result1 = await Runner.run_agent(
                agent=agent,
                inputs={"query": "Please read /tmp/a.txt and /tmp/b.txt", "conversation_id": "fg_concurrent"},
            )

            if result1.get("result_type") != "interrupt":
                pytest.skip("LLM did not call tool, skipping test")

            interrupt_ids, _ = assert_interrupt_result(result1, expected_count=2)
            assert len(interrupt_ids) == 2

            interactive_input1 = InteractiveInput()
            interactive_input1.update(interrupt_ids[0], {
                "approved": True,
                "feedback": "Confirm reading a.txt",
                "auto_confirm": True
            })

            result2 = await Runner.run_agent(
                agent=agent,
                inputs={"query": interactive_input1, "conversation_id": "fg_concurrent"},
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
                agent=agent,
                inputs={"query": interactive_input2, "conversation_id": "fg_concurrent"},
            )
            assert_answer_result(result3)
            assert read_tool.invoke_count == 2

            result4 = await Runner.run_agent(
                agent=agent,
                inputs={"query": "Please read /tmp/a.txt and /tmp/b.txt again", "conversation_id": "fg_concurrent"},
            )

            assert result4.get("result_type") == "interrupt"
            final_interrupt_ids = result4.get("interrupt_ids", [])
            assert len(final_interrupt_ids) == 1, \
                f"Expected 1 interrupt (read_b), got {len(final_interrupt_ids)}"

    finally:
        await Runner.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
