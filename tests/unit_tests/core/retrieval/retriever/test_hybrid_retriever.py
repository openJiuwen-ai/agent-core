# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
Hybrid retriever test cases
"""

from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.retrieval import HybridRetriever
from openjiuwen.core.retrieval import SearchResult
from openjiuwen.core.common.exception.exception import JiuWenBaseException


@pytest.fixture
def mock_vector_store():
    """Create mock vector store"""
    store = AsyncMock()
    store.hybrid_search = AsyncMock(
        return_value=[
            SearchResult(id="1", text="Hybrid result 1", score=0.95, metadata={"doc_id": "doc_1"}),
            SearchResult(id="2", text="Hybrid result 2", score=0.85, metadata={"doc_id": "doc_2"}),
        ]
    )
    store.search = AsyncMock(
        return_value=[
            SearchResult(id="1", text="Vector result", score=0.9, metadata={}),
        ]
    )
    store.sparse_search = AsyncMock(
        return_value=[
            SearchResult(id="1", text="Sparse result", score=0.8, metadata={}),
        ]
    )
    return store


@pytest.fixture
def mock_embed_model():
    """Create mock embedding model"""
    model = AsyncMock()
    model.embed_query = AsyncMock(return_value=[0.1] * 384)
    return model


class TestHybridRetriever:
    """Hybrid retriever tests"""

    @pytest.mark.asyncio
    async def test_retrieve_hybrid_mode(self, mock_vector_store, mock_embed_model):
        """Test hybrid retrieval mode"""
        retriever = HybridRetriever(
            vector_store=mock_vector_store,
            embed_model=mock_embed_model,
        )
        results = await retriever.retrieve("test query", top_k=5, mode="hybrid")
        assert len(results) == 2
        mock_vector_store.hybrid_search.assert_called_once()
        mock_embed_model.embed_query.assert_called_once()

    @pytest.mark.asyncio
    async def test_retrieve_vector_mode(self, mock_vector_store, mock_embed_model):
        """Test vector retrieval mode"""
        retriever = HybridRetriever(
            vector_store=mock_vector_store,
            embed_model=mock_embed_model,
        )
        results = await retriever.retrieve("test query", top_k=5, mode="vector")
        assert len(results) == 1
        mock_vector_store.search.assert_called_once()

    @pytest.mark.asyncio
    async def test_retrieve_sparse_mode(self, mock_vector_store):
        """Test sparse retrieval mode"""
        retriever = HybridRetriever(vector_store=mock_vector_store)
        results = await retriever.retrieve("test query", top_k=5, mode="sparse")
        assert len(results) == 1
        mock_vector_store.sparse_search.assert_called_once()

    @pytest.mark.asyncio
    async def test_retrieve_with_custom_alpha(self, mock_vector_store, mock_embed_model):
        """Test using custom alpha parameter"""
        retriever = HybridRetriever(
            vector_store=mock_vector_store,
            embed_model=mock_embed_model,
            alpha=0.7,
        )
        results = await retriever.retrieve("test query", top_k=5, alpha=0.8)
        # Verify custom alpha is used
        call_args = mock_vector_store.hybrid_search.call_args
        assert call_args[1]["alpha"] == 0.8

    @pytest.mark.asyncio
    async def test_retrieve_vector_mode_without_embed_model(self, mock_vector_store):
        """Test vector mode but without embedding model"""
        retriever = HybridRetriever(vector_store=mock_vector_store)
        with pytest.raises(JiuWenBaseException, match="embed_model is required"):
            await retriever.retrieve("test query", mode="vector")

    @pytest.mark.asyncio
    async def test_retrieve_with_score_threshold(self, mock_vector_store, mock_embed_model):
        """Test using score threshold (only supported in vector mode)"""
        retriever = HybridRetriever(
            vector_store=mock_vector_store,
            embed_model=mock_embed_model,
        )
        results = await retriever.retrieve("test query", top_k=5, mode="vector", score_threshold=0.85)
        # Should filter out results with score < 0.85
        for result in results:
            assert result.score >= 0.85

    @pytest.mark.asyncio
    async def test_retrieve_score_threshold_invalid_mode(self, mock_vector_store, mock_embed_model):
        """Test using score threshold in non-vector mode"""
        retriever = HybridRetriever(
            vector_store=mock_vector_store,
            embed_model=mock_embed_model,
        )
        with pytest.raises(JiuWenBaseException, match="score_threshold is only supported"):
            await retriever.retrieve("test query", mode="hybrid", score_threshold=0.5)

    @pytest.mark.asyncio
    async def test_batch_retrieve(self, mock_vector_store, mock_embed_model):
        """Test batch retrieval"""
        retriever = HybridRetriever(
            vector_store=mock_vector_store,
            embed_model=mock_embed_model,
        )
        queries = ["query 1", "query 2"]
        results_list = await retriever.batch_retrieve(queries, top_k=5)
        assert len(results_list) == 2
