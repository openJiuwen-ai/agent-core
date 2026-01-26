# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from abc import ABC
from typing import Union, Any, Optional, List, Tuple, AsyncIterator

from openjiuwen.core.session.config.base import Config
from openjiuwen.core.session.interaction.interaction import SimpleAgentInteraction
from openjiuwen.core.session.internal.agent import AgentSession
from openjiuwen.core.session.session import Session, BaseSession
from openjiuwen.core.session.stream.base import OutputSchema
from openjiuwen.core.session.stream.writer import StreamWriter
from openjiuwen.core.session.tracer import Tracer
from openjiuwen.core.session.tracer.workflow_tracer import TracerWorkflowUtils
from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.foundation.prompt import PromptTemplate
from openjiuwen.core.foundation.tool import Tool
from openjiuwen.core.foundation.tool import ToolInfo
from openjiuwen.core.session.workflow import Session as WorkflowSession


class StaticWrappedSession(Session, ABC):

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


class WrappedSession(Session, ABC):
    def __init__(self, inner: BaseSession):
        self._inner = inner

    def get_workflow_config(self, workflow_id):
        return self._inner.config().get_workflow_config(workflow_id)

    def get_agent_config(self):
        return self._inner.config().get_agent_config()

    def get_env(self, key) -> Optional[Any]:
        return self._inner.config().get_env(key)

    def base(self) -> BaseSession:
        return self._inner


class StateSession(WrappedSession, ABC):

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


class RouterSession(StateSession):
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

    def get_workflow_config(self, workflow_id):
        pass

    def get_agent_config(self):
        pass

    def get_env(self, key) -> Optional[Any]:
        pass

    def base(self) -> BaseSession:
        pass


class TaskSession(StateSession):
    def __init__(self, session_id: str = None, config: Config = None, resource_mgr=None, card=None):
        if config is None:
            config = Config()
        super().__init__(AgentSession(session_id, config, resource_mgr, card=card))
        self._interaction = None

    async def trace(self, data: dict):
        pass

    async def trace_error(self, error: Exception):
        pass

    async def interact(self, value):
        if self._interaction is None:
            self._interaction = SimpleAgentInteraction(self._inner)
        await self._interaction.wait_user_inputs(value)

    def get_inner_session(self):
        return self._inner

    # todo: all resource interaface will be deleted when resource_mgr supports tag feature
    def get_prompt(self, template_id: str) -> PromptTemplate:
        return self._inner.resource_manager()._resource_registry.prompt().get_prompt(template_id)

    def get_model(self, model_id: str) -> Model:
        return self._inner.resource_manager()._resource_registry.model().get_model(model_id, session=self._inner)

    async def get_workflow(self, workflow_id: str) -> "Workflow":
        return await self._inner.resource_manager()._resource_registry.workflow().get_workflow(workflow_id,
                                                                                               session=self._inner)

    def get_workflow_sync(self, workflow_id: str) -> Optional["Workflow"]:
        return self._inner.resource_manager()._resource_registry.workflow().get_workflow_sync(workflow_id,
                                                                                              session=self._inner)

    def get_tool(self, tool_id: str) -> Tool:
        return self._inner.resource_manager()._resource_registry.tool().get_tool(tool_id, session=self._inner)

    def stream_iterator(self) -> AsyncIterator[Any]:
        return self._inner.stream_writer_manager().stream_output()

    async def post_run(self):
        if isinstance(self._inner, AgentSession):
            await self._inner.stream_writer_manager().stream_emitter().close()
            await self._inner.checkpointer().post_agent_execute(self._inner)

    def tracer(self) -> Tracer:
        return self._inner.tracer()

    def get_envs(self):
        return getattr(self._inner.config(), "_env")

    def create_workflow_session(self) -> WorkflowSession:
        return WorkflowSession(parent=self, session_id=self.session_id())

    def get_session_id(self):
        return self.session_id()
