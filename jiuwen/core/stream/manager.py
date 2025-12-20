#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Dict, Optional, List, AsyncIterator, Any

from jiuwen.core.common.logging import logger
from jiuwen.core.stream.base import StreamMode, BaseStreamMode
from jiuwen.core.stream.emitter import StreamEmitter
from jiuwen.core.stream.writer import StreamWriter, OutputStreamWriter, TraceStreamWriter, CustomStreamWriter
from jiuwen.core.utils.config.user_config import UserConfig


class StreamWriterManager:

    def __init__(self,
                 stream_emitter: StreamEmitter,
                 modes: Optional[List[StreamMode]] = None):
        if stream_emitter is None:
            raise ValueError("stream_emitter is None")
        self._stream_emitter = stream_emitter
        self._default_modes = modes if modes is not None else [
            BaseStreamMode.OUTPUT, BaseStreamMode.TRACE, BaseStreamMode.CUSTOM
        ]
        self._writers: Dict[StreamMode, StreamWriter] = {}
        self._add_default_writers()

    @staticmethod
    def create_manager(stream_emitter: StreamEmitter,
                       modes: Optional[List[StreamMode]] = None):
        return StreamWriterManager(stream_emitter=stream_emitter, modes=modes)

    def stream_emitter(self) -> StreamEmitter:
        return self._stream_emitter

    async def stream_output(self, timeout=0.2, need_close: bool = True) -> AsyncIterator[Any]:
        while True:
            data = await self._stream_emitter.stream_queue.receive(
                timeout=timeout)
            if data is not None:
                if data == StreamEmitter.END_FRAME:
                    logger.info("Received END_FRAME, stopping stream output.")
                    if need_close:
                        await self._stream_emitter.stream_queue.close(timeout=timeout)
                    break
                else:
                    if UserConfig.is_sensitive():
                        logger.debug(f"Received stream data")
                    else:
                        logger.debug(f"Received stream data: {data}")
                    yield data
            else:
                logger.debug("No data received, waiting for data.")

    def add_writer(self, key: StreamMode, writer: StreamWriter) -> None:
        self._writers[key] = writer

    def get_writer(self, key: StreamMode) -> Optional[StreamWriter]:
        return self._writers.get(key)

    def get_output_writer(self) -> Optional[StreamWriter]:
        return self.get_writer(BaseStreamMode.OUTPUT)

    def get_trace_writer(self) -> Optional[StreamWriter]:
        return self.get_writer(BaseStreamMode.TRACE)

    def get_custom_writer(self) -> Optional[StreamWriter]:
        return self.get_writer(BaseStreamMode.CUSTOM)

    def remove_writer(self, key: StreamMode) -> Optional[StreamWriter]:
        if key in self._default_modes:
            raise ValueError(f"Can not remove default writer for mode {key}")

        return self._writers.pop(key, None)

    def _add_default_writers(self) -> None:
        for mode in self._default_modes:
            if mode == BaseStreamMode.OUTPUT:
                self.add_writer(mode, OutputStreamWriter(self._stream_emitter))
            elif mode == BaseStreamMode.TRACE:
                self.add_writer(mode, TraceStreamWriter(self._stream_emitter))
            elif mode == BaseStreamMode.CUSTOM:
                self.add_writer(mode, CustomStreamWriter(self._stream_emitter))
            else:
                raise ValueError(
                    f"default modes must be OUTPUT, TRACE, CUSTOM, {mode} is not supported."
                )
