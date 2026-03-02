# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
Simple knowledge base implementation test cases
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.retrieval import (
    Document,
    KnowledgeBaseConfig,
    RetrievalConfig,
    RetrievalResult,
    SimpleKnowledgeBase,
    TextChunk,
    retrieve_multi_kb,
    retrieve_multi_kb_with_source,
)
from openjiuwen.core.retrieval.common.retrieval_result import MultiKBRetrievalResult, RetrievalResult


@pytest.fixture
def mock_config():
    """Create mock configuration"""
    return KnowledgeBaseConfig(kb_id="test_kb", index_type="vector")


@pytest.fixture
def mock_parser():
    """Create mock parser"""
    parser = AsyncMock()
    parser.parse = AsyncMock(
        return_value=[
            Document(id_="doc_1", text="Test document 1"),
            Document(id_="doc_2", text="Test document 2"),
        ]
    )
    return parser


@pytest.fixture
def mock_chunker():
    """Create mock chunker"""
    chunker = MagicMock()
    chunker.chunk_documents = MagicMock(
        return_value=[
            TextChunk(id_="chunk_1", text="Test chunk 1", doc_id="doc_1"),
            TextChunk(id_="chunk_2", text="Test chunk 2", doc_id="doc_1"),
        ]
    )
    return chunker


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
    embed_model.embed_documents = AsyncMock(return_value=[[0.1] * 384] * 2)
    embed_model.dimension = 384
    return embed_model


@pytest.fixture
def mock_retriever():
    """Create mock retriever"""
    retriever = AsyncMock()
    retriever.retrieve = AsyncMock(
        return_value=[
            RetrievalResult(text="Test result", score=0.95),
        ]
    )
    return retriever


