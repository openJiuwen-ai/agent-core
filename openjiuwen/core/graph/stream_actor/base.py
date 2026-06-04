# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import AsyncGenerator, Any
from typing import Callable

from openjiuwen.core.common.logging import graph_logger as logger
from openjiuwen.core.common.logging import LogEventType
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
    def __init__(self, node_id: str, vertex: StreamConsumer, abilities: list[ComponentAbility],
                 source_groups: list[list[str]],
                 stream_generator_timeout: float = 1):
        self._processors: dict[ComponentAbility, StreamProcessor] = {
            ability: StreamProcessor(node_id, source_groups, stream_generator_timeout=stream_generator_timeout)
            for ability in abilities
        }
        self._task: asyncio.Task = None
        self._task_error: asyncio.Future = None
        self._vertex = vertex
        self._node_id: str = node_id
        self._running_tasks: list[asyncio.Task] = []

    async def send(self, message: dict, source_ability: ComponentAbility, first_frame: bool = False,
                   producer_id: str = None):
        log_message = dict(event_type=LogEventType.GRAPH_SEND_STREAM_CHUNK,
                           chunk=message,
                           node_id=self._node_id)
        if not self._vertex.should_handle_message():
            logger.warning(
                f"Discard chunk send from [{producer_id}], {self._node_id}[{source_ability.name}] unable to handle",
                **log_message
            )
            return
        if self._task is None or self._task.done():
            if self._task and self._task.done() and self._task.exception():
                logger.warning(
                    f"Exception occurred while sending chunk of node [{self._node_id}] ", **log_message,
                    exception=self._task.exception(), )
            if self._task_error and self._task_error.done() and self._task_error.exception():
                logger.warning(
                    f"Discard chunk send from [{producer_id}], {self._node_id}[{source_ability.name}] occur exception",
                    **log_message,
                    exception=self._task_error.exception())
                return
            if not first_frame or not self._vertex.is_done():
                logger.warning(
                    f"Discard chunk send from [{producer_id}], {self._node_id}[{source_ability.name}] vertex is done",
                    **log_message)
                return
            event = asyncio.Event()
            self._task_error = asyncio.Future()
            self._task = asyncio.create_task(self._vertex.stream_call(event, self._error_callback))
            await event.wait()
            logger.debug(f"Stream actor task node [{self._node_id}] started",
                         event_type=LogEventType.GRAPH_VERTEX_STREAM_ACTOR_START,
                         node_id=self._node_id)
            for ability, processor in self._processors.items():
                task = asyncio.create_task(processor.run(ability))
                self._running_tasks.append(task)
        logger.debug(f"Send chunk from [{producer_id}] to {self._node_id}[{source_ability.name}]", **log_message)
        for processor in self._processors.values():
            await processor.receive(StreamPayload(message, source_ability))

    async def generator(self, ability: ComponentAbility, schema: dict,
                        stream_callback: Callable[[dict], Awaitable[None]] = None) -> dict:
        processor = self._processors[ability]
        return processor.generator(schema, stream_callback)

    def _error_callback(self, error):
        if error:
            self._task_error.set_exception(error)

    async def shutdown(self):
        log_message = dict(
            event_type=LogEventType.GRAPH_VERTEX_STREAM_ACTOR_SHUTDOWN,
            node_id=self._node_id
        )
        try:
            logger.debug(f"Begin to shutdown stream actor task for {self._node_id}", **log_message)
            if self._task and not self._task.done() and not self._task.cancelled():
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    logger.debug(f"Cancel stream actor task for {self._node_id}", **log_message)
                except Exception as e:
                    logger.debug(f"Cancel stream actor task for {self._node_id} with exception", exception=e,
                                 **log_message)
            if self._task_error:
                if not self._task_error.done() and not self._task_error.cancelled():
                    self._task_error.cancel()
                    try:
                        await self._task_error
                    except asyncio.CancelledError:
                        logger.debug(f"Cancel stream actor error task for {self._node_id}", **log_message)
                    except Exception as e:
                        logger.debug(f"Cancel stream actor error task for {self._node_id} with exception",
                                     exception=e, **log_message)

                if not self._task_error.cancelled() and self._task_error.exception():
                    logger.debug(f"No need cancel stream actor error task for {self._node_id} with exception",
                                 exception=self._task_error.exception(), **log_message)

            if self._running_tasks:
                for task in self._running_tasks:
                    if not task.done() and not task.cancelled():
                        task.cancel()
                results = await asyncio.gather(*self._running_tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, Exception):
                        logger.debug(f"Cancel stream actor running task for {self._node_id} with exception",
                                     exception=result, **log_message)
            logger.debug(f"Succeed to shutdown stream actor task for {self._node_id}", **log_message)
        finally:
            self._task = None
            self._running_tasks = []


