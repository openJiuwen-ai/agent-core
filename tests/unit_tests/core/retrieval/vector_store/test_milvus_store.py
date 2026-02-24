# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""
Milvus vector store test cases
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.retrieval import MilvusVectorStore, SearchResult, VectorStoreConfig


@pytest.fixture
def vector_store_config():
    """Create vector store configuration"""
    return VectorStoreConfig(
        store_provider="milvus",
        collection_name="test_collection",
        distance_metric="cosine",
    )


class TestMilvusVectorStore:
    """Milvus vector store tests"""

    @patch("openjiuwen.core.retrieval.vector_store.milvus_store.MilvusClient")
    def test_init_success(self, mock_client_class, vector_store_config):
        """Test successful initialization"""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        store = MilvusVectorStore(
            config=vector_store_config,
            milvus_uri="http://localhost:19530",
        )
        assert store.collection_name == "test_collection"
        assert store.milvus_uri == "http://localhost:19530"
        assert store.client == mock_client
        mock_client_class.assert_called_once_with(
            uri="http://localhost:19530",
            token=None,
        )

    @patch("openjiuwen.core.retrieval.vector_store.milvus_store.MilvusClient")
    def test_init_with_token(self, mock_client_class, vector_store_config):
        """Test initialization with token"""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        store = MilvusVectorStore(
            config=vector_store_config,
            milvus_uri="http://localhost:19530",
            milvus_token="test_token",
        )
        assert store.milvus_token == "test_token"
        mock_client_class.assert_called_once_with(
            uri="http://localhost:19530",
            token="test_token",
        )

    @patch("openjiuwen.core.retrieval.vector_store.milvus_store.MilvusClient")
    def test_init_with_custom_fields(self, mock_client_class, vector_store_config):
        """Test initialization with custom fields"""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        store = MilvusVectorStore(
            config=vector_store_config,
            milvus_uri="http://localhost:19530",
            text_field="custom_text",
            vector_field="custom_vector",
            doc_id_field="custom_doc_id",
        )
        assert store.text_field == "custom_text"
        assert store.vector_field.vector_field == "custom_vector"
        assert store.doc_id_field == "custom_doc_id"

    @patch("openjiuwen.core.retrieval.vector_store.milvus_store.MilvusClient")
    def test_init_with_invalid_vector_field(self, mock_client_class, vector_store_config):
        """Test initialization with custom fields"""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        with pytest.raises(BaseError, match="vector_field must be either a str or MilvusVectorField instance"):
            _ = MilvusVectorStore(
                config=vector_store_config,
                milvus_uri="http://localhost:19530",
                text_field="custom_text",
                vector_field=dict(vector_field="custom_vector"),
                doc_id_field="custom_doc_id",
            )

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.vector_store.milvus_store.MilvusClient")
    async def test_add_single_dict(self, mock_client_class, vector_store_config):
        """Test adding single dictionary"""
        mock_client = MagicMock()
        mock_client.insert = MagicMock()
        mock_client.flush = MagicMock()
        mock_client_class.return_value = mock_client

        store = MilvusVectorStore(
            config=vector_store_config,
            milvus_uri="http://localhost:19530",
        )

        data = {
            "id": "1",
            "embedding": [0.1] * 384,
            "content": "Test content",
        }

        await store.add(data)
        mock_client.insert.assert_called()
        mock_client.flush.assert_called_once()

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.vector_store.milvus_store.MilvusClient")
    async def test_add_list_of_dicts(self, mock_client_class, vector_store_config):
        """Test adding list of dictionaries"""
        mock_client = MagicMock()
        mock_client.insert = MagicMock()
        mock_client.flush = MagicMock()
        mock_client_class.return_value = mock_client

        store = MilvusVectorStore(
            config=vector_store_config,
            milvus_uri="http://localhost:19530",
        )

        data = [
            {"id": "1", "embedding": [0.1] * 384, "content": "Content 1"},
            {"id": "2", "embedding": [0.2] * 384, "content": "Content 2"},
        ]

        await store.add(data)
        mock_client.insert.assert_called()
        mock_client.flush.assert_called_once()

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.vector_store.milvus_store.MilvusClient")
    async def test_add_with_batching(self, mock_client_class, vector_store_config):
        """Test batch adding"""
        mock_client = MagicMock()
        mock_client.insert = MagicMock()
        mock_client.flush = MagicMock()
        mock_client_class.return_value = mock_client

        store = MilvusVectorStore(
            config=vector_store_config,
            milvus_uri="http://localhost:19530",
        )

        # Create data exceeding batch_size
        data = [{"id": str(i), "embedding": [0.1] * 384, "content": f"Content {i}"} for i in range(200)]

        await store.add(data, batch_size=50)
        # Should insert in multiple batches
        assert mock_client.insert.call_count > 1
        mock_client.flush.assert_called_once()

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.vector_store.milvus_store.MilvusClient")
    async def test_search_success(self, mock_client_class, vector_store_config):
        """Test successful vector search"""
        mock_client = MagicMock()
        mock_client.search = MagicMock(
            return_value=[
                [
                    {
                        "id": "1",
                        "content": "Test content",
                        "metadata": {"source": "test"},
                        "document_id": "doc_1",
                        "score": 0.9,
                    }
                ]
            ]
        )
        mock_client_class.return_value = mock_client

        store = MilvusVectorStore(
            config=vector_store_config,
            milvus_uri="http://localhost:19530",
        )

        results = await store.search([0.1] * 384, top_k=5)
        assert len(results) == 1
        assert results[0].text == "Test content"
        assert results[0].score > 0

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.vector_store.milvus_store.MilvusClient")
    async def test_search_with_filters(self, mock_client_class, vector_store_config):
        """Test search with filters"""
        mock_client = MagicMock()
        mock_client.search = MagicMock(return_value=[[]])
        mock_client_class.return_value = mock_client

        store = MilvusVectorStore(
            config=vector_store_config,
            milvus_uri="http://localhost:19530",
        )

        filters = {"source": "test"}
        _ = await store.search([0.1] * 384, top_k=5, filters=filters)
        # Verify filters were passed
        call_kwargs = mock_client.search.call_args[1]
        assert "filter" in call_kwargs

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.vector_store.milvus_store.MilvusClient")
    async def test_search_empty_results(self, mock_client_class, vector_store_config):
        """Test search returning empty results"""
        mock_client = MagicMock()
        mock_client.search = MagicMock(return_value=[])
        mock_client_class.return_value = mock_client

        store = MilvusVectorStore(
            config=vector_store_config,
            milvus_uri="http://localhost:19530",
        )

        results = await store.search([0.1] * 384, top_k=5)
        assert results == []

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.vector_store.milvus_store.MilvusClient")
    async def test_sparse_search_success(self, mock_client_class, vector_store_config):
        """Test successful sparse search"""
        mock_client = MagicMock()
        mock_client.search = MagicMock(
            return_value=[
                [
                    {
                        "id": "1",
                        "content": "Test content",
                        "metadata": {},
                        "score": 0.8,
                    }
                ]
            ]
        )
        mock_client_class.return_value = mock_client

        store = MilvusVectorStore(
            config=vector_store_config,
            milvus_uri="http://localhost:19530",
        )

        results = await store.sparse_search("test query", top_k=5)
        assert len(results) == 1
        assert results[0].text == "Test content"

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.vector_store.milvus_store.MilvusClient")
    async def test_sparse_search_with_filters(self, mock_client_class, vector_store_config):
        """Test sparse search with filters"""
        mock_client = MagicMock()
        mock_client.search = MagicMock(return_value=[[]])
        mock_client_class.return_value = mock_client

        store = MilvusVectorStore(
            config=vector_store_config,
            milvus_uri="http://localhost:19530",
        )

        filters = {"source": "test"}
        _ = await store.sparse_search("test query", top_k=5, filters=filters)
        # Verify BM25 search was used
        call_kwargs = mock_client.search.call_args[1]
        assert call_kwargs["search_params"]["metric_type"] == "BM25"

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.vector_store.milvus_store.MilvusClient")
    async def test_sparse_search_failure(self, mock_client_class, vector_store_config):
        """Test sparse search failure"""
        mock_client = MagicMock()
        mock_client.search = MagicMock(side_effect=Exception("Search error"))
        mock_client_class.return_value = mock_client

        store = MilvusVectorStore(
            config=vector_store_config,
            milvus_uri="http://localhost:19530",
        )

        results = await store.sparse_search("test query", top_k=5)
        assert results == []

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.vector_store.milvus_store.MilvusClient")
    @patch("openjiuwen.core.retrieval.vector_store.milvus_store.AnnSearchRequest")
    @patch("openjiuwen.core.retrieval.vector_store.milvus_store.RRFRanker")
    async def test_hybrid_search_success(
        self, mock_ranker_class, mock_ann_request_class, mock_client_class, vector_store_config
    ):
        """Test successful hybrid search"""
        mock_client = MagicMock()
        mock_client.hybrid_search = MagicMock(
            return_value=[
                [
                    {
                        "id": "1",
                        "content": "Test content",
                        "metadata": {},
                        "score": 0.9,
                    }
                ]
            ]
        )
        mock_client_class.return_value = mock_client

        mock_ann_request = MagicMock()
        mock_ann_request_class.return_value = mock_ann_request
        mock_ranker = MagicMock()
        mock_ranker_class.return_value = mock_ranker

        store = MilvusVectorStore(
            config=vector_store_config,
            milvus_uri="http://localhost:19530",
        )

        results = await store.hybrid_search(
            query_text="test",
            query_vector=[0.1] * 384,
            top_k=5,
            alpha=0.5,
        )
        assert len(results) == 1
        mock_client.hybrid_search.assert_called_once()

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.vector_store.milvus_store.MilvusClient")
    async def test_hybrid_search_without_vector(self, mock_client_class, vector_store_config):
        """Test hybrid search (without vector)"""
        mock_client = MagicMock()
        mock_client.hybrid_search = MagicMock(return_value=[[]])
        mock_client_class.return_value = mock_client

        store = MilvusVectorStore(
            config=vector_store_config,
            milvus_uri="http://localhost:19530",
        )

        results = await store.hybrid_search(
            query_text="test",
            query_vector=None,
            top_k=5,
        )
        # Should only create sparse search request
        assert isinstance(results, list)

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.vector_store.milvus_store.MilvusClient")
    async def test_hybrid_search_fallback(self, mock_client_class, vector_store_config):
        """Test hybrid search fallback"""
        mock_client = MagicMock()
        mock_client.hybrid_search = MagicMock(side_effect=Exception("Hybrid search error"))
        mock_client_class.return_value = mock_client

        store = MilvusVectorStore(
            config=vector_store_config,
            milvus_uri="http://localhost:19530",
        )

        with (
            patch.object(store, "search", new_callable=AsyncMock) as mock_search,
            patch.object(store, "sparse_search", new_callable=AsyncMock) as mock_sparse,
        ):
            mock_search.return_value = [
                SearchResult(id="1", text="Vector result", score=0.9, metadata={}),
            ]
            mock_sparse.return_value = [
                SearchResult(id="2", text="Sparse result", score=0.8, metadata={}),
            ]

            results = await store.hybrid_search(
                query_text="test",
                query_vector=[0.1] * 384,
                top_k=5,
            )
            # Should use fallback method
            assert len(results) >= 0
            mock_search.assert_called_once()
            mock_sparse.assert_called_once()

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.vector_store.milvus_store.MilvusClient")
    async def test_delete_by_ids(self, mock_client_class, vector_store_config):
        """Test deletion by IDs"""
        mock_client = MagicMock()
        mock_client.delete = MagicMock(return_value={"delete_count": 2})
        mock_client.flush = MagicMock()
        mock_client_class.return_value = mock_client

        store = MilvusVectorStore(
            config=vector_store_config,
            milvus_uri="http://localhost:19530",
        )

        result = await store.delete(ids=["1", "2"])
        assert result is True
        mock_client.delete.assert_called_once()
        mock_client.flush.assert_called_once()

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.vector_store.milvus_store.MilvusClient")
    async def test_delete_by_filter_expr(self, mock_client_class, vector_store_config):
        """Test deletion by filter expression"""
        mock_client = MagicMock()
        mock_client.delete = MagicMock(return_value={"delete_count": 1})
        mock_client.flush = MagicMock()
        mock_client_class.return_value = mock_client

        store = MilvusVectorStore(
            config=vector_store_config,
            milvus_uri="http://localhost:19530",
        )

        result = await store.delete(filter_expr="source == 'test'")
        assert result is True
        mock_client.delete.assert_called_once()

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.vector_store.milvus_store.MilvusClient")
    async def test_delete_no_results(self, mock_client_class, vector_store_config):
        """Test deletion with no results"""
        mock_client = MagicMock()
        mock_client.delete = MagicMock(return_value={"delete_count": 0})
        mock_client.flush = MagicMock()
        mock_client_class.return_value = mock_client

        store = MilvusVectorStore(
            config=vector_store_config,
            milvus_uri="http://localhost:19530",
        )

        result = await store.delete(ids=["1"])
        assert result is False

    @pytest.mark.asyncio
    @patch("openjiuwen.core.retrieval.vector_store.milvus_store.MilvusClient")
    async def test_delete_with_exception(self, mock_client_class, vector_store_config):
        """Test deletion with exception"""
        mock_client = MagicMock()
        mock_client.delete = MagicMock(side_effect=Exception("Delete error"))
        mock_client_class.return_value = mock_client

        store = MilvusVectorStore(
            config=vector_store_config,
            milvus_uri="http://localhost:19530",
        )

        result = await store.delete(ids=["1"])
        assert result is False

    @patch("openjiuwen.core.retrieval.vector_store.milvus_store.MilvusClient")
    def test_close(self, mock_client_class, vector_store_config):
        """Test closing vector store"""
        mock_client = MagicMock()
        mock_client.close = MagicMock()
        mock_client_class.return_value = mock_client

        store = MilvusVectorStore(
            config=vector_store_config,
            milvus_uri="http://localhost:19530",
        )

        store.close()
        mock_client.close.assert_called_once()

    @patch("openjiuwen.core.retrieval.vector_store.milvus_store.MilvusClient")
    def test_close_with_exception(self, mock_client_class, vector_store_config):
        """Test closing with exception"""
        mock_client = MagicMock()
        mock_client.close = MagicMock(side_effect=Exception("Close error"))
        mock_client_class.return_value = mock_client

        store = MilvusVectorStore(
            config=vector_store_config,
            milvus_uri="http://localhost:19530",
        )

        # Should catch exception, not raise
        store.close()

    @patch("openjiuwen.core.retrieval.vector_store.milvus_store.MilvusClient")
    def test_check_vector_field_collection_not_exists(self, mock_client_class, vector_store_config):
        """Test check_vector_field when collection doesn't exist"""
        mock_client = MagicMock()
        mock_client.has_collection = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client

        store = MilvusVectorStore(
            config=vector_store_config,
            milvus_uri="http://localhost:19530",
        )

        # Should return early without error
        store.check_vector_field()
        mock_client.has_collection.assert_called_once_with("test_collection")

    @patch("openjiuwen.core.retrieval.vector_store.milvus_store.MilvusClient")
    def test_check_vector_field_vector_field_not_found(self, mock_client_class, vector_store_config):
        """Test check_vector_field when vector field doesn't exist in database"""
        from pymilvus import DataType

        mock_client = MagicMock()
        mock_client.has_collection = MagicMock(return_value=True)
        mock_client.describe_index = MagicMock(return_value=None)
        mock_client.describe_collection = MagicMock(
            return_value={
                "fields": [{"field_id": 1, "name": "other_vector", "type": DataType.FLOAT_VECTOR, "params": {}}]
            }
        )
        mock_client_class.return_value = mock_client

        store = MilvusVectorStore(
            config=vector_store_config,
            milvus_uri="http://localhost:19530",
        )

        with pytest.raises(BaseError, match="MilvusVectorStore has vector_field at embedding"):
            store.check_vector_field()

    @patch("openjiuwen.core.retrieval.vector_store.milvus_store.MilvusClient")
    def test_check_vector_field_index_type_mismatch(self, mock_client_class, vector_store_config):
        """Test check_vector_field when index type doesn't match"""
        from openjiuwen.core.foundation.store.vector_fields.milvus_fields import MilvusHNSW

        mock_client = MagicMock()
        mock_client.has_collection = MagicMock(return_value=True)
        mock_client.describe_index = MagicMock(
            return_value={
                "index_type": "IVF_FLAT",
                "metric_type": "COSINE",
                "params": {"nlist": 128},
            }
        )
        mock_client_class.return_value = mock_client

        # Create store with HNSW index type
        vector_field = MilvusHNSW(vector_field="embedding", M=16, efConstruction=200)
        store = MilvusVectorStore(
            config=vector_store_config,
            milvus_uri="http://localhost:19530",
            vector_field=vector_field,
        )

        with pytest.raises(BaseError, match="MilvusVectorStore has index_type of hnsw"):
            store.check_vector_field()

    @patch("openjiuwen.core.retrieval.vector_store.milvus_store.MilvusClient")
    def test_check_vector_field_config_mismatch(self, mock_client_class, vector_store_config):
        """Test check_vector_field when config parameters don't match"""
        from openjiuwen.core.foundation.store.vector_fields.milvus_fields import MilvusHNSW

        mock_client = MagicMock()
        mock_client.has_collection = MagicMock(return_value=True)
        # Database has different m value (16 vs 32)
        mock_client.describe_index = MagicMock(
            return_value={
                "index_type": "HNSW",
                "metric_type": "COSINE",
                "params": {"M": 32, "efConstruction": 200},
            }
        )
        mock_client_class.return_value = mock_client

        # Create store with m=16
        vector_field = MilvusHNSW(vector_field="embedding", M=16, efConstruction=200)
        store = MilvusVectorStore(
            config=vector_store_config,
            milvus_uri="http://localhost:19530",
            vector_field=vector_field,
        )

        with pytest.raises(BaseError, match="database actual config differs from current knowledge base"):
            store.check_vector_field()

    @patch("openjiuwen.core.retrieval.vector_store.milvus_store.MilvusClient")
    def test_check_vector_field_success_matching_config(self, mock_client_class, vector_store_config):
        """Test check_vector_field with matching configuration"""
        from openjiuwen.core.foundation.store.vector_fields.milvus_fields import MilvusHNSW

        mock_client = MagicMock()
        mock_client.has_collection = MagicMock(return_value=True)
        # Database config matches store config
        mock_client.describe_index = MagicMock(
            return_value={
                "index_type": "HNSW",
                "metric_type": "COSINE",
                "M": 16,
                "efConstruction": 200,
            }
        )
        mock_client_class.return_value = mock_client

        vector_field = MilvusHNSW(vector_field="embedding", M=16, efConstruction=200)
        store = MilvusVectorStore(
            config=vector_store_config,
            milvus_uri="http://localhost:19530",
            vector_field=vector_field,
        )

        # Should not raise exception
        store.check_vector_field()

    @patch("openjiuwen.core.retrieval.vector_store.milvus_store.MilvusClient")
    def test_check_vector_field_auto_index_type(self, mock_client_class, vector_store_config):
        """Test check_vector_field with auto index type (should skip index type check)"""
        mock_client = MagicMock()
        mock_client.has_collection = MagicMock(return_value=True)
        mock_client.describe_index = MagicMock(
            return_value={
                "index_type": "AUTOINDEX",
                "metric_type": "COSINE",
                "params": {},
            }
        )
        mock_client_class.return_value = mock_client

        # Store with auto index type
        store = MilvusVectorStore(
            config=vector_store_config,
            milvus_uri="http://localhost:19530",
            vector_field="embedding",  # Default is auto
        )

        # Should not raise exception even if index type is AUTOINDEX
        store.check_vector_field()

    @patch("openjiuwen.core.retrieval.vector_store.milvus_store.MilvusClient")
    def test_check_vector_field_ignores_ef_search_factor(self, mock_client_class, vector_store_config):
        """Test that check_vector_field ignores efSearchFactor parameter"""
        from openjiuwen.core.foundation.store.vector_fields.milvus_fields import MilvusHNSW

        mock_client = MagicMock()
        mock_client.has_collection = MagicMock(return_value=True)
        # Database config has efSearchFactor but store doesn't (should be ignored)
        mock_client.describe_index = MagicMock(
            return_value={
                "index_type": "HNSW",
                "metric_type": "COSINE",
                "M": 16,
                "efConstruction": 200,
                "efSearchFactor": 2.0,
            }
        )
        mock_client_class.return_value = mock_client

        vector_field = MilvusHNSW(vector_field="embedding", M=16, efConstruction=200)
        store = MilvusVectorStore(
            config=vector_store_config,
            milvus_uri="http://localhost:19530",
            vector_field=vector_field,
        )

        # Should not raise exception even though efSearchFactor differs
        store.check_vector_field()
