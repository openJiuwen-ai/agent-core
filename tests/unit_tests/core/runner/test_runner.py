#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import pytest

from openjiuwen.core.session.workflow import create_workflow_session
from openjiuwen.core.single_agent import create_agent_session
from openjiuwen.core.foundation.tool import tool, ToolCard
from openjiuwen.core.workflow import Workflow, WorkflowCard, WorkflowOutput, WorkflowExecutionState
from openjiuwen.core.runner import Runner
from tests.unit_tests.core.workflow.mock_nodes import MockEndNode, Node1, MockStartNode


@pytest.fixture(scope="class")
def session():
    session_id = "session_id"
    return create_agent_session(session_id=session_id)


@pytest.mark.asyncio
class TestRunner:
    @staticmethod
    def _build_workflow(name, workflow_id, version):
        workflow_card = WorkflowCard(
            id=workflow_id,
            version=version,
            name=name,

        )
        flow = Workflow(card=workflow_card)
        flow.set_start_comp("start", MockStartNode("start"),
                            inputs_schema={
                                "query": "${query}"})
        flow.add_workflow_comp("node_a", Node1("node_a"),
                               inputs_schema={
                                   "output": "${start.query}"})
        flow.set_end_comp("end", MockEndNode("end"),
                          inputs_schema={
                              "result": "${node_a.output}"})
        flow.add_connection("start", "node_a")
        flow.add_connection("node_a", "end")
        return flow

    @staticmethod
    @tool(
        card=ToolCard(
            name="add",
            description="加法",
            input_params={
                "type": "object",
                "properties": {
                    "a": {"description": "加数", "type": "number"},
                    "b": {"description": "被加数", "type": "number"},
                },
                "required": ["a", "b"],
            },
        )
    )
    def add_function(a, b):
        """加法函数，使用tool注解装饰"""
        return a + b

    @staticmethod
    @tool(
        card=ToolCard(
            name="multiply",
            description="乘法",
            input_params={
                "type": "object",
                "properties": {
                    "a": {"description": "乘数", "type": "number"},
                    "b": {"description": "被乘数", "type": "number"},
                },
                "required": ["a", "b"],
            },
        )
    )
    def multiply_function(a, b):
        """乘法函数，使用tool注解装饰"""
        return a * b

    async def test_run_workflow(self, session):
        workflow_id = "test_workflow"
        name = "test_workflow"
        version = "1"
        workflow = self._build_workflow(name, workflow_id, version)
        Runner.resource_mgr.add_workflow(workflow.card, lambda: workflow)
        session = create_workflow_session()
        result = await Runner.run_workflow(workflow_id, inputs={"query": "query workflow"}, session=session)
        assert result == WorkflowOutput(result={"result": "query workflow"}, state=WorkflowExecutionState.COMPLETED)

    async def test_run_tool(self, session):
        result = await self.add_function.invoke(inputs={"a": 1, "b": 2})
        assert result == 3
