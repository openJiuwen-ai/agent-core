#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

from abc import ABC, ABCMeta, abstractmethod
from typing import Any

from jiuwen.core.common.constants.constant import INTERACTIVE_INPUT
from jiuwen.core.runtime.interaction.interactive_input import InteractiveInput
from jiuwen.core.runtime.runtime import BaseRuntime


class BaseInteraction(ABC, metaclass=ABCMeta):
    def __init__(self, runtime: BaseRuntime, default_input=None):
        if default_input is not None:
            self._interactive_inputs = [default_input]
        else:
            self._interactive_inputs = None
        self._latest_interactive_inputs = None
        self._idx = 0
        self._runtime = runtime
        self._init_interactive_inputs()

    def _init_interactive_inputs(self):
        interactive_inputs = self._runtime.state().get(INTERACTIVE_INPUT)
        if isinstance(interactive_inputs, list):
            if self._interactive_inputs:
                self._interactive_inputs = interactive_inputs + self._interactive_inputs
            else:
                self._interactive_inputs = interactive_inputs
        if self._interactive_inputs:
            self._runtime.state().update({INTERACTIVE_INPUT: self._interactive_inputs})
            self._latest_interactive_inputs = self._interactive_inputs[-1]

    def _get_next_interactive_input(self) -> Any | None:
        if self._interactive_inputs and self._idx < len(self._interactive_inputs):
            res = self._interactive_inputs[self._idx]
            self._idx += 1
            return res
        return None

    @abstractmethod
    async def wait_user_inputs(self, value):
        pass

    async def user_latest_input(self, value):
        pass


class Checkpointer(ABC):
    @abstractmethod
    async def pre_workflow_execute(self, runtime: BaseRuntime, inputs: InteractiveInput):
        pass

    @abstractmethod
    async def post_workflow_execute(self, runtime: BaseRuntime, result, exception):
        pass

    @abstractmethod
    async def pre_agent_execute(self, runtime: BaseRuntime, inputs):
        pass

    @abstractmethod
    async def interrupt_agent_execute(self, runtime: BaseRuntime):
        pass

    @abstractmethod
    async def post_agent_execute(self, runtime: BaseRuntime):
        pass

    @abstractmethod
    async def release(self, session_id: str):
        pass


class AgentInterrupt(Exception):
    def __init__(self, message):
        self.message = message
