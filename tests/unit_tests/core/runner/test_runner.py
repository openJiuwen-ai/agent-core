#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import pytest

from openjiuwen.core.common.constants.enums import ControllerType
from openjiuwen.core.single_agent.legacy import WorkflowAgentConfig, WorkflowSchema
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.session.agent import AgentSession
from openjiuwen.core.session import Config
from openjiuwen.core.session import TaskSession
from openjiuwen.core.foundation.tool import tool, ToolCard
from openjiuwen.core.workflow import Workflow, WorkflowCard, WorkflowOutput, WorkflowExecutionState
from openjiuwen.core.runner import Runner
from tests.unit_tests.core.workflow.mock_nodes import MockEndNode, Node1, MockStartNode


@pytest.fixture(scope="class")
def session():
    workflow_id = "test_workflow"
    name = "test_workflow"
    version = "1"
    description = "test_workflow"
    test_workflow_schema = WorkflowSchema(
        id=workflow_id,
        version=version,
        name=name,
        description=description,
        inputs={"query": {
            "type": "string",
        }},
    )
    workflow_config = WorkflowAgentConfig(
        workflows=[test_workflow_schema],
        tools=["add"],
        controller_type=ControllerType.WorkflowController
    )
    config = Config()
    config.set_agent_config(agent_config=workflow_config)
    session_id = "session_id"
    return TaskSession(None, AgentSession(session_id, config=config))

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
        result = await Runner.run_workflow(workflow, inputs = {"query": "query workflow"}, session=session)
        assert result == WorkflowOutput(result={"result": "query workflow"}, state=WorkflowExecutionState.COMPLETED)

    async def test_run_tool(self, session):
        result = await self.add_function.invoke(inputs={"a": 1, "b": 2})
        assert result == 3

    async def test_run_workflow_not_bound(self, session):
        workflow_id = "test_workflow_not_bound"
        name = "test_workflow"
        version = "1"
        workflow = self._build_workflow(name, workflow_id, version)
        with pytest.raises(JiuWenBaseException) as exc_info:
            await Runner.run_workflow(workflow, inputs={"query": "query workflow"}, session=session)
        assert exc_info.value.error_code == StatusCode.WORKFLOW_NOT_BOUND_TO_AGENT.code
        assert exc_info.value.message == StatusCode.WORKFLOW_NOT_BOUND_TO_AGENT.errmsg
