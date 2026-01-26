# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from openjiuwen.core.common.constants.constant import INTERACTIVE_INPUT
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.session.checkpointer.storage import Storage
from openjiuwen.core.session.session import BaseSession
from openjiuwen.core.session.internal.workflow import NodeSession
from openjiuwen.core.graph.store import create_serializer, Serializer


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

    def save(self, session: BaseSession):
        workflow_id = session.workflow_id()
        state = session.state().get_state()
        state_blob = self.serde.dumps_typed(state)
        if state_blob:
            self.state_blobs[workflow_id] = state_blob

        updates = session.state().get_updates()
        updates_blob = self.serde.dumps_typed(updates)
        if updates_blob:
            self.state_updates_blobs[workflow_id] = updates_blob

    def recover(self, session: BaseSession, inputs: InteractiveInput = None):
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

    def clear(self, workflow_id: str):
        self.state_blobs.pop(workflow_id, None)
        self.state_updates_blobs.pop(workflow_id, None)

    def exists(self, session: BaseSession) -> bool:
        state_blob = self.state_blobs.get(session.workflow_id())
        if state_blob and state_blob[0] != "empty":
            return True
        return False
