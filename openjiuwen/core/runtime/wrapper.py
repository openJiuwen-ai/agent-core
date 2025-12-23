#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from abc import ABC
from typing import Union, Any, Optional, List, Tuple, AsyncIterator

from openjiuwen.core.runtime.agent import AgentRuntime
from openjiuwen.core.runtime.config import Config
from openjiuwen.core.runtime.interaction.interaction import WorkflowInteraction, SimpleAgentInteraction
from openjiuwen.core.runtime.runtime import Runtime, Workflow, BaseRuntime
from openjiuwen.core.runtime.workflow import NodeRuntime, WorkflowRuntime
from openjiuwen.core.stream.base import OutputSchema
from openjiuwen.core.stream.writer import StreamWriter
from openjiuwen.core.tracer.tracer import Tracer
from openjiuwen.core.tracer.workflow_tracer import TracerWorkflowUtils
from openjiuwen.core.utils.llm.base import BaseModelClient
from openjiuwen.core.utils.tool.schema import ToolInfo
from openjiuwen.core.utils.prompt.template.template import Template
from openjiuwen.core.utils.tool.base import Tool
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.common.exception.exception import JiuWenBaseException


class StaticWrappedRuntime(Runtime, ABC):

    def executable_id(self) -> str:
        pass

    def session_id(self) -> str:
        pass

    def update_state(self, data: dict):
        pass

    def get_state(self, key: Union[str, list, dict] = None) -> Any:
        pass

    def update_global_state(self, data: dict):
        pass

    def get_global_state(self, key: Union[str, list, dict] = None) -> Any:
        pass

    def stream_writer(self) -> Optional[StreamWriter]:
        pass

    def custom_writer(self) -> Optional[StreamWriter]:
        pass

    async def write_stream(self, data: Union[dict, OutputSchema]):
        pass

    async def write_custom_stream(self, data: dict):
        pass

    async def trace(self, data: dict):
        pass

    async def trace_error(self, error: Exception):
        pass

    async def interact(self, value):
        pass


class WrappedRuntime(Runtime, ABC):
    def __init__(self, inner: BaseRuntime):
        self._inner = inner

    def add_prompt(self, template_id: str, template: Template):
        self._inner.resource_manager().prompt().add_prompt(template_id, template)

    def add_prompts(self, templates: List[Tuple[str, Template]]):
        self._inner.resource_manager().prompt().add_prompts(templates)

    def remove_prompt(self, template_id: str):
        self._inner.resource_manager().prompt().remove_prompt(template_id)

    def get_prompt(self, template_id: str) -> Template:
        return self._inner.resource_manager().prompt().get_prompt(template_id)

    def add_model(self, model_id: str, model: BaseModelClient):
        self._inner.resource_manager().model().add_model(model_id, model)

    def add_models(self, models: List[Tuple[str, BaseModelClient]]):
        self._inner.resource_manager().model().add_models(models)

    def remove_model(self, model_id: str):
        self._inner.resource_manager().model().remove_model(model_id)

    def get_model(self, model_id: str) -> BaseModelClient:
        return self._inner.resource_manager().model().get_model(model_id, self._inner)

    def add_workflow(self, workflow_id: str, workflow: Workflow):
        self._inner.resource_manager().workflow().add_workflow(workflow_id, workflow)

    def add_workflows(self, workflows: List[Tuple[str, Workflow]]):
        self._inner.resource_manager().workflow().add_workflows(workflows)

    def remove_workflow(self, workflow_id: str):
        self._inner.resource_manager().workflow().remove_workflow(workflow_id)

    async def get_workflow(self, workflow_id: str) -> Workflow:
        return await self._inner.resource_manager().workflow().get_workflow(workflow_id, self._inner)

    def get_workflow_sync(self, workflow_id: str) -> Optional[Workflow]:
        return self._inner.resource_manager().workflow().get_workflow_sync(workflow_id, self._inner)

    def add_tool(self, tool_id: str, tool: Tool):
        self._inner.resource_manager().tool().add_tool(tool_id, tool)

    def add_tools(self, tools: List[Tuple[str, Tool]]):
        self._inner.resource_manager().tool().add_tools(tools)

    def remove_tool(self, tool_id: str):
        self._inner.resource_manager().tool().remove_tool(tool_id)

    def get_tool(self, tool_id: str) -> Tool:
        return self._inner.resource_manager().tool().get_tool(tool_id, self._inner)

    def get_tool_info(self, tool_id: List[str] = None, workflow_id: List[str] = None) -> List[ToolInfo]:
        infos = []
        if tool_id is None and workflow_id is None:
            infos.extend(self._inner.resource_manager().tool().get_tool_infos(tool_id))
            infos.extend(self._inner.resource_manager().workflow().get_tool_infos(workflow_id))
            return infos
        if tool_id is not None:
            infos.extend(self._inner.resource_manager().tool().get_tool_infos(tool_id))
        if workflow_id is not None:
            infos.extend(self._inner.resource_manager().workflow().get_tool_infos(workflow_id))
        return infos

    def get_workflow_config(self, workflow_id):
        return self._inner.config().get_workflow_config(workflow_id)

    def get_agent_config(self):
        return self._inner.config().get_agent_config()

    def get_env(self, key) -> Optional[Any]:
        return self._inner.config().get_env(key)

    def base(self) -> BaseRuntime:
        return self._inner


