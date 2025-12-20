#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import AsyncIterator, TypeVar

from jiuwen.core.common.exception.exception import JiuWenBaseException
from jiuwen.core.context_engine.base import Context
from jiuwen.core.graph.executable import Executable
from jiuwen.core.runtime.runtime import Runtime, BaseRuntime
from jiuwen.core.runtime.workflow import NodeRuntime
from jiuwen.core.runtime.wrapper import WrappedNodeRuntime

Input = TypeVar("Input", contravariant=True)
Output = TypeVar("Output", contravariant=True)


class ComponentExecutable(Executable):

    async def on_invoke(self, inputs: Input, runtime: BaseRuntime) -> Output:
        if not isinstance(runtime, NodeRuntime):
            raise JiuWenBaseException(-1, "runtime should be NodeRuntime instance")
        return await self.invoke(inputs, WrappedNodeRuntime(runtime), runtime.context())

    async def on_stream(self, inputs: Input, runtime: BaseRuntime) -> AsyncIterator[Output]:
        if not isinstance(runtime, NodeRuntime):
            raise JiuWenBaseException(-1, "runtime should be NodeRuntime instance")
        async for value in self.stream(inputs, WrappedNodeRuntime(runtime), runtime.context()):
            yield value

    async def on_collect(self, inputs: AsyncIterator[Input], runtime: BaseRuntime) -> Output:
        if not isinstance(runtime, NodeRuntime):
            raise JiuWenBaseException(-1, "runtime should be NodeRuntime instance")
        return await self.collect(inputs, WrappedNodeRuntime(runtime), runtime.context())

    async def on_transform(self, inputs: AsyncIterator[Input], runtime: BaseRuntime) -> AsyncIterator[Output]:
        if not isinstance(runtime, NodeRuntime):
            raise JiuWenBaseException(-1, "runtime should be NodeRuntime instance")
        async for value in self.transform(inputs, WrappedNodeRuntime(runtime), runtime.context()):
            yield value

    async def invoke(self, inputs: Input, runtime: Runtime, context: Context) -> Output:
        raise JiuWenBaseException(-1, "Invoke is not supported")

    async def stream(self, inputs: Input, runtime: Runtime, context: Context) -> AsyncIterator[Output]:
        raise JiuWenBaseException(-1, "Stream is not supported")

    async def collect(self, inputs: AsyncIterator[Input], runtime: Runtime, context: Context) -> Output:
        raise JiuWenBaseException(-1, "Collect is not supported")

    async def transform(self, inputs: AsyncIterator[Input], runtime: Runtime, context: Context) -> AsyncIterator[
        Output]:
        raise JiuWenBaseException(-1, "Transform is not supported")
