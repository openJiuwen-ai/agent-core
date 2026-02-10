# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from importlib_metadata import metadata

from openjiuwen.core.common.constants.constant import INTERACTIVE_INPUT
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import session_logger, LogEventType
from openjiuwen.core.graph.store import (
    Serializer,
    Store,
    create_serializer,
)
from openjiuwen.core.session.checkpointer import Checkpointer
from openjiuwen.core.session.checkpointer.base import Storage
from openjiuwen.core.session.constants import FORCE_DEL_WORKFLOW_STATE_KEY
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.session.internal.workflow import NodeSession
from openjiuwen.core.session.session import BaseSession


class InMemoryCheckpointer(Checkpointer):
    def __init__(self):
        self._agent_stores = {}
        self._workflow_stores = {}
        from openjiuwen.core.graph import InMemoryStore
        self._graph_store = InMemoryStore()
        self._session_to_workflow_ids = {}

    async def pre_workflow_execute(self, session: BaseSession, inputs: InteractiveInput):
        session_id = session.session_id()
        workflow_id = session.workflow_id()
        is_new_workflow_store = session_id not in self._workflow_stores
        workflow_store = self._workflow_stores.setdefault(session_id, WorkflowStorage())
        if is_new_workflow_store:
            session_logger.debug(
                "Workflow store added",
                event_type=LogEventType.CHECKPOINTER_STORE_ADD,
                session_id=session_id,
                workflow_id=workflow_id,
                metadata={"store_type": "workflow", "storage_type": "inmemory"}
            )
        self._session_to_workflow_ids.setdefault(session_id, set())
        if isinstance(inputs, InteractiveInput):
            session_logger.debug(
                "Workflow store recover",
                event_type=LogEventType.CHECKPOINT_RESTORE,
                session_id=session_id,
                workflow_id=workflow_id,
                metadata={"store_type": "workflow", "storage_type": "inmemory"}
            )
            await workflow_store.recover(session, inputs)
        else:
            if not await workflow_store.exists(session):
                return
            if session.config().get_env(FORCE_DEL_WORKFLOW_STATE_KEY, False):
                session_logger.warning(
                    "Workflow store not found during cleanup",
                    event_type=LogEventType.CHECKPOINT_ERROR,
                    session_id=session_id,
                    workflow_id=workflow_id,
                    metadata={"store_type": "workflow", "storage_type": "inmemory"}
                )
                await self._graph_store.delete(session_id, workflow_id)
                await workflow_store.clear(workflow_id)
            else:
                raise build_error(StatusCode.CHECKPOINTER_PRE_WORKFLOW_EXECUTION_ERROR, session_id=session_id,
                                  workflow=workflow_id,
                                  reason="workflow state exists but non-interactive input and cleanup is disabled")

    async def post_workflow_execute(self, session: BaseSession, result, exception):
        session_id = session.session_id()
        workflow_id = session.workflow_id()
        workflow_store = self._workflow_stores.get(session_id)
        workflow_ids = self._session_to_workflow_ids.get(session_id)
        if exception is not None:
            if workflow_store is None:
                raise build_error(StatusCode.CHECKPOINTER_POST_WORKFLOW_EXECUTION_ERROR, workflow=workflow_id,
                                  reason="workflow store not found")
            session_logger.info(
                "Workflow checkpoint save on exception",
                event_type=LogEventType.CHECKPOINT_SAVE,
                session_id=session_id,
                workflow_id=workflow_id,
                metadata={"storage_type": "inmemory"}
            )
            await workflow_store.save(session)
            workflow_ids.add(workflow_id)
            raise exception
        from openjiuwen.core.graph.pregel import TASK_STATUS_INTERRUPT
        if result.get(TASK_STATUS_INTERRUPT) is None:
            session_logger.info(
                "Workflow checkpoint cleared on completion",
                event_type=LogEventType.CHECKPOINT_CLEAR,
                session_id=session_id,
                workflow_id=workflow_id,
                metadata={"reason": "workflow_completed", "storage_type": "inmemory"}
            )
            await self._graph_store.delete(session_id, workflow_id)
            if workflow_store is not None:
                await workflow_store.clear(workflow_id)
                workflow_ids.discard(workflow_id)
            else:
                session_logger.warning(
                    "Workflow store not found during cleanup",
                    event_type=LogEventType.CHECKPOINT_ERROR,
                    session_id=session_id,
                    workflow_id=workflow_id,
                    metadata={"operation": "clear"}
                )

            from openjiuwen.core.session.internal.agent import AgentSession
            if not isinstance(session.parent(), AgentSession):
                session_logger.info(
                    "Session cleared",
                    event_type=LogEventType.CHECKPOINT_CLEAR,
                    session_id=session_id,
                    metadata={"operation": "cleanup", "storage_type": "inmemory"}
                )
                if session_id in self._workflow_stores:
                    session_logger.debug(
                        "Workflow store deleted",
                        event_type=LogEventType.CHECKPOINTER_STORE_DELETE,
                        session_id=session_id,
                        metadata={"store_type": "workflow", "storage_type": "inmemory"}
                    )
                self._workflow_stores.pop(session_id, None)
                self._session_to_workflow_ids.pop(session_id, None)
        else:
            session_logger.info(
                "Workflow checkpoint save on interrupt",
                event_type=LogEventType.CHECKPOINT_SAVE,
                session_id=session_id,
                workflow_id=workflow_id,
                metadata={"reason": "interaction_required", "storage_type": "inmemory"}
            )
            if workflow_store is None:
                raise build_error(StatusCode.CHECKPOINTER_POST_WORKFLOW_EXECUTION_ERROR, workflow=workflow_id,
                                  reason="workflow store not found")
            await workflow_store.save(session)
            workflow_ids.add(workflow_id)

    async def pre_agent_execute(self, session: BaseSession, inputs):
        session_logger.info(
            "Agent checkpoint restore initiated",
            event_type=LogEventType.CHECKPOINT_RESTORE,
            session_id=session.session_id(),
            agent_id=session.agent_id(),
            metadata={"operation": "pre_execute", "storage_type": "inmemory"}
        )
        session_id = session.session_id()
        is_new_agent_store = session_id not in self._agent_stores
        agent_store = self._agent_stores.setdefault(session_id, AgentStorage())
        if is_new_agent_store:
            session_logger.debug(
                "Agent store added",
                event_type=LogEventType.CHECKPOINTER_STORE_ADD,
                session_id=session_id,
                metadata={"store_type": "agent", "storage_type": "inmemory"}
            )
        await agent_store.recover(session)
        if inputs is not None:
            session.state().set_state({INTERACTIVE_INPUT: [inputs]})

    async def interrupt_agent_execute(self, session: BaseSession):
        agent_id = session.agent_id()
        session_logger.info(
            "Agent checkpoint save on interrupt",
            event_type=LogEventType.CHECKPOINT_SAVE,
            session_id=session.session_id(),
            agent_id=agent_id,
            metadata={"reason": "interaction_required", "storage_type": "inmemory"}
        )
        agent_store = self._agent_stores.get(session.session_id())
        if agent_store is None:
            raise build_error(StatusCode.CHECKPOINTER_INTERRUPT_AGENT_ERROR, agent=agent_id,
                              reason="agent store not found")
        await agent_store.save(session)

    async def post_agent_execute(self, session: BaseSession):
        agent_id = session.agent_id()
        session_logger.info(
            "Agent checkpoint save on completion",
            event_type=LogEventType.CHECKPOINT_SAVE,
            session_id=session.session_id(),
            agent_id=agent_id,
            metadata={"reason": "agent_finished", "storage_type": "inmemory"}
        )
        agent_store = self._agent_stores.get(session.session_id())
        if agent_store is None:
            raise build_error(StatusCode.CHECKPOINTER_POST_AGENT_EXECUTION_ERROR,
                              agent=agent_id, reason="agent store not found")
        await agent_store.save(session)

    async def session_exists(self, session_id: str) -> bool:
        return session_id in self._agent_stores or session_id in self._workflow_stores

    async def release(self, session_id: str, agent_id: str = None):
        if agent_id is not None:
            session_logger.info(
                "Agent checkpoint cleared",
                event_type=LogEventType.CHECKPOINT_CLEAR,
                session_id=session_id,
                agent_id=agent_id,
                metadata={"operation": "release", "storage_type": "inmemory"}
            )
            agent_store = self._agent_stores.get(session_id)
            if agent_store is None:
                session_logger.warning(
                    "Agent store not found during release",
                    event_type=LogEventType.CHECKPOINT_ERROR,
                    session_id=session_id,
                    agent_id=agent_id,
                    metadata={"operation": "release"}
                )
                return
            await agent_store.clear(agent_id)
        else:
            session_logger.info(
                "Session cleared",
                event_type=LogEventType.CHECKPOINT_CLEAR,
                session_id=session_id,
                metadata={"operation": "release_all", "storage_type": "inmemory"}
            )
            workflow_ids = self._session_to_workflow_ids.get(session_id)
            if workflow_ids:
                for workflow_id in workflow_ids:
                    await self._graph_store.delete(session_id, workflow_id)
            self._session_to_workflow_ids.pop(session_id, None)
            if session_id in self._workflow_stores:
                session_logger.debug(
                    "Workflow store deleted",
                    event_type=LogEventType.CHECKPOINTER_STORE_DELETE,
                    session_id=session_id,
                    metadata={"store_type": "workflow", "storage_type": "inmemory", "operation": "release"}
                )
            self._workflow_stores.pop(session_id, None)
            if session_id in self._agent_stores:
                session_logger.debug(
                    "Agent store deleted",
                    event_type=LogEventType.CHECKPOINTER_STORE_DELETE,
                    session_id=session_id,
                    metadata={"store_type": "agent", "storage_type": "inmemory", "operation": "release"}
                )
            self._agent_stores.pop(session_id, None)

    def graph_store(self) -> Store:
        return self._graph_store


