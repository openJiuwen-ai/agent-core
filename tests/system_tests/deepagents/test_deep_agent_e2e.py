# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""DeepAgent 端到端系统测试（真实 LLM + sys_operation 文件工具）。"""
from __future__ import annotations

import os
import tempfile
import unittest
import uuid
from collections import Counter
from pathlib import Path
from typing import List
from unittest.mock import patch

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
from openjiuwen.core.sys_operation import (
    LocalWorkConfig,
    OperationMode,
    SysOperationCard,
)
from openjiuwen.deepagents import create_deep_agent
from openjiuwen.deepagents.rails import TaskPlanningRail
from tests.unit_tests.fixtures.mock_llm import (
    MockLLMModel,
    create_text_response,
    create_tool_call_response,
)
from openjiuwen.deepagents.rails.filesystem_rail import FileSystemRail

API_BASE = os.getenv("API_BASE", "your api url")
API_KEY = os.getenv("API_KEY", "your api key")
MODEL_NAME = os.getenv("MODEL_NAME", "model name")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "SiliconFlow")
os.environ.setdefault("LLM_SSL_VERIFY", "false")


class ToolTraceRail(AgentRail):
    """记录工具调用顺序，供测试断言。"""

    def __init__(self):
        super().__init__()
        self.tool_calls: List[str] = []

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        if isinstance(ctx.inputs, ToolCallInputs) and ctx.inputs.tool_name:
            self.tool_calls.append(ctx.inputs.tool_name)


class TestDeepAgentE2E(unittest.IsolatedAsyncioTestCase):
    """DeepAgent 真实端到端调用。"""

    async def asyncSetUp(self):
        await Runner.start()
        self._tmp_dir = tempfile.TemporaryDirectory(prefix="deepagent_e2e_")
        self._work_dir = self._tmp_dir.name
        self._sys_operation_id = f"deepagent_sysop_{uuid.uuid4().hex}"
        card = SysOperationCard(
            id=self._sys_operation_id,
            mode=OperationMode.LOCAL,
            work_config=LocalWorkConfig(work_dir=self._work_dir),
        )
        add_result = Runner.resource_mgr.add_sys_operation(card)
        if add_result.is_err():
            raise RuntimeError(f"add_sys_operation failed: {add_result.msg()}")

    async def asyncTearDown(self):
        try:
            Runner.resource_mgr.remove_sys_operation(sys_operation_id=self._sys_operation_id)
        finally:
            self._tmp_dir.cleanup()
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
                "DeepAgent E2E requires API_KEY and API_BASE in environment. "
                "Set them before running tests."
            )

    def _get_fs_rail(self):
        sys_oper = Runner.resource_mgr.get_sys_operation(self._sys_operation_id)
        return FileSystemRail(operation=sys_oper)

    @pytest.mark.asyncio
    @unittest.skip("skip system test")
    async def test_deep_agent_invoke_e2e_require_api_key_base(self):
        """验证 DeepAgent 在真实模型下可端到端返回 answer。"""
        self._require_llm_config()

        model = self._create_model()
        agent = create_deep_agent(
            model=model,
            system_prompt="你是一个智能助手，请简洁回答。",
            enable_task_loop=False,
            max_iterations=5,
        )

        session = create_agent_session(
            session_id=f"deepagent_e2e_{uuid.uuid4().hex}"
        )
        result = await agent.invoke(
            {"query": "请用一句话介绍你自己"},
            session=session,
        )

        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("result_type"), "answer")
        self.assertIn("output", result)
        self.assertTrue(bool(result["output"]))

    @pytest.mark.asyncio
    @unittest.skip("skip system test")
    async def test_deep_agent_complex_task_multi_tool_chain(self):
        """复杂任务：连续调用 fs 工具完成写入、列举、读取。"""
        self._require_llm_config()

        tool_trace = ToolTraceRail()
        fs_rail = self._get_fs_rail()
        model = self._create_model()
        agent = create_deep_agent(
            model=model,
            system_prompt=(
                "你是一个严谨的任务执行助手。"
                "当用户要求用工具处理文件时，必须调用工具，不要凭空假设。"
            ),
            rails=[tool_trace, fs_rail],
            enable_task_loop=False,
            max_iterations=12,
        )
        session = create_agent_session(
            session_id=f"deepagent_complex_e2e_{uuid.uuid4().hex}"
        )

        query = (
            "请严格按顺序执行以下任务，并且每一步都必须调用工具：\n"
            "1. 写入 todo_alpha.txt，内容为三行：准备数据、实现功能、验证结果；\n"
            "2. 写入 todo_beta.txt，内容为两行：发布版本、回滚预案；\n"
            "3. 使用工具列出当前目录文件，确认上面两个文件存在；\n"
            "4. 使用工具读取这两个文件；\n"
            "5. 最后输出一句中文总结。"
        )
        result = await agent.invoke({"query": query}, session=session)
        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("result_type"), "answer")
        self.assertIn("output", result)
        self.assertTrue(bool(result["output"]))

        tool_counts = Counter(tool_trace.tool_calls)
        self.assertGreaterEqual(tool_counts.get("write_file", 0), 2)
        self.assertGreaterEqual(tool_counts.get("list_files", 0), 1)
        self.assertGreaterEqual(tool_counts.get("read_file", 0), 1)
        self.assertGreaterEqual(sum(tool_counts.values()), 4)

        alpha_path = Path(self._work_dir) / "todo_alpha.txt"
        beta_path = Path(self._work_dir) / "todo_beta.txt"
        self.assertTrue(alpha_path.exists())
        self.assertTrue(beta_path.exists())
        self.assertTrue(alpha_path.read_text(encoding="utf-8").strip())
        self.assertTrue(beta_path.read_text(encoding="utf-8").strip())

    @pytest.mark.asyncio
    async def test_deep_agent_task_planning(self):
        """复杂任务：agent的规划能力"""
        sys_oper = Runner.resource_mgr.get_sys_operation(self._sys_operation_id)
        task_planning = TaskPlanningRail(sys_oper)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response(
                "todo_write",
                '{"tasks": "设计打卡系统数据库表结构;实现用户打卡功能接口;开发前端打卡页面;添加打卡统计功能"}'
            ),
            create_tool_call_response(
                "todo_read",
                '{}'
            ),
            create_tool_call_response(
                "todo_modify",
                '{"action": "update", "todos": [{"id": "mock_task_id_1", "status": "completed"}]}'
            ),
            create_text_response("我已经帮你完成了打卡系统的任务规划，并完成了第一个任务的设计工作。")
        ])

        agent = create_deep_agent(
            model=self._create_model(),
            rails=[task_planning],
            enable_task_loop=False,
            max_iterations=20
        )
        session = create_agent_session(
            session_id=f"deepagent_complex_e_{uuid.uuid4().hex}"
        )

        query = "我想测试任务规划能力，帮我构建一个打卡系统，调用规划工具帮我模拟规划吧"

        with patch.object(agent._react_agent, '_get_llm', return_value=mock_llm):
            result = await agent.invoke({"query": query}, session=session)

        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("result_type"), "answer")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
