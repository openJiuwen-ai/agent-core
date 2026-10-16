# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Reentrancy tests for ``PregelGraph.compile``.

``compile`` must be a pure function of (graph structure, session): calling it
repeatedly must not leak execution state from one call into the next.
Historically the engine cached ``self.pregel`` and reused the *same* ``Vertex``
objects for every compile, so per-run state (``_call_count``, the
``_stream_done`` future, the bound ``NodeSession``) carried over between runs.
That is precisely why upper layers had to rebuild the whole graph for every
request — "reset between runs is only partial".

These tests pin the contract that upper-layer object pools depend on:
a built graph is reusable, and ``compile`` only *derives* per-run state.
"""
# Import order matters: openjiuwen.core.graph.graph sits in a circular import
# chain (graph -> vertex -> workflow.components.base -> workflow), so the
# workflow package must be imported first.
from openjiuwen.core.workflow import Workflow  # noqa: F401
from openjiuwen.core.graph.graph import PregelGraph
from openjiuwen.core.session.internal.workflow import WorkflowSession

from tests.unit_tests.core.workflow.mock_nodes import MockStartNode, MockEndNode, Node1


def _build_flow():
    flow = Workflow()
    flow.set_start_comp("s", MockStartNode("s"))
    flow.add_workflow_comp("a", Node1("a"))
    flow.set_end_comp("e", MockEndNode("e"))
    flow.add_connection("s", "a")
    flow.add_connection("a", "e")
    return flow


def _session(flow, session_id: str) -> WorkflowSession:
    return WorkflowSession(workflow_id=flow.card.id, parent=None, session_id=session_id)


def _vertices_of(compiled):
    """Map node id -> Vertex for a compiled Pregel (PregelNode wraps the Vertex)."""
    return {name: node.func for name, node in compiled._pregel.nodes.items()}


def test_compile_returns_a_fresh_pregel_per_call():
    """Second compile must not hand back the cached Pregel of the first."""
    flow = _build_flow()
    graph = flow._internal._graph

    compiled1 = graph.compile(_session(flow, "run-1"))
    compiled2 = graph.compile(_session(flow, "run-2"))

    assert compiled1 is not compiled2
    assert compiled1._pregel is not compiled2._pregel


def test_compile_does_not_mutate_build_time_vertices():
    """Build-time vertices are the reusable skeleton and must stay untouched.

    Before the fix ``compile`` called ``node.init(session)`` on ``self.nodes``,
    binding run-scoped state onto objects shared by every execution.
    """
    flow = _build_flow()
    graph = flow._internal._graph

    graph.compile(_session(flow, "run-1"))

    for node_id, vertex in graph.nodes.items():
        assert vertex._session is None, (
            f"build-time vertex {node_id} was initialised with a run-scoped session"
        )
        assert vertex._call_count == 0
        assert not vertex._stream_done.done()


def test_compile_binds_fresh_vertices_to_the_given_session():
    flow = _build_flow()
    graph = flow._internal._graph

    compiled1 = graph.compile(_session(flow, "run-1"))
    compiled2 = graph.compile(_session(flow, "run-2"))

    vertices1 = _vertices_of(compiled1)
    vertices2 = _vertices_of(compiled2)

    assert set(graph.nodes).issubset(set(vertices1))
    for node_id in graph.nodes:
        v1, v2 = vertices1[node_id], vertices2[node_id]
        # Distinct Vertex shells per compile ...
        assert v1 is not v2
        assert v1 is not graph.nodes[node_id]
        # ... each wrapping the same shared executable (structure is reused) ...
        assert v1._executable is v2._executable is graph.nodes[node_id]._executable
        # ... and each bound to its own run-scoped session.
        assert v1._session is not v2._session
        assert v1._session.workflow_id() == flow.card.id


def test_repeated_compile_does_not_accumulate_run_state():
    """Run counters/futures must start clean on every compile."""
    flow = _build_flow()
    graph = flow._internal._graph

    for i in range(3):
        compiled = graph.compile(_session(flow, f"run-{i}"))
        vertices = _vertices_of(compiled)
        for node_id in graph.nodes:
            vertex = vertices[node_id]
            assert vertex._call_count == 0, f"{node_id} carried a call count into a new run"
            assert vertex._stream_call_count == 0
            assert not vertex._stream_done.done(), f"{node_id} reused a resolved stream future"


def test_graph_structure_is_not_rebuilt_by_compile():
    """compile derives per-run state only; the graph skeleton is untouched."""
    flow = _build_flow()
    graph = flow._internal._graph
    before = dict(graph.nodes)
    edges_before = list(graph.edges)

    graph.compile(_session(flow, "run-1"))

    assert dict(graph.nodes) == before
    assert list(graph.edges) == edges_before
