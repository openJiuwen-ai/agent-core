# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.


from openjiuwen.core.common.constants.constant import INTERACTIVE_INPUT
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.common.logging import logger
from openjiuwen.core.graph.store import Store
from openjiuwen.core.session.constants import FORCE_DEL_WORKFLOW_STATE_KEY
from openjiuwen.core.session.interaction.base import Checkpointer
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.session.session import BaseSession


class InMemoryCheckpointer(Checkpointer):
    def __init__(self):
        self._agent_stores = {}
        self._workflow_stores = {}
        from openjiuwen.core.graph import InMemoryStore
        self._graph_store = InMemoryStore()
        self._session_to_workflow_ids = {}

    async def pre_workflow_execute(self, session: BaseSession, inputs: InteractiveInput):
        logger.info(f"workflow: {session.workflow_id()} create or restore checkpoint from "
                    f"session: {session.session_id()}")
        from openjiuwen.core.session.interaction.workflow_storage import WorkflowStorage
        workflow_store = self._workflow_stores.setdefault(session.session_id(), WorkflowStorage())
        self._session_to_workflow_ids.setdefault(session.session_id(), set())
        if isinstance(inputs, InteractiveInput):
            workflow_store.recover(session, inputs)
        else:
            if not workflow_store.exists(session):
                return
            if session.config().get_env(FORCE_DEL_WORKFLOW_STATE_KEY, False):
                await self._graph_store.delete(session.session_id(), session.workflow_id())
                workflow_store.clear(session.workflow_id())
            else:
                raise JiuWenBaseException(StatusCode.WORKFLOW_STATE_EXISTS_ERROR.code,
                                          StatusCode.WORKFLOW_STATE_EXISTS_ERROR.errmsg)

    async def post_workflow_execute(self, session: BaseSession, result, exception):
        workflow_store = self._workflow_stores.get(session.session_id())
        workflow_ids = self._session_to_workflow_ids.get(session.session_id())
        if exception is not None:
            logger.info(f"exception in workflow, save checkpoint for "
                        f"workflow: {session.workflow_id()} in session: {session.session_id()}")
            if workflow_store is None:
                raise JiuWenBaseException(StatusCode.SESSION_CHECKPOINTER_NONE_WORKFLOW_STORE_ERROR.code,
                                          StatusCode.SESSION_CHECKPOINTER_NONE_WORKFLOW_STORE_ERROR.errmsg)
            workflow_store.save(session)
            workflow_ids.add(session.workflow_id())
            raise exception
        from openjiuwen.core.graph.pregel import TASK_STATUS_INTERRUPT
        if result.get(TASK_STATUS_INTERRUPT) is None:
            logger.info(f"clear checkpoint for workflow: {session.workflow_id()} in session: {session.session_id()}")
            await self._graph_store.delete(session.session_id(), session.workflow_id())
            if workflow_store is not None:
                workflow_store.clear(session.workflow_id())
                workflow_ids.discard(session.workflow_id())
            else:
                logger.warning(f"workflow_store of workflow: {session.workflow_id()} dose not exist in "
                            f"session: {session.session_id()}")

            if session.config().get_agent_config() is None:
                logger.info(f"clear session: {session.session_id()}")
                self._workflow_stores.pop(session.session_id(), None)
                self._session_to_workflow_ids.pop(session.session_id(), None)
        else:
            logger.info(f"interaction required, save checkpoint for "
                        f"workflow: {session.workflow_id()} in session: {session.session_id()}")
            if workflow_store is None:
                raise JiuWenBaseException(StatusCode.SESSION_CHECKPOINTER_NONE_WORKFLOW_STORE_ERROR.code,
                                          StatusCode.SESSION_CHECKPOINTER_NONE_WORKFLOW_STORE_ERROR.errmsg)
            workflow_store.save(session)
            workflow_ids.add(session.workflow_id())

    async def pre_agent_execute(self, session: BaseSession, inputs):
        logger.info(f"agent: {session.agent_id()} create or restore checkpoint from session: {session.session_id()}")
        from openjiuwen.core.session.interaction.agent_storage import AgentStorage
        agent_store = self._agent_stores.setdefault(session.session_id(), AgentStorage())
        agent_store.recover(session)
        if inputs is not None:
            session.state().set_state({INTERACTIVE_INPUT: [inputs]})

    async def interrupt_agent_execute(self, session: BaseSession):
        logger.info(f"interaction required, save checkpoint for "
                    f"agent: {session.agent_id()} in session: {session.session_id()}")
        agent_store = self._agent_stores.get(session.session_id())
        if agent_store is None:
            raise JiuWenBaseException(StatusCode.SESSION_CHECKPOINTER_NONE_AGENT_STORE_ERROR.code,
                                      StatusCode.SESSION_CHECKPOINTER_NONE_AGENT_STORE_ERROR.errmsg)
        agent_store.save(session)

    async def post_agent_execute(self, session: BaseSession):
        logger.info(f"agent finished, save checkpoint for "
                    f"agent: {session.agent_id()} in session: {session.session_id()}")
        agent_store = self._agent_stores.get(session.session_id())
        if agent_store is None:
            raise JiuWenBaseException(StatusCode.SESSION_CHECKPOINTER_NONE_AGENT_STORE_ERROR.code,
                                      StatusCode.SESSION_CHECKPOINTER_NONE_AGENT_STORE_ERROR.errmsg)
        agent_store.save(session)

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



