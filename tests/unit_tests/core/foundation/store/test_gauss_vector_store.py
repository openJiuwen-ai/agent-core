# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for GaussVectorStore."""

import json
from unittest.mock import MagicMock, patch, call

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.store.base_vector_store import (
    CollectionSchema,
    VectorDataType,
    FieldSchema,
)
from openjiuwen.core.foundation.store.vector.gauss_vector_store import GaussVectorStore


class TestGaussVectorStoreInit:
    """Tests for GaussVectorStore initialization."""

    @patch("openjiuwen.core.foundation.store.vector.gauss_vector_store.psycopg2")
    def test_init_with_default_params(self, mock_psycopg2):
        """Test initialization with default parameters."""
        mock_conn = MagicMock()
        mock_psycopg2.connect.return_value = mock_conn

        store = GaussVectorStore()

        assert store.host == "localhost"
        assert store.port == 5432
        assert store.database == "postgres"
        assert store.user == "postgres"
        assert store.password == ""

    @patch("openjiuwen.core.foundation.store.vector.gauss_vector_store.psycopg2")
    def test_init_with_custom_params(self, mock_psycopg2):
        """Test initialization with custom parameters."""
        mock_conn = MagicMock()
        mock_psycopg2.connect.return_value = mock_conn

        store = GaussVectorStore(
            host="testhost",
            port=5433,
            database="testdb",
            user="testuser",
            password="testpass",
        )

        assert store.host == "testhost"
        assert store.port == 5433
        assert store.database == "testdb"
        assert store.user == "testuser"
        assert store.password == "testpass"

    @patch("openjiuwen.core.foundation.store.vector.gauss_vector_store.psycopg2")
    def test_lazy_init_no_connection_on_init(self, mock_psycopg2):
        """Test that connection is NOT created during __init__ with lazy initialization."""
        store = GaussVectorStore(host="testhost")

        # psycopg2.connect should NOT be called during __init__
        mock_psycopg2.connect.assert_not_called()

    @patch("openjiuwen.core.foundation.store.vector.gauss_vector_store.psycopg2")
    def test_connection_reuse(self, mock_psycopg2):
        """Test that the same connection instance is reused for multiple accesses."""
        mock_conn = MagicMock()
        mock_psycopg2.connect.return_value = mock_conn

        store = GaussVectorStore(host="testhost")

        # Access connection multiple times
        conn1 = store.connection
        conn2 = store.connection

        # Should return the same instance
        assert conn1 is conn2
        # psycopg2.connect should only be called once
        mock_psycopg2.connect.assert_called_once()

    @patch("openjiuwen.core.foundation.store.vector.gauss_vector_store.psycopg2")
    def test_close_and_reconnect(self, mock_psycopg2):
        """Test that close() releases the connection and it can be recreated."""
        mock_conn1 = MagicMock()
        mock_conn2 = MagicMock()
        mock_psycopg2.connect.side_effect = [mock_conn1, mock_conn2]

        store = GaussVectorStore(host="testhost")

        # First access creates the connection
        conn1 = store.connection
        assert mock_psycopg2.connect.call_count == 1
        assert conn1 is mock_conn1

        # Close releases the connection
        store.close()

        # Next access creates a new connection
        conn2 = store.connection
        assert mock_psycopg2.connect.call_count == 2
        assert conn2 is mock_conn2
        assert conn1 is not conn2


