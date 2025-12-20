#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
from typing import Any, Optional

from openjiuwen.core.common.logging import logger


class AsyncStreamQueue:
    # Default timeout for each send attempt in seconds
    DEFAULT_SEND_ATTEMPT_TIMEOUT = 0.2

    # Maximum number of retries for sending data
    DEFAULT_MAX_SEND_RETRIES = 5

    # Default timeout for receiving data in seconds, -1 means no timeout
    DEFAULT_RECEIVE_TIMEOUT = -1

    # Default timeout for closing the queue in seconds
    DEFAULT_CLOSE_TIMEOUT = 5.0

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
                   attempt_timeout: float = DEFAULT_SEND_ATTEMPT_TIMEOUT,
                   max_retries: int = DEFAULT_MAX_SEND_RETRIES) -> None:
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
        
        logger.error(
            f"Failed to send stream data after {max_retries} attempts, timeout: {attempt_timeout}"
        )

    async def receive(self, timeout: float = DEFAULT_RECEIVE_TIMEOUT) -> Optional[Any]:
        if self._closed:
            raise RuntimeError("StreamQueue is already closed")

        stream_item = await asyncio.wait_for(self._stream_queue.get(),
                                             timeout if timeout and timeout > 0 else None)
        self._stream_queue.task_done()
        logger.debug(f"Receiving stream data success, stream frame: {stream_item}")
        return stream_item

    async def close(self, timeout: float = DEFAULT_CLOSE_TIMEOUT) -> None:
        if self._closed:
            logger.debug("StreamQueue is already closed")
            return
        self._closed = True

        try:
            await asyncio.wait_for(self._stream_queue.join(), timeout if timeout and timeout > 0 else None)
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

    def is_closed(self) -> bool:
        return self._closed

    async def close(self) -> None:
        if self._closed:
            logger.debug("StreamWriter is already closed.")
            return
        self._closed = True

        if not self._stream_queue.is_closed:
            await self._stream_queue.send(self.END_FRAME)
