#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Any

from openjiuwen.core.context_engine.base import Context
from openjiuwen.core.runtime.agent_state import StateCollection
from openjiuwen.core.runtime.config import Config
from openjiuwen.core.runtime.interaction.base import Checkpointer
from openjiuwen.core.runtime.interaction.checkpointer import default_inmemory_checkpointer
from openjiuwen.core.runtime.resources_manager.callback_manager import CallbackManager
from openjiuwen.core.runtime.resources_manager.resource_manager import ResourceMgr, ResourceManager
from openjiuwen.core.runtime.runtime import BaseRuntime
from openjiuwen.core.runtime.state import State, InMemoryCommitState
from openjiuwen.core.runtime.workflow import WorkflowRuntime
from openjiuwen.core.runtime.workflow_state import InMemoryState
from openjiuwen.core.stream.emitter import StreamEmitter
from openjiuwen.core.stream.manager import StreamWriterManager
from openjiuwen.core.tracer.tracer import Tracer


class StaticAgentRuntime(BaseRuntime):
    def __init__(self, config: Config = None, resource_mgr: ResourceMgr = None):
        self._config = config if config is not None else Config()
        self._resource_manager = ResourceMgr() if resource_mgr is None else resource_mgr
        self._checkpointer = default_inmemory_checkpointer

    def config(self) -> Config:
        return self._config

    def resource_manager(self) -> ResourceManager:
        return self._resource_manager

    def checkpointer(self) -> Checkpointer:
        return self._checkpointer

    def state(self) -> State:
        pass

    def tracer(self) -> Any:
        pass

    def stream_writer_manager(self) -> StreamWriterManager:
        pass

    def callback_manager(self) -> CallbackManager:
        pass

    def session_id(self) -> str:
        pass

    def context(self) -> Context:
        pass

    async def create_agent_runtime(self, session_id: str, inputs=None) -> BaseRuntime:
        runtime = AgentRuntime(session_id, self._config, self._resource_manager, self._checkpointer)
        await self._checkpointer.pre_agent_execute(runtime, inputs)
        return runtime


class AgentRuntime(BaseRuntime):
    def __init__(
            self,
            session_id: str,
            config: Config = None,
            resource_manager: ResourceManager = None,
            checkpointer: Checkpointer | None = None,
            context: Context = None):
        self._session_id = session_id
        self._config = config
        self._resource_manager = resource_manager if resource_manager is not None else ResourceMgr()
        self._context = context
        self._state = StateCollection()
        self._stream_writer_manager = StreamWriterManager(StreamEmitter())
        self._callback_manager = CallbackManager()
        tracer = Tracer()
        tracer.init(self._stream_writer_manager, self._callback_manager)
        self._tracer = tracer
        self._checkpointer = default_inmemory_checkpointer if checkpointer is None else checkpointer
        self._agent_span = self._tracer.tracer_agent_span_manager.create_agent_span() if self._tracer else None

    def config(self) -> Config:
        return self._config

    def state(self) -> State:
        return self._state

    def tracer(self) -> Any:
        return self._tracer

    def span(self):
        return self._agent_span

    def stream_writer_manager(self) -> StreamWriterManager:
        return self._stream_writer_manager

    def callback_manager(self) -> CallbackManager:
        return self._callback_manager

    def session_id(self) -> str:
        return self._session_id

    def context(self) -> Context:
        return self._context

    def resource_manager(self) -> ResourceManager:
        return self._resource_manager

    def checkpointer(self) -> Checkpointer:
        return self._checkpointer

    def create_workflow_runtime(self) -> WorkflowRuntime:
        state = self._state.global_state
        return WorkflowRuntime(
            parent=self,
            state=InMemoryState(InMemoryCommitState(state)),
            context=self._context,
            session_id=self._session_id)

    def agent_id(self):
        return self._config.get_agent_config().id
