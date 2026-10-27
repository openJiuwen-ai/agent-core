# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""EDGE_TRAVERSED trigger tests for ``NodeTask``.

``NodeTask.run`` gates the per-edge trigger on a single subscriber check
(one per task, not one per traversed edge). These tests pin both sides:
with a subscriber registered every routed edge fires the event; without
one, the framework is never asked to trigger.
"""

import pytest
import pytest_asyncio

from openjiuwen.core.graph.pregel.base import PregelNode
from openjiuwen.core.graph.pregel.config import PregelConfig
from openjiuwen.core.graph.pregel.router import StaticRouter
from openjiuwen.core.graph.pregel.task import NodeTask
from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.callback.events import WorkflowEvents


@pytest_asyncio.fixture
async def edge_framework():
    fw = Runner.callback_framework
    yield fw
    await fw.unregister_event(WorkflowEvents.EDGE_TRAVERSED)


def _task_with_two_edges():
    async def node_func(config=None):  # noqa: ARG001 - config injection is engine contract
        return None

    node = PregelNode("n1", node_func, routers=[StaticRouter(["n2", "n3"])])
    config = PregelConfig(session_id="s1", ns="graph-1", recursion_limit=10)
    return NodeTask(node, config, version=0)


@pytest.mark.asyncio
async def test_edge_traversed_fires_per_routed_edge_with_subscriber(edge_framework):
    fired = []

    @edge_framework.on(WorkflowEvents.EDGE_TRAVERSED)
    async def collector(**kwargs):
        fired.append(kwargs)

    result = await _task_with_two_edges().run()

    assert [m.target for m in result] == ["n2", "n3"]
    assert [kw["target_node"] for kw in fired] == ["n2", "n3"]
    assert all(kw["source_node"] == "n1" for kw in fired)
    assert all(kw["graph_id"] == "graph-1" for kw in fired)


@pytest.mark.asyncio
async def test_edge_traversed_skips_framework_without_subscriber(monkeypatch):
    """No subscriber → the whole per-edge trigger machinery stays idle.

    Uses a fully isolated spy framework (no global state): the subscriber
    check must run exactly once per task (hoisted out of the edge loop)
    and trigger must never be called.
    """

    class _SpyFramework:
        def __init__(self):
            self.subscriber_checks = 0
            self.trigger_calls = 0

        def has_subscribers(self, event):
            self.subscriber_checks += 1
            return False

        async def trigger(self, event, **kwargs):
            self.trigger_calls += 1

    spy = _SpyFramework()
    import openjiuwen.core.graph.pregel.task as task_module

    monkeypatch.setattr(task_module, "get_callback_framework", lambda: spy)

    result = await _task_with_two_edges().run()

    assert [m.target for m in result] == ["n2", "n3"]
    assert spy.subscriber_checks == 1
    assert spy.trigger_calls == 0