class AgentStorage(Storage):
    def __init__(self):
        self.state_blobs: dict[
            str,
            tuple[str, bytes],
        ] = {}

        self.serde: Serializer = create_serializer("pickle")

    async def save(self, session: BaseSession):
        agent_id = session.agent_id()
        state = session.state().get_state()
        state_blob = self.serde.dumps_typed(state)
        if state_blob:
            self.state_blobs[agent_id] = state_blob

    async def recover(self, session: BaseSession, inputs: InteractiveInput = None):
        agent_id = session.agent_id()
        state_blob = self.state_blobs.get(agent_id)
        if state_blob is None:
            return
        state = self.serde.loads_typed(state_blob)
        session.state().set_state(state)

    async def clear(self, agent_id: str):
        self.state_blobs.pop(agent_id, None)

    async def exists(self, session: BaseSession) -> bool:
        return self.state_blobs.get(session.agent_id()) is not None


class WorkflowStorage(Storage):
    def __init__(self):
        self.serde: Serializer = create_serializer("pickle")
        self.state_blobs: dict[
            str,
            tuple[str, bytes],
        ] = {}

        self.state_updates_blobs: dict[
            str,
            tuple[str, bytes]
        ] = {}

    async def save(self, session: BaseSession):
        workflow_id = session.workflow_id()
        state = session.state().get_state()
        state_blob = self.serde.dumps_typed(state)
        if state_blob:
            self.state_blobs[workflow_id] = state_blob

        updates = session.state().get_updates()
        updates_blob = self.serde.dumps_typed(updates)
        if updates_blob:
            self.state_updates_blobs[workflow_id] = updates_blob

    async def recover(self, session: BaseSession, inputs: InteractiveInput = None):
        workflow_id = session.workflow_id()
        state_blob = self.state_blobs.get(workflow_id)
        if state_blob and state_blob[0] != "empty":
            state = self.serde.loads_typed(state_blob)
            session.state().set_state(state)

        if inputs.raw_inputs is not None:
            session.state().update_and_commit_workflow_state({INTERACTIVE_INPUT: inputs.raw_inputs})
        else:
            for node_id, value in inputs.user_inputs.items():
                node_session = NodeSession(session, node_id)
                interactive_input = node_session.state().get(INTERACTIVE_INPUT)
                if isinstance(interactive_input, list):
                    interactive_input.append(value)
                    node_session.state().update({INTERACTIVE_INPUT: interactive_input})
                else:
                    node_session.state().update({INTERACTIVE_INPUT: [value]})
            session.state().commit()

        state_updates_blob = self.state_updates_blobs.get(workflow_id)
        if state_updates_blob:
            state_updates = self.serde.loads_typed(state_updates_blob)
            session.state().set_updates(state_updates)

    async def clear(self, workflow_id: str):
        self.state_blobs.pop(workflow_id, None)
        self.state_updates_blobs.pop(workflow_id, None)

    async def exists(self, session: BaseSession) -> bool:
        state_blob = self.state_blobs.get(session.workflow_id())
        if state_blob and state_blob[0] != "empty":
            return True
        return False
