from unittest.mock import patch, MagicMock, Mock

import pytest

from openjiuwen.core.component.end_comp import End
from openjiuwen.core.component.start_comp import Start
from openjiuwen.core.component.tool_comp import ToolComponentConfig, ToolExecutable, ToolComponent
from openjiuwen.core.context_engine.config import ContextEngineConfig
from openjiuwen.core.context_engine.engine import ContextEngine
from openjiuwen.core.runtime.workflow import WorkflowRuntime, NodeRuntime
from openjiuwen.core.runtime.wrapper import WrappedNodeRuntime, TaskRuntime
from openjiuwen.core.utils.tool.param import Param
from openjiuwen.core.utils.tool.service_api.restful_api import RestfulApi
from openjiuwen.core.utils.tool.tool import tool
from openjiuwen.core.workflow.base import Workflow
from openjiuwen.core.workflow.workflow_config import WorkflowMetadata, WorkflowConfig
from tests.unit_tests.core.workflow.mock_nodes import MockStartNode, MockEndNode


@pytest.fixture
def fake_ctx():
    return WrappedNodeRuntime(NodeRuntime(WorkflowRuntime(), "test"))


@pytest.fixture()
def mock_tool_config():
    return ToolComponentConfig()


@pytest.fixture
def mock_tool_input():
    return {
        'location': 'Beijing',
        'date': 15
    }


@pytest.fixture
def mock_tool():
    return RestfulApi(
        name="test",
        description="test",
        params=[Param(name="location", description="location", type='string'),
                Param(name="date", description="date", type='int')],
        path="http://127.0.0.1:8000",
        headers={},
        method="GET",
        response=[],
    )


@pytest.fixture
def mock_tool_kwargs(mock_tool, mock_tool_input):
    return {
        "tool": mock_tool,
        "config": mock_tool_config
    }


@patch('requests.request')
@patch('openjiuwen.core.utils.tool.service_api.restful_api.RestfulApi._async_request')
@pytest.mark.asyncio
async def test_tool_comp_invoke(mock_async_request, mock_request, mock_tool_input,
                                mock_tool_kwargs, fake_ctx):
    tool_executable = ToolExecutable(mock_tool_kwargs["config"])
    tool_executable.set_tool(mock_tool_kwargs["tool"])

    # mock request的response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "{}"
    mock_response.content = b"{}"
    mock_request.return_value = mock_response
    mock_async_request.return_value = mock_response
    res = await tool_executable.invoke(mock_tool_input, fake_ctx, context=Mock())

    assert res.get('error_code') == 0


@patch('openjiuwen.core.component.tool_comp.ToolExecutable.invoke')
@pytest.mark.asyncio
async def test_tool_comp_in_workflow(mock_invoke, mock_tool, mock_tool_config, fake_ctx):
    mock_invoke.return_value = 'res'
    flow = Workflow()

    start_component = MockStartNode("s")
    end_component = MockEndNode("e")
    tool_component = ToolComponent(mock_tool_config)

    flow.set_start_comp("s", start_component)
    flow.set_end_comp("e", end_component)
    flow.add_workflow_comp("tool", tool_component)

    flow.add_connection("s", "tool")
    flow.add_connection("tool", "e")

    await flow.invoke({}, WorkflowRuntime(session_id="test"))


@tool(
    name="test_local_function",
    description="测试本地函数",
    params=[
        Param(name="a", description="参数1", param_type="string", required=True),
        Param(name="b", description="参数2", param_type="integer", default_value=789, required=True),
    ],
)
def test_local_function(a, b):
    return dict(res=a, info=b)

class TestToolComponent:

    @pytest.mark.asyncio
    async def test_invoke_workflow_with_start_tool_end(self):
        id = "tool_workflow"
        version = "1.0"
        name = "tool"
        flow = Workflow(workflow_config=WorkflowConfig(metadata=WorkflowMetadata(name=name, id=id, version=version, )))

        start_component = Start(
            {
                "inputs": [
                    {"id": "query", "type": "String", "required": "true", "sourceType": "ref"}
                ]
            }
        )
        end_component = End({"responseTemplate": "{{output}}"})

        tool_component = ToolComponent(ToolComponentConfig())
        tool_component.bind_tool(test_local_function)

        flow.set_start_comp("s", start_component, inputs_schema={"query": "${query}", "name": "${name}"})
        flow.set_end_comp("e", end_component,
                          inputs_schema={"output": "${tool.data}"})
        flow.add_workflow_comp("tool", tool_component, inputs_schema={"a": "${s.query}", "b": "${s.name}"})

        flow.add_connection("s", "tool")
        flow.add_connection("tool", "e")

        session_id = "test_tool"
        config = ContextEngineConfig()
        ce_engine = ContextEngine("123", config)
        workflow_context = ce_engine.get_workflow_context(workflow_id="tool_workflow", session_id=session_id)
        workflow_runtime = TaskRuntime(trace_id=session_id).create_workflow_runtime()
        invoke_result = await flow.invoke({"query": "你好"}, workflow_runtime, workflow_context)
        assert invoke_result.result["responseContent"] == "{'res': '你好', 'info': 789}"
