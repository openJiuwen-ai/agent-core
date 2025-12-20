#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Controller of Agent"""
from typing import AsyncIterator, Union

from pydantic import BaseModel, Field

from jiuwen.agent.config.base import AgentConfig
from jiuwen.core.runtime.interaction.interactive_input import InteractiveInput
from jiuwen.core.runtime.runtime import Runtime


class ControllerOutput(BaseModel):
    is_task: bool = False


class ControllerInput(BaseModel):
    query: Union[str, InteractiveInput] = Field(default="")


class Controller:
    def __init__(self, config: AgentConfig):
        self._config = config
        self._agent_handler = None

    def invoke(self, inputs: ControllerInput, context: Runtime) -> ControllerOutput:
        pass

    async def stream(self,
                     inputs: ControllerInput,
                     context: Runtime
                     ) -> AsyncIterator[ControllerOutput]:
        pass

    def should_continue(self, output) -> bool:
        pass
