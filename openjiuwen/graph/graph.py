# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Union, Self, AsyncIterator, Any, Callable, Hashable, Sequence, Tuple

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.common.logging import logger
from openjiuwen.core.graph.base import Graph, Router, ExecutableGraph
from openjiuwen.core.graph.executable import Executable, Input, Output
from openjiuwen.core.graph.vertex import Vertex
from openjiuwen.core.runtime.interaction.base import Checkpointer
from openjiuwen.core.runtime.interaction.checkpointer import default_inmemory_checkpointer
from openjiuwen.core.runtime.interaction.interactive_input import InteractiveInput
from openjiuwen.core.runtime.runtime import BaseRuntime
from openjiuwen.graph.pregel import Pregel, PregelBuilder, PregelConfig, MAX_RECURSIVE_LIMIT, START, END
from openjiuwen.graph.store import GraphStore


def after_step(loop):
    runtime = loop.saver.ctx if loop.saver and hasattr(loop.saver, "ctx") else None
    if runtime:
        runtime.state().commit()
    logger.debug(f"ns: {loop.config['ns']}, step: {loop.step}, active_nodes: {list(loop.active_nodes)}")


@dataclass(slots=True)
class Branch:
    condition: Callable[..., Hashable | Sequence[Hashable]]


class PregelGraph(Graph):

    def __init__(self):
        self.pregel: Pregel | None = None
        self.edges: list[Tuple[str | list[str], str]] = []
        self.waits: set[str] = set()
        self.nodes: dict[str, Vertex] = {}
        self.branches: defaultdict[str, dict[str, Branch]] = defaultdict(dict)
        self.checkpointer = None
        self._graph_store = None

    def start_node(self, node_id: str) -> Self:
        if node_id is None:
            raise JiuWenBaseException(StatusCode.GRAPH_SET_START_NODE_FAILED.code,
                                      StatusCode.GRAPH_SET_START_NODE_FAILED.errmsg.format(detail="node_id is None"))
        self.add_edge([START], node_id)
        return self

    def end_node(self, node_id: str) -> Self:
        if node_id is None:
            raise JiuWenBaseException(StatusCode.GRAPH_SET_END_NODE_FAILED.code,
                                      StatusCode.GRAPH_SET_END_NODE_FAILED.errmsg.format(
                                          detail="node_id is invalid, can not be None"))
        vertex = self.nodes.get(node_id)
        if vertex:
            vertex.is_end_node = True
        self.add_edge([node_id], END)
        return self

    def add_node(self, node_id: str, node: Executable, *, wait_for_all: bool = False) -> Self:
        if node_id is None:
            raise JiuWenBaseException(StatusCode.GRAPH_ADD_NODE_FAILED.code,
                                      StatusCode.GRAPH_ADD_NODE_FAILED.errmsg.format(
                                          detail="node_id is invalid, can not be None"))
        if node is None:
            raise JiuWenBaseException(StatusCode.GRAPH_ADD_NODE_FAILED.code,
                                      StatusCode.GRAPH_ADD_NODE_FAILED.errmsg.format(detail="node is None"))
        if node_id in self.nodes:
            raise JiuWenBaseException(StatusCode.GRAPH_ADD_NODE_FAILED.code,
                                      StatusCode.GRAPH_ADD_NODE_FAILED.errmsg.format(
                                          detail=f"already has node {node_id}, can not add again"))
        vertex_node = Vertex(node_id, node)
        self.nodes[node_id] = vertex_node
        if wait_for_all:
            self.waits.add(node_id)
        return self

    def get_nodes(self) -> dict[str, Vertex]:
        return {key: vertex for key, vertex in self.nodes.items()}

    def add_edge(self, source_node_id: Union[str, list[str]], target_node_id: str) -> Self:
        if source_node_id is None:
            raise JiuWenBaseException(StatusCode.GRAPH_ADD_EDGE_FAILED.code,
                                      StatusCode.GRAPH_ADD_EDGE_FAILED.errmsg.format(
                                          detail="source_node_id is invalid, can not be None"))
        if isinstance(source_node_id, list):
            for node_id in source_node_id:
                if node_id is None:
                    raise JiuWenBaseException(StatusCode.GRAPH_ADD_EDGE_FAILED.code,
                                              StatusCode.GRAPH_ADD_EDGE_FAILED.errmsg.format(
                                                  detail="source_node_id list is invalid, can not has None"))
        if target_node_id is None:
            raise JiuWenBaseException(StatusCode.GRAPH_ADD_EDGE_FAILED.code,
                                      StatusCode.GRAPH_ADD_EDGE_FAILED.errmsg.format(
                                          detail="target_node_id is invalid, can not be None"))
        self.edges.append((source_node_id, target_node_id))
        return self

    def add_conditional_edges(self, source_node_id: str, router: Router) -> Self:
        if source_node_id is None:
            raise JiuWenBaseException(StatusCode.GRAPH_ADD_CONDITION_EDGE_FAILED.code,
                                      StatusCode.GRAPH_ADD_CONDITION_EDGE_FAILED.errmsg.format(
                                          detail="source_node_id is invalid, can not be None"))
        if router is None:
            raise JiuWenBaseException(StatusCode.GRAPH_ADD_CONDITION_EDGE_FAILED.code,
                                      StatusCode.GRAPH_ADD_CONDITION_EDGE_FAILED.errmsg.format(
                                          detail="router is None"))
        name = _get_callable_name(router)
        self.branches[source_node_id][name] = Branch(router)
        return self

    def compile(self, runtime: BaseRuntime) -> ExecutableGraph:
        for node_id, node in self.nodes.items():
            node.init(runtime)
        if self.pregel is None:
            self.checkpointer = default_inmemory_checkpointer
            store = GraphStore(runtime, self.checkpointer.graph_store())
            self.pregel = self._compile(graph_store=store, step_callback=after_step)
            self._graph_store = store
        else:
            self._graph_store.reset(runtime)
        return CompiledGraph(self.pregel, self.checkpointer)

    def _compile(self, graph_store=None, step_callback=None) -> Pregel:
        edges: list[Tuple[str | list[str], str]] = []
        sources: dict[str, set[str]] = {}
        builder = PregelBuilder()
        for node_id, action in self.nodes.items():
            builder.add_node(node_id, action)

        for (source_node_id, target_node_id) in self.edges:
            if target_node_id in self.waits:
                if target_node_id not in sources:
                    sources[target_node_id] = set()
                if isinstance(source_node_id, str):
                    sources[target_node_id].add(source_node_id)
                elif isinstance(source_node_id, list):
                    sources[target_node_id].update(source_node_id)
            else:
                edges.append((source_node_id, target_node_id))
        for (target_node_id, source_node_id) in sources.items():
            builder.add_edge(source_node_id, target_node_id)
        for (source_node_id, target_node_id) in edges:
            builder.add_edge(source_node_id, target_node_id)

        for start, branches in self.branches.items():
            for name, branch in branches.items():
                builder.add_branch(start, branch.condition)
        return builder.build(graph_store, after_step_callback=step_callback)

    async def reset(self):
        for node in self.nodes.values():
            await node.reset()


