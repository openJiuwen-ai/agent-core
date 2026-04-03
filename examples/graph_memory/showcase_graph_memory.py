# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
End-to-end showcase for GraphMemory.

Demonstrates: loading conversation test data, chunking, adding memory per chunk
with entity/relation extraction and merging, refresh, optional semantic search,
and optional knowledge-graph visualization. Uses a single set of tunable constants
(merge options, chunk size, overlap) instead of iterating over combinations.

Prerequisites:
  - Run from examples/graph_memory (or set PYTHONPATH so memory_data and utils resolve).
  - Env variables: see .env.example (LLM, Embedding, Reranker, Milvus URI/DB name, DASHSCOPE_API_KEY).
  - Copy .env.example to .env and fill in your endpoints/keys.
"""

import asyncio
import datetime
import logging
import os
from pathlib import Path

import dotenv
from memory_data.dataloader import chunk_conv, list_data_files, load_test_data
from tqdm.rich import tqdm
from utils import visualize_kg
from utils.config import build_embedder, build_graph_config, build_llm, build_reranker, get_env_json
from utils.output import write_output

from openjiuwen.core.common.logging import llm_logger
from openjiuwen.core.foundation.store.graph import (
    ENTITY_COLLECTION,
    EPISODE_COLLECTION,
    RELATION_COLLECTION,
    Entity,
    Episode,
    Relation,
)
from openjiuwen.core.memory.config.graph import AddMemStrategy, EpisodeType
from openjiuwen.core.memory.graph.graph_memory.base import GraphMemory

# Suppress noise from llm_logger
llm_logger.set_level(logging.WARNING)

# ---------------------------------------------------------------------------
# Script constants (tweak here; env from .env.example only)
# ---------------------------------------------------------------------------

USER_ID = "showcase_user"
MERGE_ENTITIES = True
MERGE_RELATIONS = True
MERGE_FILTER = True
CHUNK_SIZE = 100
CHUNK_OVERLAP = 2
SUMMARY_TARGET = 100
RUN_VISUALIZATION = True
# When True, skip loading conversations, add_memory, and refresh; connect to existing store and run search only.
SKIP_GRAPH_BUILD = False
EXAMPLE_ROOT = Path(__file__).resolve().parent
KG_VIS_DIR = EXAMPLE_ROOT / "kg_visualization"

# Search queries to run (aligned with conversation test data: 信用卡/风控, 房贷/月供, 工资理财/自动转存, 客服/用户/App)
SEARCH_QUERIES_ENTITY = [
    "信用卡支付失败或风控",
    "房贷利率和等额本息月供",
    "工资理财和自动转存",
]
SEARCH_QUERIES_RELATION = [
    "用户咨询还款方式或额度",
    "客服解答贷款或理财问题",
]
SEARCH_TOP_K = 5
SEARCH_CONTENT_MAX_LEN = 55

dotenv.load_dotenv(str(EXAMPLE_ROOT / ".env"), override=True)


async def main():
    """Run full GraphMemory showcase: init, add memory from conversations, refresh, search, optional KG viz."""

    write_output("=" * 60)
    write_output("GraphMemory showcase")
    write_output("=" * 60)
    write_output("USER_ID: %s", USER_ID)
    write_output("SKIP_GRAPH_BUILD: %s", SKIP_GRAPH_BUILD)
    if not SKIP_GRAPH_BUILD:
        write_output(
            "MERGE_ENTITIES=%s, MERGE_RELATIONS=%s, MERGE_FILTER=%s",
            MERGE_ENTITIES,
            MERGE_RELATIONS,
            MERGE_FILTER,
        )
        write_output("CHUNK_SIZE=%d, CHUNK_OVERLAP=%d", CHUNK_SIZE, CHUNK_OVERLAP)
    write_output("")

    # Step 1: Build clients and config
    write_output("=" * 60)
    write_output("Step 1: Build LLM, embedder, reranker, graph config")
    write_output("=" * 60)

    embedder = build_embedder()
    if not embedder:
        write_output("Missing embedding config. Set JIUWEN_GRAPH_MEM_EMBED_URL and JIUWEN_GRAPH_MEM_EMBED_MODEL.")
        return
    llm = build_llm()
    if not SKIP_GRAPH_BUILD and not llm:
        write_output("Missing LLM config. Set JIUWEN_GRAPH_MEM_LLM_URL and JIUWEN_GRAPH_MEM_LLM_MODEL.")
        return

    embed_dim = getattr(embedder, "_dimension", None) or getattr(embedder, "dimension", None) or 1024
    reranker = build_reranker()
    db_config = build_graph_config(embed_dim)
    db_config.embedding_model = embedder

    write_output("Using LLM: %s", os.environ.get("JIUWEN_GRAPH_MEM_LLM_MODEL", ""))
    write_output("Using embedding: %s (dim=%s)", os.environ.get("JIUWEN_GRAPH_MEM_EMBED_MODEL", ""), embed_dim)
    write_output("Reranker: %s", "enabled" if reranker else "disabled")
    write_output("")

    # Step 2: Create GraphMemory and (optionally) rebuild store
    write_output("=" * 60)
    write_output("Step 2: Create GraphMemory%s", " and rebuild store" if not SKIP_GRAPH_BUILD else "")
    write_output("=" * 60)

    strategy = AddMemStrategy(
        summary_target=SUMMARY_TARGET,
        merge_entities=MERGE_ENTITIES,
        merge_relations=MERGE_RELATIONS,
        merge_filter=MERGE_FILTER,
    )
    llm_config = get_env_json("JIUWEN_GRAPH_MEM_LLM_CONFIG")

    # For OpenAI models, set:
    # - llm_structured_output = False
    # - llm_extra_kwargs = dict(extra_body=dict(reasoning_effort="minimal"))
    # For Qwen3 models, set:
    # - llm_structured_output = True
    # - llm_extra_kwargs = dict(extra_body=dict(enable_thinking=False))
    graph_memory = GraphMemory(
        db_config=db_config,
        llm_client=llm,
        llm_structured_output=llm_config.get("structured_output", False),
        llm_extra_kwargs=dict(extra_body=dict(enable_thinking=False)),
        reranker=reranker,
        extraction_strategy=strategy,
        language="cn",
        debug=False,
    )
    if not SKIP_GRAPH_BUILD:
        try:
            graph_memory.db_backend.rebuild()
            write_output("Store rebuilt: %s", db_config.name)
        except Exception as e:
            write_output("Failed to rebuild store: %s", e)
            write_output("Ensure Milvus is running at %s", db_config.uri)
            return
    write_output("")

    if not SKIP_GRAPH_BUILD:
        # Step 3: Load test data and add memory per chunk
        write_output("=" * 60)
        write_output("Step 3: Load conversations, chunk, add_memory")
        write_output("=" * 60)

        conv_files = list_data_files()
        if not conv_files:
            write_output("No conversation_*.json files in memory_data/mock_data/.")
            graph_memory.db_backend.close()
            return

        write_output("Conversation files: %d", len(conv_files))
        chunk_index = 0
        for fp in tqdm(conv_files):
            messages = load_test_data(fp)
            chunks = list(chunk_conv(messages, chunk=CHUNK_SIZE, overlap_last=CHUNK_OVERLAP))
            for i, chunk in enumerate(chunks):
                chunk_index += 1
                ref_time = datetime.datetime.fromisoformat(chunk[0]["iso_time"])
                write_output(
                    "Chunk %d (file=%s, slice %d/%d): %d messages, ref_time=%s",
                    chunk_index,
                    Path(fp).name,
                    i + 1,
                    len(chunks),
                    len(chunk),
                    ref_time.isoformat()[:19],
                )
                try:
                    result = await graph_memory.add_memory(
                        src_type=EpisodeType.CONVERSATION,
                        content=chunk,
                        user_id=USER_ID,
                        reference_time=ref_time,
                    )
                    write_output(
                        "  added_entity=%d, added_relation=%d, updated_entity=%d, updated_relation=%d",
                        len(result.added_entity),
                        len(result.added_relation),
                        len(result.updated_entity),
                        len(result.updated_relation),
                    )
                except Exception as e:
                    write_output("  add_memory failed: %s", e)
                    raise
        write_output("")

        # Step 4: Refresh and compact
        write_output("=" * 60)
        write_output("Step 4: Refresh (flush and compact)")
        write_output("=" * 60)
        await graph_memory.db_backend.refresh(skip_compact=False)
        write_output("Refresh completed")
        write_output("")

    # Step 5: Search demo (entity, relation, then all collections)

    write_output("=" * 60)
    write_output("Step 5a: Entity search (top-%d per query)", SEARCH_TOP_K)
    write_output("=" * 60)
    try:
        for query in SEARCH_QUERIES_ENTITY:
            results = await graph_memory.search(
                query,
                user_id=USER_ID,
                search_strategy="default",
                entity=True,
                relation=False,
                episode=False,
            )
            entity_list = results.get(ENTITY_COLLECTION, [])
            write_output("Query: %s", query)
            write_output("Top %d entities:", min(SEARCH_TOP_K, len(entity_list)))
            for i, (score, obj) in enumerate(entity_list[:SEARCH_TOP_K], 1):
                name = getattr(obj, "name", "") or getattr(obj, "uuid", "")[:8]
                content_raw = getattr(obj, "content", None) or ""
                content = content_raw[:SEARCH_CONTENT_MAX_LEN]
                suffix = "..." if len(content_raw) > SEARCH_CONTENT_MAX_LEN else ""
                write_output("  %d. %s (score=%.4f) %s%s", i, name, score, content, suffix)
            write_output("")
    except Exception as e:
        write_output("✗ Entity search failed: %r", e)
    write_output("")

    write_output("=" * 60)
    write_output("Step 5b: Relation search (top-%d per query)", SEARCH_TOP_K)
    write_output("=" * 60)
    try:
        for query in SEARCH_QUERIES_RELATION:
            results = await graph_memory.search(
                query,
                user_id=USER_ID,
                search_strategy="default",
                entity=False,
                relation=True,
                episode=False,
            )
            rel_list = results.get(RELATION_COLLECTION, [])
            write_output("Query: %s", query)
            write_output("Top %d relations:", min(SEARCH_TOP_K, len(rel_list)))
            for i, (score, obj) in enumerate(rel_list[:SEARCH_TOP_K], 1):
                name = getattr(obj, "name", "") or getattr(obj, "uuid", "")[:8]
                content_raw = getattr(obj, "content", None) or ""
                content = content_raw[:SEARCH_CONTENT_MAX_LEN]
                suffix = "..." if len(content_raw) > SEARCH_CONTENT_MAX_LEN else ""
                write_output("  %d. %s (score=%.4f) %s%s", i, name, score, content, suffix)
            write_output("")
    except Exception as e:
        write_output("✗ Relation search failed: %r", e)
    write_output("")

    write_output("=" * 60)
    write_output("Step 5c: Search all collections (single query)")
    write_output("=" * 60)
    all_query = "张伟在银行咨询过的问题"
    try:
        results = await graph_memory.search(
            all_query,
            user_id=USER_ID,
            search_strategy="default",
            entity=True,
            relation=True,
            episode=True,
        )
        write_output("Query: %s", all_query)
        for col_name in (ENTITY_COLLECTION, RELATION_COLLECTION, EPISODE_COLLECTION):
            items = results.get(col_name, [])
            write_output("  %s: %d hits", col_name, len(items))
            for score, obj in items[:2]:
                name = getattr(obj, "name", None) or getattr(obj, "uuid", "")[:8]
                write_output("    - %s (score=%.4f)", name, score)
    except Exception as e:
        write_output("✗ Search failed: %r", e)
    write_output("")

    # Step 6: Optional KG visualization
    if RUN_VISUALIZATION:
        write_output("=" * 60)
        write_output("Step 6: Export and visualize KG")
        write_output("=" * 60)
        await asyncio.sleep(1)
        backend = graph_memory.db_backend
        entities_raw = await backend.query(ENTITY_COLLECTION, limit=16000)
        relations_raw = await backend.query(RELATION_COLLECTION, limit=16000)
        episodes_raw = await backend.query(EPISODE_COLLECTION, limit=16000)
        for lst in (entities_raw, relations_raw, episodes_raw):
            for d in lst:
                for k in list(d.keys()):
                    if k.endswith("_embedding"):
                        del d[k]
        try:
            entities = [Entity(**x) for x in entities_raw]
            relations = [Relation(**x) for x in relations_raw]
            episodes = [Episode(**x) for x in episodes_raw]
            os.makedirs(KG_VIS_DIR, exist_ok=True)
            out_path = os.path.join(KG_VIS_DIR, db_config.name)
            visualize_kg.main(entities, relations, episodes, out_path)
            write_output("KG visualization saved: %s.html", out_path)
        except Exception as e:
            write_output("Visualization skipped: %s", e)
        write_output("")
    else:
        write_output("Step 6: Skipped (RUN_VISUALIZATION=False)")
        write_output("")

    graph_memory.db_backend.close()
    write_output("=" * 60)
    write_output("Showcase finished. Store closed.")
    write_output("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
