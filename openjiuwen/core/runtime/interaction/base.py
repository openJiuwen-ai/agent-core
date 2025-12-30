# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

from abc import ABC, ABCMeta, abstractmethod
from typing import Any

from openjiuwen.core.common.constants.constant import INTERACTIVE_INPUT
from openjiuwen.core.runtime.interaction.interactive_input import InteractiveInput
from openjiuwen.core.runtime.runtime import BaseRuntime
from openjiuwen.graph.store import Store


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
    @staticmethod
    def get_thread_id(runtime: BaseRuntime) -> str:
        return ":".join([runtime.session_id(), runtime.workflow_id()])

    @abstractmethod
    async def pre_workflow_execute(self, runtime: BaseRuntime, inputs: InteractiveInput):
        ...

    @abstractmethod
    async def post_workflow_execute(self, runtime: BaseRuntime, result, exception):
        ...

    @abstractmethod
    async def pre_agent_execute(self, runtime: BaseRuntime, inputs):
        ...

    @abstractmethod
    async def interrupt_agent_execute(self, runtime: BaseRuntime):
        ...

    @abstractmethod
    async def post_agent_execute(self, runtime: BaseRuntime):
        ...

    @abstractmethod
    async def release(self, session_id: str):
        ...

    @abstractmethod
    def graph_store(self) -> Store:
        ...


class AgentInterrupt(Exception):
    def __init__(self, message):
        super().__init__()
        self.message = message
