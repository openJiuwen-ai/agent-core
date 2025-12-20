#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from abc import abstractmethod, ABC

from openjiuwen.core.component.base import WorkflowComponent
from openjiuwen.core.graph.executable import Input, Output, Executable
from openjiuwen.core.runtime.runtime import BaseRuntime
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode


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
            raise JiuWenBaseException(StatusCode.BREAK_COMPONENT_INIT_ERROR.code,
                                      StatusCode.BREAK_COMPONENT_INIT_ERROR.errmsg)
        self._loop_controller.break_loop()
        return {}
