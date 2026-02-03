# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio

import pytest

from openjiuwen.core.common.logging import logger
from openjiuwen.core.graph.pregel import (
    GraphInterrupt,
    Interrupt,
    Pregel,
    PregelBuilder,
    PregelConfig,
)
from openjiuwen.core.graph.pregel.base import PregelNode
from openjiuwen.core.graph.pregel.channels import (
    BarrierChannel,
    TriggerChannel,
)
from openjiuwen.core.graph.pregel.constants import (
    END,
    NS,
    START,
)
from openjiuwen.core.graph.pregel.router import (
    BarrierRouter,
    ConditionalRouter,
    StaticRouter,
)
from openjiuwen.core.session.checkpointer import CheckpointerFactory


@pytest.fixture
def basic_nodes_and_channels_direct():
    """Fixture providing a basic graph with barrier synchronization using direct construction.

    Graph structure:
    start -> a -> a1 --\
            b --------\
            c ---------\-> collect -> end
            d --------/
    """

    # Node functions
    def fn_pass():
        return "pass"

    def fn_slow():
        return "slow_data"

    a1bcd_to_collect = BarrierChannel("collect", expected={"a1", "b", "c", "d"})

    # Define channels
    channels = [
        TriggerChannel("start"),
        TriggerChannel("a"),
        TriggerChannel("b"),
        TriggerChannel("c"),
        TriggerChannel("d"),
        TriggerChannel("a1"),
        TriggerChannel("end"),
        a1bcd_to_collect
    ]

    # Define nodes and routers
    nodes = {
        "start": PregelNode("start", fn_pass, [StaticRouter(["a", "b", "c", "d"])]),  # 1->4 Fan-out
        "a": PregelNode("a", fn_pass, [StaticRouter(["a1"])]),
        "b": PregelNode("b", fn_pass, [BarrierRouter([a1bcd_to_collect.key])]),  # Writes to barrier
        "c": PregelNode("c", fn_pass, [BarrierRouter([a1bcd_to_collect.key])]),
        "d": PregelNode("d", fn_pass, [BarrierRouter([a1bcd_to_collect.key])]),
        "a1": PregelNode("a1", fn_slow, [BarrierRouter([a1bcd_to_collect.key])]),  # The delayed input to barrier
        "collect": PregelNode("collect", fn_pass, [StaticRouter(["end"])]),
        "end": PregelNode("end", fn_pass, [StaticRouter([])]),
    }

    return nodes, channels


@pytest.fixture
def basic_nodes_and_channels_builder():
    """Fixture providing a basic graph with barrier synchronization using builder.

    Graph structure:
    start -> a -> a1 --\
            b --------\
            c ---------\-> collect -> end
            d --------/
    """

    def fn_pass():
        logger.debug("pass!!!")
        return "pass"

    def fn_slow():
        return "slow_data"

    builder = PregelBuilder()
    builder.add_node("start", fn_pass)
    builder.add_node("a", fn_pass)
    builder.add_node("b", fn_pass)
    builder.add_node("c", fn_pass)
    builder.add_node("d", fn_pass)
    builder.add_node("a1", fn_slow)
    builder.add_node("collect", fn_pass)
    builder.add_node("end", fn_pass)

    builder.add_edge("start", ["a", "b", "c", "d"])
    builder.add_edge("a", "a1")
    builder.add_edge(["a1", "b", "c", "d"], "collect")
    builder.add_edge("collect", "end")

    return builder.nodes, builder.channels


@pytest.fixture(params=[
    ("direct", "basic_nodes_and_channels_direct"),
    ("builder", "basic_nodes_and_channels_builder")
])
def basic_nodes_and_channels(request):
    """Parameterized fixture providing basic graph with barrier synchronization using both construction methods."""
    _, fixture_name = request.param
    return request.getfixturevalue(fixture_name)


@pytest.fixture
def conditional_routing_direct():
    """Fixture providing conditional routing graph using direct construction."""

    def pick_target():
        return "D"

    def fn_int():
        return 42

    def fn_receive():
        return "received"

    # Define channels
    channels = [
        TriggerChannel("A"),
        TriggerChannel("D"),
        TriggerChannel("E")
    ]

    # Define nodes and routers
    nodes = {
        "A": PregelNode("A", fn_int, [ConditionalRouter(selector=pick_target)]),
        "D": PregelNode("D", fn_receive, [StaticRouter([])]),
        "E": PregelNode("E", fn_receive, [StaticRouter([])]),
    }

    return nodes, channels


