import pytest

from openjiuwen.agent.common.enum import ControllerType
from openjiuwen.agent.common.schema import WorkflowSchema
from openjiuwen.agent.config.workflow_config import WorkflowAgentConfig
from openjiuwen.agent.workflow_agent.workflow_agent import WorkflowAgent
from openjiuwen.core.runtime.config import WorkflowConfig
from openjiuwen.core.workflow.base import Workflow
from openjiuwen.core.workflow.workflow_config import WorkflowMetadata
from tests.unit_tests.core.workflow.mock_nodes import MockStartNode, Node1, MockEndNode


@pytest.mark.skip("skip unit test")
class TestWorkflowAgent:
    @staticmethod
    def _build_workflow(name, id, version):
        workflow_config = WorkflowConfig(
            metadata=WorkflowMetadata(
                id=id,
                version=version,
                name=name,
            )
        )
        flow = Workflow(workflow_config=workflow_config)
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

    # 真正实例化
    @pytest.fixture(scope="class")
    def agent(self):
        id = "test_workflow"
        name = "test_workflow"
        version = "1"
        description = "test_workflow"
        workflow1 = self._build_workflow(name, id, version)
        test_workflow_schema = WorkflowSchema(
            id=id,
            version=version,
            name=name,
            description=description,
            inputs={"query": {
                "type": "string",
            }},
        )
        workflow_config = WorkflowAgentConfig(
            workflows=[test_workflow_schema],
            controller_type =ControllerType.WorkflowController
        )
        agent = WorkflowAgent(workflow_config)
        agent.bind_workflows([workflow1])
        return agent

    # ---------- 测试用例 ----------
    # 等待workflowAgent合入后开启
    @pytest.mark.asyncio
    async def test_invoke_single(self, agent):
        inputs = {"query": "hi"}
        result = await agent.invoke(inputs)  # ✅ 使用 await
        # 修改断言以匹配实际返回的WorkflowOutput对象结构
        assert result['result_type'] == 'answer'
        assert result['output'].result == {'result': 'hi'}
        assert result['output'].state.name == 'COMPLETED'