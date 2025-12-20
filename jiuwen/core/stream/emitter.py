#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
from typing import Any, Optional

from jiuwen.core.common.logging import logger


class AsyncStreamQueue:

    def __init__(self, maxsize: int = 0):
        if not isinstance(maxsize, int):
            raise TypeError("maxsize must be an integer")

        if maxsize < 0:
            raise ValueError("maxsize must be >= 0")

        self._stream_queue = asyncio.Queue(maxsize=maxsize)
        self._closed = False

    @property
    def is_closed(self) -> bool:
        return self._closed

    async def send(self,
                   data: Any,
                   attempt_timeout: float = 0.2,
                   max_retries: int = 5) -> None:
        if self._closed:
            raise RuntimeError("StreamQueue is already closed")

        for attempt in range(0, max_retries):
            try:
                await asyncio.wait_for(self._stream_queue.put(data),
                                       attempt_timeout)
                logger.debug(
                    f"Sending stream data success, timeout: {attempt_timeout}, attempt: {attempt + 1}"
                )
                return
            except asyncio.TimeoutError:
                logger.error(
                    f"Sending stream data timeout error, timeout: {attempt_timeout}, attempt: {attempt + 1}"
                )
                continue

    async def receive(self, timeout: float = 0.2) -> Optional[Any]:
        if timeout <= 0:
            timeout = 0.001
        if self._closed:
            raise RuntimeError("StreamQueue is already closed")

        try:
            stream_item = await asyncio.wait_for(self._stream_queue.get(),
                                                 timeout)
            self._stream_queue.task_done()
            logger.debug(f"Receiving stream data success, timeout: {timeout}")
            return stream_item
        except asyncio.TimeoutError:
            logger.debug(
                f"Receiving stream data timeout error, timeout: {timeout}")
            return None

    async def close(self, timeout: float = 5.0) -> None:
        if self._closed:
            logger.debug("StreamQueue is already closed")
            return
        self._closed = True

        try:
            await asyncio.wait_for(self._stream_queue.join(), timeout)
            logger.info(
                f"StreamQueue closed successfully, timeout: {timeout}")
        except asyncio.TimeoutError:
            logger.error(
                f"Closing StreamQueue timeout error, timeout: {timeout}, force clear stream queue."
            )
            self._force_clear()

    def _force_clear(self) -> None:
        cleared_items = 0
        while not self._stream_queue.empty():
            try:
                self._stream_queue.get_nowait()
                self._stream_queue.task_done()
                cleared_items += 1
            except (asyncio.QueueEmpty, ValueError):
                break

        unfinished = getattr(self._stream_queue, '_unfinished_tasks', 0)
        for _ in range(unfinished):
            try:
                self._stream_queue.task_done()
            except ValueError:
                break

        logger.info(f"Force cleared {cleared_items} items from StreamQueue.")


class StreamEmitter:
    END_FRAME = "all streaming outputs finish"

    def __init__(self):
        self._stream_queue = AsyncStreamQueue()
        self._closed = False

    @property
    def stream_queue(self) -> AsyncStreamQueue:
        return self._stream_queue

    async def emit(self, stream_data: Any) -> None:
        if self._closed:
            raise RuntimeError(
                "Can not emit data after the stream emitter is closed.")
        await self._stream_queue.send(stream_data)

    async def close(self) -> None:
        if self._closed:
            logger.debug("StreamWriter is already closed.")
        self._closed = True

        await self._stream_queue.send(self.END_FRAME)