class TestGaussVectorStoreCreateCollection:
    """Tests for create_collection method."""

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.gauss_vector_store.psycopg2")
    async def test_create_collection_with_schema_object(self, mock_psycopg2):
        """Test creating a collection with a CollectionSchema object."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_psycopg2.connect.return_value = mock_conn

        # First call returns False (table doesn't exist), subsequent calls for info
        mock_cursor.fetchone.side_effect = [(False,), (True,)]

        store = GaussVectorStore(host="testhost")

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

        # Should execute CREATE TABLE and CREATE INDEX
        assert mock_cursor.execute.call_count >= 2

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.gauss_vector_store.psycopg2")
    async def test_create_collection_with_dict_schema(self, mock_psycopg2):
        """Test creating a collection with a schema dictionary."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_psycopg2.connect.return_value = mock_conn
        mock_cursor.fetchone.side_effect = [(False,), (True,)]

        store = GaussVectorStore(host="testhost")

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

        assert mock_cursor.execute.call_count >= 2

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.gauss_vector_store.psycopg2")
    async def test_create_collection_with_custom_metric(self, mock_psycopg2):
        """Test creating a collection with custom distance metric."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_psycopg2.connect.return_value = mock_conn
        mock_cursor.fetchone.side_effect = [(False,), (True,)]

        store = GaussVectorStore(host="testhost")

        schema = CollectionSchema()
        schema.add_field(
            FieldSchema(name="id", dtype=VectorDataType.VARCHAR, max_length=256, is_primary=True)
        )
        schema.add_field(FieldSchema(name="embedding", dtype=VectorDataType.FLOAT_VECTOR, dim=768))

        await store.create_collection("test_collection", schema, distance_metric="L2")

        # Verify index SQL uses L2 metric
        calls = mock_cursor.execute.call_args_list
        index_call = [c for c in calls if "CREATE INDEX" in str(c)][0]
        assert "l2" in str(index_call)

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.gauss_vector_store.psycopg2")
    async def test_create_collection_already_exists(self, mock_psycopg2):
        """Test creating a collection that already exists does nothing."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_psycopg2.connect.return_value = mock_conn

        # Table already exists
        mock_cursor.fetchone.return_value = (True,)

        store = GaussVectorStore(host="testhost")

        schema = CollectionSchema()
        schema.add_field(
            FieldSchema(name="id", dtype=VectorDataType.VARCHAR, max_length=256, is_primary=True)
        )
        schema.add_field(FieldSchema(name="embedding", dtype=VectorDataType.FLOAT_VECTOR, dim=768))

        await store.create_collection("test_collection", schema)

        # Only one SELECT query should be executed (check existence)
        # No CREATE TABLE or CREATE INDEX should be executed
        assert mock_cursor.execute.call_count == 1

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.gauss_vector_store.psycopg2")
    async def test_create_collection_missing_vector_dim(self, mock_psycopg2):
        """Test creating a collection with FLOAT_VECTOR field missing dim raises BaseError."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_psycopg2.connect.return_value = mock_conn
        mock_cursor.fetchone.side_effect = [(False,), (True,)]

        store = GaussVectorStore(host="testhost")

        schema = CollectionSchema()
        schema.add_field(
            FieldSchema(name="id", dtype=VectorDataType.VARCHAR, max_length=256, is_primary=True)
        )
        schema.add_field(FieldSchema(name="embedding", dtype=VectorDataType.FLOAT_VECTOR))  # Missing dim

        with pytest.raises(BaseError):
            await store.create_collection("test_collection", schema)

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.gauss_vector_store.psycopg2")
    async def test_create_collection_missing_vector_field(self, mock_psycopg2):
        """Test creating a collection without FLOAT_VECTOR field raises BaseError."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_psycopg2.connect.return_value = mock_conn
        mock_cursor.fetchone.side_effect = [(False,), (True,)]

        store = GaussVectorStore(host="testhost")

        schema = CollectionSchema()
        schema.add_field(
            FieldSchema(name="id", dtype=VectorDataType.VARCHAR, max_length=256, is_primary=True)
        )

        with pytest.raises(BaseError):
            await store.create_collection("test_collection", schema)

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.gauss_vector_store.psycopg2")
    async def test_create_collection_with_auto_id(self, mock_psycopg2):
        """Test creating a collection with auto_id primary key."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_psycopg2.connect.return_value = mock_conn
        mock_cursor.fetchone.side_effect = [(False,), (True,)]

        store = GaussVectorStore(host="testhost")

        schema = CollectionSchema()
        schema.add_field(
            FieldSchema(name="id", dtype=VectorDataType.VARCHAR, max_length=256, is_primary=True, auto_id=True)
        )
        schema.add_field(FieldSchema(name="embedding", dtype=VectorDataType.FLOAT_VECTOR, dim=768))

        await store.create_collection("test_collection", schema)

        # Check that SERIAL PRIMARY KEY is used
        calls = mock_cursor.execute.call_args_list
        table_call = [c for c in calls if "CREATE TABLE" in str(c)][0]
        assert "SERIAL" in str(table_call) or "AUTO_INCREMENT" in str(table_call)


class TestGaussVectorStoreDeleteCollection:
    """Tests for delete_collection method."""

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.gauss_vector_store.psycopg2")
    async def test_delete_collection_success(self, mock_psycopg2):
        """Test successful deletion of a collection."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_psycopg2.connect.return_value = mock_conn
        mock_cursor.fetchone.return_value = (True,)

        store = GaussVectorStore(host="testhost")

        await store.delete_collection("test_collection")

        # Should execute DROP TABLE
        calls = mock_cursor.execute.call_args_list
        drop_call = [c for c in calls if "DROP TABLE" in str(c)][0]
        assert "test_collection" in str(drop_call)

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.gauss_vector_store.psycopg2")
    async def test_delete_collection_not_exists(self, mock_psycopg2):
        """Test deleting a collection that doesn't exist."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_psycopg2.connect.return_value = mock_conn
        mock_cursor.fetchone.return_value = (False,)

        store = GaussVectorStore(host="testhost")

        # Should not raise, just log warning
        await store.delete_collection("test_collection")

        # DROP TABLE should not be executed
        drop_calls = [c for c in mock_cursor.execute.call_args_list if "DROP TABLE" in str(c)]
        assert len(drop_calls) == 0


class TestGaussVectorStoreCollectionExists:
    """Tests for collection_exists method."""

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.gauss_vector_store.psycopg2")
    async def test_collection_exists_true(self, mock_psycopg2):
        """Test collection_exists returns True when collection exists."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_psycopg2.connect.return_value = mock_conn
        mock_cursor.fetchone.return_value = (True,)

        store = GaussVectorStore(host="testhost")

        result = await store.collection_exists("test_collection")

        assert result is True

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.gauss_vector_store.psycopg2")
    async def test_collection_exists_false(self, mock_psycopg2):
        """Test collection_exists returns False when collection does not exist."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_psycopg2.connect.return_value = mock_conn
        mock_cursor.fetchone.return_value = (False,)

        store = GaussVectorStore(host="testhost")

        result = await store.collection_exists("test_collection")

        assert result is False


class TestGaussVectorStoreGetSchema:
    """Tests for get_schema method."""

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.gauss_vector_store.psycopg2")
    async def test_get_schema_success(self, mock_psycopg2):
        """Test getting schema successfully."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_psycopg2.connect.return_value = mock_conn

        # Table exists
        mock_cursor.fetchone.side_effect = [(True,), (True,), (True,), (True,)]
        mock_cursor.fetchall.side_effect = [
            [("id", "character varying", "YES", None),
             ("embedding", "floatvector(128)", "YES", None),
             ("text", "text", "YES", None)],
            [("id",)],  # Primary keys
        ]

        store = GaussVectorStore(host="testhost")

        schema = await store.get_schema("test_collection")

        assert len(schema.fields) >= 3

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.gauss_vector_store.psycopg2")
    async def test_get_schema_collection_not_exists(self, mock_psycopg2):
        """Test getting schema for non-existent collection raises BaseError."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_psycopg2.connect.return_value = mock_conn
        mock_cursor.fetchone.return_value = (False,)

        store = GaussVectorStore(host="testhost")

        with pytest.raises(BaseError):
            await store.get_schema("non_existent_collection")


class TestGaussVectorStoreAddDocs:
    """Tests for add_docs method."""

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.gauss_vector_store.psycopg2")
    async def test_add_docs_success(self, mock_psycopg2):
        """Test adding documents successfully."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_psycopg2.connect.return_value = mock_conn
        mock_cursor.fetchone.return_value = (True,)

        store = GaussVectorStore(host="testhost")

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

        # Should call executemany
        mock_cursor.executemany.assert_called_once()

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.gauss_vector_store.psycopg2")
    async def test_add_docs_with_batch_size(self, mock_psycopg2):
        """Test adding documents with custom batch size."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_psycopg2.connect.return_value = mock_conn
        mock_cursor.fetchone.return_value = (True,)

        store = GaussVectorStore(host="testhost")

        docs = [
            {
                "id": f"doc{i}",
                "embedding": [0.1, 0.2, 0.3],
                "text": f"Test document {i}",
            }
            for i in range(10)
        ]

        await store.add_docs("test_collection", docs, batch_size=3)

        # Should call executemany multiple times (10 docs / 3 = 4 batches)
        assert mock_cursor.executemany.call_count == 4

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.gauss_vector_store.psycopg2")
    async def test_add_docs_with_json_metadata(self, mock_psycopg2):
        """Test adding documents with JSON metadata."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_psycopg2.connect.return_value = mock_conn
        mock_cursor.fetchone.return_value = (True,)

        store = GaussVectorStore(host="testhost")

        docs = [
            {
                "id": "doc1",
                "embedding": [0.1, 0.2, 0.3],
                "text": "Test document 1",
                "metadata": {"source": "test", "page": 1},
            },
        ]

        await store.add_docs("test_collection", docs)

        # Should serialize JSON metadata
        mock_cursor.executemany.assert_called_once()
        call_args = mock_cursor.executemany.call_args
        assert call_args is not None


