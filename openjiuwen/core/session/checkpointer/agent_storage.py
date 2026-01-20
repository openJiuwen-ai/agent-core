# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.session.checkpointer.storage import Storage
from openjiuwen.core.session.session import BaseSession
from openjiuwen.core.graph.store import create_serializer, Serializer


class AgentStorage(Storage):
    def __init__(self):
        self.state_blobs: dict[
            str,
            tuple[str, bytes],
        ] = {}

        self.serde: Serializer = create_serializer("pickle")

    def save(self, session: BaseSession):
        agent_id = session.agent_id()
        state = session.state().get_state()
        state_blob = self.serde.dumps_typed(state)
        if state_blob:
            self.state_blobs[agent_id] = state_blob

    def recover(self, session: BaseSession, inputs: InteractiveInput = None):
        agent_id = session.agent_id()
        state_blob = self.state_blobs.get(agent_id)
        if state_blob is None:
            return
        state = self.serde.loads_typed(state_blob)
        session.state().set_state(state)

    def clear(self, agent_id: str):
        self.state_blobs.pop(agent_id, None)

    def exists(self, session: BaseSession) -> bool:
        return self.state_blobs.get(session.agent_id()) is not None
