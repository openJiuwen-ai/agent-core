import os
from unittest.mock import patch, MagicMock, Mock

import pytest

from openjiuwen.core.context_engine import ContextEngineConfig, ContextEngine
from openjiuwen.core.session import WorkflowSession, NodeSession
from openjiuwen.core.session.node import Session
from openjiuwen.core.single_agent import create_agent_session
from openjiuwen.core.workflow import create_workflow_session
from openjiuwen.core.foundation.tool import RestfulApi, ToolCard, RestfulApiCard
from openjiuwen.core.foundation.tool import tool
from openjiuwen.core.runner import Runner
from openjiuwen.core.workflow import WorkflowCard
from openjiuwen.core.workflow import Start, End
from openjiuwen.core.workflow import ToolComponentConfig, ToolComponent
from openjiuwen.core.workflow import Workflow
from openjiuwen.core.workflow.components.tool.tool_comp import ToolExecutable
from tests.unit_tests.core.workflow.mock_nodes import MockStartNode, MockEndNode


@pytest.fixture
def fake_ctx():
    return Session(NodeSession(WorkflowSession(), "test"))


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
    os.environ["SSRF_PROTECT_ENABLED"] = "false"
    return RestfulApi(
        card=RestfulApiCard(
            name="test",
            description="test",
            input_params={
                "type": "object",
                "properties": {
                    "location": {"description": "location", "type": "string"},
                    "date": {"description": "date", "type": "integer"},
                },
                "required": ["location", "date"],
            },
            url="http://127.0.0.1:8000",
            headers={},
            method="GET",
        ),
    )


@pytest.fixture
def mock_tool_kwargs(mock_tool, mock_tool_config):
    return {
        "tool": mock_tool,
        "config": mock_tool_config
    }


@tool(
    card=ToolCard(
        id="test_local_function",
        name="test_local_function",
        description="测试本地函数",
        input_params={
            "type": "object",
            "properties": {
                "a": {"description": "参数1", "type": "string"},
                "b": {"description": "参数2", "type": ["integer", "null"], "default": 789},
            },
            "required": ["a"],
        },
    )
)
def test_local_function(a, b):
    return dict(res=a, info=b)


@patch('requests.request')
@patch('openjiuwen.core.foundation.tool.service_api.restful_api.RestfulApi._async_request')
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


@patch('openjiuwen.core.workflow.components.tool.tool_comp.ToolExecutable.invoke')
@pytest.mark.asyncio
async def test_tool_comp_in_workflow(mock_invoke, mock_tool, mock_tool_config, fake_ctx):
    mock_invoke.return_value = 'res'
    mock_tool_config = ToolComponentConfig(tool_id="test_local_function")
    Runner.resource_mgr.add_tool(test_local_function)
    flow = Workflow()

    start_component = MockStartNode("s")
    end_component = MockEndNode("e")
    tool_component = ToolComponent(mock_tool_config)

    flow.set_start_comp("s", start_component)
    flow.set_end_comp("e", end_component)
    flow.add_workflow_comp("tool", tool_component)

    flow.add_connection("s", "tool")
    flow.add_connection("tool", "e")

    await flow.invoke({}, create_workflow_session(session_id="test"))


class TestToolComponent:

    @pytest.mark.asyncio
    async def test_invoke_workflow_with_start_tool_end(self):
        id = "tool_workflow"
        version = "1.0"
        name = "tool"
        flow = Workflow(card=WorkflowCard(name=name, id=id, version=version))

        start_component = Start()
        end_component = End({"responseTemplate": "{{output}}"})


        Runner.resource_mgr.add_tool(test_local_function)
        tool_component = ToolComponent(ToolComponentConfig(tool_id="test_local_function"))

        flow.set_start_comp("s", start_component, inputs_schema={"query": "${query}", "name": "${name}"})
        flow.set_end_comp("e", end_component,
                          inputs_schema={"output": "${tool.data}"})
        flow.add_workflow_comp("tool", tool_component, inputs_schema={"a": "${s.query}", "b": "${s.name}"})

        flow.add_connection("s", "tool")
        flow.add_connection("tool", "e")

        session_id = "test_tool"
        config = ContextEngineConfig()
        ce_engine = ContextEngine(config)
        workflow_context = await ce_engine.create_context(context_id="tool_workflow")
        workflow_session = create_agent_session(session_id=session_id).create_workflow_session()
        invoke_result = await flow.invoke({"query": "你好"}, workflow_session, workflow_context)
        assert invoke_result.result["response"] == "{'res': '你好', 'info': 789}"
