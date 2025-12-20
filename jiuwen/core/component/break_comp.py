#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from abc import abstractmethod, ABC

from jiuwen.core.component.base import WorkflowComponent
from jiuwen.core.graph.executable import Input, Output, Executable
from jiuwen.core.runtime.runtime import BaseRuntime


class LoopController(ABC):
    @abstractmethod
    def break_loop(self):
        raise NotImplementedError()

    @abstractmethod
    def is_broken(self) -> bool:
        raise NotImplementedError()


class BreakComponent(WorkflowComponent, Executable):
    def __init__(self):
        super().__init__()
        self._loop_controller = None

    def set_controller(self, loop_controller: LoopController):
        self._loop_controller = loop_controller

    async def on_invoke(self, inputs: Input, runtime: BaseRuntime) -> Output:
        if self._loop_controller is None:
            raise RuntimeError('Loop controller not initialized')
        self._loop_controller.break_loop()
        return {}
