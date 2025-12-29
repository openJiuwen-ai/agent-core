# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import tempfile
from typing import List

import pytest

from openjiuwen.core.memory.store.impl.chroma_semantic_store import ChromaSemanticStore


class MockEmbedModel:

    def __init__(self):
        self.embeddings = {
            "我叫张明": [0.1, 0.9, 0.2, 0.8],
            "张明": [0.12, 0.88, 0.22, 0.78],
            "打篮球": [0.8, 0.2, 0.9, 0.1],
            "我喜欢运动": [0.75, 0.25, 0.85, 0.15],
            "今天天气好": [0.3, 0.4, 0.5, 0.6],
        }

    async def embed_queries(self, texts: List[str]) -> List[List[float]]:
        result = []
        for text in texts:
            if text in self.embeddings:
                result.append(self.embeddings[text])
            else:
                import random
                result.append([random.random() for _ in range(4)])
        return result


@pytest.fixture
def chroma_semantic_store():
    """创建一个ChromaSemanticStore实例，用于测试"""
    temp_dir_obj = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    temp_dir = temp_dir_obj.name
    embed_model = MockEmbedModel()
    store = ChromaSemanticStore(
        persist_directory=temp_dir,
        embed_model=embed_model,
    )
    test_table = "test_table"

    yield store, test_table

    # 清理资源
    temp_dir_obj.cleanup()


@pytest.mark.asyncio
async def test_add_docs_success(chroma_semantic_store):
    store, test_table = chroma_semantic_store
    docs = [
        ("doc1", "我叫张明"),
        ("doc2", "打篮球"),
        ("doc3", "今天天气好")
    ]
    result = await store.add_docs(docs, test_table)
    assert result is True


@pytest.mark.asyncio
async def test_search_basic(chroma_semantic_store):
    store, test_table = chroma_semantic_store
    docs = [
        ("doc1", "我叫张明"),
        ("doc2", "打篮球"),
        ("doc3", "今天天气好")
    ]
    await store.add_docs(docs, test_table)

    results = await store.search("张明", test_table, top_k=2)

    assert isinstance(results, list)
    assert len(results) <= 2  # top_k=2

    for result in results:
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], str)  # ID
        assert isinstance(result[1], float)  # Similarity score


@pytest.mark.asyncio
async def test_search_with_top_k_limit(chroma_semantic_store):
    store, test_table = chroma_semantic_store
    docs = [
        (f"doc{i}", f"文档内容 {i}") for i in range(10)
    ]
    await store.add_docs(docs, test_table)

    results = await store.search("文档", test_table, top_k=3)

    assert len(results) <= 3


@pytest.mark.asyncio
async def test_delete_docs(chroma_semantic_store):
    store, test_table = chroma_semantic_store
    docs = [
        ("doc1", "我叫张明"),
        ("doc2", "打篮球"),
        ("doc3", "今天天气好")
    ]
    await store.add_docs(docs, test_table)

    delete_result = await store.delete_docs(["doc1", "doc2"], test_table)

    assert delete_result is True

    search_results = await store.search("张明", test_table, top_k=5)
    assert isinstance(search_results, list)


@pytest.mark.asyncio
async def test_delete_table(chroma_semantic_store):
    store, test_table = chroma_semantic_store
    docs = [("doc1", "测试内容")]
    await store.add_docs(docs, test_table)

    search_results = await store.search("测试", test_table, top_k=5)
    initial_count = len(search_results)
    assert initial_count != 0

    delete_result = await store.delete_table(test_table)

    assert delete_result is True
    search_results_after = await store.search("测试", test_table, top_k=5)
    assert len(search_results_after) == 0


@pytest.mark.asyncio
async def test_similarity_scoring(chroma_semantic_store):
    store, test_table = chroma_semantic_store
    docs = [
        ("similar_doc", "我叫张明"),
        ("dissimilar_doc", "打篮球")
    ]
    await store.add_docs(docs, test_table)

    results = await store.search("张明", test_table, top_k=5)
    result = {}
    for doc_id, similarity in results:
        result[doc_id] = similarity
    assert result.get("similar_doc", 0) != result.get("dissimilar_doc", 0)


@pytest.mark.asyncio
async def test_empty_search(chroma_semantic_store):
    store, test_table = chroma_semantic_store
    results = await store.search("不存在的内容", test_table, top_k=5)
    assert results == []


@pytest.mark.asyncio
async def test_delete_docs_empty_list(chroma_semantic_store):
    store, test_table = chroma_semantic_store
    result = await store.delete_docs([], test_table)
    assert result is True
