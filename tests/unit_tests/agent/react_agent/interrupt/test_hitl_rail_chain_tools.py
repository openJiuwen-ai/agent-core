# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import os
from unittest.mock import patch

import pytest

from openjiuwen.core.runner import Runner
from tests.unit_tests.agent.react_agent.interrupt.test_base import (
    assert_answer_result,
    assert_interrupt_result,
    confirm_interrupt,
    create_simple_agent,
    get_tool_name_from_state,
    reject_interrupt,
)
from tests.unit_tests.fixtures.mock_llm import (
    MockLLMModel,
    create_text_response,
    create_tool_call_response,
)


@pytest.mark.asyncio
async def test_hitl_rail_chain_tools():
    """Multi-tool chain: read -> confirm -> write -> reject

    Flow: read intercepted -> confirm -> write intercepted -> reject -> complete
    """
    os.environ.setdefault("LLM_SSL_VERIFY", "false")
    await Runner.start()
    try:
        agent, session, read_tool, write_tool, trace_rail = await create_simple_agent(
            session_id_prefix="rail_chain",
            system_prompt="You are an assistant. When the user requests to read and modify a file, "
                          "first call the read tool to read the file, then call the write tool to modify the file. "
                          "if user reject, stop tool call.",
            rail_tool_names=["read", "write"],
            with_write_tool=True,
        )

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response("read", '{"filepath": "/tmp/test.txt"}'),
            create_tool_call_response("write", '{"filepath": "/tmp/test.txt", "content": '
                                               '"new content"}'),
            create_text_response("Operation completed"),
        ])

        with patch("openjiuwen.core.foundation.llm.model.Model.stream", side_effect=mock_llm.stream), \
                patch("openjiuwen.core.foundation.llm.model.Model.invoke", side_effect=mock_llm.invoke):
            result = await Runner.run_agent(
                agent=agent,
                inputs={"query": "Please read the /tmp/test.txt file, then modify it", "conversation_id": "492"},
            )
            interrupt_ids, state_list = assert_interrupt_result(result, expected_count=1)

            tool_call_id = interrupt_ids[0]
            tool_name = get_tool_name_from_state(state_list[0])
            assert tool_name == "read", f"Expected tool_name 'read', got '{tool_name}'"

            result2 = await Runner.run_agent(
                agent=agent,
                inputs={"query": confirm_interrupt(tool_call_id), "conversation_id": "492"},
            )
            interrupt_ids2, state_list2 = assert_interrupt_result(result2, expected_count=1)

            tool_call_id2 = interrupt_ids2[0]
            tool_name2 = get_tool_name_from_state(state_list2[0])
            assert tool_name2 == "write", f"Expected tool_name 'write', got '{tool_name2}'"

            assert read_tool.invoke_count == 1, f"Expected read invoke_count=1, got {read_tool.invoke_count}"

            result3 = await Runner.run_agent(
                agent=agent,
                inputs={"query": reject_interrupt(tool_call_id2, "Reject write operation"), "conversation_id":
                    "492"},
            )

            current_result = result3
            max_iterations = 2
            iteration = 0
            while current_result.get("result_type") == "interrupt" and iteration < max_iterations:
                interrupt_ids = current_result.get("interrupt_ids", [])
                if len(interrupt_ids) == 0:
                    break
                current_result = await Runner.run_agent(
                    agent=agent,
                    inputs={"query": reject_interrupt(interrupt_ids[0], "Reject"), "conversation_id": "492"},
                )
                iteration += 1

            assert_answer_result(current_result)
            assert read_tool.invoke_count == 1, f"Expected read invoke_count=1, got {read_tool.invoke_count}"
            assert write_tool.invoke_count == 0, f"Expected write invoke_count=0, got {write_tool.invoke_count}"
    finally:
        await Runner.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
