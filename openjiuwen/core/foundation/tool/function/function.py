# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import inspect
from typing import Callable, AsyncIterator

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.utils.schema_utils import SchemaUtils
from openjiuwen.core.foundation.tool.base import Tool, ToolCard, Input, Output


class LocalFunction(Tool):
    def __init__(self, card: ToolCard, func: Callable):
        super().__init__(card)
        if func is None:
            raise build_error(StatusCode.TOOL_LOCAL_FUNCTION_FUNC_NOT_SUPPORTED, card=self._card)
        self._func = func

    async def invoke(self, inputs: Input, **kwargs) -> Output:
        if self.card.input_params is not None:
            inputs = SchemaUtils.format_with_schema(inputs,
                                                    self._card.input_params,
                                                    skip_none_value=kwargs.get("skip_none_value", False),
                                                    skip_validate=kwargs.get("skip_inputs_validate", False))
        if inspect.isgeneratorfunction(self._func) or inspect.isasyncgenfunction(self._func):
            raise build_error(StatusCode.TOOL_LOCAL_FUNCTION_EXECUTION_ERROR, interface="invoke",
                              reason="func can not be generator", card=self._card)
        if inspect.iscoroutinefunction(self._func):
            res = await self._func(**inputs)
        else:
            res = self._func(**inputs)
        return res

    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[Output]:
        if self.card.input_params is not None:
            inputs = SchemaUtils.format_with_schema(inputs, self._card.input_params,
                                                    skip_none_value=kwargs.get("skip_none_value", False),
                                                    skip_validate=kwargs.get("skip_inputs_validate"))
        if inspect.isasyncgenfunction(self._func):
            async for item in self._func(**inputs):
                yield item
        elif inspect.isgeneratorfunction(self._func):
            for item in self._func(**inputs):
                yield item
        else:
            raise build_error(StatusCode.TOOL_LOCAL_FUNCTION_EXECUTION_ERROR, interface="stream",
                              reason="func is not generator", card=self._card)
