#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from abc import abstractmethod
from typing import Any

from openjiuwen.core.runtime.runtime import BaseRuntime
from openjiuwen.core.graph.atomic_node import AtomicNode
from openjiuwen.core.graph.executable import Output

FIRST_LOOP = "first_in_loop"
START_ROUND = "start_round"
END_ROUND = "end_round"
OUT_LOOP = "out_loop"


class LoopCallback(AtomicNode):
    def __call__(self, loop_stage: str, runtime: BaseRuntime, loop_times: int | None = None) -> None:
        self.atomic_invoke(loop_stage=loop_stage, runtime=runtime, loop_times=loop_times)

    def _atomic_invoke(self, **kwargs) -> Any:
        loop_stage = kwargs.get("loop_stage")
        runtime = kwargs.get("runtime")
        loop_times = kwargs.get("loop_times")
        if loop_stage == FIRST_LOOP:
            output = self.first_in_loop(runtime)
        elif loop_stage == START_ROUND:
            output = self.start_round(runtime)
        elif loop_stage == END_ROUND:
            output = self.end_round(runtime, loop_times)
        else:
            output = self.out_loop(runtime)
        if output is not None:
            runtime.state().set_outputs(output)
        return None

    @abstractmethod
    def first_in_loop(self, runtime: BaseRuntime) -> Output:
        raise NotImplementedError

    @abstractmethod
    def out_loop(self, runtime: BaseRuntime) -> Output:
        raise NotImplementedError

    @abstractmethod
    def start_round(self, runtime: BaseRuntime) -> Output:
        raise NotImplementedError

    @abstractmethod
    def end_round(self, runtime: BaseRuntime, loop_times: int) -> Output:
        raise NotImplementedError
