# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import AsyncGenerator, Any
from typing import Callable

from openjiuwen.core.common.logging import graph_logger, LogEventType
from openjiuwen.core.common.utils.dict_utils import extract_leaf_nodes, format_path, rebuild_dict
from openjiuwen.core.session import EndFrame, get_value_by_nested_path, extract_origin_key
from openjiuwen.core.workflow.components.base import ComponentAbility


class StreamConsumer(ABC):
    @abstractmethod
    async def stream_call(self, event: asyncio.Event, done_callback):
        ...

    @abstractmethod
    def should_handle_message(self) -> bool:
        ...

    @abstractmethod
    def is_done(self) -> bool:
        ...


class StreamGraph:
    def __init__(self):
        self._stream_nodes: dict[str, StreamConsumer] = {}

    def add_stream_consumer(self, consumer: StreamConsumer, node_id: str):
        if node_id not in self._stream_nodes.keys():
            self._stream_nodes[node_id] = consumer

    def get_node(self, node_id: str) -> StreamConsumer:
        return self._stream_nodes.get(node_id)


@dataclass(slots=True)
class StreamPayload:
    message: Any
    source_ability: ComponentAbility


class StreamActor:
    def __init__(self, node_id: str, vertex: StreamConsumer, abilities: list[ComponentAbility], sources: list[str],
                 stream_generator_timeout: float = 1):
        self._processors: dict[ComponentAbility, StreamProcessor] = {
            ability: StreamProcessor(node_id, sources, stream_generator_timeout=stream_generator_timeout)
            for ability in abilities
        }
        self._task: asyncio.Task = None
        self._task_error: asyncio.Future = None
        self._vertex = vertex
        self._node_id: str = node_id
        self._running_tasks: list[asyncio.Task] = []

    async def send(self, message: dict, source_ability: ComponentAbility, first_frame: bool = False):
        if not self._vertex.should_handle_message():
            graph_logger.warning(
                "Message discarded - component cannot handle",
                event_type=LogEventType.GRAPH_STREAM_CHUNK,
                chunk=str(message),
                metadata={"node_id": self._node_id}
            )
            return
        if self._task is None or self._task.done():
            if self._task and self._task.done() and self._task.exception():
                graph_logger.warning(
                    "Previous task completed with exception",
                    event_type=LogEventType.GRAPH_NODE_CALL_ERROR,
                    metadata={"node_id": self._node_id, "error": str(self._task.exception())}
                )
            if self._task_error and self._task_error.done() and self._task_error.exception():
                graph_logger.warning(
                    "Message discarded - component has error",
                    event_type=LogEventType.GRAPH_STREAM_CHUNK,
                    metadata={"node_id": self._node_id, "message": str(message),
                              "error": str(self._task_error.exception())}
                )
                return
            if not first_frame or not self._vertex.is_done():
                graph_logger.warning(
                    "Message discarded - component finished",
                    event_type=LogEventType.GRAPH_STREAM_CHUNK,
                    metadata={"node_id": self._node_id, "message": str(message)}
                )
                return
            graph_logger.debug(
                "Actor started",
                event_type=LogEventType.GRAPH_NODE_CALL_START,
                metadata={"node_id": self._node_id, "message": str(message)}
            )
            event = asyncio.Event()
            self._task_error = asyncio.Future()
            self._task = asyncio.create_task(self._vertex.stream_call(event, self._error_callback))
            await event.wait()
            for ability, processor in self._processors.items():
                task = asyncio.create_task(processor.run(ability))
                self._running_tasks.append(task)
        for processor in self._processors.values():
            graph_logger.debug(
                "Processor received message",
                event_type=LogEventType.GRAPH_STREAM_CHUNK,
                metadata={"node_id": processor.node_id, "message": str(message)}
            )
            await processor.receive(StreamPayload(message, source_ability))

    async def generator(self, ability: ComponentAbility, schema: dict,
                        stream_callback: Callable[[dict], Awaitable[None]] = None) -> dict:
        processor = self._processors[ability]
        graph_logger.debug(
            "Generating message for ability",
            event_type=LogEventType.GRAPH_STREAM_CHUNK,
            metadata={"node_id": processor.node_id, "ability": ability.name}
        )
        return processor.generator(schema, stream_callback)

    def _error_callback(self, error):
        if error:
            self._task_error.set_exception(error)

    async def shutdown(self):
        try:
            if self._task and not self._task.done() and not self._task.cancelled():
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    graph_logger.warning(
                        "Task cancelled",
                        event_type=LogEventType.GRAPH_NODE_CALL_END,
                        metadata={"node_id": self._node_id}
                    )
                except Exception as e:
                    graph_logger.warning(
                        "Task shutdown with exception",
                        event_type=LogEventType.GRAPH_NODE_CALL_ERROR,
                        metadata={"node_id": self._node_id, "error": str(e)}
                    )
            if self._task_error:
                if not self._task_error.done() and not self._task_error.cancelled():
                    self._task_error.cancel()
                    try:
                        await self._task_error
                    except asyncio.CancelledError:
                        graph_logger.warning(
                            "Task error cancelled",
                            event_type=LogEventType.GRAPH_NODE_CALL_END,
                            metadata={"node_id": self._node_id}
                        )
                    except Exception as e:
                        graph_logger.warning(
                            "Task error shutdown with exception",
                            event_type=LogEventType.GRAPH_NODE_CALL_ERROR,
                            metadata={"node_id": self._node_id, "error": str(e)}
                        )
                if not self._task_error.cancelled() and self._task_error.exception():
                    graph_logger.warning(
                        "Task error has exception",
                        event_type=LogEventType.GRAPH_NODE_CALL_ERROR,
                        metadata={"node_id": self._node_id, "error": str(self._task_error.exception())}
                    )

            if self._running_tasks:
                for task in self._running_tasks:
                    if not task.done() and not task.cancelled():
                        task.cancel()
                results = await asyncio.gather(*self._running_tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, Exception):
                        graph_logger.debug(
                            "Running task completed with exception",
                            event_type=LogEventType.GRAPH_NODE_CALL_ERROR,
                            metadata={"node_id": self._node_id, "error": str(result)}
                        )
        finally:
            self._task = None
            self._running_tasks = []


