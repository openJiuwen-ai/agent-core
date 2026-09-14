# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from types import SimpleNamespace

import pytest

from openjiuwen.core.graph.pregel.base import PregelNode
from openjiuwen.core.graph.pregel.config import PregelConfig
from openjiuwen.core.graph.pregel.engine import PregelLoop


class RecordingManager:
    def __init__(self, events):
        self.events = events

    def get_ready_nodes(self):
        return ["first", "second"]

    def consume(self, name):
        self.events.append(("consume", name))

    def is_empty(self):
        return False

    def flush(self):
        self.events.append(("flush",))


class RecordingExecutor:
    def __init__(self, events):
        self.events = events
        self.succeed_messages = []

    def submit(self, node, version):
        self.events.append(("submit", node.name, version))

    async def wait_all(self):
        return None

    def clear(self):
        self.events.append(("clear",))


@pytest.mark.asyncio
async def test_run_step_consumes_and_submits_each_node_in_one_pass():
    events = []

    async def node_func():
        return None

    graph = SimpleNamespace(
        nodes={
            "first": PregelNode("first", node_func, []),
            "second": PregelNode("second", node_func, []),
        },
        channels=[],
        store=None,
        after_step=None,
    )
    loop = PregelLoop(graph, PregelConfig(recursion_limit=10))
    loop.manager = RecordingManager(events)
    loop.executor = RecordingExecutor(events)

    assert await loop._run_step()

    assert events[:4] == [
        ("consume", "first"),
        ("submit", "first", 1),
        ("consume", "second"),
        ("submit", "second", 1),
    ]