class TestSimpleKnowledgeBase:
    """Simple knowledge base tests"""

    @pytest.mark.asyncio
    async def test_parse_files_success(self, mock_config, mock_parser):
        """Test parsing files successfully"""
        kb = SimpleKnowledgeBase(config=mock_config, parser=mock_parser)
        file_paths = ["test1.txt", "test2.txt"]
        documents = await kb.parse_files(file_paths)
        assert len(documents) == 4  # Each file returns 2 documents
        assert mock_parser.parse.call_count == 2

    @pytest.mark.asyncio
    async def test_parse_files_without_parser(self, mock_config):
        """Test parsing files without parser"""
        kb = SimpleKnowledgeBase(config=mock_config)
        with pytest.raises(BaseError, match="parser is required"):
            await kb.parse_files(["test.txt"])

    @pytest.mark.asyncio
    async def test_parse_files_with_exception(self, mock_config, mock_parser):
        """Test exception when parsing files"""
        mock_parser.parse = AsyncMock(side_effect=Exception("Parse error"))
        kb = SimpleKnowledgeBase(config=mock_config, parser=mock_parser)
        documents = await kb.parse_files(["test.txt"])
        assert len(documents) == 0

    @pytest.mark.asyncio
    async def test_database_name_mismatch(
        self, mock_config, mock_chunker, mock_vector_store, mock_index_manager, mock_embed_model
    ):
        """Test different database_name in vector store and index manager raises expected error"""
        setattr(mock_index_manager, "database_name", "different_name")
        with pytest.raises(BaseError, match="incompatible database_name configs"):
            kb = SimpleKnowledgeBase(
                config=mock_config,
                vector_store=mock_vector_store,
                chunker=mock_chunker,
                index_manager=mock_index_manager,
                embed_model=mock_embed_model,
            )
            del kb

    @pytest.mark.asyncio
    async def test_add_documents_success(self, mock_config, mock_chunker, mock_index_manager, mock_embed_model):
        """Test adding documents successfully"""
        kb = SimpleKnowledgeBase(
            config=mock_config,
            chunker=mock_chunker,
            index_manager=mock_index_manager,
            embed_model=mock_embed_model,
        )
        documents = [
            Document(id_="doc_1", text="Test document 1"),
            Document(id_="doc_2", text="Test document 2"),
        ]
        doc_ids = await kb.add_documents(documents)
        assert len(doc_ids) == 2
        assert doc_ids == ["doc_1", "doc_2"]
        mock_chunker.chunk_documents.assert_called_once()
        mock_index_manager.build_index.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_documents_without_chunker(self, mock_config):
        """Test adding documents without chunker"""
        kb = SimpleKnowledgeBase(config=mock_config)
        with pytest.raises(BaseError, match="chunker is required"):
            await kb.add_documents([Document(text="Test")])

    @pytest.mark.asyncio
    async def test_add_documents_without_index_manager(self, mock_config, mock_chunker):
        """Test adding documents without index manager"""
        kb = SimpleKnowledgeBase(config=mock_config, chunker=mock_chunker)
        with pytest.raises(BaseError, match="index_manager is required"):
            await kb.add_documents([Document(text="Test")])

    @pytest.mark.asyncio
    async def test_add_documents_build_index_failed(
        self, mock_config, mock_chunker, mock_index_manager, mock_embed_model
    ):
        """Test building index failure"""
        mock_index_manager.build_index = AsyncMock(return_value=False)
        kb = SimpleKnowledgeBase(
            config=mock_config,
            chunker=mock_chunker,
            index_manager=mock_index_manager,
            embed_model=mock_embed_model,
        )
        with pytest.raises(BaseError, match="Failed to build index"):
            await kb.add_documents([Document(text="Test")])

    @pytest.mark.asyncio
    async def test_retrieve_with_retriever(self, mock_config, mock_retriever):
        """Test retrieval with provided retriever"""
        kb = SimpleKnowledgeBase(config=mock_config, retriever=mock_retriever)
        results = await kb.retrieve("test query")
        assert len(results) == 1
        mock_retriever.retrieve.assert_called_once()

    @pytest.mark.asyncio
    async def test_retrieve_with_agentic(self, mock_config, mock_retriever):
        """Test using Agentic retrieval via SimpleKnowledgeBase"""
        with patch("openjiuwen.core.retrieval.simple_knowledge_base.AgenticRetriever") as mock_agentic_class:
            mock_agentic_retriever = AsyncMock()
            mock_agentic_retriever.retrieve = AsyncMock(
                return_value=[
                    RetrievalResult(text="Agentic result", score=0.98),
                ]
            )
            mock_agentic_class.return_value = mock_agentic_retriever

            mock_llm_client = AsyncMock()
            kb = SimpleKnowledgeBase(
                config=mock_config,
                retriever=mock_retriever,
                llm_client=mock_llm_client,
            )
            config = RetrievalConfig(agentic=True, top_k=5)
            results = await kb.retrieve("test query", config=config)
            assert len(results) == 1
            mock_agentic_class.assert_called_once()
            # Verify AgenticRetriever is constructed with retriever= and llm_client=
            call_kwargs = mock_agentic_class.call_args[1]
            assert "retriever" in call_kwargs
            assert "llm_client" in call_kwargs
            assert call_kwargs["retriever"] == mock_retriever
            assert call_kwargs["llm_client"] == mock_llm_client

    @pytest.mark.asyncio
    async def test_retrieve_without_agentic(self, mock_config, mock_retriever):
        """Test retrieval without agentic flag uses the base retriever directly"""
        kb = SimpleKnowledgeBase(config=mock_config, retriever=mock_retriever)
        config = RetrievalConfig(agentic=False, top_k=5)
        results = await kb.retrieve("test query", config=config)
        assert len(results) == 1
        mock_retriever.retrieve.assert_called_once()

    @pytest.mark.asyncio
    async def test_retrieve_without_retriever_or_vector_store(self, mock_config):
        """Test retrieval without retriever or vector store"""
        kb = SimpleKnowledgeBase(config=mock_config)
        with pytest.raises(BaseError, match="vector_store or retriever is required"):
            await kb.retrieve("test query")

    @pytest.mark.asyncio
    async def test_delete_documents_success(self, mock_config, mock_index_manager):
        """Test deleting documents successfully"""
        kb = SimpleKnowledgeBase(config=mock_config, index_manager=mock_index_manager)
        result = await kb.delete_documents(["doc_1", "doc_2"])
        assert result is True
        assert mock_index_manager.delete_index.call_count == 2

    @pytest.mark.asyncio
    async def test_delete_documents_without_index_manager(self, mock_config):
        """Test deleting documents without index manager"""
        kb = SimpleKnowledgeBase(config=mock_config)
        with pytest.raises(BaseError, match="index_manager is required"):
            await kb.delete_documents(["doc_1"])

    @pytest.mark.asyncio
    async def test_update_documents_success(self, mock_config, mock_chunker, mock_index_manager, mock_embed_model):
        """Test updating documents successfully"""
        kb = SimpleKnowledgeBase(
            config=mock_config,
            chunker=mock_chunker,
            index_manager=mock_index_manager,
            embed_model=mock_embed_model,
        )
        documents = [Document(id_="doc_1", text="Updated document")]
        doc_ids = await kb.update_documents(documents)
        assert len(doc_ids) == 1
        mock_index_manager.update_index.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_statistics_success(self, mock_config, mock_index_manager):
        """Test getting statistics successfully"""
        kb = SimpleKnowledgeBase(config=mock_config, index_manager=mock_index_manager)
        stats = await kb.get_statistics()
        assert stats["kb_id"] == "test_kb"
        assert stats["index_type"] == "vector"
        assert "index_info" in stats

    @pytest.mark.asyncio
    async def test_get_statistics_without_index_manager(self, mock_config):
        """Test getting statistics without index manager"""
        kb = SimpleKnowledgeBase(config=mock_config)
        stats = await kb.get_statistics()
        assert stats["kb_id"] == "test_kb"
        assert stats["index_exists"] is False