def _make_desc(name: str):
    """Create a mock column description with proper name attribute."""
    m = MagicMock()
    m.name = name
    return m


class TestGaussVectorStoreSearch:
    """Tests for search method."""

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.gauss_vector_store.psycopg2")
    async def test_search_success(self, mock_psycopg2):
        """Test successful vector search."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_psycopg2.connect.return_value = mock_conn

        # Mock cursor description for columns
        mock_cursor.description = [_make_desc("id"), _make_desc("text"), _make_desc("distance")]
        mock_cursor.fetchall.return_value = [
            ("doc1", "Text 1", 0.1),
            ("doc2", "Text 2", 0.3),
        ]

        store = GaussVectorStore(host="testhost")

        results = await store.search(
            "test_collection", [0.1, 0.2, 0.3], "embedding", top_k=5
        )

        assert len(results) == 2
        assert results[0].fields["id"] == "doc1"
        assert results[0].fields["text"] == "Text 1"

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.gauss_vector_store.psycopg2")
    async def test_search_with_filters(self, mock_psycopg2):
        """Test search with filters."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_psycopg2.connect.return_value = mock_conn

        mock_cursor.description = [_make_desc("id"), _make_desc("category"), _make_desc("distance")]
        mock_cursor.fetchall.return_value = [
            ("doc1", "tech", 0.1),
        ]
        store = GaussVectorStore(host="testhost")

        results = await store.search(
            "test_collection",
            [0.1, 0.2, 0.3],
            "embedding",
            top_k=5,
            filters={"category": "tech"},
        )

        assert len(results) == 1
        # Verify WHERE clause is in the query
        calls = mock_cursor.execute.call_args_list
        search_call = [c for c in calls if "SELECT" in str(c)][0]
        assert "WHERE" in str(search_call) and "category" in str(search_call)

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.gauss_vector_store.psycopg2")
    async def test_search_cosine_metric(self, mock_psycopg2):
        """Test search with COSINE metric."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_psycopg2.connect.return_value = mock_conn

        mock_cursor.description = [_make_desc("id"), _make_desc("distance")]
        # Cosine distance: 0 = identical, 2 = opposite
        mock_cursor.fetchall.return_value = [
            ("doc1", 0.0),  # Most similar
            ("doc2", 1.0),  # Orthogonal
        ]

        store = GaussVectorStore(host="testhost")

        results = await store.search(
            "test_collection", [0.1, 0.2, 0.3], "embedding", top_k=5,
            metric_type="COSINE"
        )

        assert len(results) == 2
        # First result should have higher score than second
        assert results[0].score >= results[1].score

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.gauss_vector_store.psycopg2")
    async def test_search_l2_metric(self, mock_psycopg2):
        """Test search with L2 metric."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_psycopg2.connect.return_value = mock_conn

        mock_cursor.description = [_make_desc("id"), _make_desc("distance")]
        mock_cursor.fetchall.return_value = [
            ("doc1", 0.5),
            ("doc2", 1.5),
        ]

        store = GaussVectorStore(host="testhost")

        results = await store.search(
            "test_collection", [0.1, 0.2, 0.3], "embedding", top_k=5,
            metric_type="L2"
        )

        assert len(results) == 2
        # First result should have higher score (smaller distance = more similar)
        assert results[0].score >= results[1].score


