# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""
ChromaDB vector store test cases
"""

import pytest

chromadb = pytest.importorskip("chromadb", reason="chromadb not installed")

from unittest.mock import (
    MagicMock,
    patch,
)

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.retrieval import (
    ChromaVectorStore,
    VectorStoreConfig,
)


@pytest.fixture
def vector_store_config():
    """Create vector store configuration"""
    return VectorStoreConfig(
        store_provider="chroma",
        collection_name="test_collection",
        distance_metric="cosine",
    )


class TestChromaVectorStore:
    """ChromaDB vector store tests"""

    @patch("openjiuwen.core.retrieval.vector_store.chroma_store.chromadb.PersistentClient")
    def test_init_success(self, mock_client_class, vector_store_config):
        """Test successful initialization"""
        mock_client = MagicMock()
        mock_collection = MagicMock()
        mock_collection.configuration = {"hnsw": {"space": "cosine", "m": 16}}
        mock_client.get_or_create_collection = MagicMock(return_value=mock_collection)
        mock_client_class.return_value = mock_client

        store = ChromaVectorStore(
            config=vector_store_config,
            chroma_path="/tmp/test_chroma",
        )
        assert store.collection_name == "test_collection"
        assert store.chroma_path == "/tmp/test_chroma"

    @patch("openjiuwen.core.retrieval.vector_store.chroma_store.chromadb.PersistentClient")
    def test_check_vector_field_success_matching_config(self, mock_client_class, vector_store_config):
        """Test check_vector_field with matching configuration"""
        from openjiuwen.core.foundation.store.vector_fields.chroma_fields import ChromaVectorField

        mock_client = MagicMock()
        mock_collection = MagicMock()
        # Database config matches store config
        mock_collection.configuration = {
            "hnsw": {
                "space": "cosine",
                "max_neighbors": 16,
                "ef_construction": 200,
                "ef_search": 100,
            }
        }
        mock_client.get_or_create_collection = MagicMock(return_value=mock_collection)
        mock_client_class.return_value = mock_client

        vector_field = ChromaVectorField(vector_field="embedding", max_neighbors=16, ef_construction=200)
        store = ChromaVectorStore(
            config=vector_store_config,
            chroma_path="/tmp/test_chroma",
            vector_field=vector_field,
        )

        # Should not raise exception
        store.check_vector_field()

    @patch("openjiuwen.core.retrieval.vector_store.chroma_store.chromadb.PersistentClient")
    def test_check_vector_field_config_mismatch(self, mock_client_class, vector_store_config):
        """Test check_vector_field when config parameters don't match"""
        from openjiuwen.core.foundation.store.vector_fields.chroma_fields import ChromaVectorField

        mock_client = MagicMock()
        mock_collection = MagicMock()
        # Database has different max_neighbors value (16 vs 32)
        mock_collection.configuration = {
            "hnsw": {
                "space": "cosine",
                "max_neighbors": 32,
                "ef_construction": 200,
            }
        }
        mock_client.get_or_create_collection = MagicMock(return_value=mock_collection)
        mock_client_class.return_value = mock_client

        # Create store with max_neighbors=16
        vector_field = ChromaVectorField(vector_field="embedding", max_neighbors=16, ef_construction=200)
        store = ChromaVectorStore(
            config=vector_store_config,
            chroma_path="/tmp/test_chroma",
            vector_field=vector_field,
        )

        with pytest.raises(BaseError, match="database actual config differs from current knowledge base"):
            store.check_vector_field()

    @patch("openjiuwen.core.retrieval.vector_store.chroma_store.chromadb.PersistentClient")
    def test_check_vector_field_distance_metric_mismatch(self, mock_client_class):
        """Test check_vector_field when distance metric doesn't match"""
        from openjiuwen.core.foundation.store.vector_fields.chroma_fields import ChromaVectorField

        # Store configured with cosine
        vector_store_config = VectorStoreConfig(
            store_provider="chroma",
            collection_name="test_collection",
            distance_metric="cosine",
        )

        mock_client = MagicMock()
        mock_collection = MagicMock()
        # Database has euclidean (l2) distance
        mock_collection.configuration = {
            "hnsw": {
                "space": "l2",
                "max_neighbors": 16,
                "ef_construction": 200,
            }
        }
        mock_client.get_or_create_collection = MagicMock(return_value=mock_collection)
        mock_client_class.return_value = mock_client

        vector_field = ChromaVectorField(vector_field="embedding", max_neighbors=16, ef_construction=200)
        store = ChromaVectorStore(
            config=vector_store_config,
            chroma_path="/tmp/test_chroma",
            vector_field=vector_field,
        )

        with pytest.raises(BaseError, match="database actual config differs from current knowledge base"):
            store.check_vector_field()

    @patch("openjiuwen.core.retrieval.vector_store.chroma_store.chromadb.PersistentClient")
    def test_check_vector_field_ignores_ef_search_factor(self, mock_client_class, vector_store_config):
        """Test that check_vector_field ignores efSearchFactor parameter"""
        from openjiuwen.core.foundation.store.vector_fields.chroma_fields import ChromaVectorField

        mock_client = MagicMock()
        mock_collection = MagicMock()
        # Database config has efSearchFactor but store doesn't (should be ignored)
        mock_collection.configuration = {
            "hnsw": {
                "space": "cosine",
                "max_neighbors": 16,
                "ef_construction": 200,
                "ef_search": 100,
                "efSearchFactor": 2.0,
            }
        }
        mock_client.get_or_create_collection = MagicMock(return_value=mock_collection)
        mock_client_class.return_value = mock_client

        vector_field = ChromaVectorField(vector_field="embedding", max_neighbors=16, ef_construction=200)
        store = ChromaVectorStore(
            config=vector_store_config,
            chroma_path="/tmp/test_chroma",
            vector_field=vector_field,
        )

        # Should not raise exception even though efSearchFactor differs
        store.check_vector_field()

    @patch("openjiuwen.core.retrieval.vector_store.chroma_store.chromadb.PersistentClient")
    def test_check_vector_field_empty_hnsw_config(self, mock_client_class, vector_store_config):
        """Test check_vector_field when collection has empty hnsw config"""
        from openjiuwen.core.foundation.store.vector_fields.chroma_fields import ChromaVectorField

        mock_client = MagicMock()
        mock_collection = MagicMock()
        # Collection has no hnsw config
        mock_collection.configuration = {}
        mock_client.get_or_create_collection = MagicMock(return_value=mock_collection)
        mock_client_class.return_value = mock_client

        vector_field = ChromaVectorField(vector_field="embedding", max_neighbors=16, ef_construction=200)
        store = ChromaVectorStore(
            config=vector_store_config,
            chroma_path="/tmp/test_chroma",
            vector_field=vector_field,
        )

        # Should raise error because configs don't match
        with pytest.raises(BaseError, match="database actual config differs from current knowledge base"):
            store.check_vector_field()

    @patch("openjiuwen.core.retrieval.vector_store.chroma_store.chromadb.PersistentClient")
    def test_check_vector_field_partial_match(self, mock_client_class, vector_store_config):
        """Test check_vector_field with partial matching configs"""
        from openjiuwen.core.foundation.store.vector_fields.chroma_fields import ChromaVectorField

        mock_client = MagicMock()
        mock_collection = MagicMock()
        # Some params match, some don't
        mock_collection.configuration = {
            "hnsw": {
                "space": "cosine",  # Matches
                "max_neighbors": 16,  # Matches
                "ef_construction": 300,  # Doesn't match (store has 200)
            }
        }
        mock_client.get_or_create_collection = MagicMock(return_value=mock_collection)
        mock_client_class.return_value = mock_client

        vector_field = ChromaVectorField(vector_field="embedding", max_neighbors=16, ef_construction=200)
        store = ChromaVectorStore(
            config=vector_store_config,
            chroma_path="/tmp/test_chroma",
            vector_field=vector_field,
        )

        # Should raise error showing both matches and mismatches
        with pytest.raises(BaseError, match="database actual config differs from current knowledge base"):
            store.check_vector_field()
