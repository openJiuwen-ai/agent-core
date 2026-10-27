# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Structure-parse caching tests.

``PregelGraph._parse_structure`` and ``ActorManager``'s stream-structure
parsing are pure functions of build-time data (edges/waits/branches/
branch_targets, spec.edges/comp_configs/stream_source_groups). Their results
are cached and replayed across compilations / per-execution constructions.

These tests pin the caching contract:
1. a cache hit must replay the exact structure parsed on the first run;
2. any structural mutation must invalidate the cache before the next parse;
3. per-execution state (Vertex shells, StreamActors, producer bookkeeping)
   must never be shared through the cached structure.
"""

# Import order matters: openjiuwen.core.graph.graph sits in a circular import
# chain (graph -> vertex -> workflow.components.base -> workflow), so the
# workflow package must be imported first.
from unittest.mock import MagicMock

import pytest

import openjiuwen.core.workflow  # noqa: F401
from openjiuwen.core.graph.stream_actor.manager import ActorManager
from openjiuwen.core.session.internal.workflow import WorkflowSession
from openjiuwen.core.workflow.components.base import ComponentAbility
from openjiuwen.core.workflow.workflow_config import NodeSpec, WorkflowSpec

from tests.unit_tests.core.workflow.mock_nodes import MockStartNode, MockEndNode, Node1


def _build_workflow():
    from openjiuwen.core.workflow import Workflow

    flow = Workflow()
    flow.set_start_comp("s", MockStartNode("s"))
    flow.add_workflow_comp("a", Node1("a"))
    flow.set_end_comp("e", MockEndNode("e"))
    flow.add_connection("s", "a")
    flow.add_connection("a", "e")
    return flow


def _session(flow, session_id: str) -> WorkflowSession:
    return WorkflowSession(workflow_id=flow.card.id, parent=None, session_id=session_id)


# ============================================================================
# PregelGraph._parse_structure caching
# ============================================================================


def test_structure_cache_is_reused_across_compiles():
    flow = _build_workflow()
    graph = flow._internal._graph

    graph.compile(_session(flow, "run-1"))
    cache_after_first = graph._structure_cache
    assert cache_after_first is not None

    graph.compile(_session(flow, "run-2"))

    assert graph._structure_cache is cache_after_first


def test_replayed_structure_builds_an_equivalent_pregel():
    """A cache hit must produce the same node/channel/route layout as a fresh parse."""
    flow = _build_workflow()
    graph = flow._internal._graph

    compiled1 = graph.compile(_session(flow, "run-1"))
    compiled2 = graph.compile(_session(flow, "run-2"))

    assert set(compiled1._pregel.nodes) == set(compiled2._pregel.nodes)
    assert len(compiled1._pregel.channels) == len(compiled2._pregel.channels)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda graph: graph.add_edge("s", "a"),
        lambda graph: graph.add_conditional_edges("a", lambda: "e"),
        lambda graph: graph.add_node("extra", Node1("extra"), wait_for_all=True),
        lambda graph: graph.register_branch_targets("a", {"s", "e"}),
    ],
    ids=["add_edge", "add_conditional_edges", "add_node_wait_for_all", "register_branch_targets"],
)
def test_structural_mutations_invalidate_the_cache(mutate):
    flow = _build_workflow()
    graph = flow._internal._graph
    graph.compile(_session(flow, "run-1"))
    assert graph._structure_cache is not None

    mutate(graph)

    assert graph._structure_cache is None
    graph.compile(_session(flow, "run-2"))
    assert graph._structure_cache is not None


def test_add_node_without_wait_for_all_keeps_the_cache():
    """Plain add_node touches neither edges/waits/branches — the cache stays valid."""
    flow = _build_workflow()
    graph = flow._internal._graph
    graph.compile(_session(flow, "run-1"))
    cache_before = graph._structure_cache

    graph.add_node("extra", Node1("extra"))

    assert graph._structure_cache is cache_before


def test_mutated_graph_recompiles_the_new_structure():
    """Invalidation must not just drop the cache — the replay must reflect the mutation."""
    flow = _build_workflow()
    graph = flow._internal._graph
    compiled1 = graph.compile(_session(flow, "run-1"))
    routers_before = len(compiled1._pregel.nodes["a"].routers)

    graph.add_edge("a", "e")
    compiled2 = graph.compile(_session(flow, "run-2"))

    assert len(compiled2._pregel.nodes["a"].routers) == routers_before + 1


# ============================================================================
# ActorManager stream-structure caching
# ============================================================================


def _stream_spec() -> WorkflowSpec:
    return WorkflowSpec(
        stream_edges={"p1": ["c1"]},
        comp_configs={
            "p1": NodeSpec(abilities=[ComponentAbility.STREAM]),
            "c1": NodeSpec(abilities=[ComponentAbility.COLLECT, ComponentAbility.TRANSFORM]),
        },
    )


def _actor_manager(spec: WorkflowSpec) -> ActorManager:
    session = MagicMock()
    session.config().get_env.return_value = 1
    return ActorManager(spec, MagicMock(), sub_graph=False, session=session)


def test_stream_structure_is_cached_on_the_spec():
    spec = _stream_spec()
    assert spec._stream_structure is None

    _actor_manager(spec)

    assert spec._stream_structure is not None


def test_second_actor_manager_reuses_the_parsed_structure():
    spec = _stream_spec()
    manager1 = _actor_manager(spec)
    structure = spec._stream_structure

    manager2 = _actor_manager(spec)

    assert spec._stream_structure is structure
    assert manager1._consumer_dict is manager2._consumer_dict
    assert manager1._producer_abilities is manager2._producer_abilities
    assert manager1._stream_source_groups is manager2._stream_source_groups


def test_stream_actors_and_producer_state_stay_per_execution():
    """The cached structure is shared, but execution state must never be."""
    spec = _stream_spec()
    manager1 = _actor_manager(spec)
    manager2 = _actor_manager(spec)

    assert manager1._streams is not manager2._streams
    assert manager1._streams["c1"] is not manager2._streams["c1"]
    assert manager1._active_producer_ids is not manager2._active_producer_ids

    manager1.active_produce_ability("p1", ComponentAbility.STREAM)
    assert "p1" not in manager2._active_producer_ids


def test_parsed_stream_structure_content():
    spec = _stream_spec()
    _actor_manager(spec)
    structure = spec._stream_structure

    assert structure.consumer_dict == {"c1": ["p1"]}
    assert structure.producer_abilities == {"p1": {ComponentAbility.STREAM}}
    assert structure.consumer_abilities == {"c1": [ComponentAbility.COLLECT, ComponentAbility.TRANSFORM]}
    # No spec-provided groups: fallback is one group per sorted source key.
    assert structure.stream_source_groups == {"c1": [{"p1-stream"}]}


def test_spec_provided_source_groups_win_over_fallback():
    spec = _stream_spec()
    spec.stream_source_groups["c1"] = [["p1-stream"]]
    _actor_manager(spec)

    assert spec._stream_structure.stream_source_groups == {"c1": [{"p1-stream"}]}