class StateRuntime(WrappedRuntime, ABC):

    def executable_id(self) -> str:
        return self._inner.executable_id()

    def session_id(self) -> str:
        return self._inner.session_id()

    def update_state(self, data: dict):
        return self._inner.state().update(data)

    def get_state(self, key: Union[str, list, dict] = None) -> Any:
        return self._inner.state().get(key)

    def update_global_state(self, data: dict):
        return self._inner.state().update_global(data)

    def get_global_state(self, key: Union[str, list, dict] = None) -> Any:
        return self._inner.state().get_global(key)

    def stream_writer(self) -> Optional[StreamWriter]:
        manager = self._inner.stream_writer_manager()
        if manager:
            return manager.get_output_writer()
        return None

    def custom_writer(self) -> Optional[StreamWriter]:
        manager = self._inner.stream_writer_manager()
        if manager:
            return manager.get_custom_writer()
        return None

    async def write_stream(self, data: Union[dict, OutputSchema]):
        writer = self.stream_writer()
        if writer:
            await writer.write(data)

    async def write_custom_stream(self, data: dict):
        writer = self.custom_writer()
        if writer:
            await writer.write(data)


class RouterRuntime(StateRuntime):
    async def interact(self, value):
        pass

    async def trace(self, data: dict):
        await TracerWorkflowUtils.trace(self._inner, data)

    def stream_writer(self) -> Optional[StreamWriter]:
        pass

    def custom_writer(self) -> Optional[StreamWriter]:
        pass

    async def write_stream(self, data: Union[dict, OutputSchema]):
        pass

    async def write_custom_stream(self, data: dict):
        pass

    async def trace_error(self, error: Exception):
        await TracerWorkflowUtils.trace_error(self._inner, error)

    def update_global_state(self, data: dict):
        pass

    def update_state(self, data: dict):
        pass

    def add_prompt(self, template_id: str, template: Template):
        pass

    def add_prompts(self, templates: List[Tuple[str, Template]]):
        pass

    def remove_prompt(self, template_id: str):
        pass

    def get_prompt(self, template_id: str) -> Template:
        pass

    def add_model(self, model_id: str, model: BaseModelClient):
        pass

    def add_models(self, models: List[Tuple[str, BaseModelClient]]):
        pass

    def remove_model(self, model_id: str):
        pass

    def get_model(self, model_id: str) -> BaseModelClient:
        pass

    def add_workflow(self, workflow_id: str, workflow: Workflow):
        pass

    def add_workflows(self, workflows: List[Tuple[str, Workflow]]):
        pass

    def remove_workflow(self, workflow_id: str):
        pass

    async def get_workflow(self, workflow_id: str) -> Workflow:
        pass

    def get_workflow_sync(self, workflow_id: str) -> Optional[Workflow]:
        pass

    def add_tool(self, tool_id: str, tool: Tool):
        pass

    def add_tools(self, tools: List[Tuple[str, Tool]]):
        pass

    def remove_tool(self, tool_id: str):
        pass

    def get_tool(self, tool_id: str) -> Tool:
        pass

    def get_tool_info(self, tool_id: List[str] = None, workflow_id: List[str] = None) -> List[ToolInfo]:
        pass

    def get_workflow_config(self, workflow_id):
        pass

    def get_agent_config(self):
        pass

    def get_env(self, key) -> Optional[Any]:
        pass

    def base(self) -> BaseRuntime:
        pass


