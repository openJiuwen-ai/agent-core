#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Type, Generic, TypeVar
from pydantic import BaseModel, ValidationError

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.common.logging import logger
from openjiuwen.core.stream.base import OutputSchema, TraceSchema, CustomSchema
from openjiuwen.core.stream.emitter import StreamEmitter

T = TypeVar("T")
S = TypeVar("S", bound=BaseModel)


class StreamWriter(Generic[T, S]):

    def __init__(self, stream_emitter: StreamEmitter, schema_type: Type[S]):
        if stream_emitter is None:
            raise ValueError("stream_emitter can not be None")

        self._stream_emitter = stream_emitter
        self._schema_type = schema_type

    async def write(self, stream_data: T) -> None:
        if stream_data is None:
            raise JiuWenBaseException(StatusCode.STREAM_WRITER_WRITE_FAILED.code,
                                      StatusCode.STREAM_WRITER_WRITE_FAILED.errmsg.format(reason="can not write None"))
        try:
            validated_data = self._schema_type.model_validate(stream_data)
        except ValidationError as e:
            raise JiuWenBaseException(
                StatusCode.STREAM_WRITER_WRITE_SCHEMA_FAILED.code,
                StatusCode.STREAM_WRITER_WRITE_SCHEMA_FAILED.errmsg.format(
                    detail=f"Data validation failed for schema {self._schema_type.__name__}")
            ) from e
        try:
            await self._do_write(validated_data)
        except Exception as error:
            raise JiuWenBaseException(StatusCode.STREAM_WRITER_WRITE_FAILED.code,
                                      StatusCode.STREAM_WRITER_WRITE_FAILED.errmsg.format(reason=error)) from error

    async def _do_write(self, validated_data: S) -> None:
        if self._stream_emitter and not self._stream_emitter.is_closed():
            await self._stream_emitter.emit(validated_data)
        else:
            logger.warning(f'discard message [{validated_data}], because stream emitter has already been closed')


class OutputStreamWriter(StreamWriter[dict, OutputSchema]):

    def __init__(
            self,
            stream_emitter: StreamEmitter,
            schema_type: Type[OutputSchema] = OutputSchema,
    ):
        super().__init__(stream_emitter, schema_type)


class TraceStreamWriter(StreamWriter[dict, TraceSchema]):

    def __init__(
            self,
            stream_emitter: StreamEmitter,
            schema_type: Type[TraceSchema] = TraceSchema,
    ):
        super().__init__(stream_emitter, schema_type)


class CustomStreamWriter(StreamWriter[dict, CustomSchema]):

    def __init__(
            self,
            stream_emitter: StreamEmitter,
            schema_type: Type[CustomSchema] = CustomSchema,
    ):
        super().__init__(stream_emitter, schema_type)