class CompiledGraph(ExecutableGraph):
    def __init__(self, pregel: Pregel, checkpointer: Checkpointer):
        self._pregel = pregel
        self._checkpointer = checkpointer

    async def _invoke(self, inputs: Input, runtime: BaseRuntime, config: Any = None) -> Output:
        is_main = False
        session_id = runtime.session_id()
        workflow_id = runtime.workflow_id()

        if config is None:
            is_main = True
            config = PregelConfig(session_id=session_id, ns=workflow_id, recursion_limit=MAX_RECURSIVE_LIMIT)

        if is_main:
            await self._checkpointer.pre_workflow_execute(runtime, inputs)
        if not isinstance(inputs, InteractiveInput):
            runtime.state().commit_user_inputs(inputs)

        result = None
        exception = None

        try:
            result = await self._pregel.run(config=config)
        except Exception as e:
            exception = e

        if is_main:
            await self._checkpointer.post_workflow_execute(runtime, result, exception)
        elif exception is not None:
            raise exception

    async def stream(self, inputs: Input, runtime: BaseRuntime) -> AsyncIterator[Output]:
        pass

    async def interrupt(self, message: dict):
        return


def _get_callable_name(func) -> str:
    if hasattr(func, '__name__'):
        return func.__name__
    elif hasattr(func, '__class__'):
        return func.__class__.__name__
    else:
        return repr(func)
