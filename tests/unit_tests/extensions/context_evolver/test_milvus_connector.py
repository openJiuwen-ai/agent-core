# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for MilvusConnector – End-to-end CRUD + search.

Runs against an in-memory mock Collection that is injected into
MilvusConnector._collection so all connector code paths execute without
a live Milvus server.

Run with:
    uv run python tests/unit_tests/extensions/context_evolver/test_milvus_connector.py
"""

import math
import os
import re
import socket
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_agent_core_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))
if _agent_core_root not in sys.path:
    sys.path.append(_agent_core_root)

from openjiuwen.core.common.logging import context_engine_logger as logger  # noqa: E402
from openjiuwen.extensions.context_evolver.core.db_connector import MilvusConnector  # noqa: E402

NAMESPACE = "test_milvus_ns"


# ---------------------------------------------------------------------------
# Synthetic test data
# ---------------------------------------------------------------------------

def _make_reme_data() -> Dict[str, Any]:
    """Three nodes with 4-dimensional unit-vector embeddings.

    cosine(node_01_query, node_01) == 1.0 → top search hit is always node_01.
    """
    return {
        "reme_demo_user_node_01": {
            "id": "reme_demo_user_node_01",
            "content": "When asked how to debug Python code or find bugs",
            "embedding": [1.0, 0.0, 0.0, 0.0],
            "metadata": {"type": "reme", "label": "debug_python"},
        },
        "reme_demo_user_node_02": {
            "id": "reme_demo_user_node_02",
            "content": "When asked about data structures and algorithms",
            "embedding": [0.0, 1.0, 0.0, 0.0],
            "metadata": {"type": "reme", "label": "dsa"},
        },
        "reme_demo_user_node_03": {
            "id": "reme_demo_user_node_03",
            "content": "When asked about system design patterns",
            "embedding": [0.0, 0.0, 1.0, 0.0],
            "metadata": {"type": "reme", "label": "system_design"},
        },
    }


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def reme_data() -> Dict[str, Any]:
    """Return synthetic REME sample data (module-scoped, created once)."""
    return _make_reme_data()


# ---------------------------------------------------------------------------
# In-memory Milvus mock
# ---------------------------------------------------------------------------

def _parse_milvus_expr(expr: str, row: Dict[str, Any]) -> bool:
    """Evaluate a simple Milvus filter expression against a single row."""
    expr = expr.strip()
    if not expr:
        return True

    m = re.match(r'^(\w+)\s+in\s+\[([^\]]*)\]$', expr)
    if m:
        field = m.group(1)
        vals = re.findall(r'"([^"]*)"', m.group(2))
        return row.get(field, "") in vals

    m = re.match(r'^(\w+)\s*==\s*"([^"]*)"$', expr)
    if m:
        field, val = m.group(1), m.group(2)
        return row.get(field, "") == val

    m = re.match(r'^(\w+)\s*!=\s*"([^"]*)"$', expr)
    if m:
        field, val = m.group(1), m.group(2)
        return row.get(field, "") != val

    return True


class _MockEntity:
    def __init__(self, data: Dict[str, Any]) -> None:
        self._data = data

    def get(self, key: str, default: Any = "") -> Any:
        return self._data.get(key, default)


class _MockHit:
    def __init__(self, entity_data: Dict[str, Any], distance: float) -> None:
        self.entity = _MockEntity(entity_data)
        self.distance = distance


@dataclass
class _MockSearchOptions:
    expr: Optional[str] = None
    output_fields: Optional[List[str]] = None


class _InMemoryMilvusCollection:
    """In-memory row store that mimics the pymilvus Collection API."""

    def __init__(self) -> None:
        self._rows: Dict[str, Dict[str, Any]] = {}
        self._has_index: bool = True

    def insert(self, rows: List[Dict[str, Any]]) -> None:
        for row in rows:
            self._rows[row["id"]] = dict(row)

    def delete(self, expr: str) -> None:
        to_delete = [
            id_ for id_, row in list(self._rows.items())
            if _parse_milvus_expr(expr, row)
        ]
        for id_ in to_delete:
            del self._rows[id_]

    def flush(self) -> None:
        pass

    def query(
        self,
        expr: str,
        output_fields: Optional[List[str]] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        matched = [
            row for row in self._rows.values()
            if _parse_milvus_expr(expr, row)
        ]
        if limit is not None:
            matched = matched[:limit]
        if output_fields is None:
            return matched
        return [{f: row.get(f) for f in output_fields} for row in matched]

    @staticmethod
    def _cosine(a: List[float], b: List[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(x * x for x in b))
        return dot / (na * nb) if na and nb else 0.0

    def search(
        self,
        data: List[List[float]],
        anns_field: str,
        param: Dict[str, Any],
        limit: int,
        **kwargs: Any,
    ) -> List[List[_MockHit]]:
        opts = _MockSearchOptions(
            expr=kwargs.get("expr"),
            output_fields=kwargs.get("output_fields"),
        )
        query_vec = data[0]
        candidates = [
            row for row in self._rows.values()
            if row.get(anns_field) and (not opts.expr or _parse_milvus_expr(opts.expr, row))
        ]
        scored = sorted(
            candidates,
            key=lambda r: self._cosine(query_vec, r[anns_field]),
            reverse=True,
        )[:limit]
        out_fields = opts.output_fields or []
        hits = [
            _MockHit(
                entity_data={f: row.get(f) for f in out_fields},
                distance=self._cosine(query_vec, row[anns_field]),
            )
            for row in scored
        ]
        return [hits]

    def has_index(self) -> bool:
        return self._has_index

    def create_index(self, field_name: str, index_params: Dict[str, Any]) -> None:
        self._has_index = True

    def drop_index(self) -> None:
        self._has_index = False

    def load(self) -> None:
        pass

    def release(self) -> None:
        pass

    @property
    def num_entities(self) -> int:
        return len(self._rows)


def _make_mock_connector(collection: _InMemoryMilvusCollection, dim: int):
    """Return a MilvusConnector wired to *collection* (no live Milvus needed)."""
    conn = MilvusConnector.__new__(MilvusConnector)
    conn.host = "mock"
    conn.port = 19530
    conn.collection_name = "vector_nodes_mock"
    conn.dim = dim
    conn.alias = "mock_alias"
    conn.metric_type = "COSINE"
    conn.set_collection(collection)
    return conn


# ---------------------------------------------------------------------------
# End-to-end CRUD + search tests
# ---------------------------------------------------------------------------

def test_end_to_end(reme_data: Dict[str, Any]) -> None:
    """Full CRUD + vector search cycle using the in-memory mock."""
    data = reme_data
    first_id = next(iter(data))
    dim = len(data[first_id]["embedding"])

    coll = _InMemoryMilvusCollection()
    conn = _make_mock_connector(coll, dim)

    # 1 – save
    conn.save_to_db(NAMESPACE, data)
    logger.info("save_to_db: %d nodes saved", len(data))

    # 2 – exists
    assert conn.exists(NAMESPACE), "exists() must return True after save"
    logger.info("exists: OK")

    # 3 – count (namespace-scoped)
    n = conn.count(NAMESPACE)
    assert n == len(data), f"count mismatch: expected {len(data)}, got {n}"
    logger.info("count(namespace): %d OK", n)

    # 3b – count (global)
    total = conn.count()
    assert total == len(data), f"global count mismatch: expected {len(data)}, got {total}"
    logger.info("count(global): %d OK", total)

    # 4 – load + round-trip
    loaded = conn.load_from_db(NAMESPACE)
    assert len(loaded) == len(data), f"load mismatch: expected {len(data)}, got {len(loaded)}"
    for node_id, node_data in data.items():
        assert node_id in loaded, f"node '{node_id}' missing after load"
        rt = loaded[node_id]
        assert rt["content"] == node_data["content"], f"content mismatch for '{node_id}'"
        orig_emb = node_data.get("embedding") or []
        rt_emb = rt.get("embedding") or []
        assert len(rt_emb) == len(orig_emb), f"embedding length mismatch for '{node_id}'"
        if orig_emb:
            assert abs(rt_emb[0] - orig_emb[0]) < 1e-9, f"embedding value mismatch for '{node_id}'"
        assert rt.get("metadata") == node_data.get("metadata", {}), f"metadata mismatch for '{node_id}'"
    logger.info("load_from_db + round-trip: %d nodes OK", len(data))

    # 5 – list_namespaces
    ns_list = conn.list_namespaces()
    assert NAMESPACE in ns_list, f"'{NAMESPACE}' not in list_namespaces()"
    logger.info("list_namespaces: %s OK", ns_list)

    # 6 – vector search (cosine) – top hit must be the query node itself
    query_emb = data[first_id]["embedding"]
    results = conn.search(NAMESPACE, query_emb, top_k=3, metric="cosine")
    assert len(results) > 0, "search must return at least one hit"
    assert "score" in results[0], "search result must contain 'score'"
    top_id = results[0]["id"]
    assert top_id == first_id, (
        f"top search hit '{top_id}' expected to be '{first_id}' "
        f"(cosine of identical vector should be ~1.0)"
    )
    assert abs(results[0]["score"] - 1.0) < 1e-4, (
        f"self-similarity score should be ~1.0, got {results[0]['score']}"
    )
    logger.info(
        "search (cosine): top-%d hits, top_id='%s', score=%.6f OK",
        len(results), top_id, results[0]["score"],
    )

    # 7 – upsert (idempotent save)
    modified = dict(data)
    updated_content = "UPDATED: " + data[first_id]["content"]
    modified[first_id] = {**data[first_id], "content": updated_content}
    conn.save_to_db(NAMESPACE, modified)
    after_upsert = conn.load_from_db(NAMESPACE)
    assert after_upsert[first_id]["content"] == updated_content, "upsert did not update content"
    assert len(after_upsert) == len(data), "upsert should not create duplicates"
    logger.info("upsert (idempotent): %d nodes, content updated OK", len(after_upsert))

    # 8 – delete_nodes (remove first node only)
    conn.delete_nodes(NAMESPACE, [first_id])
    after_partial = conn.load_from_db(NAMESPACE)
    assert first_id not in after_partial, f"delete_nodes: '{first_id}' should be gone"
    assert len(after_partial) == len(data) - 1, (
        f"delete_nodes count mismatch: expected {len(data) - 1}, got {len(after_partial)}"
    )
    logger.info("delete_nodes (1 node): %d remaining OK", len(after_partial))

    # 9 – delete entire namespace
    deleted = conn.delete(NAMESPACE)
    assert deleted, "delete() must return True when nodes existed"
    assert not conn.exists(NAMESPACE), "exists() must return False after delete"
    logger.info("delete + exists: OK")

    # 10 – double-delete (should return False, not raise)
    deleted_again = conn.delete(NAMESPACE)
    assert not deleted_again, "second delete() should return False"
    logger.info("double-delete safety: OK")

    # 11 – flush and close are no-ops in the mock but must not raise
    conn.flush()
    conn.close()
    logger.info("flush + close: OK")


def test_save_skips_nodes_without_embeddings() -> None:
    """Nodes without embeddings are silently skipped; embeddable nodes are saved."""
    dim = 4
    data = {
        "node_with_emb": {
            "id": "node_with_emb",
            "content": "has an embedding",
            "embedding": [0.1, 0.2, 0.3, 0.4],
            "metadata": {},
        },
        "node_no_emb": {
            "id": "node_no_emb",
            "content": "no embedding",
            "embedding": None,
            "metadata": {},
        },
    }
    coll = _InMemoryMilvusCollection()
    conn = _make_mock_connector(coll, dim)
    conn.save_to_db("ns_skip", data)

    loaded = conn.load_from_db("ns_skip")
    assert "node_with_emb" in loaded, "embeddable node should be saved"
    assert "node_no_emb" not in loaded, "node without embedding should be skipped"
    logger.info("save skips nodes without embeddings: OK")


def test_load_empty_namespace_returns_empty_dict() -> None:
    """load_from_db on a non-existent namespace returns an empty dict."""
    coll = _InMemoryMilvusCollection()
    conn = _make_mock_connector(coll, 4)
    result = conn.load_from_db("non_existent_ns")
    assert result == {}, f"expected empty dict, got {result!r}"
    logger.info("load_from_db empty namespace: OK")


def test_search_returns_empty_for_unknown_namespace() -> None:
    """search() against an empty namespace returns an empty list."""
    coll = _InMemoryMilvusCollection()
    conn = _make_mock_connector(coll, 4)
    results = conn.search("ghost_ns", [0.1, 0.2, 0.3, 0.4], top_k=5)
    assert results == [], f"expected [], got {results!r}"
    logger.info("search empty namespace: OK")


def test_delete_nodes_empty_list_is_noop() -> None:
    """delete_nodes with an empty list returns True without touching the store."""
    coll = _InMemoryMilvusCollection()
    coll.insert([{"id": "keep_me", "namespace": "ns", "content": "x",
                  "embedding": [1.0, 0.0], "metadata": {}}])
    conn = _make_mock_connector(coll, 2)
    result = conn.delete_nodes("ns", [])
    assert result is True
    assert conn.count("ns") == 1, "no nodes should have been deleted"
    logger.info("delete_nodes empty list no-op: OK")


def test_create_index_drops_and_recreates() -> None:
    """create_index() drops any existing index and builds the requested one."""
    coll = _InMemoryMilvusCollection()
    conn = _make_mock_connector(coll, 4)

    conn.create_index(index_type="HNSW", metric_type="COSINE", m=8, ef_construction=32)
    assert coll.has_index(), "create_index must leave the collection with an index"
    logger.info("create_index HNSW: OK")

    conn.create_index(index_type="IVF_FLAT", metric_type="L2", nlist=64)
    assert coll.has_index(), "create_index IVF_FLAT must leave an index"
    logger.info("create_index IVF_FLAT: OK")


# ---------------------------------------------------------------------------
# Live Milvus integration test (requires Milvus running at localhost:19530)
# ---------------------------------------------------------------------------

LIVE_HOST = "localhost"
LIVE_PORT = 19530
LIVE_NAMESPACE = "live_test_ns"
LIVE_COLLECTION = "vector_nodes_live_test"


def _milvus_is_reachable(host: str = LIVE_HOST, port: int = LIVE_PORT) -> bool:
    """Return True if Milvus gRPC port is open."""
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


@pytest.mark.skipif(
    not _milvus_is_reachable(),
    reason=f"Milvus not reachable at {LIVE_HOST}:{LIVE_PORT} – skipping live test",
)
def test_live_insert_and_retrieve() -> None:
    """Insert a real entry into Milvus at localhost:19530 and verify round-trip.

    Requires:
        docker compose up -d   (Milvus standalone)

    Cleans up the test namespace and collection on completion.
    """
    logger.info("=== Live Milvus test: insert + retrieve at %s:%s ===", LIVE_HOST, LIVE_PORT)

    node_id = "live_node_01"
    data = {
        node_id: {
            "id": node_id,
            "content": "Live test: when asked how to optimise SQL queries",
            "embedding": [1.0, 0.0, 0.0, 0.0],
            "metadata": {"type": "live", "label": "sql_optimisation"},
        }
    }

    conn = MilvusConnector(
        host=LIVE_HOST,
        port=LIVE_PORT,
        collection_name=LIVE_COLLECTION,
        dim=4,
        alias="live_test_alias",
        metric_type="COSINE",
    )

    try:
        # 1 – insert
        conn.save_to_db(LIVE_NAMESPACE, data)
        logger.info("save_to_db: node '%s' inserted OK", node_id)

        # 2 – exists
        assert conn.exists(LIVE_NAMESPACE), "exists() must return True after insert"
        logger.info("exists: OK")

        # 3 – count
        n = conn.count(LIVE_NAMESPACE)
        assert n == 1, f"count mismatch: expected 1, got {n}"
        logger.info("count: %d OK", n)

        # 4 – load + round-trip
        loaded = conn.load_from_db(LIVE_NAMESPACE)
        assert node_id in loaded, f"node '{node_id}' missing after load"
        rt = loaded[node_id]
        assert rt["content"] == data[node_id]["content"], "content round-trip mismatch"
        assert rt["metadata"] == data[node_id]["metadata"], "metadata round-trip mismatch"
        logger.info("load_from_db round-trip: OK")

        # 5 – vector search
        results = conn.search(LIVE_NAMESPACE, data[node_id]["embedding"], top_k=1)
        assert results, "search returned no hits"
        assert results[0]["id"] == node_id, f"top hit '{results[0]['id']}' != '{node_id}'"
        assert abs(results[0]["score"] - 1.0) < 1e-4, (
            f"self-similarity score should be ~1.0, got {results[0]['score']}"
        )
        logger.info("search: top hit '%s', score=%.6f OK", results[0]["id"], results[0]["score"])

    finally:
        # clean up – remove the test namespace and close connection
        conn.delete(LIVE_NAMESPACE)
        conn.close()
        logger.info("Live test cleanup: namespace '%s' deleted, connection closed", LIVE_NAMESPACE)

    logger.info("=== Live Milvus test: PASSED ===")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