class StreamProcessor:
    def __init__(self, node_id: str, sources: list[str], stream_generator_timeout: float = 1):
        self.node_id = node_id
        self.queue: asyncio.Queue[StreamPayload] = asyncio.Queue()
        self.processor_queues: dict[str, list[asyncio.Queue]] = {}
        self.sources = set(sources)
        self._timeout = stream_generator_timeout if stream_generator_timeout > 0 else None

    async def run(self, ability: ComponentAbility):
        graph_logger.info(
            "Stream processor started",
            event_type=LogEventType.GRAPH_NODE_CALL_START,
            metadata={"node_id": self.node_id, "ability": ability.name}
        )
        handle_map = set()
        source_map: dict[ComponentAbility, set[str]] = defaultdict(set)
        while True:
            payload = await self.queue.get()
            message = payload.message
            source_ability = payload.source_ability
            source_key = self._get_unique_source_key(payload)
            if _is_end_message(message):
                source_id = _get_producer_id(message)
                handle_map.add(source_key)
                for path, queues in self.processor_queues.items():
                    path = extract_origin_key(path)
                    is_handled = False
                    paths = source_map.get(source_ability)
                    if paths:
                        is_handled = path in paths
                    is_all_finish = handle_map == self.sources
                    if (is_handled or is_all_finish) and self.is_value_from_source(path, source_id):
                        for queue in queues:
                            await queue.put(EndFrame(source_id))
            else:
                for path, queues in self.processor_queues.items():
                    path = extract_origin_key(path)
                    value = get_value_by_nested_path(path, message)
                    if value is not None:
                        source_map[source_ability].add(path)
                        for queue in queues:
                            await queue.put(value)
            if handle_map == self.sources:
                break
        graph_logger.info(
            "Stream processor finished",
            event_type=LogEventType.GRAPH_NODE_CALL_END,
            metadata={"node_id": self.node_id, "ability": ability.name}
        )

    @staticmethod
    def is_value_from_source(path: str, source_id: str) -> bool:
        return path == source_id or path.startswith(f"{source_id}.")

    @staticmethod
    def _get_unique_source_key(payload: StreamPayload) -> str:
        source_id = _get_producer_id(payload.message)
        ability = payload.source_ability.name
        return f"{source_id}-{ability}"

    async def receive(self, message: StreamPayload):
        await self.queue.put(message)

    def generator(self, schema: dict, stream_callable: Callable[[dict], Awaitable[None]] = None) -> dict:
        inputs = []
        if not schema:
            return {}
        paths = extract_leaf_nodes(schema)
        for key_path, ref_path in paths:
            path_str = format_path(key_path)
            if not isinstance(ref_path, str) or '$' not in ref_path:
                inputs.append((key_path, ref_path))
                continue
            inputs.append((key_path, self._create_generator(path_str, ref_path, stream_callable)))
        input_map = rebuild_dict(inputs)
        graph_logger.debug(
            "Stream generator created",
            event_type=LogEventType.GRAPH_STREAM_CHUNK,
            metadata={"node_id": self.node_id, "schema": str(schema)}
        )
        return input_map

    def _create_generator(self, k_path: str, r_path: str,
                          stream_callable: Callable[[dict], Awaitable[None]] = None) -> AsyncGenerator:
        queue = asyncio.Queue()
        if r_path in self.processor_queues:
            self.processor_queues[r_path].append(queue)
        else:
            self.processor_queues[r_path] = [queue]

        async def generator():
            while True:
                message = await asyncio.wait_for(queue.get(), timeout=self._timeout)
                if message is None:
                    graph_logger.warning(
                        "Stream processor timeout",
                        event_type=LogEventType.GRAPH_STREAM_CHUNK,
                        metadata={"node_id": self.node_id, "timeout": self._timeout}
                    )
                    break
                if isinstance(message, EndFrame):
                    graph_logger.debug(
                        "EndFrame received",
                        event_type=LogEventType.GRAPH_STREAM_CHUNK,
                        metadata={"node_id": self.node_id, "k_path": k_path, "r_path": r_path}
                    )
                    queue.task_done()
                    break
                yield message
                if stream_callable:
                    await stream_callable({k_path: message})
                queue.task_done()

        return generator()


def _is_end_message(message: dict[str, Any]) -> bool:
    producer_id = _get_producer_id(message)
    message_content = message[producer_id]
    if isinstance(message_content, str) and message_content.startswith("END_"):
        return True
    return False


def _get_producer_id(message):
    if not isinstance(message, dict) or len(message) != 1:
        raise ValueError("message is invalid")
    return next(iter(message))  # 从一个单键值map中获取key
