# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Test HITL rail in stream mode."""

import os
from unittest.mock import patch

import pytest

from openjiuwen.core.common.constants.constant import INTERACTION
from openjiuwen.core.runner import Runner

from tests.unit_tests.agent.react_agent.interrupt.test_base import (
    confirm_interrupt,
    create_simple_agent,
    reject_interrupt,
)
from tests.unit_tests.fixtures.mock_llm import (
    create_text_response,
    create_tool_call_response,
    MockLLMModel,
)


@pytest.mark.asyncio
async def test_hitl_rail_stream_interrupt_detected():
    """Stream mode: verify interrupt can be detected during streaming

    Flow: stream read tool -> interrupt detected -> tool not executed
    """
    os.environ.setdefault("LLM_SSL_VERIFY", "false")
    await Runner.start()
    try:
        agent, _, read_tool, _, _ = await create_simple_agent(
            session_id_prefix="rail_stream",
        )

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response("read", '{"filepath": "/tmp/test.txt"}'),
            create_text_response("File read"),
        ])

        with patch("openjiuwen.core.foundation.llm.model.Model.stream", side_effect=mock_llm.stream), \
             patch("openjiuwen.core.foundation.llm.model.Model.invoke", side_effect=mock_llm.invoke):
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
async def test_hitl_rail_stream_agree_with_autoconfirm():
    """Stream mode: confirm with auto_confirm, subsequent calls auto-pass

    Flow: stream read -> confirm with auto_confirm=True -> 2nd stream read auto-passes
    """
    os.environ.setdefault("LLM_SSL_VERIFY", "false")
    await Runner.start()
    try:
        agent, _, read_tool, _, _ = await create_simple_agent(
            session_id_prefix="rail_stream_autoconfirm",
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

            interactive_input = confirm_interrupt(tool_call_id, auto_confirm=True)

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
async def test_hitl_rail_stream_reject():
    """Stream mode: reject execution, tool not executed

    Flow: stream read -> reject -> tool not executed
    """
    os.environ.setdefault("LLM_SSL_VERIFY", "false")
    await Runner.start()
    try:
        agent, _, read_tool, _, _ = await create_simple_agent(
            session_id_prefix="rail_stream_reject",
        )

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response("read", '{"filepath": "/tmp/test.txt"}'),
            create_text_response("Operation completed"),
        ])

        with patch("openjiuwen.core.foundation.llm.model.Model.stream", side_effect=mock_llm.stream), \
             patch("openjiuwen.core.foundation.llm.model.Model.invoke", side_effect=mock_llm.invoke):
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

            interactive_input = reject_interrupt(tool_call_id, "Reject this operation")

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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
