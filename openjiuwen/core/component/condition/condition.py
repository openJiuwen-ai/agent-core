#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from abc import abstractmethod
from typing import Callable, Any

from openjiuwen.core.runtime.runtime import BaseRuntime
from openjiuwen.core.graph.atomic_node import AtomicNode
from openjiuwen.core.graph.executable import Input, Output


class Condition(AtomicNode):
    def __init__(self, input_schema: Any = None):
        self._input_schema = input_schema

    def __call__(self, runtime: BaseRuntime) -> bool:
        return self.atomic_invoke(runtime=runtime)

    def _atomic_invoke(self, **kwargs) -> Any:
        runtime: BaseRuntime = kwargs["runtime"]
        inputs = runtime.state().get_inputs(self._input_schema) if self._input_schema is not None else {}
        result = self.invoke(inputs=inputs, runtime=runtime)
        if isinstance(result, tuple):
            runtime.state().set_outputs(result[1])
            result = result[0]
        return result

    @abstractmethod
    def invoke(self, inputs: Input, runtime: BaseRuntime) -> Output:
        pass

    def trace_info(self, runtime: BaseRuntime = None):
        return ""


class FuncCondition(Condition):
    def __init__(self, func: Callable[[], bool]):
        super().__init__()
        self._func = func

    def invoke(self, inputs: Input, runtime: BaseRuntime) -> Output:
        return self._func()

    def trace_info(self, runtime: BaseRuntime = None):
        return self._func.__name__


class AlwaysTrue(Condition):
    def invoke(self, inputs: Input, runtime: BaseRuntime) -> Output:
        return True

    def trace_info(self, runtime: BaseRuntime = None):
        return "True"
