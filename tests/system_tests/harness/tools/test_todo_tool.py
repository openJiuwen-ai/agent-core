# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""DeepAgent Todo工具端到端系统测试（真实 LLM + Todo 工具）。"""
from __future__ import annotations

import os
import unittest
import uuid
from collections import Counter
from typing import List

import pytest

from openjiuwen.core.foundation.llm import (
    Model,
    ModelClientConfig,
    ModelRequestConfig,
)
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent import create_agent_session
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentRail,
    ToolCallInputs,
)
from openjiuwen.harness import create_deep_agent
from tempfile import TemporaryDirectory
from openjiuwen.harness.tools.todo import TodoWriteTool

API_BASE = os.getenv("API_BASE", "")
API_KEY = os.getenv("API_KEY", "")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")
os.environ.setdefault("LLM_SSL_VERIFY", "false")


class ToolTraceRail(AgentRail):
    """记录工具调用顺序，供测试断言。"""
    def __init__(self):
        super().__init__()
        self.tool_calls: List[str] = []

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        if isinstance(ctx.inputs, ToolCallInputs) and ctx.inputs.tool_name:
            self.tool_calls.append(ctx.inputs.tool_name)


class TestDeepAgentTodoE2E(unittest.IsolatedAsyncioTestCase):
    """DeepAgent Todo工具真实端到端调用。"""

    async def asyncSetUp(self):
        await Runner.start()
        self._session_id = f"todo_e2e_{uuid.uuid4().hex}"

    async def asyncTearDown(self):
        await Runner.stop()

    @staticmethod
    def _create_model() -> Model:
        model_client_config = ModelClientConfig(
            client_provider=MODEL_PROVIDER,
            api_key=API_KEY,
            api_base=API_BASE,
            verify_ssl=False,
        )
        model_request_config = ModelRequestConfig(
            model=MODEL_NAME,
            temperature=0.2,
            top_p=0.9,
        )
        return Model(
            model_client_config=model_client_config,
            model_config=model_request_config,
        )

    def _require_llm_config(self):
        if not API_KEY or not API_BASE:
            self.fail(
                "DeepAgent Todo E2E requires API_KEY and API_BASE in environment. "
                "Set them before running tests."
            )

    def _get_todo_tools(self):
        workspace = self.enterContext(TemporaryDirectory())
        tool = TodoWriteTool(workspace)
        Runner.resource_mgr.add_tool(tool)
        return [tool.card]

    @pytest.mark.asyncio
    @unittest.skip("skip system test")
    async def test_deep_agent_todo_write(self):
        """测试Todo工具：创建、列表、修改。"""
        self._require_llm_config()

        tool_trace = ToolTraceRail()
        model = self._create_model()
        agent = create_deep_agent(
            model=model,
            system_prompt=(
                "你是一个严谨的任务执行助手。"
                "当用户要求使用todo工具时，必须调用工具，不要凭空假设。"
            ),
            tools=self._get_todo_tools(),
            rails=[tool_trace],
            enable_task_loop=False,
            max_iterations=10,
        )
        session = create_agent_session(session_id=self._session_id)

        query = (
            "用 todo_write 创建三条待办：完成需求分析、编写代码、测试验证。"
            "读取写入结果返回的 revision 和列表，再调用 todo_write 提交完整列表，"
            "将第一条设为 completed。最后输出一句中文总结。"
        )
        result = await agent.invoke({"query": query}, session=session)
        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("result_type"), "answer")
        self.assertIn("output", result)
        self.assertTrue(bool(result["output"]))

        tool_counts = Counter(tool_trace.tool_calls)
        self.assertEqual(set(tool_counts), {"todo_write"})
        self.assertGreaterEqual(tool_counts["todo_write"], 2)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])