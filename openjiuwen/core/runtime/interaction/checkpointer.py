# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.


from openjiuwen.core.common.constants.constant import INTERACTIVE_INPUT
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.common.logging import logger
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
        logger.info(f"workflow: {runtime.workflow_id()} create or restore checkpoint from "
                    f"session: {runtime.session_id()}")
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
            logger.info(f"exception in workflow, save checkpoint for "
                        f"workflow: {runtime.workflow_id()} in session: {runtime.session_id()}")
            if workflow_store is None:
                raise JiuWenBaseException(StatusCode.RUNTIME_CHECKPOINTER_NONE_WORKFLOW_STORE_ERROR.code,
                                          StatusCode.RUNTIME_CHECKPOINTER_NONE_WORKFLOW_STORE_ERROR.errmsg)
            workflow_store.save(runtime)
            workflow_ids.add(runtime.workflow_id())
            raise exception

        if result.get(TASK_STATUS_INTERRUPT) is None:
            logger.info(f"clear checkpoint for workflow: {runtime.workflow_id()} in session: {runtime.session_id()}")
            await self._graph_store.delete(runtime.session_id(), runtime.workflow_id())
            if workflow_store is not None:
                workflow_store.clear(runtime.workflow_id())
                workflow_ids.discard(runtime.workflow_id())
            else:
                logger.warning(f"workflow_store of workflow: {runtime.workflow_id()} dose not exist in "
                            f"session: {runtime.session_id()}")

            if runtime.config().get_agent_config() is None:
                logger.info(f"clear session: {runtime.session_id()}")
                self._workflow_stores.pop(runtime.session_id(), None)
                self._session_to_workflow_ids.pop(runtime.session_id(), None)
        else:
            logger.info(f"interaction required, save checkpoint for "
                        f"workflow: {runtime.workflow_id()} in session: {runtime.session_id()}")
            if workflow_store is None:
                raise JiuWenBaseException(StatusCode.RUNTIME_CHECKPOINTER_NONE_WORKFLOW_STORE_ERROR.code,
                                          StatusCode.RUNTIME_CHECKPOINTER_NONE_WORKFLOW_STORE_ERROR.errmsg)
            workflow_store.save(runtime)
            workflow_ids.add(runtime.workflow_id())

    async def pre_agent_execute(self, runtime: BaseRuntime, inputs):
        logger.info(f"agent: {runtime.agent_id()} create or restore checkpoint from session: {runtime.session_id()}")
        agent_store = self._agent_stores.setdefault(runtime.session_id(), AgentStorage())
        agent_store.recover(runtime)
        if inputs is not None:
            runtime.state().set_state({INTERACTIVE_INPUT: [inputs]})

    async def interrupt_agent_execute(self, runtime: BaseRuntime):
        logger.info(f"interaction required, save checkpoint for "
                    f"agent: {runtime.agent_id()} in session: {runtime.session_id()}")
        agent_store = self._agent_stores.get(runtime.session_id())
        if agent_store is None:
            raise JiuWenBaseException(StatusCode.RUNTIME_CHECKPOINTER_NONE_AGENT_STORE_ERROR.code,
                                      StatusCode.RUNTIME_CHECKPOINTER_NONE_AGENT_STORE_ERROR.errmsg)
        agent_store.save(runtime)

    async def post_agent_execute(self, runtime: BaseRuntime):
        logger.info(f"agent finished, save checkpoint for "
                    f"agent: {runtime.agent_id()} in session: {runtime.session_id()}")
        agent_store = self._agent_stores.get(runtime.session_id())
        if agent_store is None:
            raise JiuWenBaseException(StatusCode.RUNTIME_CHECKPOINTER_NONE_AGENT_STORE_ERROR.code,
                                      StatusCode.RUNTIME_CHECKPOINTER_NONE_AGENT_STORE_ERROR.errmsg)
        agent_store.save(runtime)

    async def release(self, session_id: str, agent_id: str = None):
        if agent_id is not None:
            logger.info(f"clear checkpoint for agent: {agent_id} in session: {session_id}")
            agent_store = self._agent_stores.get(session_id)
            if agent_store is None:
                logger.warning(f"agent_store of agent: {agent_id} does not exist in session: {session_id}")
                return
            agent_store.clear(agent_id)
        else:
            logger.info(f"clear session: {session_id}")
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
