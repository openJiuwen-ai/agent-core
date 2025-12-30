# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from openjiuwen.core.common.constants.constant import INTERACTION
from openjiuwen.core.common.constants.constant import INTERACTIVE_INPUT
from openjiuwen.core.runtime.agent import AgentRuntime
from openjiuwen.core.runtime.interaction.base import BaseInteraction, AgentInterrupt
from openjiuwen.core.runtime.runtime import BaseRuntime
from openjiuwen.core.stream.base import OutputSchema
from openjiuwen.graph.pregel import Interrupt, GraphInterrupt


class InteractionOutput(BaseModel):
    id: str
    value: Any


class WorkflowInteraction(BaseInteraction):
    def __init__(self, runtime: BaseRuntime):
        self._node_id = runtime.executable_id()
        workflow_interactive_input = runtime.state().get_workflow_state(INTERACTIVE_INPUT)
        if workflow_interactive_input is not None:
            runtime.state().update_and_commit_workflow_state({INTERACTIVE_INPUT: None})
        super().__init__(runtime, workflow_interactive_input)

    async def wait_user_inputs(self, value: Any) -> Any:
        res = self._get_next_interactive_input()
        if res is not None:
            return res
        self._runtime.state().commit_cmp()
        payload = InteractionOutput(id=self._node_id, value=value)
        if self._runtime.stream_writer_manager():
            output_writer = self._runtime.stream_writer_manager().get_output_writer()
            await output_writer.write(OutputSchema(type=INTERACTION, index=self._idx, payload=payload))
        raise GraphInterrupt((Interrupt(
            value=OutputSchema(type=INTERACTION, index=self._idx, payload=payload)),))

    async def user_latest_input(self, value: Any) -> Any:
        if self._latest_interactive_inputs:
            res = self._latest_interactive_inputs
            self._latest_interactive_inputs = None
            return res
        if self._runtime.stream_writer_manager:
            output_writer = self._runtime.stream_writer_manager().get_output_writer()
            await output_writer.write(OutputSchema(type=INTERACTION, index=self._idx, payload=(self._node_id, value)))

        raise GraphInterrupt((Interrupt(
            value=OutputSchema(type=INTERACTION, index=self._idx, payload=(self._node_id, None)), resumable=True,
            ns=self._node_id),))


class SimpleAgentInteraction:
    def __init__(self, runtime: AgentRuntime):
        self._agent_runtime = runtime

    async def wait_user_inputs(self, message):
        await self._agent_runtime.checkpointer().interrupt_agent_execute(self._agent_runtime)
        raise AgentInterrupt(message)


class AgentInteraction(BaseInteraction):
    def __init__(self, runtime: AgentRuntime):
        super().__init__(runtime)
        self._agent_runtime = runtime

    async def wait_user_inputs(self, value):
        inputs = self._get_next_interactive_input()
        if inputs is not None:
            return inputs

        await self._agent_runtime.checkpointer().interrupt_agent_execute(self._runtime)
        payload = InteractionOutput(id=self._runtime.executable_id(), value=value)
        writer_manager = self._runtime.stream_writer_manager()
        if writer_manager is not None:
            output_writer = writer_manager.get_output_writer()
            await output_writer.write(OutputSchema(type=INTERACTION, index=self._idx, payload=payload))

        raise AgentInterrupt()
