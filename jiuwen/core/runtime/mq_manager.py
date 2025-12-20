#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Dict, Any, AsyncIterator

from jiuwen.core.common.exception.exception import JiuWenBaseException
from jiuwen.core.common.exception.status_code import StatusCode
from jiuwen.core.common.logging import logger
from jiuwen.core.runtime.state import Transformer
from jiuwen.core.runtime.utils import get_by_schema
from jiuwen.core.stream.emitter import AsyncStreamQueue
from jiuwen.core.workflow.workflow_config import ComponentAbility, WorkflowSpec


class StreamTransform:
    def get_by_defined_transformer(self, origin_message: dict, transformer: Transformer) -> dict:
        return transformer(origin_message)

    def get_by_default_transformer(self, origin_message: dict, stream_inputs_schema: dict) -> dict:
        return get_by_schema(stream_inputs_schema, origin_message)


class MessageQueueManager:
    def __init__(self, workflow_spec: WorkflowSpec, sub_graph: bool):
        self._stream_edges = workflow_spec.stream_edges
        self._streams: Dict[str, dict[ComponentAbility, AsyncStreamQueue]] = {}
        self._streams_transform = StreamTransform()
        for producer_id, consumer_ids in self._stream_edges.items():
            for consumer_id in consumer_ids:
                consumer_stream_ability = [ability for ability in workflow_spec.comp_configs[consumer_id].abilites if
                                           ability in [ComponentAbility.COLLECT, ComponentAbility.TRANSFORM]]
                self._streams[consumer_id] = {ability: AsyncStreamQueue(maxsize=10 * 1024)
                                              for ability in consumer_stream_ability}
        self._sub_graph = sub_graph
        self._sub_workflow_stream = AsyncStreamQueue(maxsize=10 * 1024) if sub_graph else None

    @property
    def sub_workflow_stream(self):
        if not self._sub_graph:
            raise JiuWenBaseException(
                error_code=StatusCode.WORKFLOW_MESSAGE_QUEUE_MANAGER_ERROR.code,
                message=f"only sub graph has sub_workflow_stream")
        return self._sub_workflow_stream

    def _get_queue(self, consumer_id: str) -> dict[ComponentAbility, AsyncStreamQueue]:
        return self._streams[consumer_id]

    @property
    def stream_transform(self):
        return self._streams_transform

    async def produce(self, producer_id: str, message_content: Any):
        consumer_ids = self._stream_edges.get(producer_id)
        if consumer_ids:
            for consumer_id in consumer_ids:
                stream_queues = self._get_queue(consumer_id)
                for _, queue in stream_queues.items():
                    await queue.send({producer_id: message_content})

    async def end_message(self, producer_id: str):
        end_message_content = f"END_{producer_id}"
        await self.produce(producer_id, end_message_content)

    def _is_end_message(self, message: dict[str, Any], ended_producers: set) -> bool:
        if not isinstance(message, dict) or len(message) != 1:
            raise ValueError("message is invalid")
        produce_id = next(iter(message))
        message_content = message[produce_id]
        if isinstance(message_content, str) and message_content.startswith("END_"):
            ended_producers.add(produce_id)
            return True
        return False

    async def consume(self, consumer_id: str, ability: ComponentAbility, frame_timeout: float = 0.2) -> AsyncIterator[dict[str, Any]]:
        stream_queues = self._get_queue(consumer_id)
        queue = stream_queues[ability]
        if queue is not None:
            ended_producers = set()
            while True:
                message = await queue.receive(timeout=frame_timeout)
                if message is None:
                    continue
                if self._is_end_message(message, ended_producers):
                    if ended_producers == set(key for key, value in self._stream_edges.items() if consumer_id in value):
                        await self.close_stream(consumer_id)
                        break
                    else:
                        continue
                yield message

    async def close_stream(self, consumer_id: str):
        if consumer_id in self._streams:
            stream_queues = self._streams.pop(consumer_id)
            for _, queue in stream_queues.items():
                await queue.close()

    async def close_all_streams(self):
        for consumer_id in list(self._streams.keys()):
            await self.close_stream(consumer_id)
        self._streams.clear()
