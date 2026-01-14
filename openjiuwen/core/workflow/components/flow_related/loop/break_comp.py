# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from abc import abstractmethod, ABC

from openjiuwen.core.workflow.components.component import ComponentComposable
from openjiuwen.core.graph.executable import Input, Output, Executable
from openjiuwen.core.session import BaseSession
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode


class LoopController(ABC):
    @abstractmethod
    def break_loop(self):
        raise NotImplementedError()

    @abstractmethod
    def is_broken(self) -> bool:
        raise NotImplementedError()


class BreakComponent(ComponentComposable, Executable):
    def __init__(self):
        super().__init__()
        self._loop_controller = None

    def set_controller(self, loop_controller: LoopController):
        self._loop_controller = loop_controller

    async def on_invoke(self, inputs: Input, session: BaseSession, **kwargs) -> Output:
        if self._loop_controller is None:
            raise JiuWenBaseException(StatusCode.COMPONENT_BREAK_EXECUTION_ERROR.code,
                                      StatusCode.COMPONENT_BREAK_EXECUTION_ERROR.errmsg.format(
                                          error_msg="failed to initialize loop controller"
                                      ))
        self._loop_controller.break_loop()
        return {}