class WrappedNodeRuntime(StateRuntime):

    def __init__(self, runtime: NodeRuntime, stream_mode: bool = False):
        super().__init__(runtime)
        self._interaction = None
        self._stream_mode = stream_mode

    async def trace(self, data: dict):
        await TracerWorkflowUtils.trace(self._inner, data)

    async def trace_error(self, error: Exception):
        await TracerWorkflowUtils.trace_error(self._inner, error)

    async def interact(self, value):
        if self._stream_mode:
            raise JiuWenBaseException(
                StatusCode.INTERACTIVE_NOT_SUPPORT_STREAM_ERROR.code,
                StatusCode.INTERACTIVE_NOT_SUPPORT_STREAM_ERROR.errmsg,
            )
        if self._interaction is None:
            self._interaction = WorkflowInteraction(self._inner)
        return await self._interaction.wait_user_inputs(value)

    def get_prompt(self, template_id: str) -> Template:
        return self._inner.resource_manager().prompt().get_prompt(template_id)

    def get_model(self, model_id: str) -> BaseModelClient:
        return self._inner.resource_manager().model().get_model(model_id)

    async def get_workflow(self, workflow_id: str) -> Workflow:
        return await self._inner.resource_manager().workflow().get_workflow(workflow_id)

    def get_workflow_sync(self, workflow_id: str) -> Workflow:
        return self._inner.resource_manager().workflow().get_workflow_sync(workflow_id)

    def get_tool(self, tool_id: str) -> Tool:
        return self._inner.resource_manager().tool().get_tool(tool_id)

    def get_current_workflow_config(self):
        return self._inner.config().get_workflow_config(self._inner.workflow_id())

    def add_workflow_config(self, workflow_id, workflow_config):
        return self._inner.config().add_workflow_config(workflow_id, workflow_config)

    def get_workflow_config(self, workflow_id):
        return self._inner.config().get_workflow_config(workflow_id)

    def get_agent_config(self):
        return self._inner.config().get_agent_config()

    def get_env(self, key) -> Optional[Any]:
        return self._inner.config().get_env(key)


class TaskRuntime(StateRuntime):
    def __init__(self, trace_id: str = None, inner: BaseRuntime = None):
        if inner is None:
            super().__init__(AgentRuntime(trace_id, Config()))
        else:
            super().__init__(inner)
        self._interaction = None

    async def trace(self, data: dict):
        pass

    async def trace_error(self, error: Exception):
        pass

    async def interact(self, value):
        if self._interaction is None:
            self._interaction = SimpleAgentInteraction(self._inner)
        await self._interaction.wait_user_inputs(value)

    def get_prompt(self, template_id: str) -> Template:
        return self._inner.resource_manager().prompt().get_prompt(template_id)

    def get_model(self, model_id: str) -> BaseModelClient:
        return self._inner.resource_manager().model().get_model(model_id, runtime=self._inner)

    async def get_workflow(self, workflow_id: str) -> Workflow:
        return await self._inner.resource_manager().workflow().get_workflow(workflow_id, runtime=self._inner)

    def get_workflow_sync(self, workflow_id: str) -> Optional[Workflow]:
        return self._inner.resource_manager().workflow().get_workflow_sync(workflow_id, runtime=self._inner)

    def get_tool(self, tool_id: str) -> Tool:
        return self._inner.resource_manager().tool().get_tool(tool_id, runtime=self._inner)

    def stream_iterator(self) -> AsyncIterator[Any]:
        return self._inner.stream_writer_manager().stream_output()

    async def post_run(self):
        if isinstance(self._inner, AgentRuntime):
            await self._inner.stream_writer_manager().stream_emitter().close()
            await self._inner.checkpointer().post_agent_execute(self._inner)

    def tracer(self) -> Tracer:
        return self._inner.tracer()

    def create_workflow_runtime(self) -> WorkflowRuntime:
        return self._inner.create_workflow_runtime()
