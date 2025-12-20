#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
from typing import AsyncIterator, Any

from jiuwen.core.common.exception.exception import JiuWenBaseException
from jiuwen.core.common.exception.status_code import StatusCode


class TimeoutAsyncIteratorWrapper:
    def __init__(self, aiter: AsyncIterator, timeout: float, raise_on_timeout: bool = True):
        self._aiter = aiter
        self._timeout = timeout
        self._raise_on_timeout = raise_on_timeout

    def __aiter__(self):
        return self

    async def __anext__(self) -> Any:
        try:
            return await asyncio.wait_for(
                self._aiter.__anext__(),
                timeout=self._timeout
            )
        except StopAsyncIteration:
            raise
        except asyncio.TimeoutError:
            if self._raise_on_timeout:
                raise JiuWenBaseException(StatusCode.STREAM_FRAME_TIMEOUT_FAILED.code,
                                          StatusCode.STREAM_FRAME_TIMEOUT_FAILED.errmsg.format(timeout=self._timeout))
            else:
                raise StopAsyncIteration