"""Characterization tests for the SQLite memory store.

The store is the boundary between memory construction and retrieval; every
snapshot in outputs/snapshots/ has this shape, so the schema and the edge
queries are pinned here before any module reshuffling.
"""

from pathlib import Path

import pytest

from video_memory.memory.store import SQLiteMemoryStore
from video_memory.schemas import Entity, MemoryNode, NodeEntityEdge, NodeFrameEdge


@pytest.fixture()
def store(tmp_path: Path):
    store = SQLiteMemoryStore(tmp_path / "nested" / "memory.sqlite")
    yield store
    store.close()


def _node(node_id: str, node_type: str = "detail", time_ids: list[int] | None = None) -> MemoryNode:
    return MemoryNode(node_id, node_type, f"text of {node_id}", time_ids if time_ids is not None else [1])


def test_creates_the_four_tables_and_its_parent_directory(store, tmp_path: Path) -> None:
    assert (tmp_path / "nested" / "memory.sqlite").exists()
    tables = {
        row[0]
        for row in store.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert tables == {"memory_nodes", "entities", "node_frame_edges", "node_entity_edges"}


def test_nodes_round_trip_with_time_ids_as_json(store) -> None:
    store.add_nodes([_node("b", time_ids=[3, 1]), _node("a", "summary", [2])])

    assert [node.node_id for node in store.list_nodes()] == ["a", "b"]  # ordered by node_id
    assert store.get_node("b").time_ids == [3, 1]
    assert store.get_node("a").node_type == "summary"
    assert store.get_node("missing") is None


def test_add_node_replaces_an_existing_id(store) -> None:
    store.add_node(_node("a", "detail", [1]))
    store.add_node(MemoryNode("a", "summary", "rewritten", [9]))

    nodes = store.list_nodes()
    assert len(nodes) == 1
    assert (nodes[0].node_type, nodes[0].description_text, nodes[0].time_ids) == ("summary", "rewritten", [9])


def test_get_nodes_skips_unknown_ids_and_follows_the_requested_order(store) -> None:
    store.add_nodes([_node("a"), _node("b")])
    assert [node.node_id for node in store.get_nodes(["b", "missing", "a"])] == ["b", "a"]


def test_add_entity_deduplicates_on_canonical_name(store) -> None:
    first = store.add_entity(Entity("ent_1", "cnn", "ORG", ["CNN"]))
    second = store.add_entity(Entity("ent_2", "cnn", "ORG", ["cnn.com"]))

    assert second.entity_id == first.entity_id == "ent_1"
    assert second.aliases == ["CNN"]  # the stored row wins
    assert len(store.list_entities()) == 1


def test_entities_are_listed_by_canonical_name(store) -> None:
    store.add_entity(Entity("e2", "putin", "PERSON", []))
    store.add_entity(Entity("e1", "cnn", "ORG", []))
    assert [entity.canonical_name for entity in store.list_entities()] == ["cnn", "putin"]
    assert store.get_entity_by_name("cnn").entity_id == "e1"
    assert store.get_entity_by_name("absent") is None


def test_frame_edges_are_scoped_by_node_id(store) -> None:
    store.add_node_frame_edges(
        [NodeFrameEdge("a", "f1"), NodeFrameEdge("a", "f2"), NodeFrameEdge("b", "f3")]
    )

    assert len(store.node_frame_edges()) == 3
    assert {edge.frame_key for edge in store.node_frame_edges({"a"})} == {"f1", "f2"}
    assert store.node_frame_edges({"missing"}) == []


def test_duplicate_edges_collapse_on_the_composite_primary_key(store) -> None:
    store.add_node_frame_edges([NodeFrameEdge("a", "f1", 1.0)])
    store.add_node_frame_edges([NodeFrameEdge("a", "f1", 0.5)])

    edges = store.node_frame_edges({"a"})
    assert len(edges) == 1
    assert edges[0].confidence == 0.5


def test_entity_edges_are_scoped_by_node_id(store) -> None:
    store.add_node_entity_edges([NodeEntityEdge("a", "e1"), NodeEntityEdge("b", "e1")])
    assert {edge.node_id for edge in store.node_entity_edges({"a"})} == {"a"}
    assert len(store.node_entity_edges()) == 2


def test_frame_keys_for_nodes_is_sorted_and_deduplicated(store) -> None:
    store.add_node_frame_edges(
        [NodeFrameEdge("a", "f2"), NodeFrameEdge("a", "f1"), NodeFrameEdge("b", "f1")]
    )

    assert store.frame_keys_for_nodes(["a", "b"]) == ["f1", "f2"]
    assert store.frame_keys_for_nodes([]) == []


def test_clear_empties_every_table(store) -> None:
    store.add_nodes([_node("a")])
    store.add_entity(Entity("e1", "cnn"))
    store.add_node_frame_edges([NodeFrameEdge("a", "f1")])
    store.add_node_entity_edges([NodeEntityEdge("a", "e1")])

    store.clear()

    assert store.list_nodes() == []
    assert store.list_entities() == []
    assert store.node_frame_edges() == []
    assert store.node_entity_edges() == []
