# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import inspect
from typing import Callable, AsyncIterator

from openjiuwen.core.common.utils.schema_utils import SchemaUtils
from openjiuwen.core.foundation.tool.base import Tool, ToolCard, Input, Output
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode


class LocalFunction(Tool):
    def __init__(self, card: ToolCard, func: Callable = None):
        super().__init__(card)
        self._func = func

    async def invoke(self, inputs: Input, **kwargs) -> Output:
        if self.card.input_params is not None:
            inputs = SchemaUtils.format_with_schema(inputs,
                                                    self._card.input_params,
                                                    skip_none_value=kwargs.get("skip_none_value", False),
                                                    skip_validate=kwargs.get("skip_inputs_validate", False))
        if inspect.isgeneratorfunction(self._func) or inspect.isasyncgenfunction(self._func):
            raise JiuWenBaseException(
                error_code=StatusCode.PLUGIN_UNEXPECTED_ERROR.code, message="invoke function not support generator"
            )
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
        else:
            raise JiuWenBaseException(
                error_code=StatusCode.PLUGIN_UNEXPECTED_ERROR.code, message="stream function need aysnc generator"
            )
