#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import pytest

from jiuwen.core.component.end_comp import End
from jiuwen.core.component.start_comp import Start
from jiuwen.core.component.tool_comp import ToolComponentConfig, ToolExecutable, ToolComponent
from jiuwen.core.context_engine.config import ContextEngineConfig
from jiuwen.core.context_engine.engine import ContextEngine
from jiuwen.core.runtime.wrapper import TaskRuntime
from jiuwen.core.utils.tool.param import Param
from jiuwen.core.utils.tool.tool import tool
from jiuwen.core.workflow.base import Workflow
from jiuwen.core.workflow.workflow_config import WorkflowMetadata, WorkflowConfig


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
