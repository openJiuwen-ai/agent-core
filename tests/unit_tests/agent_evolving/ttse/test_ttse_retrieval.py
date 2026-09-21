# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for TTSE in-category hybrid/BM25 consult recall."""

from __future__ import annotations

from typing import Dict, List

import pytest

from openjiuwen.agent_evolving.ttse.config import TTSEConfig
from openjiuwen.agent_evolving.ttse.consult import (
    clamp_top_k,
    parse_consult_query,
    render_consult_result_async,
)
from openjiuwen.agent_evolving.ttse.index import _rrf
from openjiuwen.agent_evolving.ttse.stores import TTSERecordStore, reset_shared_stores

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
    def __init__(self, table: Dict[str, List[float]], model: str = "dict-v1") -> None:
        self.table = table
        self.model = model

    async def embed_query(self, text: str) -> List[float]:
        if text in self.table:
            return list(self.table[text])
        normalized = " ".join(text.lower().split())
        for key, value in self.table.items():
            if " ".join(key.lower().split()) == normalized:
                return list(value)
        return [0.0, 0.0]

    async def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return [await self.embed_query(text) for text in texts]


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


def test_rrf_fuses_by_id():
    fused = _rrf([["a", "b"], ["b", "a"]], 60)
    assert fused[0] in {"a", "b"}
    scores = {
        "a": 1.0 / 61 + 1.0 / 62,
        "b": 1.0 / 62 + 1.0 / 61,
    }
    assert scores["a"] == pytest.approx(scores["b"])


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
    result = await store.index.retrieve(store, category=OFFICE, query="PresentBench slides.md", top_k=2)
    assert result.mode == "bm25"
    assert [r["text"] for r in result.facts] == [HIT]
    assert CPP not in {r["text"] for r in result.facts}


@pytest.mark.asyncio
async def test_tiny_class_dumps_without_scoring(tmp_path):
    store = await _office_store(tmp_path, [HIT, CPP])
    result = await store.index.retrieve(store, category=OFFICE, query="PresentBench slides.md", top_k=8)
    assert result.mode == "dump"
    assert {r["text"] for r in result.facts} == {HIT, CPP}


@pytest.mark.asyncio
async def test_embed_only_when_bm25_has_no_overlap(tmp_path, monkeypatch):
    query = "xyzzyq quuxplugh"
    table = {
        query: [1.0, 0.0],
        SEMANTIC: [1.0, 0.0],
        HIT: [0.0, 1.0],
        **{text: [0.0, 1.0] for text in NOISE},
    }
    store = await _office_store(tmp_path, [SEMANTIC, HIT, *NOISE], embedding=DictEmbedding(table))
    semantic_id = next(record["id"] for record in store.facts if record["text"] == SEMANTIC)

    async def fake_ann(self, query_vector, *, track, category, top_k, embedding):
        del self, query_vector, track, category, top_k, embedding
        return [semantic_id]

    monkeypatch.setattr(type(store.index), "_search_ann", fake_ann)
    result = await store.index.retrieve(store, category=OFFICE, query=query, top_k=2)
    assert result.mode == "embed"
    assert result.facts[0]["text"] == SEMANTIC


@pytest.mark.asyncio
async def test_hybrid_keeps_lexical_and_semantic_hits(tmp_path, monkeypatch):
    query = "PresentBench slides.md xyzzyq"
    table = {
        query: [1.0, 0.0],
        SEMANTIC: [1.0, 0.0],
        HIT: [0.2, 0.8],
        **{text: [0.0, 1.0] for text in NOISE},
    }
    store = await _office_store(tmp_path, [HIT, SEMANTIC, *NOISE], embedding=DictEmbedding(table))
    semantic_id = next(record["id"] for record in store.facts if record["text"] == SEMANTIC)

    async def fake_ann(self, query_vector, *, track, category, top_k, embedding):
        del self, query_vector, track, category, top_k, embedding
        return [semantic_id]

    monkeypatch.setattr(type(store.index), "_search_ann", fake_ann)
    result = await store.index.retrieve(store, category=OFFICE, query=query, top_k=2)
    assert result.mode == "hybrid"
    texts = {r["text"] for r in result.facts}
    assert HIT in texts
    assert SEMANTIC in texts


@pytest.mark.asyncio
async def test_config_bm25_skips_ann_even_when_vectors_hit(tmp_path, monkeypatch):
    query = "PresentBench slides.md xyzzyq"
    table = {
        query: [1.0, 0.0],
        SEMANTIC: [1.0, 0.0],
        HIT: [0.2, 0.8],
        **{text: [0.0, 1.0] for text in NOISE},
    }
    store = await _office_store(tmp_path, [HIT, SEMANTIC, *NOISE], embedding=DictEmbedding(table))
    semantic_id = next(record["id"] for record in store.facts if record["text"] == SEMANTIC)
    called = {"ann": 0}

    async def fake_ann(self, query_vector, *, track, category, top_k, embedding):
        del self, query_vector, track, category, top_k, embedding
        called["ann"] += 1
        return [semantic_id]

    monkeypatch.setattr(type(store.index), "_search_ann", fake_ann)
    store._config.consult_retrieve_mode = "bm25"
    result = await store.index.retrieve(store, category=OFFICE, query=query, top_k=2)
    assert called["ann"] == 0
    assert result.mode == "bm25"
    assert result.facts[0]["text"] == HIT
    assert SEMANTIC not in {r["text"] for r in result.facts}


