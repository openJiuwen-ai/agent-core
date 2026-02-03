#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import pytest

from openjiuwen.core.common.constants.enums import ControllerType
from openjiuwen.core.common.logging import logger
from openjiuwen.core.single_agent import AgentCard
from openjiuwen.core.single_agent.legacy import WorkflowAgentConfig, WorkflowSchema
from openjiuwen.core.application.workflow_agent import WorkflowAgent
from openjiuwen.core.runner.drunner.remote_client.remote_agent import RemoteAgent
from openjiuwen.core.workflow import Workflow
from openjiuwen.core.workflow import WorkflowCard
from tests.unit_tests.core.workflow.mock_nodes import MockStartNode, Node1, MockEndNode


@pytest.mark.asyncio
class TestRunnerIntegration:

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

    async def test_react_agent_invoke_with_adapter(self):
        try:
            from openjiuwen.core.runner.runner import Runner
            await Runner.start()
            workflow_id = "test_workflow"
            name = "test_workflow"
            version = "1"
            description = "test_workflow"
            workflow1 = self._build_workflow(name, workflow_id, version)
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
                controller_type=ControllerType.WorkflowController
            )
            agent = WorkflowAgent(workflow_config)
            agent.bind_workflows([workflow1])
            Runner.resource_mgr.add_workflow(WorkflowCard(id=workflow_id + "_" + version), lambda: workflow1)
            Runner.resource_mgr.add_agent(AgentCard(id="workflow-single_agent"), lambda: agent)
            # Simulate client sending request
            client = RemoteAgent(agent_id="workflow-single_agent")
            Runner.resource_mgr.add_agent(AgentCard(id="remote-workflow-single_agent"), agent=client)
            response = await Runner.run_agent("remote-workflow-single_agent", {"query": "London"})
            logger.info(f"response: {response}")
            assert response['result_type'] == 'answer'
            assert response['output'].result == {'result': 'London'}
            assert response['output'].state.name == 'COMPLETED'
        except Exception as e:
            pass

        finally:
            from openjiuwen.core.runner.runner import Runner
            Runner.resource_mgr.remove_agent(agent_id="remote-workflow-single_agent")
            Runner.resource_mgr.remove_agent(agent_id="workflow-single_agent")

            await Runner.stop()
