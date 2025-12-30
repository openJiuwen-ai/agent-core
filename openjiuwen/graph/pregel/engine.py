# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Dict, Optional, Union, Callable, Any, Coroutine, List

from openjiuwen.core.common.logging import logger
from openjiuwen.graph.pregel.base import TriggerMessage, PregelNode, Channel, Interrupt, GraphInterrupt
from openjiuwen.graph.pregel.channels import ChannelManager
from openjiuwen.graph.pregel.config import PregelConfig, DEFAULT_PREGEL_CONFIG, InnerPregelConfig, \
    create_inner_config
from openjiuwen.graph.pregel.constants import END, TASK_STATUS_INTERRUPT, START, \
    PARENT_NS, NS, SESSION_ID, RECURSION_LIMIT
from openjiuwen.graph.pregel.task import TaskExecutorPool
from openjiuwen.graph.store import GraphState, PendingNode, create_state, Store


class PregelLoop:
    def __init__(self, graph: Pregel, config: PregelConfig):
        self.graph = graph
        self.manager = ChannelManager(graph.channels)
        self.step: int = 0
        self.max_step: int = 0
        self.config: PregelConfig = config
        self.saver = graph.store
        self.active_nodes: List[str] = []
        self.executor: TaskExecutorPool | None = None
        self._retry_pending_nodes: Dict[str, PendingNode] = {}
        # loop subgraph version
        self.node_version: Dict[str, int] = defaultdict(int)

    async def init(self) -> None:
        self.executor = TaskExecutorPool(self.config)
        self.max_step = self.config[RECURSION_LIMIT]
        state = None
        if self.config.get(SESSION_ID) and self.config.get(NS) and self.saver:
            state = await self.saver.get(self.config[SESSION_ID], self.config[NS])
        if self._is_resume(state):
            # Restore barrier channel
            self.manager.restore(state.channel_values)
            # restore loop node version
            self.node_version = state.node_version
            # Restore step
            self.step = state.step
            self.max_step = state.step + self.config[RECURSION_LIMIT]
            # Restore task result message
            for msg in state.pending_buffer:
                self.manager.buffer_message(msg)
            # Pending node
            if state.pending_node:
                self._retry_pending_nodes = state.pending_node
        else:
            # Trigger start node
            self.manager.buffer_message(TriggerMessage(sender=self.graph.initial, target=self.graph.initial))
            self.manager.flush()

    async def run_step(self) -> bool:
        try:
            return await self._run_step()
        except Exception as e:
            await self._save_state_on_error(e)
            raise e

    @staticmethod
    def _is_resume(state: GraphState) -> bool:
        return state is not None and (
                bool(state.pending_node) or bool(state.pending_buffer) or bool(state.channel_values))

    async def _run_step(self) -> bool:
        # 1. Determine tasks for this round
        tasks_to_run = []

        # Retry tasks from graph state
        if self._retry_pending_nodes:
            self.active_nodes = list(self._retry_pending_nodes.keys())
            # Clear retry queue (scheduled this round)
            self._retry_pending_nodes.clear()
        else:
            # First in
            ready_nodes = self.manager.get_ready_nodes()
            # Active nodes
            self.active_nodes = []
            for n in ready_nodes:
                if n in self.graph.nodes and n != END:
                    self.active_nodes.append(n)
                    self.node_version[n] += 1

        if not self.active_nodes:
            if self.manager.is_empty():
                return False  # End

            # Only flush buffer
            self.manager.flush()
            self.step += 1
            return True

        if self.step > self.max_step:
            raise RecursionError(
                f"Recursion limit of {self.max_step} reached at step {self.step} ns: {self.config[NS]}."
            )

        for name in self.active_nodes:
            self.manager.consume(name)
            tasks_to_run.append(self.graph.nodes[name])

        # 2. Execute tasks
        for node in tasks_to_run:
            self.executor.submit(node, self.node_version[node.name])

        try:
            await self.executor.wait_all()
        except Exception as e:
            raise e

        # 3. Summarize results
        for msg in self.executor.succeed_messages:
            self.manager.buffer_message(msg)
        self.manager.flush()
        self.executor.clear()

        # Hook
        if self.graph.after_step:
            callback = self.graph.after_step
            if asyncio.iscoroutinefunction(callback):
                await callback(self)
            else:
                callback(self)
        self.step += 1
        return True

    async def _save_state_on_error(self, exception: Exception):
        logger.debug(f"save_state_on_error: {exception}")
        if not self.config.get(SESSION_ID) or not self.config.get(NS) or not self.saver:
            return
        pending_buffer = self.manager.buffer
        pending_node = {}
        if self.executor:
            pending_buffer.extend(self.executor.succeed_messages)
            pending_node = self.executor.failed
        error_state = create_state(
            ns=self.config[NS],
            step=self.step,
            channel_snapshot=self.manager.snapshot(),
            pending_buffer=pending_buffer,
            pending_node=pending_node,
            node_version=self.node_version
        )
        await self.saver.save(
            session_id=self.config[SESSION_ID],
            ns=self.config[NS],
            state=error_state
        )


class Pregel:
    def __init__(
            self,
            nodes: Dict[str, PregelNode],
            channels: List[Channel],
            initial: str = START,
            store: Store | None = None,
            after_step: Optional[
                Union[
                    Callable[[PregelLoop], Any],
                    Callable[[PregelLoop], Coroutine[Any, Any, Any]]
                ]
            ] = None,
    ):
        self.nodes = nodes
        self.store = store
        # key:node node_name, value:list[Channel]
        self.channels = channels
        self.initial = initial
        self.after_step = after_step

    async def run(self, config: Optional[PregelConfig] = None) -> None | dict[
        Any, Any] | dict[str, Interrupt | tuple[Interrupt, ...] | None]:
        inner_config: InnerPregelConfig = create_inner_config(config or DEFAULT_PREGEL_CONFIG)
        is_top_level = not inner_config.get(PARENT_NS)
        if is_top_level:
            current_ns = inner_config.get(NS)
            if current_ns:
                inner_config[PARENT_NS] = current_ns

        loop = PregelLoop(self, inner_config)
        try:
            await loop.init()
            while await loop.run_step():
                pass
            return {}
        except GraphInterrupt as e:
            if is_top_level:
                return {TASK_STATUS_INTERRUPT: e.value}
            else:
                raise e
