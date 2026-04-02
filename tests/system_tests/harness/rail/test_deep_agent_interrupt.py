# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""DeepAgent interrupt/resume system tests (mock LLM).

Verifies that DeepAgent with enable_task_loop=True correctly handles
InteractiveInput for interrupt resume, bypassing the outer task loop
and delegating to the inner ReActAgent's resume path.
"""
from __future__ import annotations

import json
import os
import unittest
from typing import Any, List, cast

import pytest

from openjiuwen.core.common.constants.constant import INTERACTION
from openjiuwen.core.runner import Runner
from openjiuwen.core.session import InteractiveInput
from openjiuwen.harness import create_deep_agent
from openjiuwen.harness.rails import ConfirmInterruptRail
from tests.system_tests.agent.react_agent.interrupt.test_base import (
    ReadTool,
    WriteTool,
)
from tests.unit_tests.fixtures.mock_llm import (
    MockLLMModel,
    create_text_response,
    create_tool_call_response,
)

os.environ.setdefault("LLM_SSL_VERIFY", "false")
os.environ.setdefault("IS_SENSITIVE", "false")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _MockRuntimeModel:
    """Wrap MockLLMModel into the Model contract expected by DeepAgent."""

    def __init__(self, client: MockLLMModel) -> None:
        self.client = client
        self.model_client_config = client.model_client_config
        self.model_config = client.model_config

    async def invoke(self, *args: Any, **kwargs: Any) -> Any:
        return await self.client.invoke(*args, **kwargs)

    async def stream(self, *args: Any, **kwargs: Any) -> Any:
        async for chunk in self.client.stream(*args, **kwargs):
            yield chunk


def _build_mock_model(mock_llm: MockLLMModel) -> Any:
    return cast(Any, _MockRuntimeModel(mock_llm))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDeepAgentInterrupt(unittest.IsolatedAsyncioTestCase):
    """DeepAgent interrupt/resume — system-level tests."""

    async def asyncSetUp(self) -> None:
        await Runner.start()

    async def asyncTearDown(self) -> None:
        await Runner.stop()

    @pytest.mark.asyncio
    async def test_deepagent_stream_interrupt_resume(self) -> None:
        """Task-loop mode: stream interrupt then resume with InteractiveInput.

        Flow:
          1. First stream — LLM calls "write" tool → ConfirmInterruptRail
             fires → stream emits INTERACTION chunk, tool NOT executed.
          2. Second stream — caller sends InteractiveInput(approved=True)
             → DeepAgent bypasses task loop, delegates to inner
             ReActAgent resume → tool executed, no further interrupt.
        """
        write_args = json.dumps({
            "filepath": "/tmp/test.txt",
            "content": "hello world",
        })

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            # Round 1: LLM decides to call "write" tool → triggers interrupt
            create_tool_call_response("write", write_args),
            # Round 2 (after resume): LLM produces final answer
            create_text_response("文件已写入完成。"),
        ])
        model = _build_mock_model(mock_llm)

        read_tool = ReadTool()
        write_tool = WriteTool()

        agent = create_deep_agent(
            model=model,
            enable_task_loop=True,
            max_iterations=5,
            tools=[read_tool, write_tool],
            rails=[ConfirmInterruptRail(tool_names=["write"])],
            restrict_to_work_dir=False,
        )

        # --- Round 1: expect interrupt on "write" ---
        outputs1: List[Any] = []
        interrupt_detected = False
        tool_call_id = None

        async for output in Runner.run_agent_streaming(
            agent=agent,
            inputs={
                "query": "请写入文件 test.txt 内容为 hello world",
                "conversation_id": "test_resume_1",
            },
        ):
            outputs1.append(output)
            if output.type == INTERACTION:
                interrupt_detected = True
                tool_call_id = output.payload.id

        self.assertTrue(
            interrupt_detected,
            "Should detect interrupt on write tool",
        )
        self.assertIsNotNone(
            tool_call_id,
            "Should get tool_call_id from INTERACTION output",
        )
        self.assertEqual(
            write_tool.invoke_count,
            0,
            "Write tool should NOT be invoked before confirmation",
        )

        # --- Round 2: resume with InteractiveInput ---
        assert tool_call_id is not None
        interactive_input = InteractiveInput()
        interactive_input.update(tool_call_id, {
            "approved": True,
            "feedback": "Confirm",
            "auto_confirm": False,
        })

        outputs2: List[Any] = []
        second_interrupt_detected = False

        async for output in Runner.run_agent_streaming(
            agent=agent,
            inputs={
                "query": interactive_input,
                "conversation_id": "test_resume_1",
            },
        ):
            outputs2.append(output)
            if output.type == INTERACTION:
                second_interrupt_detected = True

        self.assertFalse(
            second_interrupt_detected,
            "Should not interrupt after confirm",
        )
        self.assertEqual(
            write_tool.invoke_count,
            1,
            f"Expected write invoke_count=1, "
            f"got {write_tool.invoke_count}",
        )


if __name__ == "__main__":
    unittest.main()
