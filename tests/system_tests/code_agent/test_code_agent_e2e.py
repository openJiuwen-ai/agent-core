# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""CodeAgent 端到端系统测试。"""
from __future__ import annotations

import unittest
import uuid

import pytest

from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import (
    LocalWorkConfig,
    OperationMode,
    SysOperationCard,
)
from openjiuwen.harness.rails.task_planning_rail import TaskPlanningRail
from openjiuwen.harness.subagents.code_agent import create_code_agent
from tests.unit_tests.fixtures.mock_llm import (
    MockLLMModel,
    create_text_response,
    create_tool_call_response,
)
from tests.system_tests.harness.test_deep_agent_e2e import _build_mock_runtime_model


class TestCodeAgentE2E(unittest.IsolatedAsyncioTestCase):
    """CodeAgent 端到端测试。"""

    async def asyncSetUp(self):
        await Runner.start()
        self._sys_operation_id = f"codeagent_sysop_{uuid.uuid4().hex}"
        card = SysOperationCard(
            id=self._sys_operation_id,
            mode=OperationMode.LOCAL,
            work_config=LocalWorkConfig(work_dir=""),
        )
        add_result = Runner.resource_mgr.add_sys_operation(card)
        if add_result.is_err():
            raise RuntimeError(f"add_sys_operation failed: {add_result.msg()}")

    async def asyncTearDown(self):
        try:
            Runner.resource_mgr.remove_sys_operation(sys_operation_id=self._sys_operation_id)
        finally:
            await Runner.stop()

    @pytest.mark.asyncio
    async def test_code_agent_normal_e2e(self):
        """测试 CodeAgent 正常流程，使用 TaskPlanningRail。"""
        sys_oper = Runner.resource_mgr.get_sys_operation(self._sys_operation_id)
        task_planning_rail = TaskPlanningRail()
        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response(
                "todo_create",
                '{"tasks": "设计模块架构;实现核心功能;编写单元测试;集成测试"}'
            ),
            create_tool_call_response(
                "todo_list",
                '{}'
            ),
            create_tool_call_response(
                "todo_modify",
                '{"action": "update", "todos": [{"id": "mock_task_id_1", "status": "completed"}]}'
            ),
            create_text_response("我已经完成了模块架构设计和核心功能的开发规划。")
        ])

        agent = create_code_agent(
            model=_build_mock_runtime_model(mock_llm),
            rails=[task_planning_rail],
            enable_task_loop=False,
            max_iterations=20,
            sys_operation=sys_oper,
        )

        query = "帮我规划一个简单的模块开发任务"

        result = await Runner.run_agent(agent, {"query": query})

        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("result_type"), "answer")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])