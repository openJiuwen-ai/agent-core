# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for TTSE in-category hybrid/BM25 consult recall."""

from __future__ import annotations

from typing import Dict, List

import pytest

from openjiuwen.harness.rails.evolution.ttse.config import TTSEConfig
from openjiuwen.harness.rails.evolution.ttse.consult import (
    parse_consult_query,
    render_consult_result_async,
)
from openjiuwen.harness.rails.evolution.ttse.retrieval import (
    clamp_top_k,
    retrieve_rules,
    rrf_fuse_indices,
)
from openjiuwen.harness.rails.evolution.ttse.stores import TTSERecordStore, reset_shared_stores

OFFICE = "documents-office-and-records"

HIT = "PresentBench grades slides.md not a pptx file"
SEMANTIC = "Office deck workflow uses markdown source"
CPP = "When compiling C++: use cl /utf-8"
NOISE = [
    "go run works on windows",
    "csv grader is case-sensitive",
    "keep charts as native office objects",
    "timeout on large pptx export",
    "utf-8 bom required for csv",
    "python venv must be activated",
    "lark bot needs tenant token",
    CPP,
]


class DictEmbedding:
    def __init__(self, table: Dict[str, List[float]]) -> None:
        self.table = table

    async def embed_query(self, text: str) -> List[float]:
        if text in self.table:
            return list(self.table[text])
        normalized = " ".join(text.lower().split())
        for key, value in self.table.items():
            if " ".join(key.lower().split()) == normalized:
                return list(value)
        return [0.0, 0.0]


class BoomEmbedding:
    async def embed_query(self, text: str) -> List[float]:
        raise RuntimeError("embed down")


@pytest.fixture(autouse=True)
def _reset_shared_stores():
    reset_shared_stores()
    yield
    reset_shared_stores()


def test_clamp_top_k_parses_and_caps():
    assert clamp_top_k(None, default=8, max_rules=40) == 8
    assert clamp_top_k("", default=8, max_rules=40) == 8
    assert clamp_top_k("3", default=8, max_rules=40) == 3
    assert clamp_top_k(100, default=8, max_rules=40) == 40
    assert clamp_top_k(0, default=8, max_rules=40) == 8
    assert clamp_top_k("nope", default=8, max_rules=40) == 8


def test_rrf_fuse_indices_matches_retrieval_formula():
    fused = rrf_fuse_indices([[0, 1], [1, 0]], k=60)
    scores = {idx: score for idx, score in fused}
    assert scores[0] == pytest.approx(1.0 / 61 + 1.0 / 62)
    assert scores[1] == pytest.approx(1.0 / 62 + 1.0 / 61)
    assert fused[0][0] in {0, 1}


def test_parse_consult_query_strips():
    assert parse_consult_query("  hello  ") == "hello"
    assert parse_consult_query(None) == ""


async def _office_store(tmp_path, texts, *, embedding=None, rtype="fact"):
    store = TTSERecordStore(
        TTSEConfig(store_path=str(tmp_path / "bank.json"), embedding=embedding),
        embedding=embedding,
    )
    assignments = []
    for text in texts:
        await store.add_record_direct(rtype, text, save=False)
        assignments.append((text, rtype, OFFICE))
    await store.set_categories(assignments)
    return store


@pytest.mark.asyncio
async def test_bm25_ranks_keyword_hit_without_embedding(tmp_path):
    store = await _office_store(tmp_path, [HIT, *NOISE])
    result = await retrieve_rules(
        store, category=OFFICE, query="PresentBench slides.md", top_k=2
    )
    assert result.mode == "bm25"
    assert [r["text"] for r in result.facts] == [HIT]
    assert CPP not in {r["text"] for r in result.facts}


@pytest.mark.asyncio
async def test_tiny_class_dumps_without_scoring(tmp_path):
    store = await _office_store(tmp_path, [HIT, CPP])
    result = await retrieve_rules(
        store, category=OFFICE, query="PresentBench slides.md", top_k=8
    )
    assert result.mode == "dump"
    assert {r["text"] for r in result.facts} == {HIT, CPP}


@pytest.mark.asyncio
async def test_embed_only_when_bm25_has_no_overlap(tmp_path):
    query = "xyzzyq quuxplugh"
    table = {
        query: [1.0, 0.0],
        SEMANTIC: [1.0, 0.0],
        HIT: [0.0, 1.0],
        **{text: [0.0, 1.0] for text in NOISE},
    }
    store = await _office_store(
        tmp_path, [SEMANTIC, HIT, *NOISE], embedding=DictEmbedding(table)
    )
    result = await retrieve_rules(store, category=OFFICE, query=query, top_k=2)
    assert result.mode == "embed"
    assert result.facts[0]["text"] == SEMANTIC


@pytest.mark.asyncio
async def test_hybrid_keeps_lexical_and_semantic_hits(tmp_path):
    query = "PresentBench slides.md xyzzyq"
    table = {
        query: [1.0, 0.0],
        SEMANTIC: [1.0, 0.0],
        HIT: [0.2, 0.8],
        **{text: [0.0, 1.0] for text in NOISE},
    }
    store = await _office_store(
        tmp_path, [HIT, SEMANTIC, *NOISE], embedding=DictEmbedding(table)
    )
    result = await retrieve_rules(store, category=OFFICE, query=query, top_k=2)
    assert result.mode == "hybrid"
    texts = {r["text"] for r in result.facts}
    assert HIT in texts
    assert SEMANTIC in texts


@pytest.mark.asyncio
async def test_embed_failure_falls_back_to_bm25(tmp_path):
    store = await _office_store(tmp_path, [HIT, *NOISE], embedding=BoomEmbedding())
    result = await retrieve_rules(
        store, category=OFFICE, query="PresentBench slides.md", top_k=2
    )
    assert result.mode == "bm25"
    assert result.facts[0]["text"] == HIT


@pytest.mark.asyncio
async def test_consult_query_without_category_errors(tmp_path):
    store = await _office_store(tmp_path, [HIT, *NOISE])
    text = await render_consult_result_async(
        store, query="PresentBench slides.md", top_k=2
    )
    assert "query requires category" in text
    assert HIT not in text


@pytest.mark.asyncio
async def test_consult_query_returns_top_k_not_whole_class(tmp_path):
    store = await _office_store(tmp_path, [HIT, *NOISE])
    text = await render_consult_result_async(
        store,
        category=OFFICE,
        query="PresentBench slides.md",
        top_k=2,
        mark_injected=True,
    )
    assert HIT in text
    assert "most relevant confirmed observations" in text
    assert CPP not in text
    hit_record = next(r for r in store.facts if r["text"] == HIT)
    assert hit_record["inject_hits"] >= 1
    cpp_record = next(r for r in store.facts if r["text"] == CPP)
    assert cpp_record["inject_hits"] == 0


@pytest.mark.asyncio
async def test_consult_top_k_string_is_clamped(tmp_path):
    store = await _office_store(tmp_path, [HIT, *NOISE])
    text = await render_consult_result_async(
        store,
        category=OFFICE,
        query="PresentBench slides.md",
        top_k="1",
        max_rules=40,
        default_top_k=8,
        mark_injected=False,
    )
    assert HIT in text
    assert text.count("1. ") == 1
