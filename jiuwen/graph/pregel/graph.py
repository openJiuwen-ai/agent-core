#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Union, Self, AsyncIterator, Any, Callable

from openjiuwen._internal._constants import INTERRUPT
from openjiuwen.graph import StateGraph
from openjiuwen.graph.state import CompiledStateGraph
from openjiuwen.pregel._loop import PregelLoop

from jiuwen.core.common.exception.exception import JiuWenBaseException
from jiuwen.core.common.exception.status_code import StatusCode
from jiuwen.core.runtime.interaction.base import Checkpointer
from jiuwen.core.runtime.interaction.checkpointer import default_inmemory_checkpointer
from jiuwen.core.runtime.runtime import BaseRuntime
from jiuwen.core.graph.base import Graph, Router, ExecutableGraph
from jiuwen.core.graph.executable import Executable, Input, Output
from jiuwen.core.graph.graph_state import GraphState
from jiuwen.core.runtime.interaction.interactive_input import InteractiveInput
from jiuwen.core.graph.vertex import Vertex
from jiuwen.graph.checkpoint.checkpointer import GraphCheckpointer


class AfterProcessor:
    def __init__(self, after_tick: Callable[..., Any]):
        self._after_tick = after_tick

    def after_tick(self, loop: PregelLoop, runtime: BaseRuntime) -> None:
        if runtime:
            runtime.state().commit()
        return self._after_tick(loop)


after_processor: AfterProcessor = AfterProcessor(PregelLoop.after_tick)


def after_tick(self) -> None:
    runtime = self.checkpointer.ctx if self.checkpointer and hasattr(self.checkpointer, "ctx") else None
    return after_processor.after_tick(self, runtime)


PregelLoop.after_tick = after_tick
MAX_RECURSIVE_LIMIT = 10000


class PregelGraph(Graph):

    def __init__(self):
        self.pregel: StateGraph = StateGraph(GraphState)
        self.compiledStateGraph = None
        self.edges: list[Union[str, list[str]], str] = []
        self.waits: set[str] = set()
        self.nodes: dict[str, Vertex] = {}
        self.checkpoint_saver = None
        self._graph_checkpointer = None

    def start_node(self, node_id: str) -> Self:
        if node_id is None:
            raise JiuWenBaseException(StatusCode.GRAPH_SET_START_NODE_FAILED.code,
                                      StatusCode.GRAPH_SET_START_NODE_FAILED.errmsg.format(detail="node_id is None"))
        self.pregel.set_entry_point(node_id)
        return self

    def end_node(self, node_id: str) -> Self:
        if node_id is None:
            raise JiuWenBaseException(StatusCode.GRAPH_SET_END_NODE_FAILED.code,
                                      StatusCode.GRAPH_SET_END_NODE_FAILED.errmsg.format(
                                          detail="node_id is invalid, can not be None"))
        self.pregel.set_finish_point(node_id)
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
        self.pregel.add_node(node_id, vertex_node)
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
        self.pregel.add_conditional_edges(source_node_id, router)
        return self

    def compile(self, runtime: BaseRuntime) -> ExecutableGraph:
        for node_id, node in self.nodes.items():
            node.init(runtime)
        if self.compiledStateGraph is None:
            self._pre_compile()
            self.checkpoint_saver = default_inmemory_checkpointer
            graph_checkpointer = GraphCheckpointer(runtime, self.checkpoint_saver.graph_checkpointer(runtime.session_id()))
            self.compiledStateGraph = self.pregel.compile(checkpointer=graph_checkpointer)
            self._graph_checkpointer = graph_checkpointer
        else:
            self._graph_checkpointer.reset(runtime)
        return CompiledGraph(self.compiledStateGraph, self.checkpoint_saver)

    def _pre_compile(self):
        edges: list[Union[str, list[str]], str] = []
        sources: dict[str, list[str]] = {}
        for (source_node_id, target_node_id) in self.edges:
            if target_node_id in self.waits:
                if target_node_id not in sources:
                    sources[target_node_id] = []
                if isinstance(source_node_id, str):
                    sources[target_node_id].append(source_node_id)
                elif isinstance(source_node_id, list):
                    sources[target_node_id].extend(source_node_id)
            else:
                edges.append((source_node_id, target_node_id))
        for (target_node_id, source_node_id) in sources.items():
            self.pregel.add_edge(source_node_id, target_node_id)
        for (source_node_id, target_node_id) in edges:
            self.pregel.add_edge(source_node_id, target_node_id)


class CompiledGraph(ExecutableGraph):
    def __init__(self, compiled_state_graph: CompiledStateGraph,
                 checkpoint_saver: Checkpointer) -> None:
        self._compiled_state_graph = compiled_state_graph
        self._checkpoint_saver = checkpoint_saver

    async def _invoke(self, inputs: Input, runtime: BaseRuntime, config: Any = None) -> Output:
        is_main = False
        workflow_id = runtime.workflow_id()
        graph_inputs = None if isinstance(inputs, InteractiveInput) else {"source_node_id": []}

        if config is None:
            is_main = True
            config = {"configurable": {"thread_id": workflow_id}, "recursion_limit": MAX_RECURSIVE_LIMIT}
        if isinstance(inputs, InteractiveInput):
            await self._checkpoint_saver.pre_workflow_execute(runtime, inputs)
        else:
            runtime.state().commit_user_inputs(inputs)

        result = None
        exception = None

        try:
            result = await self._compiled_state_graph.ainvoke(graph_inputs,
                                                              config=config,
                                                              durability="exit")
        except Exception as e:
            exception = e

        if is_main:
            await self._checkpoint_saver.post_workflow_execute(runtime, result, exception)
        elif exception is not None:
            raise exception

    async def stream(self, inputs: Input, runtime: BaseRuntime) -> AsyncIterator[Output]:
        async for chunk in self._compiled_state_graph.astream({"source_node_id": []}):
            yield chunk

    async def interrupt(self, message: dict):
        return
