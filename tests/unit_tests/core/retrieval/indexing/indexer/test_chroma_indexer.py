# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
ChromaDB index manager test cases
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.retrieval import ChromaIndexer
from openjiuwen.core.retrieval import IndexConfig
from openjiuwen.core.retrieval import TextChunk
from openjiuwen.core.common.exception.errors import BaseError


@pytest.fixture
def mock_embed_model():
    """Create mock embedding model"""
    model = AsyncMock()
    model.embed_documents = AsyncMock(return_value=[[0.1] * 384] * 2)
    model.dimension = 384
    return model


class TestChromaIndexer:
    """ChromaDB index manager tests"""

    @patch("openjiuwen.core.retrieval.indexing.indexer.chroma_indexer.chromadb.PersistentClient")
    def test_init_success(self, mock_client_class):
        """Test successful initialization"""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        indexer = ChromaIndexer(chroma_path="/tmp/test_chroma")
        assert indexer.chroma_path == "/tmp/test_chroma"
        assert indexer.client == mock_client
        mock_client_class.assert_called_once_with(path="/tmp/test_chroma", database="default_database")

    @patch("openjiuwen.core.retrieval.indexing.indexer.chroma_indexer.chromadb.PersistentClient")
    def test_init_with_empty_path(self, mock_client_class):
        """Test initialization with empty path"""
        with pytest.raises(BaseError, match="chroma_path is required"):
            ChromaIndexer(chroma_path="")

    @patch("openjiuwen.core.retrieval.indexing.indexer.chroma_indexer.chromadb.PersistentClient")
    def test_init_with_whitespace_path(self, mock_client_class):
        """Test initialization with whitespace-only path"""
        with pytest.raises(BaseError, match="chroma_path is required"):
            ChromaIndexer(chroma_path="   ")

    @patch("openjiuwen.core.retrieval.indexing.indexer.chroma_indexer.chromadb.PersistentClient")
    def test_init_with_custom_fields(self, mock_client_class):
        """Test initialization with custom fields"""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        indexer = ChromaIndexer(
            chroma_path="/tmp/test_chroma",
            text_field="custom_text",
            vector_field="custom_vector",
            doc_id_field="custom_doc_id",
        )
        assert indexer.text_field == "custom_text"
        assert indexer.vector_field == "custom_vector"
        assert indexer.doc_id_field == "custom_doc_id"

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.indexing.indexer.chroma_indexer.chromadb.PersistentClient")
    @patch("openjiuwen.core.retrieval.indexing.indexer.chroma_indexer.ChromaVectorStore")
    async def test_build_index_vector_type(self, mock_store_class, mock_client_class, mock_embed_model):
        """Test building vector index"""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        mock_store = AsyncMock()
        mock_store.add = AsyncMock()
        mock_store.collection = MagicMock(get=MagicMock(return_value={}))
        mock_store_class.return_value = mock_store

        indexer = ChromaIndexer(chroma_path="/tmp/test_chroma")
        chunks = [
            TextChunk(id_="1", text="chunk 1", doc_id="doc_1"),
            TextChunk(id_="2", text="chunk 2", doc_id="doc_1"),
        ]
        config = IndexConfig(index_name="test_index", index_type="vector")
        await indexer.delete_index("doc_1", "test_index")

        result = await indexer.build_index(chunks, config, mock_embed_model)
        assert result is True
        mock_embed_model.embed_documents.assert_called_once()
        mock_store.add.assert_called_once()

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.indexing.indexer.chroma_indexer.chromadb.PersistentClient")
    @patch("openjiuwen.core.retrieval.indexing.indexer.chroma_indexer.ChromaVectorStore")
    async def test_build_index_bm25_type(self, mock_store_class, mock_client_class):
        """Test building BM25 index"""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        mock_store = AsyncMock()
        mock_store.add = AsyncMock()
        mock_store.collection = MagicMock(get=MagicMock(return_value={}))
        mock_store_class.return_value = mock_store

        indexer = ChromaIndexer(chroma_path="/tmp/test_chroma")
        chunks = [TextChunk(id_="1", text="chunk 1", doc_id="doc_1")]
        config = IndexConfig(index_name="test_index", index_type="bm25")

        result = await indexer.build_index(chunks, config)
        assert result is True
        mock_store.add.assert_called_once()

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.indexing.indexer.chroma_indexer.chromadb.PersistentClient")
    @patch("openjiuwen.core.retrieval.indexing.indexer.chroma_indexer.ChromaVectorStore")
    async def test_build_index_hybrid_type(self, mock_store_class, mock_client_class, mock_embed_model):
        """Test building hybrid index"""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        mock_store = AsyncMock()
        mock_store.add = AsyncMock()
        mock_store.collection = MagicMock(get=MagicMock(return_value={}))
        mock_store_class.return_value = mock_store

        indexer = ChromaIndexer(chroma_path="/tmp/test_chroma")
        chunks = [
            TextChunk(id_="1", text="chunk 1", doc_id="doc_1"),
            TextChunk(id_="2", text="chunk 2", doc_id="doc_1"),
        ]
        config = IndexConfig(index_name="test_index", index_type="hybrid")

        result = await indexer.build_index(chunks, config, mock_embed_model)
        assert result is True
        mock_embed_model.embed_documents.assert_called_once()
        mock_store.add.assert_called_once()

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.indexing.indexer.chroma_indexer.chromadb.PersistentClient")
    @patch("openjiuwen.core.retrieval.indexing.indexer.chroma_indexer.ChromaVectorStore")
    async def test_build_index_vector_type_without_embed_model(self, mock_store_class, mock_client_class):
        """Test vector index but without embedding model"""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        mock_store = AsyncMock()
        mock_store.add = AsyncMock()
        mock_store.collection = MagicMock(get=MagicMock(return_value={}))
        mock_store_class.return_value = mock_store

        indexer = ChromaIndexer(chroma_path="/tmp/test_chroma")
        chunks = [TextChunk(id_="1", text="chunk 1", doc_id="doc_1")]
        config = IndexConfig(index_name="test_index", index_type="vector")

        result = await indexer.build_index(chunks, config)
        assert result is False  # Should fail

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.indexing.indexer.chroma_indexer.chromadb.PersistentClient")
    async def test_build_index_exception(self, mock_client_class, mock_embed_model):
        """Test build_index with exception"""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        indexer = ChromaIndexer(chroma_path="/tmp/test_chroma")
        chunks = [TextChunk(id_="1", text="chunk 1", doc_id="doc_1")]
        config = IndexConfig(index_name="test_index", index_type="vector")

        with patch("openjiuwen.core.retrieval.indexing.indexer.chroma_indexer.ChromaVectorStore") as mock_store_class:
            mock_store_class.side_effect = Exception("ChromaDB error")
            result = await indexer.build_index(chunks, config, mock_embed_model)
            assert result is False

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.indexing.indexer.chroma_indexer.chromadb.PersistentClient")
    async def test_build_index_with_duplicate_doc_ids(self, mock_client_class):
        """Test vector index but without embedding model"""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        indexer = ChromaIndexer(chroma_path="/tmp/test_chroma")
        chunks = [TextChunk(id_="1", text="chunk 1", doc_id="doc_1")]
        config = IndexConfig(index_name="test_index", index_type="vector")

        with pytest.raises(BaseError, match="some documents with same doc_id already exist"):
            await indexer.build_index(chunks, config)

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.indexing.indexer.chroma_indexer.chromadb.PersistentClient")
    async def test_update_index(self, mock_client_class, mock_embed_model):
        """Test updating index"""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        indexer = ChromaIndexer(chroma_path="/tmp/test_chroma")
        chunks = [TextChunk(id_="1", text="updated chunk", doc_id="doc_1")]
        config = IndexConfig(index_name="test_index", index_type="vector")

        with (
            patch.object(indexer, "delete_index", new_callable=AsyncMock) as mock_delete,
            patch.object(indexer, "build_index", new_callable=AsyncMock) as mock_build,
        ):
            mock_delete.return_value = True
            mock_build.return_value = True

            result = await indexer.update_index(chunks, "doc_1", config, mock_embed_model)
            assert result is True
            mock_delete.assert_called_once_with("doc_1", "test_index")
            mock_build.assert_called_once()

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.indexing.indexer.chroma_indexer.chromadb.PersistentClient")
    async def test_update_index_exception(self, mock_client_class, mock_embed_model):
        """Test update_index with exception"""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        indexer = ChromaIndexer(chroma_path="/tmp/test_chroma")
        chunks = [TextChunk(id_="1", text="updated chunk", doc_id="doc_1")]
        config = IndexConfig(index_name="test_index", index_type="vector")

        with patch.object(indexer, "delete_index", new_callable=AsyncMock) as mock_delete:
            mock_delete.side_effect = Exception("Delete error")
            result = await indexer.update_index(chunks, "doc_1", config, mock_embed_model)
            assert result is False

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.indexing.indexer.chroma_indexer.chromadb.PersistentClient")
    @patch("openjiuwen.core.retrieval.indexing.indexer.chroma_indexer.asyncio.to_thread")
    async def test_delete_index_success(self, mock_to_thread, mock_client_class):
        """Test deleting index successfully"""
        mock_client = MagicMock()
        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "ids": ["id1", "id2"],
            "documents": ["doc1", "doc2"],
        }
        mock_collection.delete.return_value = None
        mock_client.get_collection.return_value = mock_collection
        mock_client_class.return_value = mock_client

        # Mock asyncio.to_thread
        async def mock_to_thread_impl(func, *args, **kwargs):
            return func(*args, **kwargs)

        mock_to_thread.side_effect = mock_to_thread_impl

        indexer = ChromaIndexer(chroma_path="/tmp/test_chroma")
        result = await indexer.delete_index("doc_1", "test_index")
        assert result is True
        mock_collection.delete.assert_called_once_with(ids=["id1", "id2"])

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.indexing.indexer.chroma_indexer.chromadb.PersistentClient")
    @patch("openjiuwen.core.retrieval.indexing.indexer.chroma_indexer.asyncio.to_thread")
    async def test_delete_index_not_found(self, mock_to_thread, mock_client_class):
        """Test deleting non-existent index entries"""
        mock_client = MagicMock()
        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "ids": [],
            "documents": [],
        }
        mock_client.get_collection.return_value = mock_collection
        mock_client_class.return_value = mock_client

        # Mock asyncio.to_thread
        async def mock_to_thread_impl(func, *args, **kwargs):
            return func(*args, **kwargs)

        mock_to_thread.side_effect = mock_to_thread_impl

        indexer = ChromaIndexer(chroma_path="/tmp/test_chroma")
        result = await indexer.delete_index("doc_1", "test_index")
        assert result is False

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.indexing.indexer.chroma_indexer.chromadb.PersistentClient")
    @patch("openjiuwen.core.retrieval.indexing.indexer.chroma_indexer.asyncio.to_thread")
    async def test_delete_index_no_ids_key(self, mock_to_thread, mock_client_class):
        """Test deleting when results have no ids key"""
        mock_client = MagicMock()
        mock_collection = MagicMock()
        mock_collection.get.return_value = {}
        mock_client.get_collection.return_value = mock_collection
        mock_client_class.return_value = mock_client

        # Mock asyncio.to_thread
        async def mock_to_thread_impl(func, *args, **kwargs):
            return func(*args, **kwargs)

        mock_to_thread.side_effect = mock_to_thread_impl

        indexer = ChromaIndexer(chroma_path="/tmp/test_chroma")
        result = await indexer.delete_index("doc_1", "test_index")
        assert result is False

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.indexing.indexer.chroma_indexer.chromadb.PersistentClient")
    @patch("openjiuwen.core.retrieval.indexing.indexer.chroma_indexer.asyncio.to_thread")
    async def test_delete_index_exception(self, mock_to_thread, mock_client_class):
        """Test delete_index with exception"""
        mock_client = MagicMock()
        mock_client.get_collection.side_effect = Exception("Collection not found")
        mock_client_class.return_value = mock_client

        # Mock asyncio.to_thread to raise exception
        async def mock_to_thread_impl(func, *args, **kwargs):
            return func(*args, **kwargs)

        mock_to_thread.side_effect = mock_to_thread_impl

        indexer = ChromaIndexer(chroma_path="/tmp/test_chroma")
        result = await indexer.delete_index("doc_1", "test_index")
        assert result is False

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.indexing.indexer.chroma_indexer.chromadb.PersistentClient")
    @patch("openjiuwen.core.retrieval.indexing.indexer.chroma_indexer.asyncio.to_thread")
    async def test_index_exists_true(self, mock_to_thread, mock_client_class):
        """Test index exists"""
        mock_client = MagicMock()
        mock_collection = MagicMock()
        mock_client.get_collection.return_value = mock_collection
        mock_client_class.return_value = mock_client

        # Mock asyncio.to_thread
        async def mock_to_thread_impl(func, *args, **kwargs):
            return func(*args, **kwargs)

        mock_to_thread.side_effect = mock_to_thread_impl

        indexer = ChromaIndexer(chroma_path="/tmp/test_chroma")
        result = await indexer.index_exists("test_index")
        assert result is True

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.indexing.indexer.chroma_indexer.chromadb.PersistentClient")
    async def test_index_exists_false(self, mock_client_class):
        """Test index does not exist"""
        mock_client = MagicMock()
        mock_client.get_collection.side_effect = Exception("Collection not found")
        mock_client_class.return_value = mock_client

        indexer = ChromaIndexer(chroma_path="/tmp/test_chroma")
        result = await indexer.index_exists("nonexistent_index")
        assert result is False

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.indexing.indexer.chroma_indexer.chromadb.PersistentClient")
    @patch("openjiuwen.core.retrieval.indexing.indexer.chroma_indexer.asyncio.to_thread")
    async def test_get_index_info_exists(self, mock_to_thread, mock_client_class):
        """Test getting existing index information"""
        mock_client = MagicMock()
        mock_collection = MagicMock()
        mock_collection.count.return_value = 100
        mock_collection.metadata = {"key": "value"}
        mock_client.get_collection.return_value = mock_collection
        mock_client_class.return_value = mock_client

        # Mock asyncio.to_thread
        async def mock_to_thread_impl(func, *args, **kwargs):
            return func(*args, **kwargs)

        mock_to_thread.side_effect = mock_to_thread_impl

        indexer = ChromaIndexer(chroma_path="/tmp/test_chroma")
        info = await indexer.get_index_info("test_index")
        assert info["exists"] is True
        assert info["collection_name"] == "test_index"
        assert info["count"] == 100
        assert info["metadata"] == {"key": "value"}

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.indexing.indexer.chroma_indexer.chromadb.PersistentClient")
    @patch("openjiuwen.core.retrieval.indexing.indexer.chroma_indexer.asyncio.to_thread")
    async def test_get_index_info_not_exists(self, mock_to_thread, mock_client_class):
        """Test getting non-existent index information"""
        mock_client = MagicMock()
        mock_client.get_collection.side_effect = Exception("Collection not found")
        mock_client_class.return_value = mock_client

        # Mock asyncio.to_thread to raise exception
        async def mock_to_thread_impl(func, *args, **kwargs):
            return func(*args, **kwargs)

        mock_to_thread.side_effect = mock_to_thread_impl

        indexer = ChromaIndexer(chroma_path="/tmp/test_chroma")
        info = await indexer.get_index_info("nonexistent_index")
        assert info["exists"] is False

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.indexing.indexer.chroma_indexer.chromadb.PersistentClient")
    @patch("openjiuwen.core.retrieval.indexing.indexer.chroma_indexer.asyncio.to_thread")
    async def test_get_index_info_exception(self, mock_to_thread, mock_client_class):
        """Test get_index_info with exception"""
        mock_client = MagicMock()
        mock_collection = MagicMock()
        mock_collection.count.side_effect = Exception("Count error")
        mock_client.get_collection.return_value = mock_collection
        mock_client_class.return_value = mock_client

        # Mock asyncio.to_thread
        async def mock_to_thread_impl(func, *args, **kwargs):
            return func(*args, **kwargs)

        mock_to_thread.side_effect = mock_to_thread_impl

        indexer = ChromaIndexer(chroma_path="/tmp/test_chroma")
        info = await indexer.get_index_info("test_index")
        assert info["exists"] is False
        assert "error" in info

    @patch("openjiuwen.core.retrieval.indexing.indexer.chroma_indexer.chromadb.PersistentClient")
    def test_close(self, mock_client_class):
        """Test closing index manager"""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        indexer = ChromaIndexer(chroma_path="/tmp/test_chroma")
        # Should not raise exception (ChromaDB client doesn't have close method)
        indexer.close()
