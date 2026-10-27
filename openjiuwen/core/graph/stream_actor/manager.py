# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Dict, Any, Callable, Awaitable, NamedTuple

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


class StreamStructure(NamedTuple):
    """Streaming structure parsed once per WorkflowSpec and shared across executions.

    All fields are read-only by convention after parsing; per-execution state
    (_streams / _active_producer_ids) stays on the ActorManager instance.
    """

    consumer_dict: Dict[str, list[str]]
    producer_abilities: Dict[str, set[ComponentAbility]]
    stream_source_groups: Dict[str, list[set[str]]]
    consumer_abilities: Dict[str, list[ComponentAbility]]


def _parse_stream_structure(workflow_spec: WorkflowSpec) -> StreamStructure:
    consumer_dict = _build_reverse_graph(workflow_spec.stream_edges)
    producer_abilities: Dict[str, set[ComponentAbility]] = {}
    stream_source_groups: Dict[str, list[set[str]]] = {}
    consumer_abilities: Dict[str, list[ComponentAbility]] = {}
    for consumer_id, producer_ids in consumer_dict.items():
        consumer_abilities[consumer_id] = [
            ability
            for ability in workflow_spec.comp_configs[consumer_id].abilities
            if ability in [ComponentAbility.COLLECT, ComponentAbility.TRANSFORM]
        ]
        sources = set()
        for producer_id in producer_ids:
            abilities = producer_abilities.get(producer_id, set())
            for ability in workflow_spec.comp_configs[producer_id].abilities:
                if ability in [ComponentAbility.STREAM, ComponentAbility.TRANSFORM]:
                    abilities.add(ability)
                    sources.add(f"{producer_id}-{ability.name}")
            producer_abilities[producer_id] = abilities

        source_groups = workflow_spec.stream_source_groups.get(consumer_id)
        if not source_groups:
            source_groups = [[source] for source in sorted(sources)]
        stream_source_groups[consumer_id] = [set(group) for group in source_groups if group]
    return StreamStructure(consumer_dict, producer_abilities, stream_source_groups, consumer_abilities)


class ActorManager:
    def __init__(self, workflow_spec: WorkflowSpec, graph: StreamGraph, sub_graph: bool, session):
        self._stream_edges = workflow_spec.stream_edges
        self._streams: Dict[str, StreamActor] = {}
        self._streams_transform = StreamTransform()
        self._active_producer_ids: dict[str, set[ComponentAbility]] = {}
        # 流式结构只依赖构建期固定的 spec 数据：解析一次挂 spec 私有缓存
        # （_stream_structure），每次循环体迭代等高频构造直接复用；
        # StreamActor / 执行态照旧按次新建。
        structure = workflow_spec._stream_structure  # pylint: disable=protected-access
        if structure is None:
            structure = _parse_stream_structure(workflow_spec)
            workflow_spec._stream_structure = structure  # pylint: disable=protected-access
        self._consumer_dict = structure.consumer_dict
        self._producer_abilities = structure.producer_abilities
        self._stream_source_groups = structure.stream_source_groups
        self._workflow_session = session
        for consumer_id in self._consumer_dict:
            self._streams[consumer_id] = StreamActor(
                consumer_id,
                graph.get_node(consumer_id),
                structure.consumer_abilities[consumer_id],
                self._stream_source_groups[consumer_id],
                stream_generator_timeout=session.config().get_env(STREAM_INPUT_GEN_TIMEOUT_KEY),
            )
        self._sub_graph = sub_graph
        self._sub_workflow_stream = AsyncStreamQueue(maxsize=10 * 1024) if sub_graph else None

    def sub_workflow_stream(self) -> AsyncStreamQueue:
        if not self._sub_graph:
            raise build_error(
                StatusCode.GRAPH_STREAM_ACTOR_EXECUTION_ERROR, reason="only sub graph has sub_workflow_stream"
            )
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
            {"finished_stream_nodes": finished_stream_nodes}
        )

    def should_sanitize_stream_source(self, consumer_id: str, producer_id: str) -> bool:
        """Return True when a stream source is known to be inactive for this run."""
        source_groups = self._stream_source_groups.get(consumer_id, [])
        matched_groups = [
            group
            for group in source_groups
            if any(self._source_key_matches_producer(source_key, producer_id) for source_key in group)
        ]
        if not matched_groups:
            return True

        for group in matched_groups:
            # A single-source group is an AND dependency. It may simply be late,
            # so it must stay open until that producer sends its end frame.
            if len(group) == 1:
                return False

            if self._group_has_active_alternative(group, producer_id):
                return True

        return False

    def _get_actor(self, consumer_id: str) -> StreamActor:
        return self._streams[consumer_id]

    @property
    def stream_transform(self):
        return self._streams_transform

    async def produce(
        self, producer_id: str, message_content: Any, ability: ComponentAbility, first_frame: bool = False
    ):
        self.active_produce_ability(producer_id, ability)
        consumer_ids = self._stream_edges.get(producer_id)
        if consumer_ids:
            for consumer_id in consumer_ids:
                actor = self._get_actor(consumer_id)
                await actor.send(
                    {producer_id: message_content}, ability, first_frame=first_frame, producer_id=producer_id
                )
        else:
            logger.debug(
                "Discard chunk send from [%s] to none consumer",
                producer_id,
                event_type=LogEventType.GRAPH_SEND_STREAM_CHUNK,
                chunk=message_content,
            )

    async def end_message(self, producer_id: str, ability: ComponentAbility):
        end_message_content = f"END_{producer_id}"
        await self.produce(producer_id, end_message_content, ability)

    async def consume(
        self,
        consumer_id: str,
        ability: ComponentAbility,
        schema: dict,
        stream_callback: Callable[[dict], Awaitable[None]] = None,
    ) -> dict:
        actor = self._get_actor(consumer_id)
        consume_iter = await actor.generator(ability, schema, stream_callback)
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
        return consume_iter

    def _group_has_active_alternative(self, group: set[str], producer_id: str) -> bool:
        for source_key in group:
            group_producer_id, ability = self._split_source_key(source_key)
            if group_producer_id == producer_id:
                continue
            if ability in self._active_producer_ids.get(group_producer_id, set()):
                return True
        return False

    @staticmethod
    def _source_key_matches_producer(source_key: str, producer_id: str) -> bool:
        return ActorManager._split_source_key(source_key)[0] == producer_id

    @staticmethod
    def _split_source_key(source_key: str) -> tuple[str, ComponentAbility]:
        producer_id, ability_name = source_key.rsplit("-", 1)
        for ability in ComponentAbility:
            if ability.name == ability_name:
                return producer_id, ability
        raise ValueError(f"Unknown component ability: {ability_name}")

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
