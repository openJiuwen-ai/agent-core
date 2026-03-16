# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
Example script demonstrating MilvusGraphStore usage.

Covers: connection, add entities/relations/episodes, hybrid search, BFS graph expansion
search for both entities and relations, query by id/expr, refresh, delete, and close.
Uses a large scenario with relevant (ML/Search) and irrelevant (HR, Finance, Sales, etc.)
subgraphs to test that BFS expands only within the relevant neighborhood. Requires a running Milvus
instance and an embedding service for full demo (see "Prerequisites" below).

Prerequisites:
  - Milvus: set MILVUS_URI (default http://localhost:19530) and optionally MILVUS_DB_NAME.
  - Embedding (for add + search): set EMBEDDING_MODEL, EMBEDDING_API_BASE, EMBEDDING_API_KEY
    (e.g. in .env or environment). Same variable names as examples/retrieval.
"""

import asyncio
import os
from typing import Optional

from configs import EMBEDDING_API_KEY, EMBEDDING_BASE_URL, EMBEDDING_MODEL, MILVUS_DB_NAME, MILVUS_URI
from graph_scenario_data import build_large_scenario_with_irrelevant_data
from utils.output import write_output

from openjiuwen.core.foundation.store.base_reranker import Reranker
from openjiuwen.core.foundation.store.graph import GraphStoreFactory
from openjiuwen.core.foundation.store.graph.config import GraphConfig, GraphStoreIndexConfig
from openjiuwen.core.foundation.store.graph.constants import (
    ENTITY_COLLECTION,
    EPISODE_COLLECTION,
    RELATION_COLLECTION,
)
from openjiuwen.core.foundation.store.vector_fields.milvus_fields import MilvusAUTO
from openjiuwen.core.retrieval import OpenAIEmbedding
from openjiuwen.core.retrieval.common.config import RerankerConfig
from openjiuwen.core.retrieval.common.result_ranking import WeightedRankConfig
from openjiuwen.extensions.vendor_specific.aliyun_reranker import AliyunReranker

EMBED_DIM = 256
RERANKER: Optional[Reranker] = AliyunReranker(
    config=RerankerConfig(
        api_key=os.getenv("DASHSCOPE_API_KEY"),
        api_base="https://dashscope.aliyuncs.com/api/v1/services/",
        model="qwen3-rerank",
    )
)


def _log_score_comparison(with_rerank, without_rerank, label="item"):
    """Log score comparison: per-position and summary."""
    write_output("  [Score comparison]")
    for i in range(min(len(with_rerank), len(without_rerank))):
        content_w = with_rerank[i].get("content", with_rerank[i].get("uuid", ""))[:20]
        content_n = without_rerank[i].get("content", without_rerank[i].get("uuid", ""))[:20]
        score_w = with_rerank[i].get("distance", 0)
        score_n = without_rerank[i].get("distance", 0)
        diff = score_w - score_n if (score_w or score_n) else None
        same = " (same)" if content_w == content_n else ""
        if diff is not None:
            write_output(
                "    Rank %d: with_reranker=%.4f  without=%.4f  diff=%+.4f%s", i + 1, score_w, score_n, diff, same
            )
        else:
            write_output("    Rank %d: with_reranker=%.4f  without=%.4f%s", i + 1, score_w, score_n, same)
    if with_rerank:
        write_output(
            "    With reranker score range: [%.4f, %.4f]",
            min(d.get("distance", 0) for d in with_rerank),
            max(d.get("distance", 0) for d in with_rerank),
        )
    if without_rerank:
        write_output(
            "    Without reranker score range: [%.4f, %.4f]",
            min(d.get("distance", 0) for d in without_rerank),
            max(d.get("distance", 0) for d in without_rerank),
        )


def _build_graph_config(embed_dim: int = EMBED_DIM) -> GraphConfig:
    """Build GraphConfig for Milvus (optional embedding_model is set in main)."""
    db_embed_config = GraphStoreIndexConfig(
        index_type=MilvusAUTO(),
        distance_metric="cosine",
    )
    return GraphConfig(
        uri=MILVUS_URI,
        name=MILVUS_DB_NAME,
        timeout=30.0,
        embed_dim=embed_dim,
        db_embed_config=db_embed_config,
    )


async def main():
    """Run full MilvusGraphStore demo: connect, add, search, query, delete, close."""

    write_output("=" * 60)
    write_output("MilvusGraphStore demo")
    write_output("=" * 60)
    write_output("MILVUS_URI: %s", MILVUS_URI)
    write_output("MILVUS_DB_NAME: %s", MILVUS_DB_NAME)
    write_output("")

    # Resolve embedding: required for add_entity/add_relation/add_episode and search
    embedder = None
    try:
        from openjiuwen.core.retrieval.common.config import EmbeddingConfig

        base_url = EMBEDDING_BASE_URL
        model = EMBEDDING_MODEL
        api_key = EMBEDDING_API_KEY
        if base_url and model:
            cfg = EmbeddingConfig(model_name=model, base_url=base_url, api_key=api_key)
            embedder = OpenAIEmbedding(cfg, dimension=EMBED_DIM, timeout=30)
            write_output("Using embedding: %s", model)
        else:
            write_output("Embedding env not set (EMBEDDING_API_BASE, EMBEDDING_MODEL). Skipping add/search.")
    except Exception as e:
        write_output("Embedding not available: %r. Skipping add/search.", e)

    config = _build_graph_config()
    if embedder:
        config.embedding_model = embedder

    # Step 1: Create store and check empty state
    write_output("=" * 60)
    write_output("Step 1: Create store and check collections")
    write_output("=" * 60)
    try:
        store = GraphStoreFactory.from_config(config)
        store.rebuild()
    except Exception as e:
        write_output("Failed to create MilvusGraphStore: %s", e)
        write_output("Ensure Milvus is running at %s", MILVUS_URI)
        raise

    for col in [ENTITY_COLLECTION, RELATION_COLLECTION, EPISODE_COLLECTION]:
        empty = store.is_empty(col)
        write_output("  %s: %s", col, "empty" if empty else "has data")
    write_output("")

    if not embedder:
        store.close()
        write_output("Demo stopped (embedding required for add/search). Set EMBEDDING_* env and run again.")
        return

    # Load scenario: large graph with relevant (ML/Search) + irrelevant (HR, Finance, etc.) subgraphs
    entities, relations, episodes = build_large_scenario_with_irrelevant_data()
    write_output(
        "Scenario: %d entities, %d relations, %d episodes (relevant ML/Search + irrelevant HR/Finance/Sales/etc.)",
        len(entities),
        len(relations),
        len(episodes),
    )
    write_output("")

    # Step 2: Add entities (with relation refs populated)
    write_output("=" * 60)
    write_output("Step 2: Add entities")
    write_output("=" * 60)
    try:
        await store.add_entity(entities, flush=True)
        write_output("✓ Added %d entities", len(entities))
        for e in entities:
            ref_count = len(e.relations) if e.relations else 0
            write_output("  - %s (uuid=%s..., relations=%d)", e.name, e.uuid[:8], ref_count)
    except Exception as e:
        write_output("✗ add_entity failed: %r", e)
        store.close()
        return
    write_output("")

    # Step 3: Add relations
    write_output("=" * 60)
    write_output("Step 3: Add relations")
    write_output("=" * 60)
    try:
        await store.add_relation(relations, flush=True)
        write_output("✓ Added %d relations", len(relations))
    except Exception as e:
        write_output("✗ add_relation failed: %r", e)
    write_output("")

    # Step 4: Add episodes
    write_output("=" * 60)
    write_output("Step 4: Add episodes")
    write_output("=" * 60)
    try:
        await store.add_episode(episodes, flush=True)
        write_output("✓ Added %d episodes", len(episodes))
        for ep in episodes:
            write_output("  - uuid=%s...", ep.uuid[:8])
    except Exception as e:
        write_output("✗ add_episode failed: %r", e)
    write_output("")

    # Step 5: Refresh (flush + compact)
    write_output("=" * 60)
    write_output("Step 5: Refresh (flush and compact)")
    write_output("=" * 60)
    try:
        await store.refresh(skip_compact=True)
        write_output("✓ Refresh completed")
    except Exception as e:
        write_output("✗ refresh failed: %r", e)
    write_output("")

    # Step 6: Hybrid search
    write_output("=" * 60)
    write_output("Step 6: Hybrid search (entities)")
    write_output("=" * 60)
    ranker_config = WeightedRankConfig(dense_name=0.2, dense_content=0.6, sparse_content=0.2)
    query = "Who works on that thing, you know- those AI stuffs like gpt or gemini?"
    try:
        # With reranker
        results = await store.search(
            query,
            k=3,
            collection=ENTITY_COLLECTION,
            ranker_config=ranker_config,
            reranker=RERANKER,
        )
        entity_list = results.get(ENTITY_COLLECTION, [])
        write_output("Query: %s", query)
        write_output("With reranker — Top %d entities:", len(entity_list))
        for i, doc in enumerate(entity_list[:5], 1):
            name = doc.get("name", "")
            content = (doc.get("content") or "")[:60]
            dist = doc.get("distance", 0)
            write_output(
                "  %d. %s (score=%g) %s",
                i,
                name,
                dist,
                content + "..." if len(str(doc.get("content"))) > 60 else "",
            )
        # Without reranker
        results_no_rerank = await store.search(
            query,
            k=3,
            collection=ENTITY_COLLECTION,
            ranker_config=ranker_config,
            reranker=None,
        )
        entity_list_no_rerank = results_no_rerank.get(ENTITY_COLLECTION, [])
        write_output("Without reranker — Top %d entities:", len(entity_list_no_rerank))
        for i, doc in enumerate(entity_list_no_rerank[:5], 1):
            name = doc.get("name", "")
            content = (doc.get("content") or "")[:60]
            dist = doc.get("distance", 0)
            write_output(
                "  %d. %s (score=%g) %s",
                i,
                name,
                dist,
                content + "..." if len(str(doc.get("content"))) > 60 else "",
            )
        _log_score_comparison(entity_list[:5], entity_list_no_rerank[:5], "entity")
    except Exception as e:
        write_output("✗ search failed: %r", e)
        raise
    write_output("")

    # Step 6b: BFS graph expansion search (same query, expand via relations)
    write_output("=" * 60)
    write_output("Step 6b: BFS graph expansion search (entities, bfs_depth=2, bfs_k=5)")
    write_output("=" * 60)
    try:
        # With reranker
        bfs_results = await store.search(
            query,
            k=3,
            collection=ENTITY_COLLECTION,
            ranker_config=ranker_config,
            reranker=RERANKER,
            bfs_depth=2,
            bfs_k=5,
        )
        bfs_entity_list = bfs_results.get(ENTITY_COLLECTION, [])
        write_output("Query: %s", query)
        write_output(
            "With reranker — BFS: %d entities (expanded from top-k via relation neighbors)", len(bfs_entity_list)
        )
        for i, doc in enumerate(bfs_entity_list[:10], 1):
            name = doc.get("name", "")
            content = (doc.get("content") or "")[:50]
            dist = doc.get("distance", 0)
            write_output(
                "  %d. %s (score=%g) %s...",
                i,
                name,
                dist,
                content,
            )
        if len(bfs_entity_list) > 10:
            write_output("  ... and %d more", len(bfs_entity_list) - 10)
        # Without reranker
        bfs_results_no_rerank = await store.search(
            query,
            k=3,
            collection=ENTITY_COLLECTION,
            ranker_config=ranker_config,
            reranker=None,
            bfs_depth=2,
            bfs_k=5,
        )
        bfs_entity_list_no_rerank = bfs_results_no_rerank.get(ENTITY_COLLECTION, [])
        write_output("Without reranker — BFS: %d entities", len(bfs_entity_list_no_rerank))
        for i, doc in enumerate(bfs_entity_list_no_rerank[:10], 1):
            name = doc.get("name", "")
            content = (doc.get("content") or "")[:50]
            dist = doc.get("distance", 0)
            write_output(
                "  %d. %s (score=%g) %s...",
                i,
                name,
                dist,
                content,
            )
        if len(bfs_entity_list_no_rerank) > 10:
            write_output("  ... and %d more", len(bfs_entity_list_no_rerank) - 10)
        _log_score_comparison(bfs_entity_list[:5], bfs_entity_list_no_rerank[:5], "entity")
    except Exception as e:
        write_output("✗ BFS search failed: %r", e)
        raise
    write_output("")

    # Step 6c: Hybrid search (relations)
    write_output("=" * 60)
    write_output("Step 6c: Hybrid search (relations)")
    write_output("=" * 60)
    relation_query = "collaboration and reporting on machine learning and search"
    try:
        # With reranker
        rel_results = await store.search(
            relation_query,
            k=3,
            collection=RELATION_COLLECTION,
            ranker_config=ranker_config,
            reranker=RERANKER,
        )
        rel_list = rel_results.get(RELATION_COLLECTION, [])
        write_output("Query: %s", relation_query)
        write_output("With reranker — Top %d relations:", len(rel_list))
        for i, doc in enumerate(rel_list[:5], 1):
            name = doc.get("name", "")
            content = (doc.get("content") or "")[:55]
            dist = doc.get("distance", 0)
            write_output(
                "  %d. %s (score=%g) %s...",
                i,
                name,
                dist,
                content,
            )
        # Without reranker
        rel_results_no_rerank = await store.search(
            relation_query,
            k=3,
            collection=RELATION_COLLECTION,
            ranker_config=ranker_config,
            reranker=None,
        )
        rel_list_no_rerank = rel_results_no_rerank.get(RELATION_COLLECTION, [])
        write_output("Without reranker — Top %d relations:", len(rel_list_no_rerank))
        for i, doc in enumerate(rel_list_no_rerank[:5], 1):
            name = doc.get("name", "")
            content = (doc.get("content") or "")[:55]
            dist = doc.get("distance", 0)
            write_output(
                "  %d. %s (score=%g) %s...",
                i,
                name,
                dist,
                content,
            )
        _log_score_comparison(rel_list[:5], rel_list_no_rerank[:5], "relation")
    except Exception as e:
        write_output("✗ relation search failed: %r", e)
        raise
    write_output("")

    # Step 6d: BFS graph expansion search (relations, expand via entities)
    write_output("=" * 60)
    write_output("Step 6d: BFS graph expansion search (relations, bfs_depth=2, bfs_k=5)")
    write_output("=" * 60)
    try:
        # With reranker
        bfs_rel_results = await store.search(
            relation_query,
            k=3,
            collection=RELATION_COLLECTION,
            ranker_config=ranker_config,
            reranker=RERANKER,
            bfs_depth=2,
            bfs_k=5,
        )
        bfs_rel_list = bfs_rel_results.get(RELATION_COLLECTION, [])
        write_output("Query: %s", relation_query)
        write_output(
            "With reranker — BFS: %d relations (expanded from top-k via shared entity neighbors)",
            len(bfs_rel_list),
        )
        for i, doc in enumerate(bfs_rel_list[:10], 1):
            name = doc.get("name", "")
            content = (doc.get("content") or "")[:45]
            dist = doc.get("distance", 0)
            write_output(
                "  %d. %s (score=%g) %s...",
                i,
                name,
                dist,
                content,
            )
        if len(bfs_rel_list) > 10:
            write_output("  ... and %d more", len(bfs_rel_list) - 10)
        # Without reranker
        bfs_rel_results_no_rerank = await store.search(
            relation_query,
            k=3,
            collection=RELATION_COLLECTION,
            ranker_config=ranker_config,
            reranker=None,
            bfs_depth=2,
            bfs_k=5,
        )
        bfs_rel_list_no_rerank = bfs_rel_results_no_rerank.get(RELATION_COLLECTION, [])
        write_output("Without reranker — BFS: %d relations", len(bfs_rel_list_no_rerank))
        for i, doc in enumerate(bfs_rel_list_no_rerank[:10], 1):
            name = doc.get("name", "")
            content = (doc.get("content") or "")[:45]
            dist = doc.get("distance", 0)
            write_output(
                "  %d. %s (score=%g) %s...",
                i,
                name,
                dist,
                content,
            )
        if len(bfs_rel_list_no_rerank) > 10:
            write_output("  ... and %d more", len(bfs_rel_list_no_rerank) - 10)
        _log_score_comparison(bfs_rel_list[:5], bfs_rel_list_no_rerank[:5], "relation")
    except Exception as e:
        write_output("✗ BFS relation search failed: %r", e)
        raise
    write_output("")

    # Step 7: Search over all collections
    write_output("=" * 60)
    write_output("Step 7: Search over all collections (collection='all')")
    write_output("=" * 60)
    all_query = "product roadmap and user research"
    try:
        # With reranker
        all_results = await store.search(
            all_query,
            k=3,
            collection="all",
            ranker_config=ranker_config,
            reranker=RERANKER,
        )
        write_output("Query: %s", all_query)
        write_output("With reranker — per collection:")
        for col in [ENTITY_COLLECTION, RELATION_COLLECTION, EPISODE_COLLECTION]:
            items = all_results.get(col, [])
            write_output("  %s: %d hits", col, len(items))
            for doc in items[:2]:
                write_output(
                    "    - %s (score=%.4f)", doc.get("name") or doc.get("uuid", "")[:8], doc.get("distance", 0)
                )
    except Exception as e:
        write_output("✗ search(all) failed: %r", e)
        raise
    write_output("")

    # Step 8: Query by ids
    write_output("=" * 60)
    write_output("Step 8: Query by ids")
    write_output("=" * 60)
    try:
        entity_ids = [e.uuid for e in entities[:2]]
        fetched = await store.query(ENTITY_COLLECTION, ids=entity_ids)
        write_output("Fetched %d entities by id", len(fetched))
        for row in fetched:
            write_output("  - %s: %s", row.get("name"), (row.get("content") or "")[:50] + "...")
    except Exception as e:
        write_output("✗ query(ids) failed: %r", e)
    write_output("")

    # Step 9: Query with limit (no filter)
    write_output("=" * 60)
    write_output("Step 9: Query with limit")
    write_output("=" * 60)
    try:
        limited = await store.query(ENTITY_COLLECTION, limit=2, output_fields=["uuid", "name", "content"])
        write_output("Fetched %d entities (limit=2)", len(limited))
    except Exception as e:
        write_output("✗ query(limit) failed: %r", e)
    write_output("")

    # Step 10: Delete by ids and verify
    write_output("=" * 60)
    write_output("Step 10: Delete test data by ids")
    write_output("=" * 60)
    try:
        rel_ids = [r.uuid for r in relations]
        await store.delete(RELATION_COLLECTION, ids=rel_ids)
        write_output("✓ Deleted %d relations", len(rel_ids))
        await store.delete(EPISODE_COLLECTION, ids=[ep.uuid for ep in episodes])
        write_output("✓ Deleted %d episodes", len(episodes))
        await store.delete(ENTITY_COLLECTION, ids=[e.uuid for e in entities])
        write_output("✓ Deleted %d entities", len(entities))
    except Exception as e:
        write_output("✗ delete failed: %r", e)
    write_output("")

    # Step 11: Verify empty again
    write_output("=" * 60)
    write_output("Step 11: Verify collections are empty")
    write_output("=" * 60)
    for col in [ENTITY_COLLECTION, RELATION_COLLECTION, EPISODE_COLLECTION]:
        empty = store.is_empty(col)
        write_output("  %s: %s", col, "empty" if empty else "has data")
    write_output("")

    store.close()
    write_output("=" * 60)
    write_output("Demo finished. Store closed.")
    write_output("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
