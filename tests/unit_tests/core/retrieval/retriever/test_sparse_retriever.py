# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
Sparse retriever test cases
"""

from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.retrieval import SparseRetriever
from openjiuwen.core.retrieval import SearchResult
from openjiuwen.core.common.exception.errors import BaseError


@pytest.fixture
def mock_vector_store():
    """Create mock vector store"""
    store = AsyncMock()
    store.sparse_search = AsyncMock(
        return_value=[
            SearchResult(id="1", text="Result 1", score=0.95, metadata={"doc_id": "doc_1"}),
            SearchResult(id="2", text="Result 2", score=0.85, metadata={"doc_id": "doc_2"}),
        ]
    )
    return store


class TestSparseRetriever:
    """Sparse retriever tests"""

    @pytest.mark.asyncio
    async def test_retrieve_success(self, mock_vector_store):
        """Test retrieval success"""
        retriever = SparseRetriever(vector_store=mock_vector_store)
        results = await retriever.retrieve("test query", top_k=5)
        assert len(results) == 2
        assert results[0].text == "Result 1"
        assert results[0].score == 0.95
        mock_vector_store.sparse_search.assert_called_once()

    @pytest.mark.asyncio
    async def test_retrieve_invalid_mode(self, mock_vector_store):
        """Test invalid retrieval mode"""
        retriever = SparseRetriever(vector_store=mock_vector_store)
        with pytest.raises(BaseError, match="only supports 'sparse' mode"):
            await retriever.retrieve("test query", mode="vector")

    @pytest.mark.asyncio
    async def test_batch_retrieve(self, mock_vector_store):
        """Test batch retrieval"""
        retriever = SparseRetriever(vector_store=mock_vector_store)
        queries = ["query 1", "query 2"]
        results_list = await retriever.batch_retrieve(queries, top_k=5)
        assert len(results_list) == 2
        assert len(results_list[0]) == 2
        assert len(results_list[1]) == 2


class TestSparseRetrieverFilters:
    """issue #2431: filters 透传到 sparse_search"""

    @pytest.mark.asyncio
    async def test_retrieve_forwards_filters(self, mock_vector_store):
        retriever = SparseRetriever(vector_store=mock_vector_store)
        await retriever.retrieve("query", top_k=3, filters={"doc_type": "code"})

        assert mock_vector_store.sparse_search.await_args.kwargs["filters"] == {"doc_type": "code"}

    @pytest.mark.asyncio
    async def test_retrieve_search_results_forwards_filters(self, mock_vector_store):
        retriever = SparseRetriever(vector_store=mock_vector_store)
        await retriever.retrieve_search_results("query", top_k=3, filters={"doc_type": "code"})

        assert mock_vector_store.sparse_search.await_args.kwargs["filters"] == {"doc_type": "code"}
