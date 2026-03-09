# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Vector store factory (create_vector_store) test cases
"""

from unittest.mock import MagicMock, patch

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.retrieval import VectorStoreConfig, create_vector_store
from openjiuwen.core.retrieval.common.config import StoreType


@pytest.fixture
def milvus_config():
    """Create Milvus vector store configuration"""
    return VectorStoreConfig(
        store_provider=StoreType.Milvus,
        collection_name="test_collection",
        distance_metric="cosine",
    )


@pytest.fixture
def chroma_config():
    """Create Chroma vector store configuration"""
    return VectorStoreConfig(
        store_provider=StoreType.Chroma,
        collection_name="test_collection",
        distance_metric="cosine",
    )


@pytest.fixture
def pgvector_config():
    """Create PGVector vector store configuration"""
    return VectorStoreConfig(
        store_provider=StoreType.PGVector,
        collection_name="test_collection",
        distance_metric="cosine",
    )


class TestCreateVectorStore:
    """Tests for the create_vector_store factory function"""

    @patch("openjiuwen.core.retrieval.vector_store.milvus_store.MilvusClient")
    def test_create_milvus_store(self, mock_client_class, milvus_config):
        """Test that create_vector_store returns a MilvusVectorStore for Milvus config"""
        from openjiuwen.core.retrieval import MilvusVectorStore

        mock_client_class.return_value = MagicMock()

        store = create_vector_store(milvus_config, milvus_uri="http://localhost:19530")

        assert isinstance(store, MilvusVectorStore)

    @patch("openjiuwen.core.retrieval.vector_store.chroma_store.chromadb")
    def test_create_chroma_store(self, mock_chromadb, chroma_config):
        """Test that create_vector_store returns a ChromaVectorStore for Chroma config"""
        chromadb = pytest.importorskip("chromadb", reason="chromadb not installed")

        from openjiuwen.core.retrieval import ChromaVectorStore

        mock_client = MagicMock()
        mock_collection = MagicMock()
        mock_collection.configuration = {"hnsw": {"space": "cosine", "m": 16}}
        mock_client.get_or_create_collection.return_value = mock_collection
        mock_chromadb.PersistentClient.return_value = mock_client

        store = create_vector_store(chroma_config, chroma_path="/tmp/test_chroma")

        assert isinstance(store, ChromaVectorStore)

    @patch("openjiuwen.core.retrieval.vector_store.pg_store.create_async_engine")
    def test_create_pgvector_store(self, mock_engine, pgvector_config):
        """Test that create_vector_store returns a PGVectorStore for PGVector config"""
        pytest.importorskip("pgvector", reason="pgvector not installed")

        from openjiuwen.core.retrieval.vector_store.pg_store import PGVectorStore

        store = create_vector_store(pgvector_config, pg_uri="postgresql+asyncpg://user:pass@localhost/testdb")

        assert isinstance(store, PGVectorStore)

    @staticmethod
    def test_create_milvus_store_by_string_provider():
        """Test that create_vector_store accepts string provider value"""
        from openjiuwen.core.retrieval import MilvusVectorStore

        config = VectorStoreConfig(
            store_provider="milvus",
            collection_name="test_collection",
        )
        with patch("openjiuwen.core.retrieval.vector_store.milvus_store.MilvusClient") as mock_client_class:
            mock_client_class.return_value = MagicMock()
            store = create_vector_store(config, milvus_uri="http://localhost:19530")
            assert isinstance(store, MilvusVectorStore)

    @staticmethod
    def test_create_vector_store_invalid_provider():
        """Test that create_vector_store raises BaseError for an unsupported provider"""
        config = VectorStoreConfig(
            store_provider=StoreType.Milvus,
            collection_name="test_collection",
        )
        # Force an unsupported provider value by bypassing enum validation
        config.store_provider = "unsupported_provider"

        with pytest.raises(BaseError):
            create_vector_store(config)

    @patch("openjiuwen.core.retrieval.vector_store.milvus_store.MilvusClient")
    def test_create_vector_store_passes_kwargs_to_store(self, mock_client_class, milvus_config):
        """Test that extra kwargs are forwarded to the store constructor"""
        mock_client_class.return_value = MagicMock()

        store = create_vector_store(
            milvus_config,
            milvus_uri="http://localhost:19530",
            milvus_token="secret_token",
        )

        assert store.milvus_token == "secret_token"

    @patch("openjiuwen.core.retrieval.vector_store.milvus_store.MilvusClient")
    def test_create_milvus_store_collection_name_preserved(self, mock_client_class, milvus_config):
        """Test that collection name from config is preserved in created store"""
        mock_client_class.return_value = MagicMock()

        store = create_vector_store(milvus_config, milvus_uri="http://localhost:19530")

        assert store.collection_name == "test_collection"
