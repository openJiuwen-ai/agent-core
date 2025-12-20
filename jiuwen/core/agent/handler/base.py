#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Handler of Agent"""
from typing import Dict, Callable, Any, Awaitable

from pydantic import BaseModel, Field

from jiuwen.agent.common.enum import SubTaskType
from jiuwen.agent.common.schema import WorkflowSchema
from jiuwen.agent.config.base import AgentConfig
from jiuwen.core.common.constants.constant import INTERACTION
from jiuwen.core.common.exception.exception import JiuWenBaseException
from jiuwen.core.runtime.interaction.base import AgentInterrupt
from jiuwen.core.stream.base import BaseStreamMode, OutputSchema
from jiuwen.core.workflow.base import WorkflowOutput, WorkflowExecutionState


class AgentHandlerInputs(BaseModel):
    query: str = Field(default="")
    name: str = Field(default="")
    arguments: Any = Field(default_factory=dict)
    context: Any = Field(default=None)


class AgentHandler:
    def __init__(self, agent_config: AgentConfig):
        self._function_map: Dict[SubTaskType, Callable[[AgentHandlerInputs], Awaitable[dict]]] = {
            SubTaskType.WORKFLOW: self.invoke_workflow,
            SubTaskType.PLUGIN: self.invoke_plugin
        }
        self._config = agent_config

    async def invoke(self, sub_task_type: SubTaskType, inputs: AgentHandlerInputs):
        handler = self._function_map.get(sub_task_type)
        if not handler:
            raise JiuWenBaseException()
        return await handler(inputs)

    async def invoke_workflow(self, inputs: AgentHandlerInputs):
        return dict()

    async def invoke_plugin(self, inputs: AgentHandlerInputs):
        return dict()

    async def invoke_llm(self, inputs: AgentHandlerInputs):
        return dict()

    async def send_message(self, inputs: AgentHandlerInputs):
        return dict()

    def search_workflow_metadata_by_workflow_name(self, workflow_name: str) -> WorkflowSchema:
        pass


class AgentHandlerImpl(AgentHandler):
    def __init__(self, agent_config: AgentConfig):
        super().__init__(agent_config)

    async def invoke(self, sub_task_type: SubTaskType, inputs: AgentHandlerInputs):
        handler = self._function_map.get(sub_task_type)
        if not handler:
            raise JiuWenBaseException()
        return await handler(inputs)

    async def invoke_workflow(self, inputs: AgentHandlerInputs):
        context = inputs.context
        workflow_name = inputs.name
        workflow_metadata = self.search_workflow_metadata_by_workflow_name(workflow_name)
        workflow = context.get_workflow(workflow_metadata.id+"_"+workflow_metadata.version)

        workflow_result = None
        async for chunk in self._handle_workflow_stream_output(workflow, inputs.arguments,
                                                               context.create_workflow_runtime(),
                                                               context):
            if isinstance(chunk, WorkflowOutput):
                workflow_result = chunk
                if workflow_result.state == WorkflowExecutionState.INPUT_REQUIRED:
                    raise AgentInterrupt(workflow_result.result)
        return workflow_result.result if workflow_result and hasattr(workflow_result, "result") else workflow_result

    async def invoke_plugin(self, inputs: AgentHandlerInputs):
        context = inputs.context
        plugin_name = inputs.name
        plugin_args = inputs.arguments

        plugin = context.get_tool(plugin_name)

        plugin_result = plugin.invoke(plugin_args)
        return plugin_result

    def search_workflow_metadata_by_workflow_name(self, workflow_name: str) -> WorkflowSchema:
        workflows_config = self._config.workflows
        for item in workflows_config:
            if workflow_name == item.name:
                return item
        raise JiuWenBaseException()

    async def _handle_workflow_stream_output(self, workflow, inputs, workflow_runtime, agent_runtime):
        chunks = []
        async for chunk in workflow.stream(inputs, workflow_runtime, stream_modes=[BaseStreamMode.OUTPUT]):
            chunks.append(chunk)
            await agent_runtime.write_stream(chunk)

        is_interaction = False
        for chunk in chunks:
            if isinstance(chunk, OutputSchema) and chunk.type == INTERACTION:
                is_interaction = True
                break
        if is_interaction:
            output = WorkflowOutput(result=chunks,
                                    state=WorkflowExecutionState.INPUT_REQUIRED)
        else:
            output = WorkflowOutput(result=workflow_runtime.state().get_outputs(workflow._end_comp_id),
                                    state=WorkflowExecutionState.COMPLETED)
        yield output
