# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
Vector store abstract base class test cases
"""
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.retrieval.vector_store.base import VectorStore
from openjiuwen.core.retrieval.common.retrieval_result import SearchResult


class ConcreteVectorStore(VectorStore):
    """Concrete vector store implementation for testing abstract base class"""

    @staticmethod
    def create_client(database_name: str, path_or_uri: str, token: str = "", **kwargs) -> None:
        pass

    async def add(self, data, batch_size=None, **kwargs):
        pass

    async def search(self, query_vector, top_k=5, filters=None, **kwargs):
        return []

    async def sparse_search(self, query_text, top_k=5, filters=None, **kwargs):
        return []

    async def hybrid_search(
        self, query_text, query_vector=None, top_k=5, alpha=0.5, filters=None, **kwargs
    ):
        return []

    async def delete(self, ids=None, filter_expr=None, **kwargs):
        return True


class TestVectorStore:
    """Vector store abstract base class tests"""

    @pytest.mark.asyncio
    async def test_add(self):
        """Test adding vectors"""
        store = ConcreteVectorStore()
        # Should not raise exception
        await store.add({"id": "1", "text": "test", "embedding": [0.1] * 384})

    @pytest.mark.asyncio
    async def test_search(self):
        """Test vector search"""
        store = ConcreteVectorStore()
        results = await store.search([0.1] * 384, top_k=5)
        assert results == []

    @pytest.mark.asyncio
    async def test_sparse_search(self):
        """Test sparse search"""
        store = ConcreteVectorStore()
        results = await store.sparse_search("test query", top_k=5)
        assert results == []

    @pytest.mark.asyncio
    async def test_hybrid_search(self):
        """Test hybrid search"""
        store = ConcreteVectorStore()
        results = await store.hybrid_search(
            "test query", query_vector=[0.1] * 384, top_k=5
        )
        assert results == []

    @pytest.mark.asyncio
    async def test_delete(self):
        """Test deleting vectors"""
        store = ConcreteVectorStore()
        result = await store.delete(ids=["1", "2"])
        assert result is True