@pytest.mark.asyncio
async def test_config_embed_skips_bm25_lexical_hit(tmp_path, monkeypatch):
    query = "PresentBench slides.md xyzzyq"
    table = {
        query: [1.0, 0.0],
        SEMANTIC: [1.0, 0.0],
        HIT: [0.2, 0.8],
        **{text: [0.0, 1.0] for text in NOISE},
    }
    store = await _office_store(tmp_path, [HIT, SEMANTIC, *NOISE], embedding=DictEmbedding(table))
    semantic_id = next(record["id"] for record in store.facts if record["text"] == SEMANTIC)

    async def fake_ann(self, query_vector, *, track, category, top_k, embedding):
        del self, query_vector, track, category, top_k, embedding
        return [semantic_id]

    monkeypatch.setattr(type(store.index), "_search_ann", fake_ann)
    store._config.consult_retrieve_mode = "embed"
    result = await store.index.retrieve(store, category=OFFICE, query=query, top_k=2)
    assert result.mode == "embed"
    assert [r["text"] for r in result.facts] == [SEMANTIC]


@pytest.mark.asyncio
async def test_config_embed_falls_back_to_bm25_when_ann_empty(tmp_path, monkeypatch):
    store = await _office_store(tmp_path, [HIT, *NOISE], embedding=BoomEmbedding())
    store._config.consult_retrieve_mode = "embed"
    result = await store.index.retrieve(store, category=OFFICE, query="PresentBench slides.md", top_k=2)
    assert result.mode == "bm25"
    assert result.facts[0]["text"] == HIT


@pytest.mark.asyncio
async def test_config_mode_still_dumps_tiny_pool(tmp_path):
    store = await _office_store(tmp_path, [HIT, CPP])
    store._config.consult_retrieve_mode = "embed"
    result = await store.index.retrieve(store, category=OFFICE, query="PresentBench slides.md", top_k=8)
    assert result.mode == "dump"
    assert {r["text"] for r in result.facts} == {HIT, CPP}


def test_normalize_consult_retrieve_mode():
    from openjiuwen.agent_evolving.ttse.config import normalize_consult_retrieve_mode

    assert normalize_consult_retrieve_mode("EMBED") == "embed"
    assert normalize_consult_retrieve_mode("bm25") == "bm25"
    assert normalize_consult_retrieve_mode("nope") == "hybrid"
    assert normalize_consult_retrieve_mode(None) == "hybrid"


@pytest.mark.asyncio
async def test_unindexed_vectors_are_not_scanned(tmp_path, monkeypatch):
    query = "xyzzyq quuxplugh"
    table = {
        query: [1.0, 0.0],
        SEMANTIC: [1.0, 0.0],
        HIT: [0.0, 1.0],
        **{text: [0.0, 1.0] for text in NOISE},
    }
    store = await _office_store(tmp_path, [SEMANTIC, HIT, *NOISE], embedding=DictEmbedding(table))

    async def empty_ann(self, query_vector, *, track, category, top_k, embedding):
        del self, query_vector, track, category, top_k, embedding
        return []

    monkeypatch.setattr(type(store.index), "_search_ann", empty_ann)
    result = await store.index.retrieve(store, category=OFFICE, query=query, top_k=2)
    assert result.mode == "dump"
    assert result.mode != "embed"


@pytest.mark.asyncio
async def test_embed_failure_falls_back_to_bm25(tmp_path):
    store = await _office_store(tmp_path, [HIT, *NOISE], embedding=BoomEmbedding())
    result = await store.index.retrieve(store, category=OFFICE, query="PresentBench slides.md", top_k=2)
    assert result.mode == "bm25"
    assert result.facts[0]["text"] == HIT


@pytest.mark.asyncio
async def test_consult_all_searches_full_bank(tmp_path):
    store = await _office_store(tmp_path, [HIT, *NOISE])
    text = await render_consult_result_async(
        store, category="all", query="PresentBench slides.md", top_k=2
    )
    assert HIT in text
    assert "query is required" not in text
    assert "category is required" not in text


@pytest.mark.asyncio
async def test_consult_multiple_categories_retrieves_each_class(tmp_path):
    devops = "software-engineering-devops"
    store = TTSERecordStore(TTSEConfig(store_path=str(tmp_path / "bank.json")))
    await store.add_record_direct("fact", HIT, category=OFFICE, save=False)
    await store.add_record_direct("fact", CPP, category=devops, save=False)
    await store.save()
    text = await render_consult_result_async(
        store,
        category=f"{OFFICE},{devops}",
        query="PresentBench slides compile utf-8",
        mark_injected=False,
    )
    assert HIT in text
    assert CPP in text
    assert f"`{OFFICE}`" in text
    assert f"`{devops}`" in text


