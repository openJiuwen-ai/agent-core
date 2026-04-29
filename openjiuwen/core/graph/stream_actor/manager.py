# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Dict, Any, Callable, Awaitable

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import graph_logger as logger
from openjiuwen.core.common.logging import LogEventType
from openjiuwen.core.workflow.components.base import ComponentAbility
from openjiuwen.core.workflow.workflow_config import WorkflowSpec
from openjiuwen.core.session import Transformer, get_by_schema, STREAM_INPUT_GEN_TIMEOUT_KEY
from openjiuwen.core.session.stream import AsyncStreamQueue
from openjiuwen.core.graph.stream_actor.base import StreamActor, StreamGraph


class StreamTransform:
    @classmethod
    def get_by_defined_transformer(cls, origin_message: dict, transformer: Transformer) -> dict:
        return transformer(origin_message)

    @classmethod
    def get_by_default_transformer(cls, origin_message: dict, stream_inputs_schema: dict) -> dict:
        return get_by_schema(stream_inputs_schema, origin_message)


class ActorManager:
    def __init__(self, workflow_spec: WorkflowSpec, graph: StreamGraph, sub_graph: bool, session):
        self._stream_edges = workflow_spec.stream_edges
        self._streams: Dict[str, StreamActor] = {}
        self._streams_transform = StreamTransform()
        self._active_producer_ids: dict[str, set[ComponentAbility]] = {}
        self._consumer_dict = _build_reverse_graph(self._stream_edges)
        self._producer_abilities: dict[str, set[ComponentAbility]] = {}
        self._workflow_session = session
        for consumer_id, producer_ids in self._consumer_dict.items():
            consumer_stream_ability = [ability for ability in workflow_spec.comp_configs[consumer_id].abilities if
                                       ability in [ComponentAbility.COLLECT, ComponentAbility.TRANSFORM]]
            sources = set()
            for producer_id in producer_ids:
                abilities = self._producer_abilities.get(producer_id, set())
                for ability in workflow_spec.comp_configs[producer_id].abilities:
                    if ability in [ComponentAbility.STREAM, ComponentAbility.TRANSFORM]:
                        abilities.add(ability)
                        sources.add(f"{producer_id}-{ability.name}")
                self._producer_abilities[producer_id] = abilities

            self._streams[consumer_id] = StreamActor(consumer_id, graph.get_node(consumer_id),
                                                     consumer_stream_ability, list(sources),
                                                     stream_generator_timeout=session.config().get_env(
                                                         STREAM_INPUT_GEN_TIMEOUT_KEY))
        self._sub_graph = sub_graph
        self._sub_workflow_stream = AsyncStreamQueue(maxsize=10 * 1024) if sub_graph else None

    def sub_workflow_stream(self) -> AsyncStreamQueue:
        if not self._sub_graph:
            raise build_error(StatusCode.GRAPH_STREAM_ACTOR_EXECUTION_ERROR,
                              reason=f"only sub graph has sub_workflow_stream")
        return self._sub_workflow_stream

    def active_produce_ability(self, producer_id, ability):
        abilities = self._active_producer_ids.get(producer_id, set())
        abilities.add(ability)
        self._active_producer_ids[producer_id] = abilities

    def mark_producer_done(self, producer_id: str):
        finished_stream_nodes = self._workflow_session.state().get_workflow_state("finished_stream_nodes") or []
        if producer_id not in finished_stream_nodes:
            finished_stream_nodes.append(producer_id)
        self._workflow_session.state().update_and_commit_workflow_state(
            {"finished_stream_nodes": finished_stream_nodes})

    def _get_actor(self, consumer_id: str) -> StreamActor:
        return self._streams[consumer_id]

    @property
    def stream_transform(self):
        return self._streams_transform

    async def produce(self, producer_id: str, message_content: Any,
                      ability: ComponentAbility, first_frame: bool = False):
        self.active_produce_ability(producer_id, ability)
        consumer_ids = self._stream_edges.get(producer_id)
        if consumer_ids:
            for consumer_id in consumer_ids:
                actor = self._get_actor(consumer_id)
                await actor.send({producer_id: message_content}, ability, first_frame=first_frame,
                                 producer_id=producer_id)
        else:
            logger.warning(
                f"Discard chunk send from [{producer_id}] to none consumer",
                event_type=LogEventType.GRAPH_SEND_STREAM_CHUNK,
                chunk=message_content
            )

    async def end_message(self, producer_id: str, ability: ComponentAbility):
        end_message_content = f"END_{producer_id}"
        await self.produce(producer_id, end_message_content, ability)

    async def consume(self, consumer_id: str, ability: ComponentAbility, schema: dict,
                      stream_callback: Callable[[dict], Awaitable[None]] = None) -> dict:
        producer_ids = self._consumer_dict.get(consumer_id, [])
        finished_stream_nodes = self._workflow_session.state().get_workflow_state("finished_stream_nodes") or []

        for producer_id in producer_ids:
            if producer_id not in finished_stream_nodes:
                continue
            all_abilities = self._producer_abilities.get(producer_id)
            active_abilities = self._active_producer_ids.get(producer_id, set())
            if not active_abilities:
                for ab in all_abilities:
                    await self.end_message(producer_id, ab)
                    self.active_produce_ability(producer_id, ab)
        actor = self._get_actor(consumer_id)
        consume_iter = await actor.generator(ability, schema, stream_callback)
        return consume_iter

    async def shutdown(self):
        for actor in self._streams.values():
            await actor.shutdown()


def _build_reverse_graph(graph):
    reverse_graph = {}

    for source, targets in graph.items():
        for target in targets:
            if target not in reverse_graph:
                reverse_graph[target] = []
            reverse_graph[target].append(source)

    return reverse_graph
