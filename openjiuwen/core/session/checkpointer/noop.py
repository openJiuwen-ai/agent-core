# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from openjiuwen.core.graph.store import Store
from openjiuwen.core.session.checkpointer.base import Checkpointer
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.session.session import BaseSession


class NoopCheckpointer(Checkpointer):
    """Checkpointer that neither restores nor saves session state.

    Inject it into a Session whose cross-run state is owned by another store.
    Session lifecycle methods still run; only their checkpoint I/O is skipped.
    """

    def __init__(self):
        from openjiuwen.core.graph import InMemoryStore

        # Workflow components inside one Session may still need a graph store;
        # it lives and dies with this instance and is never shared across Sessions.
        self._graph_store = InMemoryStore()

    async def pre_workflow_execute(self, session: BaseSession, inputs: InteractiveInput):
        return None

    async def post_workflow_execute(self, session: BaseSession, result, exception):
        return None

    async def pre_agent_execute(self, session: BaseSession, inputs):
        return None

    async def pre_agent_team_execute(self, session: BaseSession, inputs):
        return None

    async def interrupt_agent_execute(self, session: BaseSession):
        return None

    async def post_agent_execute(self, session: BaseSession):
        return None

    async def post_agent_team_execute(self, session: BaseSession):
        return None

    async def session_exists(self, session_id: str) -> bool:
        return False

    async def release(self, session_id: str, agent_id: str = None):
        return None

    def graph_store(self) -> Store:
        return self._graph_store