@pytest.mark.asyncio
async def test_consult_missing_args_return_warnings(tmp_path):
    store = await _office_store(tmp_path, [HIT, *NOISE])
    missing = await render_consult_result_async(store)
    assert "category is required" in missing
    assert "query is required" in missing
    assert "already attached" in missing
    assert HIT not in missing
    missing_category = await render_consult_result_async(store, query="PresentBench slides.md")
    assert "category is required" in missing_category
    assert HIT not in missing_category


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
    assert "# FACT" in text
    assert "most relevant confirmed observations" not in text
    assert "Treat as true." not in text
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


@pytest.mark.asyncio
async def test_consult_reads_live_config_top_k(tmp_path):
    store = await _office_store(tmp_path, [HIT, *NOISE])
    store._config.consult_top_k = 1
    text = await render_consult_result_async(
        store,
        category=OFFICE,
        query="PresentBench slides.md",
        mark_injected=False,
    )
    assert HIT in text
    assert text.count("1. ") == 1


@pytest.mark.asyncio
async def test_retrieve_uses_persisted_bm25_sidecar(tmp_path):
    store = await _office_store(tmp_path, [HIT, *NOISE])
    result = await store.index.retrieve(store, category=OFFICE, query="PresentBench slides.md", top_k=2)
    assert result.facts[0]["text"] == HIT
    assert (tmp_path / "bm25" / f"{OFFICE}_fact.json").is_file()


@pytest.mark.asyncio
async def test_category_and_full_bank_use_separate_bm25(tmp_path):
    devops = "software-engineering-devops"
    store = TTSERecordStore(TTSEConfig(store_path=str(tmp_path / "bank.json")))
    await store.add_record_direct("fact", HIT, category=OFFICE, save=False)
    other = "PresentBench slides.md lives in a C++ build tree"
    await store.add_record_direct("fact", other, category=devops, save=False)
    for i in range(8):
        await store.add_record_direct("fact", f"office noise {i}", category=OFFICE, save=False)
        await store.add_record_direct("fact", f"devops noise {i}", category=devops, save=False)
    await store.save()
    office = await store.index.retrieve(store, category=OFFICE, query="PresentBench slides.md", top_k=2)
    assert HIT in {r["text"] for r in office.facts}
    assert other not in {r["text"] for r in office.facts}
    full = await store.index.retrieve(store, category=None, query="PresentBench slides.md", top_k=4)
    texts = {r["text"] for r in full.facts}
    assert HIT in texts
    assert other in texts


class _FakeChroma:
    def __init__(self) -> None:
        self.adds: List[List[dict]] = []
        self.docs: List[dict] = []
        self.wipes = 0

    async def delete_table(self, name: str) -> None:
        del name
        self.wipes += 1
        self.docs = []

    async def add(self, data, batch_size=128, **kwargs):
        del batch_size, kwargs
        if isinstance(data, dict):
            data = [data]
        payload = list(data)
        self.adds.append(payload)
        ids = {item.get("id") for item in payload}
        self.docs = [item for item in self.docs if item.get("id") not in ids]
        self.docs.extend(payload)

    async def delete(self, ids=None, **kwargs):
        del kwargs
        drop = set(ids or [])
        self.docs = [item for item in self.docs if item.get("id") not in drop]


def _office_vectors(*texts: str) -> Dict[str, List[float]]:
    return {HIT: [1.0, 0.0], **{text: [0.0, 1.0] for text in texts if text != HIT}}


@pytest.mark.asyncio
async def test_late_embedding_batch_rebuilds_chroma(tmp_path, monkeypatch):
    store = await _office_store(tmp_path, [HIT, *NOISE])
    fake = _FakeChroma()
    monkeypatch.setattr(store.index, "_chroma_store", lambda: fake)
    store.attach_embedding(DictEmbedding(_office_vectors(HIT, *NOISE), model="late-v1"))
    assert store.index._chroma_stale is True
    await store.index.retrieve(store, category=OFFICE, query="PresentBench slides.md", top_k=2)
    assert fake.wipes == 1
    assert len(fake.adds) == 1
    assert len(fake.adds[0]) == len(store.facts)
    assert {item["id"] for item in fake.adds[0]} == {record["id"] for record in store.facts}
    assert store.index._chroma_stale is False
    assert store.index._chroma_fingerprint == "late-v1"
    await store.index.retrieve(store, category=OFFICE, query="PresentBench slides.md", top_k=2)
    assert len(fake.adds) == 1


@pytest.mark.asyncio
async def test_embedding_model_change_rebuilds_chroma(tmp_path, monkeypatch):
    table = _office_vectors(HIT, *NOISE)
    store = await _office_store(tmp_path, [HIT, *NOISE], embedding=DictEmbedding(table, model="v1"))
    fake = _FakeChroma()
    monkeypatch.setattr(store.index, "_chroma_store", lambda: fake)
    store.attach_embedding(DictEmbedding(table, model="v2"))
    await store.index.ensure_chroma(store)
    assert fake.wipes == 1
    assert len(fake.adds) == 1
    assert len(fake.adds[0]) == len(store.facts)
    assert store.index._chroma_fingerprint == "v2"
    assert store._embedding.model == "v2"
