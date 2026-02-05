# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for MilvusVectorStore."""

import json
from unittest.mock import MagicMock, patch

import pytest
from pymilvus import DataType as MilvusDataType

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.store.base_vector_store import (
    CollectionSchema,
    VectorDataType,
    FieldSchema,
)
from openjiuwen.core.foundation.store.vector.milvus_vector_store import MilvusVectorStore


class TestMilvusVectorStoreInit:
    """Tests for MilvusVectorStore initialization."""

    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.MilvusClient")
    def test_init_with_default_database(self, mock_milvus_client):
        """Test initialization with default database."""
        mock_client = MagicMock()
        mock_client.list_databases.return_value = ["default"]
        mock_milvus_client.return_value = mock_client

        store = MilvusVectorStore(milvus_uri="http://testhost:testport")

        # Access client property to trigger lazy initialization
        _ = store.client

        mock_milvus_client.assert_called_once_with(uri="http://testhost:testport", token="")
        assert store.milvus_uri == "http://testhost:testport"
        assert store.milvus_token is None
        assert store.database_name == "default"

    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.MilvusClient")
    def test_init_with_token(self, mock_milvus_client):
        """Test initialization with authentication token."""
        mock_client = MagicMock()
        mock_client.list_databases.return_value = ["default"]
        mock_milvus_client.return_value = mock_client

        store = MilvusVectorStore(
            milvus_uri="http://testhost:testport",
            milvus_token="test_token",
        )

        # Access client property to trigger lazy initialization
        _ = store.client

        mock_milvus_client.assert_called_once_with(uri="http://testhost:testport", token="test_token")
        assert store.milvus_token == "test_token"

    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.MilvusClient")
    def test_init_with_custom_database(self, mock_milvus_client):
        """Test initialization with custom database name."""
        mock_client = MagicMock()
        mock_client.list_databases.return_value = ["default", "custom_db"]
        mock_milvus_client.return_value = mock_client

        store = MilvusVectorStore(
            milvus_uri="http://testhost:testport",
            database_name="custom_db",
        )

        # Access client property to trigger lazy initialization
        _ = store.client

        # Database already exists, so create_database should NOT be called
        mock_client.create_database.assert_not_called()
        mock_client.use_database.assert_called_once_with("custom_db")
        assert store.database_name == "custom_db"

    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.MilvusClient")
    def test_init_with_new_database(self, mock_milvus_client):
        """Test initialization creates new database if it doesn't exist."""
        mock_client = MagicMock()
        mock_client.list_databases.return_value = ["default"]
        mock_milvus_client.return_value = mock_client

        store = MilvusVectorStore(
            milvus_uri="http://testhost:testport",
            database_name="new_db",
        )

        # Access client property to trigger lazy initialization
        _ = store.client

        mock_client.create_database.assert_called_once_with("new_db")
        mock_client.use_database.assert_called_once_with("new_db")

    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.MilvusClient")
    def test_lazy_init_no_connection_on_init(self, mock_milvus_client):
        """Test that client is NOT created during __init__ with lazy initialization."""
        mock_milvus_client.return_value = MagicMock()

        store = MilvusVectorStore(milvus_uri="http://testhost:testport")

        # Client should be created when accessed
        _ = store.client
        mock_milvus_client.assert_called_once()

    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.MilvusClient")
    def test_client_reuse(self, mock_milvus_client):
        """Test that the same client instance is reused for multiple accesses."""
        mock_client_instance = MagicMock()
        mock_client_instance.list_databases.return_value = ["default"]
        mock_milvus_client.return_value = mock_client_instance

        store = MilvusVectorStore(milvus_uri="http://testhost:testport")

        # Access client multiple times
        client1 = store.client
        client2 = store.client

        # Should return the same instance
        assert client1 is client2
        # MilvusClient should only be created once
        mock_milvus_client.assert_called_once()

    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.MilvusClient")
    def test_close_and_reconnect(self, mock_milvus_client):
        """Test that close() releases the client and it can be recreated."""
        # Return different instances for each call
        mock_client_instance1 = MagicMock()
        mock_client_instance1.list_databases.return_value = ["default"]
        mock_client_instance2 = MagicMock()
        mock_client_instance2.list_databases.return_value = ["default"]
        mock_milvus_client.side_effect = [mock_client_instance1, mock_client_instance2]

        store = MilvusVectorStore(milvus_uri="http://testhost:testport")

        # First access creates the client
        client1 = store.client
        assert mock_milvus_client.call_count == 1
        assert client1 is mock_client_instance1

        # Close releases the client
        store.close()

        # Next access creates a new client
        client2 = store.client
        assert mock_milvus_client.call_count == 2
        assert client2 is mock_client_instance2
        assert client1 is not client2