class TestRetrieveMultiKb:
    """Multi-knowledge base retrieval tests"""

    @pytest.mark.asyncio
    async def test_retrieve_multi_kb_empty_list(self):
        """Test empty knowledge base list"""
        results = await retrieve_multi_kb([], "test query")
        assert results == []

    @pytest.mark.asyncio
    async def test_retrieve_multi_kb_success(self):
        """Test multi-knowledge base retrieval successfully"""
        mock_kb1 = AsyncMock()
        mock_kb1.config = MagicMock(kb_id="kb1")
        mock_kb1.retrieve = AsyncMock(
            return_value=[
                RetrievalResult(text="Result 1", score=0.9),
                RetrievalResult(text="Result 2", score=0.8),
            ]
        )

        mock_kb2 = AsyncMock()
        mock_kb2.config = MagicMock(kb_id="kb2")
        mock_kb2.retrieve = AsyncMock(
            return_value=[
                RetrievalResult(text="Result 2", score=0.85),
                RetrievalResult(text="Result 3", score=0.7),
            ]
        )

        results = await retrieve_multi_kb([mock_kb1, mock_kb2], "test query", top_k=5)
        assert len(results) <= 5
        # Should deduplicate and sort by score
        texts = [r for r in results]
        assert "Result 2" in texts or "Result 1" in texts

    @pytest.mark.asyncio
    async def test_retrieve_multi_kb_with_failure(self):
        """Test multi-knowledge base retrieval with partial failure"""
        mock_kb1 = AsyncMock()
        mock_kb1.config = MagicMock(kb_id="kb1")
        mock_kb1.retrieve = AsyncMock(side_effect=Exception("Error"))

        mock_kb2 = AsyncMock()
        mock_kb2.config = MagicMock(kb_id="kb2")
        mock_kb2.retrieve = AsyncMock(
            return_value=[
                RetrievalResult(text="Result 1", score=0.9),
            ]
        )

        results = await retrieve_multi_kb([mock_kb1, mock_kb2], "test query")
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_retrieve_multi_kb_with_source(self):
        """Test multi-knowledge base retrieval (with source information)"""
        mock_kb1 = AsyncMock()
        mock_kb1.config = MagicMock(kb_id="kb1")
        mock_kb1.retrieve = AsyncMock(
            return_value=[
                RetrievalResult(
                    text="Result 1",
                    score=0.9,
                    metadata={"raw_score": 0.9, "raw_score_scaled": 0.9},
                ),
            ]
        )

        mock_kb2 = AsyncMock()
        mock_kb2.config = MagicMock(kb_id="kb2")
        mock_kb2.retrieve = AsyncMock(
            return_value=[
                RetrievalResult(
                    text="Result 1",
                    score=0.95,
                    metadata={"raw_score": 0.95, "raw_score_scaled": 0.95},
                ),
            ]
        )

        results = await retrieve_multi_kb_with_source(kbs=[mock_kb1, mock_kb2], query="test query", top_k=5)
        assert len(results) <= 5
        if results:
            assert isinstance(results[0], MultiKBRetrievalResult)
            assert results[0].text == "Result 1"
            assert results[0].score == 0.95
            assert results[0].kb_ids == ["kb1", "kb2"]
