#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.


from openjiuwen.core.common.constants.constant import INTERACTIVE_INPUT
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.runtime.constants import FORCE_DEL_WORKFLOW_STATE_KEY
from openjiuwen.core.runtime.interaction.agent_storage import AgentStorage
from openjiuwen.core.runtime.interaction.base import Checkpointer
from openjiuwen.core.runtime.interaction.interactive_input import InteractiveInput
from openjiuwen.core.runtime.interaction.workflow_storage import WorkflowStorage
from openjiuwen.core.runtime.runtime import BaseRuntime
from openjiuwen.graph.store import Store
from openjiuwen.graph.store.inmemory import InMemoryStore
from openjiuwen.graph.pregel.constants import TASK_STATUS_INTERRUPT


class InMemoryCheckpointer(Checkpointer):
    def __init__(self):
        self._agent_stores = {}
        self._workflow_stores = {}
        self._graph_store = InMemoryStore()
        self._session_to_workflow_ids = {}

    async def pre_workflow_execute(self, runtime: BaseRuntime, inputs: InteractiveInput):
        workflow_store = self._workflow_stores.setdefault(runtime.session_id(), WorkflowStorage())
        self._session_to_workflow_ids.setdefault(runtime.session_id(), set())
        if isinstance(inputs, InteractiveInput):
            workflow_store.recover(runtime, inputs)
        else:
            if not workflow_store.exists(runtime):
                return
            if runtime.config().get_env(FORCE_DEL_WORKFLOW_STATE_KEY, False):
                await self._graph_store.delete(runtime.session_id(), runtime.workflow_id())
                workflow_store.clear(runtime.workflow_id())
            else:
                raise JiuWenBaseException(StatusCode.WORKFLOW_STATE_EXISTS_ERROR.code,
                                          StatusCode.WORKFLOW_STATE_EXISTS_ERROR.errmsg)

    async def post_workflow_execute(self, runtime: BaseRuntime, result, exception):
        workflow_store = self._workflow_stores.get(runtime.session_id())
        workflow_ids = self._session_to_workflow_ids.get(runtime.session_id())
        if exception is not None:
            workflow_store.save(runtime)
            workflow_ids.add(runtime.workflow_id())
            raise exception

        if result.get(TASK_STATUS_INTERRUPT) is None:
            await self._graph_store.delete(runtime.session_id(), runtime.workflow_id())
            workflow_store.clear(runtime.workflow_id())
            workflow_ids.discard(runtime.workflow_id())
            if runtime.config().get_agent_config() is None:
                self._workflow_stores.pop(runtime.session_id(), None)
                self._session_to_workflow_ids.pop(runtime.session_id(), None)
        else:
            workflow_store.save(runtime)
            workflow_ids.add(runtime.workflow_id())

    async def pre_agent_execute(self, runtime: BaseRuntime, inputs):
        agent_store = self._agent_stores.setdefault(runtime.session_id(), AgentStorage())
        agent_store.recover(runtime)
        if inputs is not None:
            runtime.state().set_state({INTERACTIVE_INPUT: [inputs]})

    async def interrupt_agent_execute(self, runtime: BaseRuntime):
        agent_store = self._agent_stores.get(runtime.session_id())
        agent_store.save(runtime)

    async def post_agent_execute(self, runtime: BaseRuntime):
        agent_store = self._agent_stores.get(runtime.session_id())
        agent_store.save(runtime)

    async def release(self, session_id: str, agent_id: str = None):
        if agent_id is not None:
            agent_store = self._agent_stores.get(session_id)
            agent_store.clear(agent_id)
        else:
            workflow_ids = self._session_to_workflow_ids.get(session_id)
            if workflow_ids:
                for workflow_id in workflow_ids:
                    await self._graph_store.delete(session_id, workflow_id)
            self._session_to_workflow_ids.pop(session_id, None)
            self._workflow_stores.pop(session_id, None)
            self._agent_stores.pop(session_id, None)

    def graph_store(self) -> Store:
        return self._graph_store


default_inmemory_checkpointer: Checkpointer = InMemoryCheckpointer()