@pytest.fixture
def conditional_routing_builder():
    """Fixture providing conditional routing graph using builder."""

    def pick_target():
        return "D"

    def fn_int():
        return 42

    def fn_receive():
        return "received"

    builder = PregelBuilder()
    builder.add_node("A", fn_int)
    builder.add_node("D", fn_receive)
    builder.add_node("E", fn_receive)
    builder.add_branch("A", pick_target)

    return builder.nodes, builder.channels


@pytest.fixture(params=[
    ("direct", "conditional_routing_direct"),
    ("builder", "conditional_routing_builder")
])
def conditional_routing(request):
    """Parameterized fixture providing conditional routing graph using both construction methods."""
    _, fixture_name = request.param
    return request.getfixturevalue(fixture_name)


@pytest.fixture
def multi_routing_direct():
    """Fixture providing multi-routing graph using direct construction."""

    # Define node functions
    def fn_int():
        return 1

    def fn_receive():
        return 2

    def fn_end():
        logger.debug("END")

    def pick_target():
        # Simple condition: if value > 0, go to E, otherwise go to F
        return "E"

    # Define barrier channels
    abc_to_d = BarrierChannel("D", expected={"A", "B", "C"})
    ay_to_d = BarrierChannel("D", expected={"A", "Y"})
    deg_to_end = BarrierChannel("END", expected={"D", "E", "G"})

    # Define channels
    channels = [
        TriggerChannel("START"),
        TriggerChannel("A"),
        TriggerChannel("B"),
        TriggerChannel("C"),
        TriggerChannel("X"),
        TriggerChannel("Y"),
        TriggerChannel("E"),
        TriggerChannel("F"),
        TriggerChannel("G"),
        TriggerChannel("D"),  # For Y -> D
        TriggerChannel("END"),
        abc_to_d,  # Barrier A,B,C -> D
        ay_to_d,  # Barrier A,Y -> D
        deg_to_end,  # Barrier D,E,G -> END
    ]

    # Define nodes
    nodes = {
        # START → A,B,C,X
        "START": PregelNode("START", fn_int, [
            StaticRouter(["A", "B", "C", "X"])
        ]),

        # A routes to:
        # - G (static)
        # - E or F (conditional)
        # - D (barrier A,B,C)
        # - D (barrier A,X)
        "A": PregelNode("A", fn_int, [
            StaticRouter(["G"]),
            ConditionalRouter(selector=pick_target),
            BarrierRouter([abc_to_d.key, ay_to_d.key]),
        ]),

        "B": PregelNode("B", fn_int, [
            BarrierRouter([abc_to_d.key]),
        ]),

        "C": PregelNode("C", fn_int, [
            BarrierRouter([abc_to_d.key]),
        ]),

        # X routes to:
        # - Y (static)
        # - D (barrier A,X)
        "X": PregelNode("X", fn_int, [
            StaticRouter(["Y"]),
            BarrierRouter([ay_to_d.key]),
        ]),

        # Y -> D (trigger channel)
        "Y": PregelNode("Y", fn_receive, [
            StaticRouter(["D"])
        ]),

        # D triggered either by barrier(A,B,C) or by barrier(A,X) or by Y
        "D": PregelNode("D", fn_receive, [
            BarrierRouter([deg_to_end.key]),
        ]),

        "E": PregelNode("E", fn_receive, [
            BarrierRouter([deg_to_end.key]),
        ]),

        "F": PregelNode("F", fn_receive, [
            StaticRouter(["END"]),
        ]),

        "G": PregelNode("G", fn_receive, [
            BarrierRouter([deg_to_end.key]),
        ]),

        "END": PregelNode("END", fn_end, [
            StaticRouter([]),
        ]),
    }

    return nodes, channels


@pytest.fixture
def multi_routing_builder():
    """Fixture providing multi-routing graph using builder."""

    # Define node functions
    def fn_int():
        return 1

    def fn_receive():
        return 2

    def fn_end():
        logger.debug("END")

    def pick_target():
        return "E"

    builder = PregelBuilder()

    builder.add_node("START", fn_int)
    builder.add_node("A", fn_int)
    builder.add_node("B", fn_int)
    builder.add_node("C", fn_int)
    builder.add_node("X", fn_int)
    builder.add_node("Y", fn_receive)
    builder.add_node("D", fn_receive)
    builder.add_node("E", fn_receive)
    builder.add_node("F", fn_receive)
    builder.add_node("G", fn_receive)
    builder.add_node("END", fn_end)

    builder.add_edge("START", ["A", "B", "C", "X"])  # fan-out
    builder.add_edge("A", "G")  # static
    builder.add_branch("A", pick_target)  # conditional
    builder.add_edge(["A", "B", "C"], "D")  # barrier A,B,C -> D
    builder.add_edge(["A", "Y"], "D")  # barrier A,Y -> D
    builder.add_edge("X", "Y")  # static
    builder.add_edge("Y", "D")  # static
    builder.add_edge(["D", "E", "G"], "END")  # barrier D,E,G -> END
    builder.add_edge("F", "END")  # static

    return builder.nodes, builder.channels


