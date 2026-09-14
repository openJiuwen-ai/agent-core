# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import openjiuwen.core.graph.pregel.engine as engine_module
from openjiuwen.core.common.logging import LogEventType
from openjiuwen.core.graph.pregel.base import GraphInterrupt, Interrupt, PregelNode
from openjiuwen.core.graph.pregel.config import PregelConfig
from openjiuwen.core.graph.pregel.constants import TASK_STATUS_INTERRUPT
from openjiuwen.core.graph.pregel.engine import Pregel, PregelLoop


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


def _patch_graph_lifecycle(monkeypatch, loop):
    monkeypatch.setattr(engine_module, "PregelLoop", MagicMock(return_value=loop))
    monkeypatch.setattr(engine_module, "trigger", AsyncMock())
    debug = MagicMock()
    info = MagicMock()
    monkeypatch.setattr(engine_module.graph_logger, "debug", debug)
    monkeypatch.setattr(engine_module.graph_logger, "info", info)
    return debug, info


@pytest.mark.asyncio
async def test_graph_lifecycle_logs_use_debug_level(monkeypatch):
    loop = MagicMock(step=2)
    loop.init = AsyncMock()
    loop.run_step = AsyncMock(return_value=False)
    debug, info = _patch_graph_lifecycle(monkeypatch, loop)

    assert await Pregel({}, []).run(PregelConfig()) == {}

    assert info.call_count == 0
    assert [call.kwargs["event_type"] for call in debug.call_args_list] == [
        LogEventType.GRAPH_START,
        LogEventType.GRAPH_END,
    ]


@pytest.mark.asyncio
async def test_interrupted_graph_lifecycle_log_uses_debug_level(monkeypatch):
    loop = MagicMock(step=3)
    loop.init = AsyncMock()
    loop.run_step = AsyncMock(side_effect=GraphInterrupt(Interrupt("stop")))
    debug, info = _patch_graph_lifecycle(monkeypatch, loop)

    result = await Pregel({}, []).run(PregelConfig())

    assert TASK_STATUS_INTERRUPT in result
    assert info.call_count == 0
    assert [call.kwargs["event_type"] for call in debug.call_args_list] == [
        LogEventType.GRAPH_START,
        LogEventType.GRAPH_END,
    ]
