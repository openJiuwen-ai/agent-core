# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Optional, Any, Union

from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.foundation.llm import BaseModelClient
from openjiuwen.core.foundation.prompt import PromptTemplate
from openjiuwen.core.foundation.tool import Tool
from openjiuwen.core.session.interaction.interaction import WorkflowInteraction
from openjiuwen.core.session.internal.workflow import NodeSession
from openjiuwen.core.session.stream import StreamWriter, OutputSchema
from openjiuwen.core.session.tracer import TracerWorkflowUtils


class Session:
    def __init__(self, session: NodeSession, stream_mode: bool = False):
        self._inner = session
        self._interaction = None
        self._stream_mode = stream_mode

    def get_workflow_id(self):
        return self._inner.workflow_id()

    def get_component_id(self):
        return self._inner.node_id()

    def get_component_type(self):
        return self._inner.node_type()

    async def trace(self, data: dict):
        await TracerWorkflowUtils.trace(self._inner, data)

    async def trace_error(self, error: Exception):
        await TracerWorkflowUtils.trace_error(self._inner, error)

    async def interact(self, value):
        if self._stream_mode:
            raise JiuWenBaseException(
                StatusCode.WORKFLOW_STREAM_NOT_SUPPORT.code,
                StatusCode.WORKFLOW_STREAM_NOT_SUPPORT.errmsg.format
                (error_msg="streaming process interface(transform or collect)"),
            )
        if self._interaction is None:
            self._interaction = WorkflowInteraction(self._inner)
        return await self._interaction.wait_user_inputs(value)

    def get_executable_id(self) -> str:
        return self._inner.executable_id()

    def get_session_id(self) -> str:
        return self._inner.session_id()

    def update_state(self, data: dict):
        return self._inner.state().update(data)

    def get_state(self, key: Union[str, list, dict] = None) -> Any:
        return self._inner.state().get(key)

    def update_global_state(self, data: dict):
        return self._inner.state().update_global(data)

    def get_global_state(self, key: Union[str, list, dict] = None) -> Any:
        return self._inner.state().get_global(key)

    async def write_stream(self, data: Union[dict, OutputSchema]):
        writer = self._stream_writer()
        if writer:
            await writer.write(data)

    async def write_custom_stream(self, data: dict):
        writer = self._custom_writer()
        if writer:
            await writer.write(data)

    def get_callback_manager(self):
        return self._inner.callback_manager()

    def get_env(self, key) -> Optional[Any]:
        return self._inner.config().get_env(key)

    def _stream_writer(self) -> Optional[StreamWriter]:
        manager = self._inner.stream_writer_manager()
        if manager:
            return manager.get_output_writer()
        return None

    def _custom_writer(self) -> Optional[StreamWriter]:
        manager = self._inner.stream_writer_manager()
        if manager:
            return manager.get_custom_writer()
        return None

    # todo: resource interface will be deleted when resource_mgr supports tag feature
    def get_prompt(self, template_id: str) -> PromptTemplate:
        return self._inner.resource_manager()._resource_registry.prompt().get_prompt(template_id)


    def get_model(self, model_id: str) -> BaseModelClient:
        return self._inner.resource_manager()._resource_registry.model().get_model(model_id)


    async def get_workflow(self, workflow_id: str) -> "Workflow":
        return await self._inner.resource_manager()._resource_registry.workflow().get_workflow(workflow_id)


    def get_workflow_sync(self, workflow_id: str) -> "Workflow":
        return self._inner.resource_manager()._resource_registry.workflow().get_workflow_sync(workflow_id)


    def get_tool(self, tool_id: str) -> Tool:
        return self._inner.resource_manager()._resource_registry.tool().get_tool(tool_id)