@pytest.fixture(params=[
    ("direct", "multi_routing_direct"),
    ("builder", "multi_routing_builder")
])
def multi_routing(request):
    """Parameterized fixture providing multi-routing graph using both construction methods."""
    _, fixture_name = request.param
    return request.getfixturevalue(fixture_name)


@pytest.fixture
def nested_subgraph_builder():
    """Fixture providing a graph with a nested subgraph, demonstrating
    FIRST_EXCEPTION resume behavior.

    Outer Graph: start -> a -> end

    Inner Subgraph (node a):
    start1 -> [a1, a2, a3] ()
    a1 (0.2s, raises RuntimeError)
    a2 (1.0s, cancelled)
    a3 (0s, passes)
    """

    async def fn_a1_fail(config):
        await asyncio.sleep(0.2)
        raise RuntimeError("a1 exception")

    async def fn_a2_slow(config):
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            logger.warning("Task was cancelled")
            return "cancelled"

    def fn_a3_fast(config):
        return "a3_done"

    def fn_pass():
        return "pass"

    inner_builder = PregelBuilder()

    inner_builder.add_node("start1", fn_pass)
    inner_builder.add_node("a1", fn_a1_fail)
    inner_builder.add_node("a2", fn_a2_slow)
    inner_builder.add_node("a3", fn_a3_fast)
    inner_builder.add_node("end1", fn_pass)

    inner_builder.add_edge("start1", ("a1", "a2", "a3"))

    inner_builder.add_edge(("a1", "a2", "a3"), "end1")

    inner_nodes, inner_channels = inner_builder.nodes, inner_builder.channels

    def inner_logger(loop):
        logger.debug(f"[{loop.config.get(NS)}] Inner Step {loop.step}, Active: {list(loop.active_nodes)}")

    inner_app = Pregel(inner_nodes, inner_channels, initial="start1",
                       store=CheckpointerFactory.get_checkpointer().graph_store(),
                       after_step=inner_logger)

    class RunInner:
        def __init__(self, inner_app):
            self.inner_app = inner_app

        async def __call__(self, state, config):
            logger.debug(f"[{config.get(NS)}] Subgraph Invoked.")
            return await self.inner_app.run(config)

    builder = PregelBuilder()
    builder.add_node("start", fn_pass)
    builder.add_node("a", RunInner(inner_app))
    builder.add_node("end", fn_pass)

    builder.add_edge("start", "a")
    builder.add_edge("a", "end")

    return builder.nodes, builder.channels


