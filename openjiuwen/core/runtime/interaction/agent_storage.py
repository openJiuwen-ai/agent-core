# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from openjiuwen.core.runtime.interaction.interactive_input import InteractiveInput
from openjiuwen.core.runtime.interaction.storage import Storage
from openjiuwen.core.runtime.runtime import BaseRuntime
from openjiuwen.graph.store import create_serializer, Serializer


class AgentStorage(Storage):
    def __init__(self):
        self.state_blobs: dict[
            str,
            tuple[str, bytes],
        ] = {}

        self.serde: Serializer = create_serializer("pickle")

    def save(self, runtime: BaseRuntime):
        agent_id = runtime.agent_id()
        state = runtime.state().get_state()
        state_blob = self.serde.dumps_typed(state)
        if state_blob:
            self.state_blobs[agent_id] = state_blob

    def recover(self, runtime: BaseRuntime, inputs: InteractiveInput = None):
        agent_id = runtime.agent_id()
        state_blob = self.state_blobs.get(agent_id)
        if state_blob is None:
            return
        state = self.serde.loads_typed(state_blob)
        runtime.state().set_state(state)

    def clear(self, agent_id: str):
        self.state_blobs.pop(agent_id, None)

    def exists(self, runtime: BaseRuntime) -> bool:
        return self.state_blobs.get(runtime.agent_id()) is not None
