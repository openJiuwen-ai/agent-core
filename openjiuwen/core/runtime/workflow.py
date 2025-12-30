# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import uuid
from typing import Any

from openjiuwen.core.context_engine.base import Context
from openjiuwen.core.runtime.config import Config
from openjiuwen.core.runtime.resources_manager.callback_manager import CallbackManager
from openjiuwen.core.runtime.resources_manager.resource_manager import ResourceManager, ResourceMgr
from openjiuwen.core.runtime.runtime import BaseRuntime
from openjiuwen.core.runtime.state import State
from openjiuwen.core.runtime.workflow_state import InMemoryState
from openjiuwen.core.stream.manager import StreamWriterManager
from openjiuwen.core.stream_actor.manager import ActorManager
from openjiuwen.core.tracer.tracer import Tracer


class WorkflowRuntime(BaseRuntime):
    def __init__(self, workflow_id: str = '', parent: BaseRuntime = None, session_id: str = None, state: State = None,
                 context: Context = None):
        self._session_id = session_id
        self._parent = parent
        self._context = context
        if parent is not None:
            if self._session_id is None:
                self._session_id = parent.session_id()
            self._config = parent.config()
            self._resource_manager = parent.resource_manager()
            self._tracer = parent.tracer()
        else:
            if self._session_id is None:
                self._session_id = uuid.uuid4().hex
            self._config = Config()
            self._resource_manager = ResourceMgr()
            self._tracer = None

        self._state = state if state is not None else InMemoryState()
        self._callback_manager = CallbackManager()
        self._stream_writer_manager = None  # type: StreamWriterManager
        self._actor_manager = None
        self._workflow_id = workflow_id

    def set_stream_writer_manager(self, stream_writer_manager: StreamWriterManager) -> None:
        if self._stream_writer_manager is not None:
            return
        self._stream_writer_manager = stream_writer_manager

    def set_tracer(self, tracer: Tracer) -> None:
        self._tracer = tracer

    def set_context(self, context: Context) -> None:
        self._context = context

    def set_actor_manager(self, queue_manager: ActorManager):
        if self._actor_manager is not None:
            return
        self._actor_manager = queue_manager

    def set_workflow_id(self, workflow_id):
        self._workflow_id = workflow_id

    def actor_manager(self) -> ActorManager:
        return self._actor_manager

    def config(self) -> Config:
        return self._config

    def state(self) -> State:
        return self._state

    def tracer(self) -> Any:
        return self._tracer

    def stream_writer_manager(self) -> StreamWriterManager:
        return self._stream_writer_manager

    def callback_manager(self) -> CallbackManager:
        return self._callback_manager

    def session_id(self) -> str:
        return self._session_id

    def resource_manager(self) -> ResourceManager:
        return self._resource_manager

    def context(self) -> Context:
        return self._context

    def checkpointer(self):
        return self._parent.checkpointer()

    def workflow_id(self):
        return self._workflow_id

    def main_workflow_id(self):
        return self.workflow_id()

    def workflow_nesting_depth(self):
        return 0

    async def close(self):
        if self._actor_manager is not None:
            await self._actor_manager.shutdown()


def create_parent_id(runtime: BaseRuntime):
    return runtime.executable_id() if isinstance(runtime, NodeRuntime) else ''


def create_executable_id(node_id: str, parent_id: str):
    return parent_id + "." + node_id if len(parent_id) != 0 else node_id


class NodeRuntime(BaseRuntime):
    def __init__(self, runtime: BaseRuntime, node_id: str, node_type: str = None):
        self._node_id = node_id
        self._node_type = node_type
        parent_id = create_parent_id(runtime)
        executable_id = create_executable_id(node_id, parent_id)
        state = runtime.state().create_node_state(executable_id, parent_id)
        self._state = state
        self._parent_id = parent_id
        self._executable_id = executable_id
        self._runtime = runtime
        self._workflow_id = runtime.workflow_id()
        self._workflow_nesting_depth = runtime.workflow_nesting_depth()
        self._main_workflow_id = runtime.main_workflow_id()

    def node_id(self):
        return self._node_id

    def node_type(self):
        return self._node_type

    def executable_id(self):
        return self._executable_id

    def parent_id(self):
        return self._parent_id

    def workflow_id(self):
        return self._workflow_id

    def main_workflow_id(self):
        return self._main_workflow_id

    def workflow_nesting_depth(self):
        return self._workflow_nesting_depth

    def actor_manager(self) -> ActorManager:
        return self._runtime.actor_manager()

    def parent(self):
        return self._runtime

    def tracer(self) -> Tracer:
        return self._runtime.tracer()

    def state(self) -> State:
        return self._state

    def config(self) -> Config:
        return self._runtime.config()

    def stream_writer_manager(self) -> StreamWriterManager:
        return self._runtime.stream_writer_manager()

    def callback_manager(self) -> CallbackManager:
        return self._runtime.callback_manager()

    def session_id(self) -> str:
        return self._runtime.session_id()

    def resource_manager(self):
        return self._runtime.resource_manager()

    def context(self) -> Context:
        return self._runtime.context()

    def checkpointer(self):
        pass

    def node_config(self):
        workflow_config = self.config().get_workflow_config(self.workflow_id())
        if workflow_config:
            return workflow_config.spec.comp_configs.get(self._node_id)
        else:
            return None


class SubWorkflowRuntime(NodeRuntime):
    def __init__(self, runtime: NodeRuntime, workflow_id: str, actor_manager: ActorManager = None):
        super().__init__(runtime=runtime.parent(), node_id=runtime.node_id(), node_type=runtime.node_type())
        self._workflow_id = workflow_id
        self._workflow_nesting_depth = runtime.workflow_nesting_depth() + 1
        self._main_workflow_id = runtime.main_workflow_id()
        self._actor_manager = actor_manager

    def workflow_id(self):
        return self._workflow_id

    def workflow_nesting_depth(self):
        return self._workflow_nesting_depth

    def main_workflow_id(self):
        return self._main_workflow_id

    def actor_manager(self) -> ActorManager:
        return self._actor_manager

    async def close(self):
        if self._actor_manager is not None:
            await self._actor_manager.shutdown()
