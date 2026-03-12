# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
Milvus index manager test cases
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.retrieval import IndexConfig, MilvusIndexer, TextChunk, VectorStoreConfig


@pytest.fixture
def mock_embed_model():
    """Create mock embedding model"""
    model = AsyncMock()
    model.embed_documents = AsyncMock(return_value=[[0.1] * 384] * 2)
    model.dimension = 384
    return model


class TestMilvusIndexer:
    """Milvus index manager tests"""

    @patch("openjiuwen.core.retrieval.indexing.indexer.milvus_indexer.MilvusVectorStore.create_client")
    def test_init_success(self, mock_client_class):
        """Test successful initialization"""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        config = VectorStoreConfig(store_provider="milvus", collection_name="test_collection", database_name="name")
        indexer = MilvusIndexer(config=config, milvus_uri="http://localhost:19530")
        assert indexer.milvus_uri == "http://localhost:19530"
        assert indexer.client == mock_client
        mock_client_class.assert_called_once_with(
            database_name="name",
            path_or_uri="http://localhost:19530",
            token=None,
            alias=None,
        )

    @patch("openjiuwen.core.retrieval.indexing.indexer.milvus_indexer.MilvusVectorStore.create_client")
    def test_init_with_token(self, mock_client_class):
        """Test initialization with token"""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        config = VectorStoreConfig(store_provider="milvus", collection_name="test_collection", database_name="name")
        indexer = MilvusIndexer(
            config=config,
            milvus_uri="http://localhost:19530",
            milvus_token="test_token",
        )
        assert indexer.milvus_token == "test_token"

        mock_client_class.assert_called_once_with(
            database_name="name",
            path_or_uri="http://localhost:19530",
            token="test_token",
            alias=None,
        )

    @patch("openjiuwen.core.retrieval.indexing.indexer.milvus_indexer.MilvusVectorStore.create_client")
    def test_init_with_milvus_alias(self, mock_client_class):
        """Test initialization with milvus_alias for connection isolation"""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        config = VectorStoreConfig(store_provider="milvus", collection_name="test", database_name="db")
        indexer = MilvusIndexer(config=config, milvus_uri="http://localhost:19530", milvus_alias="idx_1_2")
        assert indexer.milvus_alias == "idx_1_2"
        mock_client_class.assert_called_once_with(
            database_name="db",
            path_or_uri="http://localhost:19530",
            token=None,
            alias="idx_1_2",
        )

    @patch("openjiuwen.core.retrieval.indexing.indexer.milvus_indexer.MilvusVectorStore.create_client")
    def test_init_with_custom_fields(self, mock_client_class):
        """Test initialization with custom fields"""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        config = VectorStoreConfig(store_provider="milvus", collection_name="test_collection")
        indexer = MilvusIndexer(
            config=config,
            milvus_uri="http://localhost:19530",
            text_field="custom_text",
            vector_field="custom_vector",
            doc_id_field="custom_doc_id",
        )
        assert indexer.text_field == "custom_text"
        assert indexer.vector_field.vector_field == "custom_vector"
        assert indexer.doc_id_field == "custom_doc_id"

    @patch("openjiuwen.core.retrieval.indexing.indexer.milvus_indexer.MilvusVectorStore.create_client")
    def test_init_with_invalid_vector_field(self, mock_client_class):
        """Test initialization with custom fields"""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        with pytest.raises(BaseError, match="vector_field must be either a str or MilvusVectorField instance"):
            config = VectorStoreConfig(store_provider="milvus", collection_name="test_collection")
            _ = MilvusIndexer(
                config=config,
                milvus_uri="http://localhost:19530",
                text_field="custom_text",
                vector_field=dict(vector_field="custom_vector"),
                doc_id_field="custom_doc_id",
            )

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.indexing.indexer.milvus_indexer.MilvusVectorStore.create_client")
    @patch("openjiuwen.core.retrieval.indexing.indexer.milvus_indexer.MilvusVectorStore")
    async def test_build_index_vector_type(self, mock_store_class, mock_client_class, mock_embed_model):
        """Test building vector index"""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        mock_store = AsyncMock()
        mock_store.add = AsyncMock()
        mock_store_class.return_value = mock_store

        config = VectorStoreConfig(store_provider="milvus", collection_name="test_collection")
        indexer = MilvusIndexer(config=config, milvus_uri="http://localhost:19530")
        chunks = [
            TextChunk(id_="1", text="chunk 1", doc_id="doc_1"),
            TextChunk(id_="2", text="chunk 2", doc_id="doc_1"),
        ]
        config = IndexConfig(index_name="test_index", index_type="vector")

        with patch.object(indexer, "_ensure_collection", new_callable=AsyncMock) as mock_ensure:
            mock_ensure.return_value = None
            result = await indexer.build_index(chunks, config, mock_embed_model)
            assert result is True
            mock_embed_model.embed_documents.assert_called_once()
            mock_store.add.assert_called_once()

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.indexing.indexer.milvus_indexer.MilvusVectorStore.create_client")
    @patch("openjiuwen.core.retrieval.indexing.indexer.milvus_indexer.MilvusVectorStore")
    async def test_build_index_bm25_type(self, mock_store_class, mock_client_class):
        """Test building BM25 index"""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        mock_store = AsyncMock()
        mock_store.add = AsyncMock()
        mock_store_class.return_value = mock_store

        config = VectorStoreConfig(store_provider="milvus", collection_name="test_collection")
        indexer = MilvusIndexer(config=config, milvus_uri="http://localhost:19530")
        chunks = [TextChunk(id_="1", text="chunk 1", doc_id="doc_1")]
        config = IndexConfig(index_name="test_index", index_type="bm25")

        with patch.object(indexer, "_ensure_collection", new_callable=AsyncMock) as mock_ensure:
            mock_ensure.return_value = None
            result = await indexer.build_index(chunks, config)
            assert result is True
            mock_store.add.assert_called_once()

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.indexing.indexer.milvus_indexer.MilvusVectorStore.create_client")
    async def test_build_index_vector_type_without_embed_model(self, mock_client_class):
        """Test vector index but without embedding model"""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        config = VectorStoreConfig(store_provider="milvus", collection_name="test_collection")
        indexer = MilvusIndexer(config=config, milvus_uri="http://localhost:19530")
        chunks = [TextChunk(id_="1", text="chunk 1", doc_id="doc_1")]
        config = IndexConfig(index_name="test_index", index_type="vector")

        with patch.object(indexer, "_ensure_collection", new_callable=AsyncMock) as mock_ensure:
            mock_ensure.return_value = None
            result = await indexer.build_index(chunks, config)
            assert result is False  # Should fail

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.indexing.indexer.milvus_indexer.MilvusVectorStore.create_client")
    async def test_build_index_with_duplicate_doc_ids(self, mock_client_class):
        """Test vector index but without embedding model"""
        mock_client = MagicMock(query=MagicMock(return_value=[dict(document_id="doc_1")]))
        mock_client_class.return_value = mock_client

        config = VectorStoreConfig(store_provider="milvus", collection_name="test_collection")
        indexer = MilvusIndexer(config=config, milvus_uri="http://localhost:19530")
        chunks = [TextChunk(id_="1", text="chunk 1", doc_id="doc_1")]
        config = IndexConfig(index_name="test_index", index_type="vector")

        with pytest.raises(BaseError, match="some documents with same doc_id already exist"):
            await indexer.build_index(chunks, config)

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.indexing.indexer.milvus_indexer.MilvusVectorStore.create_client")
    async def test_update_index(self, mock_client_class, mock_embed_model):
        """Test updating index"""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        config = VectorStoreConfig(store_provider="milvus", collection_name="test_collection")
        indexer = MilvusIndexer(config=config, milvus_uri="http://localhost:19530")
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
            mock_delete.assert_called_once()
            mock_build.assert_called_once()

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.indexing.indexer.milvus_indexer.MilvusVectorStore.create_client")
    async def test_delete_index_success(self, mock_client_class):
        """Test deleting index successfully"""
        mock_client = MagicMock()
        mock_client.delete.return_value = {"delete_count": 2}
        mock_client_class.return_value = mock_client

        config = VectorStoreConfig(store_provider="milvus", collection_name="test_collection")
        indexer = MilvusIndexer(config=config, milvus_uri="http://localhost:19530")
        result = await indexer.delete_index("doc_1", "test_index")
        assert result is True

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.indexing.indexer.milvus_indexer.MilvusVectorStore.create_client")
    async def test_delete_index_not_found(self, mock_client_class):
        """Test deleting non-existent index"""
        mock_client = MagicMock()
        mock_client.delete.return_value = {"delete_count": 0}
        mock_client_class.return_value = mock_client

        config = VectorStoreConfig(store_provider="milvus", collection_name="test_collection")
        indexer = MilvusIndexer(config=config, milvus_uri="http://localhost:19530")
        result = await indexer.delete_index("doc_1", "test_index")
        assert result is False

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.indexing.indexer.milvus_indexer.MilvusVectorStore.create_client")
    async def test_index_exists_true(self, mock_client_class):
        """Test index exists"""
        mock_client = MagicMock()
        mock_client.has_collection.return_value = True
        mock_client_class.return_value = mock_client

        config = VectorStoreConfig(store_provider="milvus", collection_name="test_collection")
        indexer = MilvusIndexer(config=config, milvus_uri="http://localhost:19530")
        result = await indexer.index_exists("test_index")
        assert result is True

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.indexing.indexer.milvus_indexer.MilvusVectorStore.create_client")
    async def test_index_exists_false(self, mock_client_class):
        """Test index does not exist"""
        mock_client = MagicMock()
        mock_client.has_collection.return_value = False
        mock_client_class.return_value = mock_client

        config = VectorStoreConfig(store_provider="milvus", collection_name="test_collection")
        indexer = MilvusIndexer(config=config, milvus_uri="http://localhost:19530")
        result = await indexer.index_exists("nonexistent_index")
        assert result is False

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.indexing.indexer.milvus_indexer.MilvusVectorStore.create_client")
    async def test_get_index_info_exists(self, mock_client_class):
        """Test getting existing index information"""
        mock_client = MagicMock()
        mock_client.has_collection.return_value = True
        mock_client.get_collection_stats.return_value = {"row_count": 100}
        mock_client_class.return_value = mock_client

        config = VectorStoreConfig(store_provider="milvus", collection_name="test_collection")
        indexer = MilvusIndexer(config=config, milvus_uri="http://localhost:19530")
        info = await indexer.get_index_info("test_index")
        assert info["exists"] is True
        assert info["collection_name"] == "test_index"
        assert "count" in info

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.indexing.indexer.milvus_indexer.MilvusVectorStore.create_client")
    async def test_get_index_info_not_exists(self, mock_client_class):
        """Test getting non-existent index information"""
        mock_client = MagicMock()
        mock_client.has_collection.return_value = False
        mock_client_class.return_value = mock_client

        config = VectorStoreConfig(store_provider="milvus", collection_name="test_collection")
        indexer = MilvusIndexer(config=config, milvus_uri="http://localhost:19530")
        info = await indexer.get_index_info("nonexistent_index")
        assert info["exists"] is False

    @patch("openjiuwen.core.retrieval.indexing.indexer.milvus_indexer.MilvusVectorStore.create_client")
    def test_close(self, mock_client_class):
        """Test closing index manager"""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        config = VectorStoreConfig(store_provider="milvus", collection_name="test_collection")
        indexer = MilvusIndexer(config=config, milvus_uri="http://localhost:19530")
        # Should not raise exception
        indexer.close()
