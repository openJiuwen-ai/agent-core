# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Optional, AsyncIterator, Any

from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.foundation.prompt import PromptTemplate
from openjiuwen.core.foundation.tool import Tool
from openjiuwen.core.session import BaseSession, Config
from openjiuwen.core.session.interaction.interaction import SimpleAgentInteraction
from openjiuwen.core.session.internal.agent import AgentSession
from openjiuwen.core.session.internal.wrapper import StateSession
from openjiuwen.core.session.tracer import Tracer
from openjiuwen.core.session.workflow import Session as WorkflowSession


class Session(StateSession):
    def __init__(self, session_id: str = None, inner: BaseSession = None):
        if inner is None:
            super().__init__(AgentSession(session_id, Config()))
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

    def create_workflow_session(self) -> WorkflowSession:
        return WorkflowSession(parent=self, session_id=self.session_id())

    def get_envs(self):
        return getattr(self._inner.config(), '_envs', {})


def create_agent_session(trace_id: str = None, inner: BaseSession = None) -> Session:
    return Session(trace_id, inner)