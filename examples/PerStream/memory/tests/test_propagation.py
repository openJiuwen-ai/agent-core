from video_memory.retrieval.propagation import GraphPropagator
from video_memory.schemas import Entity, NodeEntityEdge, RankedNode


def test_graph_propagation_bridges_nodes() -> None:
    propagator = GraphPropagator(
        max_hops=2,
        entity_score_threshold=0.5,
        propagation_score_threshold=0.1,
        decay=0.8,
    )
    result = propagator.propagate(
        ranked_nodes=[RankedNode("n1", 1.0)],
        allowed_node_ids={"n1", "n2"},
        node_entity_edges=[NodeEntityEdge("n1", "e1"), NodeEntityEdge("n2", "e1")],
        entities=[Entity("e1", "apple")],
        query_entities=["apple"],
    )
    assert result.node_scores["n2"] == 0.8
    assert len(result.steps) == 1

