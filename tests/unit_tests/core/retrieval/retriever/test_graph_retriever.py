# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
Graph retriever test cases
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.retrieval.retriever.graph_retriever import GraphRetriever
from openjiuwen.core.retrieval.common.retrieval_result import RetrievalResult


@pytest.fixture
def mock_chunk_retriever():
    """Create mock chunk retriever"""
    retriever = AsyncMock()
    retriever.retrieve = AsyncMock(return_value=[
        RetrievalResult(
            text="Chunk result 1",
            score=0.9,
            chunk_id="chunk_1",
            doc_id="doc_1",
            metadata={"chunk_id": "chunk_1", "doc_id": "doc_1"},
        ),
        RetrievalResult(
            text="Chunk result 2",
            score=0.8,
            chunk_id="chunk_2",
            doc_id="doc_1",
            metadata={"chunk_id": "chunk_2", "doc_id": "doc_1"},
        ),
    ])
    return retriever


@pytest.fixture
def mock_triple_retriever():
    """Create mock triple retriever"""
    retriever = AsyncMock()
    retriever.retrieve = AsyncMock(return_value=[
        RetrievalResult(
            text="Triple result 1",
            score=0.85,
            metadata={"chunk_id": "chunk_3", "doc_id": "doc_1"},
        ),
    ])
    return retriever


@pytest.fixture
def mock_vector_store():
    """Create mock vector store"""
    store = MagicMock()
    store.collection_name = None
    return store


@pytest.fixture
def mock_embed_model():
    """Create mock embedding model"""
    model = AsyncMock()
    model.embed_query = AsyncMock(return_value=[0.1] * 384)
    return model


class TestGraphRetriever:
    """Graph retriever tests"""

    @classmethod
    def test_init_with_retrievers(cls, mock_chunk_retriever, mock_triple_retriever):
        """Test initialization with retrievers"""
        retriever = GraphRetriever(
            chunk_retriever=mock_chunk_retriever,
            triple_retriever=mock_triple_retriever,
        )
        assert retriever.chunk_retriever == mock_chunk_retriever
        assert retriever.triple_retriever == mock_triple_retriever

    @classmethod
    def test_init_with_vector_store(cls, mock_vector_store, mock_embed_model):
        """Test initialization with vector store"""
        retriever = GraphRetriever(
            vector_store=mock_vector_store,
            embed_model=mock_embed_model,
            chunk_collection="chunks",
            triple_collection="triples",
        )
        assert retriever.vector_store == mock_vector_store
        assert retriever.embed_model == mock_embed_model
        assert retriever.chunk_collection == "chunks"
        assert retriever.triple_collection == "triples"

    @pytest.mark.asyncio
    async def test_retrieve_score_threshold_invalid_mode(self, mock_chunk_retriever):
        """Test using score threshold in non-vector mode"""
        retriever = GraphRetriever(chunk_retriever=mock_chunk_retriever)
        
        with pytest.raises(ValueError, match="score_threshold is only supported"):
            await retriever.retrieve(
                "test query", top_k=5, mode="sparse", score_threshold=0.8
            )

    @pytest.mark.asyncio
    async def test_graph_expansion_empty_chunks(self, mock_chunk_retriever):
        """Test graph expansion (empty initial chunks)"""
        retriever = GraphRetriever(chunk_retriever=mock_chunk_retriever)
        
        results = await retriever.graph_expansion(
            query="test query",
            chunks=[],
            topk=5,
            mode="hybrid",
        )
        # Should return empty list or fallback results
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_close(self, mock_chunk_retriever, mock_triple_retriever):
        """Test closing retriever"""
        mock_chunk_retriever.close = AsyncMock()
        mock_triple_retriever.close = AsyncMock()
        
        retriever = GraphRetriever(
            chunk_retriever=mock_chunk_retriever,
            triple_retriever=mock_triple_retriever,
        )
        
        await retriever.close()
        mock_chunk_retriever.close.assert_called_once()
        mock_triple_retriever.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_sync_close(self, mock_chunk_retriever):
        """Test closing retriever (synchronous close method)"""
        mock_chunk_retriever.close = MagicMock()
        
        retriever = GraphRetriever(chunk_retriever=mock_chunk_retriever)
        
        await retriever.close()
        mock_chunk_retriever.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_no_close_method(self):
        """Test closing retriever without close method"""
        mock_retriever = MagicMock()
        del mock_retriever.close  # Remove close method
        
        retriever = GraphRetriever(chunk_retriever=mock_retriever)
        # Should not raise exception
        await retriever.close()

