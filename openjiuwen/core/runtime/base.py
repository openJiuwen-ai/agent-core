#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import inspect
from typing import AsyncIterator, TypeVar

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.context_engine.base import Context
from openjiuwen.core.graph.executable import Executable
from openjiuwen.core.runtime.runtime import Runtime, BaseRuntime
from openjiuwen.core.runtime.workflow import NodeRuntime
from openjiuwen.core.runtime.wrapper import WrappedNodeRuntime

Input = TypeVar("Input", contravariant=True)
Output = TypeVar("Output", contravariant=True)


class ComponentExecutable(Executable):

    async def on_invoke(self, inputs: Input, runtime: BaseRuntime) -> Output:
        if not isinstance(runtime, NodeRuntime):
            raise JiuWenBaseException(StatusCode.RUNTIME_COMPONENT_INVALID_RUNTIME_TYPE.code, 
                                    StatusCode.RUNTIME_COMPONENT_INVALID_RUNTIME_TYPE.errmsg)
        
        current_class = type(self)
        # Check if the attribute exists, is callable, and is not the base implementation
        if (hasattr(current_class, 'invoke') and 
            callable(getattr(current_class, 'invoke')) and 
            current_class.invoke is ComponentExecutable.invoke):
            raise JiuWenBaseException(StatusCode.RUNTIME_COMPONENT_ABILITY_NOT_IMPLEMENTED.code, 
                                    StatusCode.RUNTIME_COMPONENT_ABILITY_NOT_IMPLEMENTED.errmsg.format(
                                        ability='INVOKE', method='invoke', class_name=type(self).__name__))
        
        return await self.invoke(inputs, WrappedNodeRuntime(runtime), runtime.context())

    async def on_stream(self, inputs: Input, runtime: BaseRuntime) -> AsyncIterator[Output]:
        if not isinstance(runtime, NodeRuntime):
            raise JiuWenBaseException(StatusCode.RUNTIME_COMPONENT_INVALID_RUNTIME_TYPE.code, 
                                    StatusCode.RUNTIME_COMPONENT_INVALID_RUNTIME_TYPE.errmsg)
        
        current_class = type(self)
        
        # Check if the attribute exists, is callable, and is not the base implementation
        if (hasattr(current_class, 'stream') and 
            callable(getattr(current_class, 'stream')) and 
            current_class.stream is ComponentExecutable.stream):
            raise JiuWenBaseException(StatusCode.RUNTIME_COMPONENT_ABILITY_NOT_IMPLEMENTED.code, 
                                    StatusCode.RUNTIME_COMPONENT_ABILITY_NOT_IMPLEMENTED.errmsg.format(
                                        ability='STREAM', method='stream', class_name=type(self).__name__))
        
        async for value in self.stream(inputs, WrappedNodeRuntime(runtime), runtime.context()):
            yield value

    async def on_collect(self, inputs: Input, runtime: BaseRuntime) -> Output:
        if not isinstance(runtime, NodeRuntime):
            raise JiuWenBaseException(StatusCode.RUNTIME_COMPONENT_INVALID_RUNTIME_TYPE.code, 
                                    StatusCode.RUNTIME_COMPONENT_INVALID_RUNTIME_TYPE.errmsg)
        
        current_class = type(self)
        
        # Check if the attribute exists, is callable, and is not the base implementation
        if (hasattr(current_class, 'collect') and 
            callable(getattr(current_class, 'collect')) and 
            current_class.collect is ComponentExecutable.collect):
            raise JiuWenBaseException(StatusCode.RUNTIME_COMPONENT_ABILITY_NOT_IMPLEMENTED.code, 
                                    StatusCode.RUNTIME_COMPONENT_ABILITY_NOT_IMPLEMENTED.errmsg.format(
                                        ability='COLLECT', method='collect', class_name=type(self).__name__))
        
        return await self.collect(inputs, WrappedNodeRuntime(runtime, True), runtime.context())

    async def on_transform(self, inputs: Input, runtime: BaseRuntime) -> AsyncIterator[Output]:
        if not isinstance(runtime, NodeRuntime):
            raise JiuWenBaseException(StatusCode.RUNTIME_COMPONENT_INVALID_RUNTIME_TYPE.code, 
                                    StatusCode.RUNTIME_COMPONENT_INVALID_RUNTIME_TYPE.errmsg)
        
        current_class = type(self)
        
        # Check if the attribute exists, is callable, and is not the base implementation
        if (hasattr(current_class, 'transform') and 
            callable(getattr(current_class, 'transform')) and 
            current_class.transform is ComponentExecutable.transform):
            raise JiuWenBaseException(StatusCode.RUNTIME_COMPONENT_ABILITY_NOT_IMPLEMENTED.code, 
                                    StatusCode.RUNTIME_COMPONENT_ABILITY_NOT_IMPLEMENTED.errmsg.format(
                                        ability='TRANSFORM', method='transform', class_name=type(self).__name__))
        
        async for value in self.transform(inputs, WrappedNodeRuntime(runtime, True), runtime.context()):
            yield value

    async def invoke(self, inputs: Input, runtime: Runtime, context: Context) -> Output:
        raise JiuWenBaseException(StatusCode.RUNTIME_COMPONENT_ABILITY_NOT_SUPPORTED.code, 
                                StatusCode.RUNTIME_COMPONENT_ABILITY_NOT_SUPPORTED.errmsg.format(ability='Invoke'))

    async def stream(self, inputs: Input, runtime: Runtime, context: Context) -> AsyncIterator[Output]:
        raise JiuWenBaseException(StatusCode.RUNTIME_COMPONENT_ABILITY_NOT_SUPPORTED.code, 
                                StatusCode.RUNTIME_COMPONENT_ABILITY_NOT_SUPPORTED.errmsg.format(ability='Stream'))

    async def collect(self, inputs: Input, runtime: Runtime, context: Context) -> Output:
        raise JiuWenBaseException(StatusCode.RUNTIME_COMPONENT_ABILITY_NOT_SUPPORTED.code, 
                                StatusCode.RUNTIME_COMPONENT_ABILITY_NOT_SUPPORTED.errmsg.format(ability='Collect'))

    async def transform(self, inputs: Input, runtime: Runtime, context: Context) -> AsyncIterator[Output]:
        raise JiuWenBaseException(StatusCode.RUNTIME_COMPONENT_ABILITY_NOT_SUPPORTED.code, 
                                StatusCode.RUNTIME_COMPONENT_ABILITY_NOT_SUPPORTED.errmsg.format(ability='Transform'))