@pytest.fixture
def nested_subgraph_interrupt_with_outer_parallel_builder():
    """
    Outer Graph: start -> [a, b] -> end

    - a: nested subgraph
      Inner: start1 -> [a1, a2, a3] -> end1
        a1: interrupts twice then passes (0.2s)
        a2: slow completes (1.0s)
        a3: fast completes (0s)
    - b: outer parallel node; interrupts twice then passes (0.1s)
    """

    execution_trace = []

    # --- Inner subgraph nodes ---
    async def fn_a1_fail(config):
        if not hasattr(fn_a1_fail, "call_count"):
            fn_a1_fail.call_count = 0
        fn_a1_fail.call_count += 1

        await asyncio.sleep(0.2)
        if fn_a1_fail.call_count > 2:
            logger.info("a1 done")
            return "a1_done"
        else:
            logger.info(f"a1 interrupt, {fn_a1_fail.call_count}")
            raise GraphInterrupt(Interrupt("a1_Interrupt"))

    async def fn_a2_slow(config):
        await asyncio.sleep(1.0)
        logger.info("a2 done")
        return "a2_done"

    def fn_a3_fast(config):
        logger.info("a3 done")
        return "a3_done"

    def fn_pass(config=None):
        return "pass"

    inner_builder = PregelBuilder()
    inner_builder.add_node("start1", fn_pass)
    inner_builder.add_node("a1", fn_a1_fail)
    inner_builder.add_node("a2", fn_a2_slow)
    inner_builder.add_node("a3", fn_a3_fast)
    inner_builder.add_node("end1", fn_pass)

    inner_builder.add_edge("start1", ("a1", "a2", "a3"))
    inner_builder.add_edge(("a1", "a2", "a3"), "end1")

    inner_nodes, inner_channels = inner_builder.nodes, inner_builder.channels

    def inner_logger(loop):
        execution_trace.append({
            "step": loop.step,
            "active_nodes": list(loop.active_nodes),
            "ns": loop.config.get(NS)
        })
        logger.info(f"[{loop.config.get(NS)}] Inner Step {loop.step}, Active: {list(loop.active_nodes)}")

    inner_app = Pregel(
        inner_nodes,
        inner_channels,
        initial="start1",
        store=CheckpointerFactory.get_checkpointer().graph_store(),
        after_step=inner_logger
    )

    class RunInner:
        def __init__(self, inner_app):
            self.inner_app = inner_app

        async def __call__(self, state, config):
            logger.info(f"[{config.get(NS)}] Subgraph Invoked.")
            return await self.inner_app.run(config)

    # --- Outer parallel node b: interrupts twice then passes ---
    async def fn_b_interrupt_then_pass(config):
        if not hasattr(fn_b_interrupt_then_pass, "call_count"):
            fn_b_interrupt_then_pass.call_count = 0
        fn_b_interrupt_then_pass.call_count += 1

        await asyncio.sleep(0.1)
        if fn_b_interrupt_then_pass.call_count > 2:
            logger.info("b done")
            return "b_done"
        else:
            logger.info(f"b interrupt, {fn_b_interrupt_then_pass.call_count}")
            raise GraphInterrupt(Interrupt("b_Interrupt"))

    # --- Outer graph assembly: start -> [a, b] -> end ---
    builder = PregelBuilder()
    builder.add_node("start", fn_pass)
    builder.add_node("a", RunInner(inner_app))
    builder.add_node("b", fn_b_interrupt_then_pass)
    builder.add_node("end", fn_pass)

    builder.add_edge("start", ("a", "b"))
    builder.add_edge(("a", "b"), "end")

    def outer_logger(loop):
        execution_trace.append({
            "step": loop.step,
            "active_nodes": list(loop.active_nodes),
            "ns": loop.config.get(NS)
        })
        logger.info(f"[Outer] Step {loop.step}, Active: {list(loop.active_nodes)}")

    graph = Pregel(
        nodes=builder.nodes,
        channels=builder.channels,
        initial="start",
        store=CheckpointerFactory.get_checkpointer().graph_store(),
        after_step=outer_logger
    )
    return graph, execution_trace


