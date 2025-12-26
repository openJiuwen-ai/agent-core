# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
GraphRAG knowledge base implementation test cases
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.retrieval.graph_knowledge_base import GraphKnowledgeBase
from openjiuwen.core.retrieval.common.config import (
    KnowledgeBaseConfig,
    RetrievalConfig,
)
from openjiuwen.core.retrieval.common.document import Document, TextChunk
from openjiuwen.core.retrieval.common.triple import Triple
from openjiuwen.core.retrieval.common.retrieval_result import RetrievalResult


@pytest.fixture
def mock_config():
    """Create mock configuration"""
    return KnowledgeBaseConfig(kb_id="test_kb", index_type="vector", use_graph=True)


@pytest.fixture
def mock_parser():
    """Create mock parser"""
    parser = AsyncMock()
    parser.parse = AsyncMock(return_value=[
        Document(id_="doc_1", text="Test document 1"),
    ])
    return parser


@pytest.fixture
def mock_chunker():
    """Create mock chunker"""
    chunker = MagicMock()
    chunker.chunk_documents = MagicMock(return_value=[
        TextChunk(id_="chunk_1", text="Test chunk 1", doc_id="doc_1"),
    ])
    return chunker


@pytest.fixture
def mock_extractor():
    """Create mock extractor"""
    extractor = AsyncMock()
    extractor.extract = AsyncMock(return_value=[
        Triple(
            subject="Alice",
            predicate="knows",
            object="Bob",
            metadata={"doc_id": "doc_1"},
        ),
    ])
    return extractor


@pytest.fixture
def mock_index_manager():
    """Create mock index manager"""
    index_manager = AsyncMock()
    index_manager.build_index = AsyncMock(return_value=True)
    index_manager.update_index = AsyncMock(return_value=True)
    index_manager.delete_index = AsyncMock(return_value=True)
    index_manager.get_index_info = AsyncMock(return_value={"count": 10})
    return index_manager


@pytest.fixture
def mock_vector_store():
    """Create mock vector store"""
    return AsyncMock()


@pytest.fixture
def mock_embed_model():
    """Create mock embedding model"""
    embed_model = AsyncMock()
    embed_model.embed_query = AsyncMock(return_value=[0.1] * 384)
    embed_model.embed_documents = AsyncMock(return_value=[[0.1] * 384])
    embed_model.dimension = 384
    return embed_model