class TestGaussVectorStoreDeleteDocsByIds:
    """Tests for delete_docs_by_ids method."""

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.gauss_vector_store.psycopg2")
    async def test_delete_docs_by_ids_success(self, mock_psycopg2):
        """Test deleting documents by ids successfully."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_psycopg2.connect.return_value = mock_conn

        store = GaussVectorStore(host="testhost")

        await store.delete_docs_by_ids("test_collection", ["doc1", "doc2"])

        # Should execute DELETE query
        calls = mock_cursor.execute.call_args_list
        delete_call = [c for c in calls if "DELETE" in str(c)][0]
        assert "IN" in str(delete_call)

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.gauss_vector_store.psycopg2")
    async def test_delete_docs_by_ids_empty_list(self, mock_psycopg2):
        """Test deleting with empty id list returns early."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_psycopg2.connect.return_value = mock_conn

        store = GaussVectorStore(host="testhost")

        # Should not raise, just return
        await store.delete_docs_by_ids("test_collection", [])

        # No DELETE should be executed
        delete_calls = [c for c in mock_cursor.execute.call_args_list if "DELETE" in str(c)]
        assert len(delete_calls) == 0


class TestGaussVectorStoreDeleteDocsByFilters:
    """Tests for delete_docs_by_filters method."""

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.gauss_vector_store.psycopg2")
    async def test_delete_docs_by_filters_success(self, mock_psycopg2):
        """Test deleting documents by filters successfully."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_psycopg2.connect.return_value = mock_conn

        mock_cursor.fetchone.return_value = (5,)

        store = GaussVectorStore(host="testhost")

        await store.delete_docs_by_filters("test_collection", {"source": "test"})

        # Should execute DELETE query with WHERE clause
        calls = mock_cursor.execute.call_args_list
        delete_call = [c for c in calls if "DELETE" in str(c)][0]
        assert "WHERE" in str(delete_call)

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.gauss_vector_store.psycopg2")
    async def test_delete_docs_by_filters_empty(self, mock_psycopg2):
        """Test deleting with empty filters returns early."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_psycopg2.connect.return_value = mock_conn

        store = GaussVectorStore(host="testhost")

        # Should not raise, just return
        await store.delete_docs_by_filters("test_collection", {})

        # No DELETE should be executed
        delete_calls = [c for c in mock_cursor.execute.call_args_list if "DELETE" in str(c)]
        assert len(delete_calls) == 0


