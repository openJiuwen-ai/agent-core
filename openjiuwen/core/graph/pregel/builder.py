# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

from openjiuwen.core.graph.pregel.base import PregelNode
from openjiuwen.core.graph.pregel.channels import BarrierChannel, TriggerChannel
from openjiuwen.core.graph.pregel.constants import START, END
from openjiuwen.core.graph.pregel.engine import Pregel
from openjiuwen.core.graph.pregel.router import BarrierRouter, StaticRouter, ConditionalRouter


class PregelBuilder:
    def __init__(self):
        self.nodes = {}
        self.channels = []

        self.add_node(START, lambda: None, [])
        self.add_node(END, lambda: None, [])

    def add_node(self, name, fn, routers=None):
        if routers is None:
            routers = []
        self.nodes[name] = PregelNode(name, fn, routers)
        self.channels.append(TriggerChannel(name))
        return self

    def add_edge(self, start: str | list[str] | set[str] | tuple[str, ...],
                 end: str | list[str] | set[str] | tuple[str, ...]):
        """
        - N to 1 -> barrier (supports CNF groups: List[str | Set[str]])
        - 1 to N -> static
        """
        if isinstance(start, (list, set, tuple)) and isinstance(end, str):
            # barrier: build CNF groups from start items
            expected_groups = []
            for item in start:
                if isinstance(item, str):
                    expected_groups.append({item})
                elif isinstance(item, (set, frozenset)):
                    expected_groups.append(set(item))
                else:
                    raise ValueError(f"Unsupported barrier source item type: {type(item)}")
            barrier = BarrierChannel(end, expected_groups=expected_groups)
            self.channels.append(barrier)
            # Register BarrierRouter on all senders from start items
            for item in start:
                if isinstance(item, str):
                    self.nodes[item].routers.append(BarrierRouter([barrier.key]))
                elif isinstance(item, (set, frozenset)):
                    for s in item:
                        self.nodes[s].routers.append(BarrierRouter([barrier.key]))

        elif isinstance(start, str) and isinstance(end, (list, set, tuple)):
            # multi-static
            self.nodes[start].routers.append(StaticRouter(list(end)))

        elif isinstance(start, str) and isinstance(end, str):
            # single-static
            self.nodes[start].routers.append(StaticRouter([end]))

        else:
            raise ValueError(f"Unsupported edge format: {start} -> {end}")

        return self

    def add_branch(self, src, selector):
        self.nodes[src].routers.append(ConditionalRouter(selector=selector))
        return self

    def build(self, store=None, after_step_callback=None):
        return Pregel(
            nodes=self.nodes,
            channels=self.channels,
            store=store,
            after_step=after_step_callback
        )