class TestGraphKnowledgeBase:
    """Graph knowledge base tests"""

    @pytest.mark.asyncio
    async def test_parse_files_success(
        self, mock_config, mock_parser
    ):
        """Test parsing files successfully"""
        kb = GraphKnowledgeBase(config=mock_config, parser=mock_parser)
        file_paths = ["test1.txt"]
        documents = await kb.parse_files(file_paths)
        assert len(documents) == 1
        mock_parser.parse.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_documents_with_graph(
        self,
        mock_config,
        mock_chunker,
        mock_extractor,
        mock_index_manager,
        mock_embed_model,
    ):
        """Test adding documents (with graph index enabled)"""
        kb = GraphKnowledgeBase(
            config=mock_config,
            chunker=mock_chunker,
            extractor=mock_extractor,
            index_manager=mock_index_manager,
            embed_model=mock_embed_model,
        )
        documents = [Document(id_="doc_1", text="Test document")]
        doc_ids = await kb.add_documents(documents)
        assert len(doc_ids) == 1
        # Should build both chunk index and triple index
        assert mock_index_manager.build_index.call_count == 2
        mock_extractor.extract.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_documents_without_graph(
        self,
        mock_config,
        mock_chunker,
        mock_index_manager,
        mock_embed_model,
    ):
        """Test adding documents (without graph index)"""
        mock_config.use_graph = False
        kb = GraphKnowledgeBase(
            config=mock_config,
            chunker=mock_chunker,
            index_manager=mock_index_manager,
            embed_model=mock_embed_model,
        )
        documents = [Document(id_="doc_1", text="Test document")]
        doc_ids = await kb.add_documents(documents)
        assert len(doc_ids) == 1
        # Only build chunk index
        assert mock_index_manager.build_index.call_count == 1

    @pytest.mark.asyncio
    async def test_retrieve_with_graph(
        self,
        mock_config,
        mock_vector_store,
        mock_embed_model,
    ):
        """Test graph retrieval"""
        with patch(
            "openjiuwen.core.retrieval.graph_knowledge_base.GraphRetriever"
        ) as mock_graph_retriever_class:
            mock_graph_retriever = AsyncMock()
            mock_graph_retriever.retrieve = AsyncMock(return_value=[
                RetrievalResult(text="Test result", score=0.95),
            ])
            mock_graph_retriever_class.return_value = mock_graph_retriever

            kb = GraphKnowledgeBase(
                config=mock_config,
                vector_store=mock_vector_store,
                embed_model=mock_embed_model,
            )
            config = RetrievalConfig(use_graph=True, top_k=5)
            results = await kb.retrieve("test query", config=config)
            assert len(results) == 1
            mock_graph_retriever_class.assert_called_once()

    @pytest.mark.asyncio
    async def test_retrieve_with_agentic(
        self,
        mock_config,
        mock_vector_store,
        mock_embed_model,
    ):
        """Test using Agentic retrieval"""
        with patch(
            "openjiuwen.core.retrieval.graph_knowledge_base.GraphRetriever"
        ) as mock_graph_retriever_class, patch(
            "openjiuwen.core.retrieval.graph_knowledge_base.AgenticRetriever"
        ) as mock_agentic_retriever_class:
            mock_graph_retriever = AsyncMock()
            mock_graph_retriever.retrieve = AsyncMock(return_value=[
                RetrievalResult(text="Test result", score=0.95),
            ])
            mock_graph_retriever_class.return_value = mock_graph_retriever

            mock_agentic_retriever = AsyncMock()
            mock_agentic_retriever.retrieve = AsyncMock(return_value=[
                RetrievalResult(text="Agentic result", score=0.98),
            ])
            mock_agentic_retriever_class.return_value = mock_agentic_retriever

            kb = GraphKnowledgeBase(
                config=mock_config,
                vector_store=mock_vector_store,
                embed_model=mock_embed_model,
            )
            config = RetrievalConfig(use_graph=True, agentic=True, top_k=5)
            results = await kb.retrieve("test query", config=config)
            assert len(results) == 1
            mock_agentic_retriever_class.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_documents_with_graph(
        self, mock_config, mock_index_manager
    ):
        """Test deleting documents (with graph index enabled)"""
        kb = GraphKnowledgeBase(config=mock_config, index_manager=mock_index_manager)
        result = await kb.delete_documents(["doc_1"])
        assert result is True
        # Should delete both chunk index and triple index
        assert mock_index_manager.delete_index.call_count == 2

    @pytest.mark.asyncio
    async def test_delete_documents_without_graph(
        self, mock_config, mock_index_manager
    ):
        """Test deleting documents (without graph index)"""
        mock_config.use_graph = False
        kb = GraphKnowledgeBase(config=mock_config, index_manager=mock_index_manager)
        result = await kb.delete_documents(["doc_1"])
        assert result is True
        # Only delete chunk index
        assert mock_index_manager.delete_index.call_count == 1

    @pytest.mark.asyncio
    async def test_update_documents(
        self,
        mock_config,
        mock_chunker,
        mock_extractor,
        mock_index_manager,
        mock_embed_model,
    ):
        """Test updating documents"""
        kb = GraphKnowledgeBase(
            config=mock_config,
            chunker=mock_chunker,
            extractor=mock_extractor,
            index_manager=mock_index_manager,
            embed_model=mock_embed_model,
        )
        documents = [Document(id_="doc_1", text="Updated document")]
        doc_ids = await kb.update_documents(documents)
        assert len(doc_ids) == 1
        # Should delete first then add
        assert mock_index_manager.delete_index.call_count >= 1
        assert mock_index_manager.build_index.call_count >= 1

    @pytest.mark.asyncio
    async def test_get_statistics_with_graph(
        self, mock_config, mock_index_manager
    ):
        """Test getting statistics (with graph index enabled)"""
        kb = GraphKnowledgeBase(config=mock_config, index_manager=mock_index_manager)
        stats = await kb.get_statistics()
        assert stats["kb_id"] == "test_kb"
        assert stats["use_graph"] is True
        assert "chunk_index_info" in stats
        assert "triple_index_info" in stats

    @pytest.mark.asyncio
    async def test_get_statistics_without_index_manager(self, mock_config):
        """Test getting statistics without index manager"""
        kb = GraphKnowledgeBase(config=mock_config)
        stats = await kb.get_statistics()
        assert stats["kb_id"] == "test_kb"
        assert stats["index_exists"] is False

    @pytest.mark.asyncio
    async def test_close(self, mock_config):
        """Test closing knowledge base"""
        mock_graph_retriever = AsyncMock()
        mock_chunk_retriever = AsyncMock()
        mock_triple_retriever = AsyncMock()

        kb = GraphKnowledgeBase(
            config=mock_config,
            chunk_retriever=mock_chunk_retriever,
            triple_retriever=mock_triple_retriever,
        )
        kb.graph_retriever = mock_graph_retriever

        await kb.close()
        mock_graph_retriever.close.assert_called_once()
        mock_chunk_retriever.close.assert_called_once()
        mock_triple_retriever.close.assert_called_once()

