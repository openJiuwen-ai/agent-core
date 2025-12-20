#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from openjiuwen._internal._constants import INTERRUPT

from jiuwen.agent.config.base import AgentConfig
from jiuwen.core.common.constants.constant import INTERACTIVE_INPUT
from jiuwen.core.runtime.interaction.agent_storage import AgentStorage
from jiuwen.core.runtime.interaction.base import Checkpointer
from jiuwen.core.runtime.interaction.interactive_input import InteractiveInput
from jiuwen.core.runtime.interaction.workflow_storage import WorkflowStorage
from jiuwen.core.runtime.runtime import BaseRuntime


class InMemoryCheckpointer(Checkpointer):
    def __init__(self):
        self._agent_stores = {}
        self._workflow_stores = {}

    async def pre_workflow_execute(self, runtime: BaseRuntime, inputs: InteractiveInput):
        workflow_store = self._workflow_stores.setdefault(runtime.session_id(), WorkflowStorage())
        workflow_store.recover(runtime, inputs)

    async def post_workflow_execute(self, runtime: BaseRuntime, result, exception):
        workflow_store = self._workflow_stores.setdefault(runtime.session_id(), WorkflowStorage())
        if exception is not None:
            workflow_store.save(runtime)
            raise exception

        if result.get(INTERRUPT) is None:
            workflow_id = runtime.workflow_id()
            await workflow_store.graph_checkpointer().adelete_thread(workflow_id)
            workflow_store.clear(workflow_id)
            if not isinstance(runtime.config(), AgentConfig):
                self._workflow_stores.pop(runtime.session_id(), None)
        else:
            workflow_store.save(runtime)

    async def pre_agent_execute(self, runtime: BaseRuntime, inputs):
        agent_store = self._agent_stores.setdefault(runtime.session_id(), AgentStorage())
        agent_store.recover(runtime)
        if inputs is not None:
            runtime.state().set_state({INTERACTIVE_INPUT: [inputs]})

    async def interrupt_agent_execute(self, runtime: BaseRuntime):
        agent_store = self._agent_stores.setdefault(runtime.session_id(), AgentStorage())
        agent_store.save(runtime)

    async def post_agent_execute(self, runtime: BaseRuntime):
        agent_store = self._agent_stores.setdefault(runtime.session_id(), AgentStorage())
        agent_store.save(runtime)

    async def release(self, session_id: str, agent_id: str = None):
        if agent_id is not None:
            agent_store = self._agent_stores.get(session_id)
            agent_store.clear(agent_id)
        else:
            self._workflow_stores.pop(session_id, None)
            self._agent_stores.pop(session_id, None)

    def graph_checkpointer(self, session_id):
        workflow_store = self._workflow_stores.setdefault(session_id, WorkflowStorage())
        return workflow_store.graph_checkpointer()


default_inmemory_checkpointer: Checkpointer = InMemoryCheckpointer()