@pytest.fixture
def nested_loop_with_inner_parallel_builder():
    """
    start -> loop -> end
    subgraph loop:
        start1 -> body -> condition -> end2
                  ↖______↑
    subgraph body:
    start3 -> [a|b|c] -> end3
    """
    execution_trace = []

    def outer_logger(loop):
        execution_trace.append({
            "step": loop.step,
            "active_nodes": list(loop.active_nodes),
            "ns": loop.config.get("ns")
        })
        logger.info(f"ns: {loop.config[NS]} Step {loop.step}, Active: {list(loop.active_nodes)}")

    def fn_pass(config=None):
        return "pass"

    async def fn_a_interrupt(config):
        if not hasattr(fn_a_interrupt, "call_count"):
            fn_a_interrupt.call_count = 0
        fn_a_interrupt.call_count += 1
        await asyncio.sleep(0.1)

        # Calls 1, 2: interrupt
        if fn_a_interrupt.call_count <= 2:
            logger.info(f"a interrupt, {fn_a_interrupt.call_count}")
            raise GraphInterrupt(Interrupt("a_interrupt"))
        # Calls 3, 5, 7...: success
        elif fn_a_interrupt.call_count % 2 == 1:
            logger.info("a done")
            return "a_done"
        # Calls 4, 6, 8...: interrupt
        else:
            logger.info(f"a interrupt, {fn_a_interrupt.call_count}")
            raise GraphInterrupt(Interrupt("a_interrupt"))

    async def fn_b_interrupt(config):
        if not hasattr(fn_b_interrupt, "call_count"):
            fn_b_interrupt.call_count = 0
        fn_b_interrupt.call_count += 1
        await asyncio.sleep(0.1)

        # Calls 1, 2: interrupt
        if fn_b_interrupt.call_count <= 2:
            logger.info(f"b interrupt, {fn_b_interrupt.call_count}")
            raise GraphInterrupt(Interrupt("b_interrupt"))
        # Calls 3, 5, 7...: success
        elif fn_b_interrupt.call_count % 2 == 1:
            logger.info("b done")
            return "b_done"
        # Calls 4, 6, 8...: interrupt
        else:
            logger.info(f"b interrupt, {fn_b_interrupt.call_count}")
            raise GraphInterrupt(Interrupt("b_interrupt"))

    def fn_c_normal(config):
        logger.info("c done")
        return "c_done"

    def build_body_subgraph():
        builder = PregelBuilder()
        builder.add_node("start3", fn_pass)
        builder.add_node("a", fn_a_interrupt)
        builder.add_node("b", fn_b_interrupt)
        builder.add_node("c", fn_c_normal)
        builder.add_node("end3", fn_pass)

        builder.add_edge("start3", ("a", "b", "c"))
        builder.add_edge(("a", "b", "c"), "end3")
        builder.add_edge(START, "start3")
        builder.add_edge("end3", END)
        return builder.build(
            store=CheckpointerFactory.get_checkpointer().graph_store(),
            after_step_callback=outer_logger
        )

    class RunBody:
        def __init__(self, app):
            self.app = app

        async def __call__(self, state, config):
            logger.info(f"[{config.get('ns')}] Body Subgraph Invoked.")
            return await self.app.run(config)

    # --- loop: start1 -> body -> condition -> end2 ---
    def build_loop_subgraph():
        body_app = build_body_subgraph()

        # condition
        def fn_condition():
            if not hasattr(fn_condition, "count"):
                fn_condition.count = 0
            fn_condition.count += 1
            logger.info(f"condition check, {fn_condition.count}")
            if fn_condition.count < 4:
                return "body"
            else:
                return "end1"

        builder = PregelBuilder()
        builder.add_node("start1", fn_pass)
        builder.add_node("body", RunBody(body_app))
        builder.add_node("condition", fn_condition)
        builder.add_node("end1", fn_pass)

        builder.add_edge("start1", "body")
        builder.add_edge("body", "condition")
        builder.add_branch("condition", fn_condition)
        builder.add_edge(START, "start1")
        builder.add_edge("end1", END)

        return builder.build(
            store=CheckpointerFactory.get_checkpointer().graph_store(),
            after_step_callback=outer_logger
        )

    class RunLoop:
        def __init__(self, app):
            self.app = app

        async def __call__(self, state, config):
            logger.info(f"[{config.get('ns')}] Loop Subgraph Invoked.")
            return await self.app.run(config)

    loop_app = build_loop_subgraph()

    builder = PregelBuilder()
    builder.add_node("start", fn_pass)
    builder.add_node("loop", RunLoop(loop_app))
    builder.add_node("end", fn_pass)

    builder.add_edge("start", "loop")
    builder.add_edge("loop", "end")
    builder.add_edge(START, "start")
    builder.add_edge("end", END)

    graph = builder.build(
        store=CheckpointerFactory.get_checkpointer().graph_store(),
        after_step_callback=outer_logger
    )
    return graph, execution_trace


@pytest.fixture
def linear_nested_subgraph_setup():
    async def fn_a1_fail(config):
        pass

    def fn_generic_pass():
        return

    execution_trace = []

    def inner_logger(loop):
        execution_trace.append({
            "step": loop.step,
            "active_nodes": list(loop.active_nodes),
            "ns": loop.config['ns']
        })
        logger.debug(f"[{loop.config['ns']}] Inner Step {loop.step}, Active: {list(loop.active_nodes)}")

    inner_builder = PregelBuilder()
    inner_builder.add_node("start1", fn_generic_pass)
    inner_builder.add_node("a1", fn_a1_fail)
    inner_builder.add_node("b1", fn_generic_pass)
    inner_builder.add_node("c1", fn_generic_pass)
    inner_builder.add_node("end1", fn_generic_pass)

    inner_builder.add_edge("start1", "a1")
    inner_builder.add_edge("a1", "b1")
    inner_builder.add_edge("b1", "c1")
    inner_builder.add_edge("c1", "end1")
    inner_builder.add_edge(START, "start1")
    inner_builder.add_edge("end1", END)

    inner_app = Pregel(
        nodes=inner_builder.nodes,
        channels=inner_builder.channels,
        store=CheckpointerFactory.get_checkpointer().graph_store(),
        after_step=inner_logger
    )

    class RunInner:
        def __init__(self, inner_app):
            self.inner_app = inner_app

        async def __call__(self, state, config):
            logger.debug(f"[{config['ns']}] Subgraph Invoked by C.")
            return await self.inner_app.run(config)

    outer_builder = PregelBuilder()

    outer_builder.add_node("start", fn_generic_pass)
    outer_builder.add_node("a", fn_generic_pass)
    outer_builder.add_node("b", fn_generic_pass)
    # inner node
    outer_builder.add_node("c", RunInner(inner_app))
    outer_builder.add_node("d", fn_generic_pass)
    outer_builder.add_node("end", fn_generic_pass)

    outer_builder.add_edge("start", "a")
    outer_builder.add_edge("a", "b")
    outer_builder.add_edge("b", "c")
    outer_builder.add_edge("c", "d")
    outer_builder.add_edge("d", "end")
    outer_builder.add_edge(START, "start")
    outer_builder.add_edge("end", END)

    # Checkpointer 实例
    checkpointer = CheckpointerFactory.get_checkpointer().graph_store()

    execution_trace = []

    def log_loop(loop):
        execution_trace.append({
            "step": loop.step,
            "active_nodes": list(loop.active_nodes),
            "ns": loop.config['ns']
        })
        logger.debug(f"[Outer] Step {loop.step}, Active: {list(loop.active_nodes)}")

    graph = outer_builder.build(
        store=checkpointer,
        after_step_callback=log_loop)
    return graph, execution_trace