class TestGaussVectorStoreListCollectionNames:
    """Tests for list_collection_names method."""

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.gauss_vector_store.psycopg2")
    async def test_list_collection_names_success(self, mock_psycopg2):
        """Test listing collection names successfully."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_psycopg2.connect.return_value = mock_conn

        mock_cursor.fetchall.return_value = [
            ("collection1",),
            ("collection2",),
            ("collection3",),
        ]

        store = GaussVectorStore(host="testhost")

        result = await store.list_collection_names()

        assert result == ["collection1", "collection2", "collection3"]


class TestGaussVectorStoreGetCollectionMetadata:
    """Tests for get_collection_metadata method."""

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.gauss_vector_store.psycopg2")
    async def test_get_collection_metadata_from_cache(self, mock_psycopg2):
        """Test getting collection metadata from cache."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_psycopg2.connect.return_value = mock_conn

        store = GaussVectorStore(host="testhost")

        mock_cursor.fetchall.return_value = [
            ("embedding",)
        ]
        result = await store.get_collection_metadata("test_collection")

        assert result["distance_metric"] == "COSINE"
        assert result["vector_field"] == "embedding"
        assert result["schema_version"] == 0

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.gauss_vector_store.psycopg2")
    async def test_get_collection_metadata_not_exists(self, mock_psycopg2):
        """Test getting metadata for non-existent collection returns defaults."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_psycopg2.connect.return_value = mock_conn
        mock_cursor.fetchone.return_value = (False,)

        store = GaussVectorStore(host="testhost")

        result = await store.get_collection_metadata("non_existent_collection")

        assert result["distance_metric"] == "COSINE"
        assert result["schema_version"] == 0


class TestGaussVectorStoreClose:
    """Tests for close method."""

    @patch("openjiuwen.core.foundation.store.vector.gauss_vector_store.psycopg2")
    def test_close_connection(self, mock_psycopg2):
        """Test closing the connection."""
        mock_conn = MagicMock()
        mock_psycopg2.connect.return_value = mock_conn

        store = GaussVectorStore(host="testhost")
        _ = store.connection

        store.close()
        mock_conn.close.assert_called_once()
