#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import json
from typing import TypeVar, Generic, AsyncIterator, Any

from openjiuwen.core.common.exception.exception import InterruptException, JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.runtime.runtime import BaseRuntime

Input = TypeVar("Input", contravariant=True)
Output = TypeVar("Output", covariant=True)


class Executable(Generic[Input, Output]):
    async def on_invoke(self, inputs: Input, runtime: BaseRuntime) -> Output:
        raise JiuWenBaseException(-1, "Invoke is not supported")

    async def on_stream(self, inputs: Input, runtime: BaseRuntime) -> AsyncIterator[Output]:
        raise JiuWenBaseException(-1, "Stream is not supported")

    async def on_collect(self, inputs: Input, runtime: BaseRuntime) -> Output:
        raise JiuWenBaseException(-1, "Collect is not supported")

    async def on_transform(self, inputs: Input, runtime: BaseRuntime) -> AsyncIterator[Output]:
        raise JiuWenBaseException(-1, "Transform is not supported")

    async def interrupt(self, message: dict):
        raise InterruptException(
            error_code=StatusCode.CONTROLLER_INTERRUPTED_ERROR.code,
            message=json.dumps(message, ensure_ascii=False)
        )

    def skip_trace(self) -> bool:
        return False

    def graph_invoker(self) -> bool:
        return False

    def post_commit(self) -> bool:
        return True

    def component_type(self) -> str:
        return ""

GeneralExecutor = Executable[dict[str, Any], dict[str, Any]]