class StreamProcessor:
    def __init__(self, node_id: str, source_groups: list[list[str]], stream_generator_timeout: float = 1):
        self.node_id = node_id
        self.queue: asyncio.Queue[StreamPayload] = asyncio.Queue()
        self.processor_queues: dict[str, list[asyncio.Queue]] = {}
        self.source_groups = [set(group) for group in source_groups if group]
        self.sources = set().union(*self.source_groups) if self.source_groups else set()
        self.source_ids = {self._producer_id_from_source_key(source_key) for source_key in self.sources}
        self._timeout = stream_generator_timeout if stream_generator_timeout > 0 else None

    async def run(self, ability: ComponentAbility):
        handle_map = set()
        source_path_map: dict[str, set[str]] = {}
        while True:
            payload = await self.queue.get()
            message = payload.message
            source_key = self._get_unique_source_key(payload)
            if _is_end_message(message):
                source_id = _get_producer_id(message)
                handle_map.add(source_key)
                await self._close_queues_for_source_key(source_id, source_key, source_path_map)

                if self._all_source_groups_finished(handle_map):
                    await self._close_all_queues(source_id)

            else:
                await self._close_inactive_group_sources(source_key)
                for path, queues in self.processor_queues.items():
                    origin_path = extract_origin_key(path)
                    value = get_value_by_nested_path(origin_path, message)
                    if value is not None:
                        source_path_map.setdefault(source_key, set()).add(path)
                        for queue in queues:
                            await queue.put(value)
            if self._all_source_groups_finished(handle_map):
                break

    def _all_source_groups_finished(self, handled_sources: set[str]) -> bool:
        if not self.source_groups:
            return False
        return all(group & handled_sources for group in self.source_groups)

    async def _close_inactive_group_sources(self, active_source_key: str) -> None:
        for group in self.source_groups:
            if active_source_key not in group or len(group) <= 1:
                continue
            for inactive_source_key in group - {active_source_key}:
                await self._close_queues_for_source(self._producer_id_from_source_key(inactive_source_key))

    async def _close_queues_for_source(self, source_id: str) -> None:
        for path, queues in self.processor_queues.items():
            if self.is_value_from_source(extract_origin_key(path), source_id):
                for queue in queues:
                    await queue.put(EndFrame(source_id))

    async def _close_queues_for_source_key(self, source_id: str, source_key: str,
                                           source_path_map: dict[str, set[str]]) -> None:
        handled_paths = source_path_map.get(source_key)
        if not handled_paths:
            return
        for path in handled_paths:
            for queue in self.processor_queues.get(path, []):
                await queue.put(EndFrame(source_id))

    async def _close_all_queues(self, source_id: str) -> None:
        all_queues = []
        for queues in self.processor_queues.values():
            all_queues.extend(queues)
        for queue in all_queues:
            await queue.put(EndFrame(source_id))

    @staticmethod
    def is_value_from_source(path: str, source_id: str) -> bool:
        return path == source_id or path.startswith(f"{source_id}.")

    @staticmethod
    def _get_unique_source_key(payload: StreamPayload) -> str:
        source_id = _get_producer_id(payload.message)
        ability = payload.source_ability.name
        return f"{source_id}-{ability}"

    @staticmethod
    def _producer_id_from_source_key(source_key: str) -> str:
        return source_key.rsplit("-", 1)[0]

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
        return input_map

    def _create_generator(self, k_path: str, r_path: str,
                          stream_callable: Callable[[dict], Awaitable[None]] = None) -> AsyncGenerator:
        queue = asyncio.Queue()
        if r_path in self.processor_queues:
            self.processor_queues[r_path].append(queue)
        else:
            self.processor_queues[r_path] = [queue]
        timeout = None if self._path_has_declared_source(r_path) else self._timeout

        async def generator():
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=timeout)
                except asyncio.TimeoutError:
                    logger.warning(
                        f"Receive chunk timeout {timeout}s of [{self.node_id}.{k_path}]",
                        event_type=LogEventType.GRAPH_RECEIVE_STREAM_CHUNK,
                        node_id=self.node_id,
                        chunk=None,
                        metadata={"k_path": k_path, "r_path": r_path}
                    )
                    break
                if isinstance(message, EndFrame):
                    logger.debug(
                        f"Receive EndFrame chunk of [{self.node_id}.{k_path}]",
                        event_type=LogEventType.GRAPH_RECEIVE_STREAM_CHUNK,
                        node_id=self.node_id,
                        chunk=message,
                        metadata={"k_path": k_path, "r_path": r_path}
                    )
                    queue.task_done()
                    break
                logger.debug(
                    f"Receive chunk of [{self.node_id}.{k_path}]",
                    event_type=LogEventType.GRAPH_RECEIVE_STREAM_CHUNK,
                    node_id=self.node_id,
                    chunk=message,
                    metadata={"k_path": k_path, "r_path": r_path}
                )
                yield message
                if stream_callable:
                    await stream_callable({k_path: message})
                queue.task_done()

        return generator()

    def _path_has_declared_source(self, r_path: str) -> bool:
        origin_key = extract_origin_key(r_path)
        if not origin_key:
            return False
        source_id = origin_key.split(".", 1)[0]
        return source_id in self.source_ids


def _is_end_message(message: dict[str, Any]) -> bool:
    producer_id = _get_producer_id(message)
    message_content = message[producer_id]
    if isinstance(message_content, str) and message_content.startswith("END_"):
        return True
    return False


def _get_producer_id(message):
    if not isinstance(message, dict) or len(message) != 1:
        raise ValueError("message is invalid")
    return next(iter(message))