class TestMilvusVectorStoreCreateCollection:
    """Tests for create_collection method."""

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.MilvusClient")
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.asyncio.to_thread")
    async def test_create_collection_with_schema_object(self, mock_to_thread, mock_milvus_client):
        """Test creating a collection with a CollectionSchema object."""
        mock_client = MagicMock()
        mock_client.list_databases.return_value = ["default"]
        mock_milvus_client.return_value = mock_client
        mock_to_thread.return_value = None

        store = MilvusVectorStore(milvus_uri="http://testhost:testport")

        schema = CollectionSchema(
            description="Test collection",
            enable_dynamic_field=False,
        )
        schema.add_field(
            FieldSchema(name="id", dtype=VectorDataType.VARCHAR, max_length=256, is_primary=True)
        )
        schema.add_field(FieldSchema(name="embedding", dtype=VectorDataType.FLOAT_VECTOR, dim=768))
        schema.add_field(FieldSchema(name="text", dtype=VectorDataType.VARCHAR, max_length=65535))

        await store.create_collection("test_collection", schema)

        # Calls to_thread twice: once for has_collection check, once for create
        assert mock_to_thread.call_count == 2

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.MilvusClient")
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.asyncio.to_thread")
    async def test_create_collection_with_dict_schema(self, mock_to_thread, mock_milvus_client):
        """Test creating a collection with a schema dictionary."""
        mock_client = MagicMock()
        mock_client.list_databases.return_value = ["default"]
        mock_milvus_client.return_value = mock_client
        mock_to_thread.return_value = None

        store = MilvusVectorStore(milvus_uri="http://testhost:testport")

        schema_dict = {
            "fields": [
                {
                    "name": "id",
                    "type": "VARCHAR",
                    "max_length": 256,
                    "is_primary": True,
                },
                {
                    "name": "embedding",
                    "type": "FLOAT_VECTOR",
                    "dim": 768,
                },
                {
                    "name": "text",
                    "type": "VARCHAR",
                    "max_length": 65535,
                },
            ],
            "description": "Test collection",
            "enable_dynamic_field": False,
        }

        await store.create_collection("test_collection", schema_dict)

        # Calls to_thread twice: once for has_collection check, once for create
        assert mock_to_thread.call_count == 2

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.MilvusClient")
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.asyncio.to_thread")
    async def test_create_collection_with_custom_metric(self, mock_to_thread, mock_milvus_client):
        """Test creating a collection with custom distance metric."""
        mock_client = MagicMock()
        mock_client.list_databases.return_value = ["default"]
        mock_milvus_client.return_value = mock_client
        mock_to_thread.return_value = None

        store = MilvusVectorStore(milvus_uri="http://testhost:testport")

        schema = CollectionSchema()
        schema.add_field(
            FieldSchema(name="id", dtype=VectorDataType.VARCHAR, max_length=256, is_primary=True)
        )
        schema.add_field(FieldSchema(name="embedding", dtype=VectorDataType.FLOAT_VECTOR, dim=768))

        await store.create_collection("test_collection", schema, distance_metric="L2")

        # Calls to_thread twice: once for has_collection check, once for create
        assert mock_to_thread.call_count == 2

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.MilvusClient")
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.asyncio.to_thread")
    async def test_create_collection_with_custom_index_type(self, mock_to_thread, mock_milvus_client):
        """Test creating a collection with custom index type."""
        mock_client = MagicMock()
        mock_client.list_databases.return_value = ["default"]
        mock_milvus_client.return_value = mock_client
        mock_to_thread.return_value = None

        store = MilvusVectorStore(milvus_uri="http://testhost:testport")

        schema = CollectionSchema()
        schema.add_field(
            FieldSchema(name="id", dtype=VectorDataType.VARCHAR, max_length=256, is_primary=True)
        )
        schema.add_field(FieldSchema(name="embedding", dtype=VectorDataType.FLOAT_VECTOR, dim=768))

        await store.create_collection("test_collection", schema, index_type="HNSW")

        # Calls to_thread twice: once for has_collection check, once for create
        assert mock_to_thread.call_count == 2

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.MilvusClient")
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.asyncio.to_thread")
    async def test_create_collection_already_exists(self, mock_to_thread, mock_milvus_client):
        """Test creating a collection that already exists does nothing."""
        mock_client = MagicMock()
        mock_client.list_databases.return_value = ["default"]
        mock_milvus_client.return_value = mock_client

        # First call to has_collection returns True (collection exists)
        call_count = [0]

        def _side_effect(func, *args, **kwargs):
            call_count[0] += 1
            # First call is has_collection check
            if call_count[0] == 1:
                return True  # Collection exists
            return None

        mock_to_thread.side_effect = _side_effect

        store = MilvusVectorStore(milvus_uri="http://testhost:testport")

        schema = CollectionSchema()
        schema.add_field(
            FieldSchema(name="id", dtype=VectorDataType.VARCHAR, max_length=256, is_primary=True)
        )
        schema.add_field(FieldSchema(name="embedding", dtype=VectorDataType.FLOAT_VECTOR, dim=768))

        await store.create_collection("test_collection", schema)

        # Should only call to_thread once for has_collection check, not for create_collection
        assert call_count[0] == 1
        # create_collection should not be called
        mock_client.create_collection.assert_not_called()

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.MilvusClient")
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.asyncio.to_thread")
    async def test_create_collection_missing_vector_dim(self, mock_to_thread, mock_milvus_client):
        """Test creating a collection with FLOAT_VECTOR field missing dim raises BaseError."""
        mock_client = MagicMock()
        mock_client.list_databases.return_value = ["default"]
        mock_client.has_collection.return_value = False  # Collection doesn't exist
        mock_milvus_client.return_value = mock_client

        call_count = [0]

        def _side_effect(func, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:  # First call is has_collection
                return False
            return func()  # Execute the actual function to trigger validation

        mock_to_thread.side_effect = _side_effect

        store = MilvusVectorStore(milvus_uri="http://testhost:testport")

        schema = CollectionSchema()
        schema.add_field(
            FieldSchema(name="id", dtype=VectorDataType.VARCHAR, max_length=256, is_primary=True)
        )
        schema.add_field(FieldSchema(name="embedding", dtype=VectorDataType.FLOAT_VECTOR))  # Missing dim

        with pytest.raises(BaseError):
            await store.create_collection("test_collection", schema)

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.MilvusClient")
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.asyncio.to_thread")
    async def test_create_collection_missing_vector_field(self, mock_to_thread, mock_milvus_client):
        """Test creating a collection without FLOAT_VECTOR field raises BaseError."""
        mock_client = MagicMock()
        mock_client.list_databases.return_value = ["default"]
        mock_client.has_collection.return_value = False  # Collection doesn't exist
        mock_milvus_client.return_value = mock_client

        call_count = [0]

        def _side_effect(func, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:  # First call is has_collection
                return False
            return func()  # Execute the actual function to trigger validation

        mock_to_thread.side_effect = _side_effect

        store = MilvusVectorStore(milvus_uri="http://testhost:testport")

        schema = CollectionSchema()
        schema.add_field(
            FieldSchema(name="id", dtype=VectorDataType.VARCHAR, max_length=256, is_primary=True)
        )

        with pytest.raises(BaseError):
            await store.create_collection("test_collection", schema)


class TestMilvusVectorStoreDeleteCollection:
    """Tests for delete_collection method."""

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.MilvusClient")
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.asyncio.to_thread")
    async def test_delete_collection_success(self, mock_to_thread, mock_milvus_client):
        """Test successful deletion of a collection."""
        mock_client = MagicMock()
        mock_client.list_databases.return_value = ["default"]
        mock_milvus_client.return_value = mock_client
        mock_to_thread.return_value = None

        store = MilvusVectorStore(milvus_uri="http://testhost:testport")

        await store.delete_collection("test_collection")

        assert mock_to_thread.call_count == 1

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.MilvusClient")
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.asyncio.to_thread")
    async def test_delete_collection_not_exists(self, mock_to_thread, mock_milvus_client):
        """Test deleting a collection that doesn't exist."""
        mock_client = MagicMock()
        mock_client.list_databases.return_value = ["default"]
        mock_milvus_client.return_value = mock_client

        from pymilvus import MilvusException

        # Mock drop_collection to raise MilvusException for non-existent collection
        mock_client.drop_collection.side_effect = MilvusException("collection not exist")

        store = MilvusVectorStore(milvus_uri="http://testhost:testport")

        # Should not raise for non-existent collection (logs warning instead)
        await store.delete_collection("test_collection")

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.MilvusClient")
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.asyncio.to_thread")
    async def test_delete_collection_other_error(self, mock_to_thread, mock_milvus_client):
        """Test deletion with other error raises exception."""
        mock_client = MagicMock()
        mock_client.list_databases.return_value = ["default"]
        mock_milvus_client.return_value = mock_client

        from pymilvus import MilvusException

        # Mock drop_collection to raise MilvusException for other errors
        def _side_effect(func):
            if callable(func):
                raise MilvusException("some other error")
            return None

        mock_to_thread.side_effect = _side_effect

        store = MilvusVectorStore(milvus_uri="http://testhost:testport")

        with pytest.raises(MilvusException, match="some other error"):
            await store.delete_collection("test_collection")


class TestMilvusVectorStoreCollectionExists:
    """Tests for collection_exists method."""

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.MilvusClient")
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.asyncio.to_thread")
    async def test_collection_exists_true(self, mock_to_thread, mock_milvus_client):
        """Test collection_exists returns True when collection exists."""
        mock_client = MagicMock()
        mock_client.list_databases.return_value = ["default"]
        mock_milvus_client.return_value = mock_client
        mock_to_thread.return_value = True

        store = MilvusVectorStore(milvus_uri="http://testhost:testport")

        result = await store.collection_exists("test_collection")

        assert result is True

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.MilvusClient")
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.asyncio.to_thread")
    async def test_collection_exists_false(self, mock_to_thread, mock_milvus_client):
        """Test collection_exists returns False when collection does not exist."""
        mock_client = MagicMock()
        mock_client.list_databases.return_value = ["default"]
        mock_milvus_client.return_value = mock_client
        mock_to_thread.return_value = False

        store = MilvusVectorStore(milvus_uri="http://testhost:testport")

        result = await store.collection_exists("test_collection")

        assert result is False


class TestMilvusVectorStoreGetSchema:
    """Tests for get_schema method."""

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.MilvusClient")
    async def test_get_schema_success(self, mock_milvus_client):
        """Test getting schema successfully."""
        mock_client = MagicMock()
        mock_client.list_databases.return_value = ["default"]
        mock_milvus_client.return_value = mock_client
        mock_client.has_collection.return_value = True

        # Mock describe_collection to return schema info
        mock_client.describe_collection.return_value = {
            "description": "Test collection",
            "enable_dynamic_field": False,
            "fields": [
                {
                    "name": "id",
                    "type": MilvusDataType.VARCHAR,
                    "is_primary": True,
                    "auto_id": False,
                    "max_length": 256,
                },
                {
                    "name": "embedding",
                    "type": MilvusDataType.FLOAT_VECTOR,
                    "dim": 768,
                },
                {
                    "name": "text",
                    "type": MilvusDataType.VARCHAR,
                    "max_length": 65535,
                },
            ],
        }

        store = MilvusVectorStore(milvus_uri="http://testhost:testport")

        schema = await store.get_schema("test_collection")

        assert len(schema.fields) == 3
        assert schema.fields[0].name == "id"
        assert schema.fields[0].dtype == VectorDataType.VARCHAR
        assert schema.fields[0].is_primary is True
        assert schema.fields[1].name == "embedding"
        assert schema.fields[1].dtype == VectorDataType.FLOAT_VECTOR
        assert schema.fields[1].dim == 768

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.MilvusClient")
    async def test_get_schema_collection_not_exists(self, mock_milvus_client):
        """Test getting schema for non-existent collection raises BaseError."""
        mock_client = MagicMock()
        mock_client.list_databases.return_value = ["default"]
        mock_milvus_client.return_value = mock_client
        mock_client.has_collection.return_value = False

        store = MilvusVectorStore(milvus_uri="http://testhost:testport")

        with pytest.raises(BaseError):
            await store.get_schema("non_existent_collection")

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.MilvusClient")
    async def test_get_schema_with_string_types(self, mock_milvus_client):
        """Test getting schema when field types are strings."""
        mock_client = MagicMock()
        mock_client.list_databases.return_value = ["default"]
        mock_milvus_client.return_value = mock_client
        mock_client.has_collection.return_value = True

        # Mock describe_collection with string types (some Milvus versions return strings)
        mock_client.describe_collection.return_value = {
            "description": "Test collection",
            "enable_dynamic_field": True,
            "fields": [
                {
                    "name": "id",
                    "type": "VARCHAR",  # String instead of enum
                    "is_primary": True,
                    "max_length": 256,
                },
                {
                    "name": "embedding",
                    "type": "FLOAT_VECTOR",
                    "dim": 1536,
                },
            ],
        }

        store = MilvusVectorStore(milvus_uri="http://testhost:testport")

        schema = await store.get_schema("test_collection")

        assert len(schema.fields) == 2
        assert schema.fields[0].dtype == VectorDataType.VARCHAR
        assert schema.fields[1].dtype == VectorDataType.FLOAT_VECTOR


class TestMilvusVectorStoreAddDocs:
    """Tests for add_docs method."""

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.MilvusClient")
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.asyncio.to_thread")
    async def test_add_docs_success(self, mock_to_thread, mock_milvus_client):
        """Test adding documents successfully."""
        mock_client = MagicMock()
        mock_client.list_databases.return_value = ["default"]
        mock_milvus_client.return_value = mock_client
        mock_client.describe_collection.return_value = {
            "fields": [
                {"name": "id", "type": "VARCHAR"},
                {"name": "embedding", "type": "FLOAT_VECTOR"},
                {"name": "text", "type": "VARCHAR"},
            ]
        }
        mock_to_thread.return_value = None

        store = MilvusVectorStore(milvus_uri="http://testhost:testport")

        docs = [
            {
                "id": "doc1",
                "embedding": [0.1, 0.2, 0.3],
                "text": "Test document 1",
            },
            {
                "id": "doc2",
                "embedding": [0.4, 0.5, 0.6],
                "text": "Test document 2",
            },
        ]

        await store.add_docs("test_collection", docs)

        # Should call describe_collection + insert + flush
        assert mock_to_thread.call_count == 3

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.MilvusClient")
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.asyncio.to_thread")
    async def test_add_docs_with_batch_size(self, mock_to_thread, mock_milvus_client):
        """Test adding documents with custom batch size."""
        mock_client = MagicMock()
        mock_client.list_databases.return_value = ["default"]
        mock_milvus_client.return_value = mock_client
        mock_client.describe_collection.return_value = {
            "fields": [
                {"name": "id", "type": "VARCHAR"},
                {"name": "embedding", "type": "FLOAT_VECTOR"},
                {"name": "text", "type": "VARCHAR"},
            ]
        }
        mock_to_thread.return_value = None

        store = MilvusVectorStore(milvus_uri="http://testhost:testport")

        docs = [
            {
                "id": f"doc{i}",
                "embedding": [0.1, 0.2, 0.3],
                "text": f"Test document {i}",
            }
            for i in range(10)
        ]

        await store.add_docs("test_collection", docs, batch_size=3)

        # Should call describe_collection + 4 batch inserts + flush = 6 calls
        assert mock_to_thread.call_count == 6

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.MilvusClient")
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.asyncio.to_thread")
    async def test_add_docs_zero_batch_size(self, mock_to_thread, mock_milvus_client):
        """Test adding documents with zero batch size uses default."""
        mock_client = MagicMock()
        mock_client.list_databases.return_value = ["default"]
        mock_milvus_client.return_value = mock_client
        mock_client.describe_collection.return_value = {
            "fields": [
                {"name": "id", "type": "VARCHAR"},
                {"name": "embedding", "type": "FLOAT_VECTOR"},
                {"name": "text", "type": "VARCHAR"},
            ]
        }
        mock_to_thread.return_value = None

        store = MilvusVectorStore(milvus_uri="http://testhost:testport")

        docs = [
            {
                "id": "doc1",
                "embedding": [0.1, 0.2, 0.3],
                "text": "Test document",
            },
        ]

        await store.add_docs("test_collection", docs, batch_size=0)

        # Should call describe_collection + insert + flush = 3 calls
        assert mock_to_thread.call_count == 3


class TestMilvusVectorStoreSearch:
    """Tests for search method."""

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.MilvusClient")
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.asyncio.to_thread")
    async def test_search_success(self, mock_to_thread, mock_milvus_client):
        """Test successful vector search."""
        mock_client = MagicMock()
        mock_client.list_databases.return_value = ["default"]
        mock_milvus_client.return_value = mock_client
        mock_client.describe_collection.return_value = {
            "fields": [
                {"name": "id", "type": "VARCHAR"},
                {"name": "embedding", "type": "FLOAT_VECTOR"},
                {"name": "text", "type": "VARCHAR"},
            ]
        }

        mock_results = [
            [
                {
                    "id": "doc1",
                    "distance": 0.1,
                    "score": 0.95,
                    "entity": {"text": "Text 1", "source": "test1"},
                },
                {
                    "id": "doc2",
                    "distance": 0.3,
                    "score": 0.85,
                    "entity": {"text": "Text 2", "source": "test2"},
                },
            ]
        ]

        # Side effect that returns mock_results for search, executes describe_collection
        def _side_effect(func, *args, **kwargs):
            if callable(func):
                return func(*args, **kwargs)
            return mock_results

        mock_to_thread.side_effect = _side_effect
        mock_client.search.return_value = mock_results

        store = MilvusVectorStore(milvus_uri="http://testhost:testport")

        results = await store.search(
            "test_collection", [0.1, 0.2, 0.3], "embedding", top_k=5
        )

        assert len(results) == 2
        assert results[0].fields["id"] == "doc1"
        assert results[0].fields["text"] == "Text 1"
        assert results[0].score == 0.95

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.MilvusClient")
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.asyncio.to_thread")
    async def test_search_with_filters(self, mock_to_thread, mock_milvus_client):
        """Test search with filters."""
        mock_client = MagicMock()
        mock_client.list_databases.return_value = ["default"]
        mock_milvus_client.return_value = mock_client
        mock_client.describe_collection.return_value = {
            "fields": [
                {"name": "id", "type": "VARCHAR"},
                {"name": "embedding", "type": "FLOAT_VECTOR"},
                {"name": "category", "type": "VARCHAR"},
            ]
        }

        mock_results = [
            [
                {
                    "id": "doc1",
                    "distance": 0.1,
                    "entity": {"category": "tech"},
                },
            ]
        ]

        def _side_effect(func, *args, **kwargs):
            if callable(func):
                return func(*args, **kwargs)
            return mock_results

        mock_to_thread.side_effect = _side_effect
        mock_client.search.return_value = mock_results

        store = MilvusVectorStore(milvus_uri="http://testhost:testport")

        results = await store.search(
            "test_collection",
            [0.1, 0.2, 0.3],
            "embedding",
            top_k=5,
            filters={"category": "tech"},
        )

        assert len(results) == 1

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.MilvusClient")
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.asyncio.to_thread")
    async def test_search_with_pk_field(self, mock_to_thread, mock_milvus_client):
        """Test search results with pk field instead of id."""
        mock_client = MagicMock()
        mock_client.list_databases.return_value = ["default"]
        mock_milvus_client.return_value = mock_client
        mock_client.describe_collection.return_value = {
            "fields": [
                {"name": "id", "type": "VARCHAR"},
                {"name": "embedding", "type": "FLOAT_VECTOR"},
            ]
        }

        mock_results = [
            [{"pk": "123", "distance": 0.1, "entity": {"text": "Text 1"}}]
        ]

        def _side_effect(func, *args, **kwargs):
            if callable(func):
                return func(*args, **kwargs)
            return mock_results

        mock_to_thread.side_effect = _side_effect
        mock_client.search.return_value = mock_results

        store = MilvusVectorStore(milvus_uri="http://testhost:testport")

        results = await store.search(
            "test_collection", [0.1, 0.2, 0.3], "embedding", top_k=5
        )

        assert results[0].fields["id"] == "123"

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.MilvusClient")
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.asyncio.to_thread")
    async def test_search_with_json_metadata(self, mock_to_thread, mock_milvus_client):
        """Test search with JSON strings in entity are parsed."""
        mock_client = MagicMock()
        mock_client.list_databases.return_value = ["default"]
        mock_milvus_client.return_value = mock_client
        mock_client.describe_collection.return_value = {
            "fields": [
                {"name": "id", "type": "VARCHAR"},
                {"name": "embedding", "type": "FLOAT_VECTOR"},
            ]
        }

        mock_results = [
            [{"id": "doc1", "distance": 0.1, "entity": {"tags": json.dumps(["tag1", "tag2"])}}]
        ]

        def _side_effect(func, *args, **kwargs):
            if callable(func):
                return func(*args, **kwargs)
            return mock_results

        mock_to_thread.side_effect = _side_effect
        mock_client.search.return_value = mock_results

        store = MilvusVectorStore(milvus_uri="http://testhost:testport")

        results = await store.search(
            "test_collection", [0.1, 0.2, 0.3], "embedding", top_k=5
        )

        assert results[0].fields["tags"] == ["tag1", "tag2"]

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.MilvusClient")
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.asyncio.to_thread")
    async def test_search_with_output_fields(self, mock_to_thread, mock_milvus_client):
        """Test search with custom output fields."""
        mock_client = MagicMock()
        mock_client.list_databases.return_value = ["default"]
        mock_milvus_client.return_value = mock_client
        mock_client.describe_collection.return_value = {
            "fields": [
                {"name": "id", "type": "VARCHAR"},
                {"name": "embedding", "type": "FLOAT_VECTOR"},
                {"name": "text", "type": "VARCHAR"},
                {"name": "source", "type": "VARCHAR"},
            ]
        }

        mock_results = [[{"id": "doc1", "distance": 0.1, "entity": {"text": "Text 1"}}]]

        def _side_effect(func, *args, **kwargs):
            if callable(func):
                return func(*args, **kwargs)
            return mock_results

        mock_to_thread.side_effect = _side_effect
        mock_client.search.return_value = mock_results

        store = MilvusVectorStore(milvus_uri="http://testhost:testport")

        results = await store.search(
            "test_collection",
            [0.1, 0.2, 0.3],
            "embedding",
            top_k=5,
            output_fields=["text", "source"],
        )

        assert len(results) == 1

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.MilvusClient")
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.asyncio.to_thread")
    async def test_search_ip_distance_conversion(self, mock_to_thread, mock_milvus_client):
        """Test IP distance conversion (Milvus returns similarity in [-1,1])."""
        mock_client = MagicMock()
        mock_client.list_databases.return_value = ["default"]
        mock_milvus_client.return_value = mock_client
        mock_client.describe_collection.return_value = {
            "fields": [
                {"name": "id", "type": "VARCHAR"},
                {"name": "embedding", "type": "FLOAT_VECTOR"},
            ]
        }

        # Milvus IP returns similarity in [-1, 1] (larger = more similar)
        # distance=1.0 (most similar, IP=1.0) -> score = (1.0 + 1.0) / 2.0 = 1.0
        # distance=0.0 (neutral, IP=0.0) -> score = (0.0 + 1.0) / 2.0 = 0.5
        # distance=-1.0 (dissimilar, IP=-1.0) -> score = (-1.0 + 1.0) / 2.0 = 0.0
        mock_results = [
            [
                {"id": "doc1", "distance": 1.0, "entity": {}},  # Most similar, score=1.0
                {"id": "doc2", "distance": 0.0, "entity": {}},  # Neutral, score=0.5
                {"id": "doc3", "distance": -1.0, "entity": {}},  # Dissimilar, score=0.0
            ]
        ]

        def _side_effect(func, *args, **kwargs):
            if callable(func):
                return func(*args, **kwargs)
            return mock_results

        mock_to_thread.side_effect = _side_effect
        mock_client.search.return_value = mock_results

        store = MilvusVectorStore(milvus_uri="http://testhost:testport")

        results = await store.search(
            "test_collection", [0.1, 0.2, 0.3], "embedding", top_k=5, metric_type="IP"
        )

        assert len(results) == 3
        assert results[0].score == 1.0
        assert results[1].score == 0.5
        assert results[2].score == 0.0

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.MilvusClient")
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.asyncio.to_thread")
    async def test_search_cosine_distance_conversion(self, mock_to_thread, mock_milvus_client):
        """Test COSINE distance conversion (Milvus returns similarity in [-1,1])."""
        mock_client = MagicMock()
        mock_client.list_databases.return_value = ["default"]
        mock_milvus_client.return_value = mock_client
        mock_client.describe_collection.return_value = {
            "fields": [
                {"name": "id", "type": "VARCHAR"},
                {"name": "embedding", "type": "FLOAT_VECTOR"},
            ]
        }

        # Milvus COSINE returns similarity in [-1, 1] (larger = more similar)
        # distance=1.0 (most similar, cos_sim=1.0) -> score = (1.0 + 1.0) / 2.0 = 1.0
        # distance=0.0 (neutral, cos_sim=0.0) -> score = (0.0 + 1.0) / 2.0 = 0.5
        # distance=-1.0 (dissimilar, cos_sim=-1.0) -> score = (-1.0 + 1.0) / 2.0 = 0.0
        mock_results = [
            [
                {"id": "doc1", "distance": 1.0, "entity": {}},  # Most similar, score=1.0
                {"id": "doc2", "distance": 0.0, "entity": {}},  # Neutral, score=0.5
                {"id": "doc3", "distance": -1.0, "entity": {}},  # Dissimilar, score=0.0
            ]
        ]

        def _side_effect(func, *args, **kwargs):
            if callable(func):
                return func(*args, **kwargs)
            return mock_results

        mock_to_thread.side_effect = _side_effect
        mock_client.search.return_value = mock_results

        store = MilvusVectorStore(milvus_uri="http://testhost:testport")

        results = await store.search(
            "test_collection", [0.1, 0.2, 0.3], "embedding", top_k=5
        )

        assert len(results) == 3
        assert results[0].score == 1.0
        assert results[1].score == 0.5
        assert results[2].score == 0.0


class TestMilvusVectorStoreDeleteDocsByIds:
    """Tests for delete_docs_by_ids method."""

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.MilvusClient")
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.asyncio.to_thread")
    async def test_delete_docs_by_ids_success(self, mock_to_thread, mock_milvus_client):
        """Test deleting documents by ids successfully."""
        mock_client = MagicMock()
        mock_client.list_databases.return_value = ["default"]
        mock_milvus_client.return_value = mock_client
        mock_to_thread.return_value = None

        store = MilvusVectorStore(milvus_uri="http://testhost:testport")

        await store.delete_docs_by_ids("test_collection", ["doc1", "doc2"])

        assert mock_to_thread.call_count == 1

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.MilvusClient")
    async def test_delete_docs_by_ids_empty_list(self, mock_milvus_client):
        """Test deleting with empty id list returns early."""
        mock_client = MagicMock()
        mock_client.list_databases.return_value = ["default"]
        mock_milvus_client.return_value = mock_client

        store = MilvusVectorStore(milvus_uri="http://testhost:testport")

        # Should not raise, just return
        await store.delete_docs_by_ids("test_collection", [])


class TestMilvusVectorStoreDeleteDocsByFilters:
    """Tests for delete_docs_by_filters method."""

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.MilvusClient")
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.asyncio.to_thread")
    async def test_delete_docs_by_filters_success(self, mock_to_thread, mock_milvus_client):
        """Test deleting documents by filters successfully."""
        mock_client = MagicMock()
        mock_client.list_databases.return_value = ["default"]
        mock_milvus_client.return_value = mock_client
        mock_to_thread.return_value = None

        store = MilvusVectorStore(milvus_uri="http://testhost:testport")

        await store.delete_docs_by_filters("test_collection", {"source": "test"})

        assert mock_to_thread.call_count == 1

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.milvus_vector_store.MilvusClient")
    async def test_delete_docs_by_filters_empty(self, mock_milvus_client):
        """Test deleting with empty filters returns early."""
        mock_client = MagicMock()
        mock_client.list_databases.return_value = ["default"]
        mock_milvus_client.return_value = mock_client

        store = MilvusVectorStore(milvus_uri="http://testhost:testport")

        # Should not raise, just return
        await store.delete_docs_by_filters("test_collection", {})
