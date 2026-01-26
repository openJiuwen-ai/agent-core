# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
测试 WorkflowAgent 基本功能

本测试用例验证 WorkflowAgent 的基本执行流程。
"""
import unittest

import pytest

from openjiuwen.core.single_agent.legacy import WorkflowAgentConfig
from openjiuwen.core.application.workflow_agent import (
    WorkflowAgent
)
from openjiuwen.core.workflow import WorkflowCard
from openjiuwen.core.workflow import Workflow
from openjiuwen.core.runner import Runner
from tests.unit_tests.core.workflow.mock_nodes import (
    MockStartNode,
    Node1,
    MockEndNode
)


class TestWorkflowAgent(unittest.IsolatedAsyncioTestCase):
    """测试 WorkflowAgent 基本功能"""

    async def asyncSetUp(self):
        """测试前准备"""
        await Runner.start()

    async def asyncTearDown(self):
        """测试后清理"""
        await Runner.stop()

    @staticmethod
    def _build_workflow(name, workflow_id, version):
        workflow_card = WorkflowCard(
            id=workflow_id,
            version=version,
            name=name,
        )
        flow = Workflow(card=workflow_card)
        flow.set_start_comp(
            "start",
            MockStartNode("start"),
            inputs_schema={"query": "${query}"}
        )
        flow.add_workflow_comp(
            "node_a",
            Node1("node_a"),
            inputs_schema={"output": "${start.query}"}
        )
        flow.set_end_comp(
            "end",
            MockEndNode("end"),
            inputs_schema={"result": "${node_a.output}"}
        )
        flow.add_connection("start", "node_a")
        flow.add_connection("node_a", "end")
        return flow

    @pytest.mark.asyncio
    async def test_invoke_single(self):
        """测试单次调用 WorkflowAgent"""
        workflow_id = "test_workflow"
        name = "test_workflow"
        version = "1"
        description = "test_workflow"

        workflow = self._build_workflow(name, workflow_id, version)

        # 使用新 API: add_workflows() 自动从 workflow.card 提取 schema
        workflow_config = WorkflowAgentConfig(
            id="test_workflow_agent",
            version="0.1.0",
            description=description,
            workflows=[],
        )
        agent = WorkflowAgent(workflow_config)
        agent.add_workflows([workflow])

        inputs = {"query": "hi"}
        result = await agent.invoke(inputs)

        # 验证返回结果
        self.assertEqual(result['result_type'], 'answer')
        self.assertEqual(result['output'].result, {'result': 'hi'})
        self.assertEqual(result['output'].state.name, 'COMPLETED')


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