@pytest.mark.asyncio
class TestPregelV2:
    async def test_barrier_wait_for_all(self, basic_nodes_and_channels):
        """Test the barrier synchronization.

        Graph structure:
        start -> a -> a1 --\
                b --------\
                c ---------\-> collect -> end
                d --------/
        """
        nodes, channels = basic_nodes_and_channels

        # Track execution steps and active nodes
        execution_trace = []

        def log_loop(loop):
            execution_trace.append({
                "step": loop.step,
                "active_nodes": list(loop.active_nodes),
            })

        # Create and run the graph
        app = Pregel(nodes, channels, initial="start", after_step=log_loop)
        await app.run()

        # Verify execution trace
        assert len(execution_trace) == 5

        # Step 0: start node
        assert execution_trace[0]["active_nodes"] == ["start"]

        # Step 1: a, b, c, d nodes
        assert set(execution_trace[1]["active_nodes"]) == {"a", "b", "c", "d"}

        # Step 2: a1 node
        assert execution_trace[2]["active_nodes"] == ["a1"]

        # Step 3: collect node (barrier)
        assert execution_trace[3]["active_nodes"] == ["collect"]

        # Step 4: end node
        assert execution_trace[4]["active_nodes"] == ["end"]

    async def test_conditional_routing(self, conditional_routing):
        """Test conditional router.

        Graph structure:
        A -> D (if output is even)
        A -> E (if output is odd)
        """
        nodes, channels = conditional_routing

        # Track execution steps and active nodes
        execution_trace = []

        def log_loop(loop):
            execution_trace.append({
                "step": loop.step,
                "active_nodes": list(loop.active_nodes),
            })

        app = Pregel(nodes, channels, initial="A", after_step=log_loop)
        await app.run()

        # Verify execution trace
        assert len(execution_trace) == 2  # 2 steps expected

        # Step 0: A node
        assert execution_trace[0]["active_nodes"] == ["A"]

        # Step 1: D node (since 42 is even)
        assert execution_trace[1]["active_nodes"] == ["D"]

        # Verify E node was not activated (since 42 is even, not odd)
        all_active_nodes = [node for trace in execution_trace for node in trace["active_nodes"]]
        assert "E" not in all_active_nodes

    async def test_multi_routing(self, multi_routing):
        """Test multi-routing.

        a | b | c  --->  d
        a ---> e | f    (conditional)
        a ---> g
        x ---> y ---> d ---> end
        a    | y ---> d
        d | e | g ---> end
        """
        nodes, channels = multi_routing

        # Track execution steps and active nodes
        execution_trace = []

        def log_loop(loop):
            execution_trace.append({
                "step": loop.step,
                "active_nodes": list(loop.active_nodes),
            })
            logger.debug(f"step:{loop.step}, nodes:{list(loop.active_nodes)}")

        # Build Pregel graph
        graph = Pregel(
            nodes=nodes,
            channels=channels,
            initial="START",
            after_step=log_loop
        )

        await graph.run()

        assert len(execution_trace) == 4

        # Step 0: START node
        assert execution_trace[0]["active_nodes"] == ["START"]

        assert set(execution_trace[1]["active_nodes"]) == {'B', 'A', 'C', 'X'}
        assert set(execution_trace[2]["active_nodes"]) == {'Y', 'E', 'G', 'D'}
        assert set(execution_trace[3]["active_nodes"]) == {'END', 'D'}

    async def test_subgraph_with_exception(self, nested_subgraph_builder):
        nodes, channels = nested_subgraph_builder
        execution_trace = []

        def log_loop(loop):
            execution_trace.append({
                "step": loop.step,
                "active_nodes": list(loop.active_nodes),
                "ns": loop.config.get(NS)
            })
            logger.debug(f"[Outer] Step {loop.step}, Active: {list(loop.active_nodes)}")

        graph = Pregel(
            nodes=nodes,
            channels=channels,
            initial="start",
            store=CheckpointerFactory.get_checkpointer().graph_store(),
            after_step=log_loop
        )
        config = PregelConfig(session_id="test_parallel_fail", ns="start-a-end")
        logger.debug("\n=============== Invoke 1 (Failure) ===============")

        with pytest.raises(RuntimeError, match="a1 exception"):
            await graph.run(config)
        checkpoint = await graph.store.get(config.get("session_id"), config.get('ns'))
        assert checkpoint is not None
        assert checkpoint.pending_node is not None

        logger.debug("\n=============== Invoke 2 (Resume) ===============")
        execution_trace.clear()
        with pytest.raises(RuntimeError, match="a1 exception"):
            await graph.run(config)
        assert len(execution_trace) == 0

        logger.debug("\n=============== Invoke 3/4 (No sessionId) ===============")
        config_stateless = PregelConfig()
        execution_trace.clear()
        with pytest.raises(RuntimeError, match="a1 exception"):
            await graph.run(config_stateless)
        assert execution_trace[0]["active_nodes"] == ["start"]
        execution_trace.clear()
        with pytest.raises(RuntimeError, match="a1 exception"):
            await graph.run(config_stateless)
        assert execution_trace[0]["active_nodes"] == ["start"]

    async def test_recursion_limit_recovery(self, linear_nested_subgraph_setup):
        """
        Outer Graph: start -> a -> b -> [c (Subgraph)] -> d -> end
        Inner Subgraph (c): start1 -> a1 -> b1| -> c1 -> end1
        """
        graph, execution_trace = linear_nested_subgraph_setup
        recursion_limit = 3
        session_id = "test_recursion_limit_nested"
        ns_outer = "outer-linear-test"

        config = PregelConfig(
            session_id=session_id,
            ns=ns_outer,
            recursion_limit=recursion_limit
        )

        logger.debug("\n=============== Invoke 1 (Failure at Step 4 / Node C) ===============")

        with pytest.raises(RecursionError) as excinfo:
            await graph.run(config)

        assert f"Recursion limit of {recursion_limit} reached" in str(excinfo.value)

        assert execution_trace[-1]['active_nodes'] == ['b']

        checkpoint = await graph.store.get(session_id, config['ns'])
        assert checkpoint is not None
        assert checkpoint.step == 4
        assert not checkpoint.pending_node
        logger.debug(f"Channel Values: {checkpoint.channel_values}")
        logger.debug(f"pending_buffer: {checkpoint.pending_buffer}")
        logger.debug("=============== Invoke 2 (Resume from Node C) ===============")

        execution_trace.clear()
        with pytest.raises(RecursionError) as excinfo:
            await graph.run(config)
        assert f"Recursion limit of {recursion_limit} reached" in str(excinfo.value)
        assert execution_trace[-1]['active_nodes'] == ['b1']
        checkpoint = await graph.store.get(session_id, config['ns'])

        logger.debug("=============== Invoke 3 (Resume from Node b1) ===============")

        execution_trace.clear()
        await graph.run(config)
        assert execution_trace[-1]['active_nodes'] == ['end']

    @pytest.mark.asyncio
    async def test_subgraph_with_interrupt(self, nested_subgraph_interrupt_with_outer_parallel_builder):
        graph, execution_trace = nested_subgraph_interrupt_with_outer_parallel_builder
        config = PregelConfig(session_id="test_parallel_interrupt", ns="start-a-end")

        logger.info("=============== Invoke 1 (Interrupt Failure) ===============")

        result = await graph.run(config)
        assert result["__interrupt__"] is not None

        checkpoint = await graph.store.get(config.get("session_id"), config.get('ns'))
        assert checkpoint is not None
        assert "a" in checkpoint.pending_node
        assert "b" in checkpoint.pending_node
        checkpoint_inner = await graph.store.get(config.get("session_id"), config.get('ns') + ":a:1")
        assert "a1" in checkpoint_inner.pending_node
        assert "a2" not in checkpoint_inner.pending_node
        assert "a3" not in checkpoint_inner.pending_node
        logger.info(f"Channel Values: {checkpoint.channel_values}")
        logger.info(f"pending_buffer: {checkpoint.pending_buffer}")
        logger.info("=============== Invoke 2 (Resume, a1 Interrupt Again) ===============")
        execution_trace.clear()
        result = await graph.run(config)
        assert result["__interrupt__"] is not None

        assert len(execution_trace) == 0
        checkpoint = await graph.store.get(config.get("session_id"), config.get('ns'))
        assert checkpoint is not None
        assert "a" in checkpoint.pending_node
        assert "b" in checkpoint.pending_node
        checkpoint_inner = await graph.store.get(config.get("session_id"), config.get('ns') + ":a:1")
        assert "a1" in checkpoint_inner.pending_node
        assert "a2" not in checkpoint_inner.pending_node
        assert "a3" not in checkpoint_inner.pending_node

        logger.info("=============== Invoke 3 (Resume to End) ===============")
        execution_trace.clear()
        await graph.run(config)

        flat = [n for trace in execution_trace for n in trace['active_nodes']]
        assert "end" in flat
        assert "end1" in flat
        assert "a" in flat
        assert "b" in flat

    @pytest.mark.asyncio
    async def test_nested_loop_with_inner_parallel(self, nested_loop_with_inner_parallel_builder):
        graph, execution_trace = nested_loop_with_inner_parallel_builder
        config = PregelConfig(session_id="test_loop_interrupt", ns="start-loop-end")

        logger.info("=============== Invoke 1 (Interrupt Failure, loop iteration 1) ===============")
        result = await graph.run(config)
        assert result["__interrupt__"] is not None

        outer_state = await graph.store.get(config.get("session_id"), config.get("ns"))
        assert outer_state is not None
        assert "loop" in outer_state.pending_node

        # loop checkpoint
        loop_state = await graph.store.get(config.get("session_id"), config.get("ns") + ":loop:1")
        assert "body" in loop_state.pending_node
        # body checkpoint
        body_state = await graph.store.get(config.get("session_id"), config.get("ns") + ":loop:1:body:1")
        assert "a" in body_state.pending_node
        assert "b" in body_state.pending_node
        assert body_state.pending_buffer[0].sender == "c"

        logger.info("=============== Invoke 2 (Resume, a/b Interrupt Again, loop iteration 1) ===============")
        execution_trace.clear()
        result = await graph.run(config)
        assert result["__interrupt__"] is not None

        # 确认 trace 没有重复执行 c
        flat = [n for trace in execution_trace for n in trace['active_nodes']]
        assert "c" not in flat

        outer_state = await graph.store.get(config.get("session_id"), config.get("ns"))
        assert outer_state is not None
        assert "loop" in outer_state.pending_node

        # loop checkpoint
        loop_state = await graph.store.get(config.get("session_id"), config.get("ns") + ":loop:1")
        assert "body" in loop_state.pending_node
        # body checkpoint
        body_state = await graph.store.get(config.get("session_id"), config.get("ns") + ":loop:1:body:1")
        assert "a" in body_state.pending_node
        assert "b" in body_state.pending_node
        assert body_state.pending_buffer[0].sender == "c"

        logger.info("=============== Invoke 3 (Resume loop iteration 2 a/b Interrupt) ===============")
        execution_trace.clear()
        result = await graph.run(config)
        assert "__interrupt__" in result

        flat = [n for trace in execution_trace for n in trace['active_nodes']]
        # ab resume
        assert 'a' in flat
        assert 'b' in flat
        # next loop
        assert 'start3' in flat
        # body checkpoint
        body_state = await graph.store.get(config.get("session_id"), config.get("ns") + ":loop:1:body:2")
        assert "a" in body_state.pending_node
        assert "b" in body_state.pending_node
        assert body_state.pending_buffer[0].sender == "c"

        logger.info("=============== Invoke 4 (Resume loop iteration 3 condition to end ===============")
        execution_trace.clear()
        result = await graph.run(config)
        assert "__interrupt__" not in result

        flat = [n for trace in execution_trace for n in trace['active_nodes']]
        assert 'end3' in flat
        assert 'end1' in flat
        assert 'end' in flat

        await graph.store.delete(config.get("session_id"), config.get("ns"))
        assert await graph.store.get(config.get("session_id"), config.get("ns") + ":loop:1:body:2") is None
        assert await graph.store.get(config.get("session_id"), config.get("ns") + ":loop:1:body:1") is None
        assert await graph.store.get(config.get("session_id"), config.get("ns") + ":loop:1") is None
        assert await graph.store.get(config.get("session_id"), config.get("ns")) is None
