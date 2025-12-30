# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from openjiuwen.core.common.constants.constant import INTERACTIVE_INPUT
from openjiuwen.core.runtime.interaction.interactive_input import InteractiveInput
from openjiuwen.core.runtime.interaction.storage import Storage
from openjiuwen.core.runtime.runtime import BaseRuntime
from openjiuwen.core.runtime.workflow import NodeRuntime
from openjiuwen.graph.store import create_serializer, Serializer


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

    def save(self, runtime: BaseRuntime):
        workflow_id = runtime.workflow_id()
        state = runtime.state().get_state()
        state_blob = self.serde.dumps_typed(state)
        if state_blob:
            self.state_blobs[workflow_id] = state_blob

        updates = runtime.state().get_updates()
        updates_blob = self.serde.dumps_typed(updates)
        if updates_blob:
            self.state_updates_blobs[workflow_id] = updates_blob

    def recover(self, runtime: BaseRuntime, inputs: InteractiveInput = None):
        workflow_id = runtime.workflow_id()
        state_blob = self.state_blobs.get(workflow_id)
        if state_blob and state_blob[0] != "empty":
            state = self.serde.loads_typed(state_blob)
            runtime.state().set_state(state)

        if inputs.raw_inputs is not None:
            runtime.state().update_and_commit_workflow_state({INTERACTIVE_INPUT: inputs.raw_inputs})
        else:
            for node_id, value in inputs.user_inputs.items():
                node_runtime = NodeRuntime(runtime, node_id)
                interactive_input = node_runtime.state().get(INTERACTIVE_INPUT)
                if isinstance(interactive_input, list):
                    interactive_input.append(value)
                    node_runtime.state().update({INTERACTIVE_INPUT: interactive_input})
                else:
                    node_runtime.state().update({INTERACTIVE_INPUT: [value]})
            runtime.state().commit()

        state_updates_blob = self.state_updates_blobs.get(workflow_id)
        if state_updates_blob:
            state_updates = self.serde.loads_typed(state_updates_blob)
            runtime.state().set_updates(state_updates)

    def clear(self, workflow_id: str):
        self.state_blobs.pop(workflow_id, None)
        self.state_updates_blobs.pop(workflow_id, None)

    def exists(self, runtime: BaseRuntime) -> bool:
        state_blob = self.state_blobs.get(runtime.workflow_id())
        if state_blob and state_blob[0] != "empty":
            return True
        return False
