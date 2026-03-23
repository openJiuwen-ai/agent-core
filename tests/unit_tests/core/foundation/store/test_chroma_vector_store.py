# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for ChromaVectorStore."""

import json
from unittest.mock import AsyncMock, MagicMock, patch, call
import tempfile
import os

import pytest

chromadb = pytest.importorskip("chromadb", reason="chromadb not installed")

from openjiuwen.core.common.exception.errors import BaseError, ValidationError
from openjiuwen.core.foundation.store.base_vector_store import (
    CollectionSchema,
    VectorDataType,
    FieldSchema,
)
from openjiuwen.core.foundation.store.vector.chroma_vector_store import ChromaVectorStore


def _execute_thread_func(func, *args, **kwargs):
    """Helper function to execute the thread function for testing."""
    return func()


class TestChromaVectorStoreCreateCollection:
    """Tests for create_collection method."""

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.chroma_vector_store.chromadb")
    async def test_create_collection_with_schema_object(self, mock_chromadb):
        """Test creating a collection with a CollectionSchema object."""
        mock_client = MagicMock()
        mock_chromadb.PersistentClient.return_value = mock_client
        mock_collection = MagicMock(metadata={})
        mock_client.get_or_create_collection.return_value = mock_collection

        # Mock the task manager
        with patch('openjiuwen.core.foundation.store.vector.chroma_vector_store.get_task_manager') as mock_get_tm:
            mock_task_manager = MagicMock()
            mock_task = MagicMock()
            mock_task.result = mock_collection
            mock_task_manager.task_group.return_value.__aenter__ = AsyncMock()
            mock_task_manager.task_group.return_value.__aexit__ = AsyncMock()
            mock_task_manager.create_task = AsyncMock(return_value=mock_task)
            mock_get_tm.return_value = mock_task_manager

            store = ChromaVectorStore(persist_directory=tempfile.mkdtemp())

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


    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.chroma_vector_store.chromadb")
    async def test_create_collection_with_dict_schema(self, mock_chromadb):
        """Test creating a collection with a schema dictionary."""
        mock_client = MagicMock()
        mock_chromadb.PersistentClient.return_value = mock_client
        mock_collection = MagicMock(metadata={})
        mock_client.get_or_create_collection.return_value = mock_collection

        # Mock the task manager
        with patch('openjiuwen.core.foundation.store.vector.chroma_vector_store.get_task_manager') as mock_get_tm:
            mock_task_manager = MagicMock()
            mock_task = MagicMock()
            mock_task.result = mock_collection
            mock_task_manager.task_group.return_value.__aenter__ = AsyncMock()
            mock_task_manager.task_group.return_value.__aexit__ = AsyncMock()
            mock_task_manager.create_task = AsyncMock(return_value=mock_task)
            mock_get_tm.return_value = mock_task_manager

            store = ChromaVectorStore(persist_directory=tempfile.mkdtemp())

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

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.chroma_vector_store.chromadb")
    async def test_create_collection_with_custom_distance_metric(self, mock_chromadb):
        """Test creating a collection with a custom distance metric."""
        mock_client = MagicMock()
        mock_chromadb.PersistentClient.return_value = mock_client
        mock_collection = MagicMock(metadata={})
        mock_client.get_or_create_collection.return_value = mock_collection


        # Mock the task manager
        with patch('openjiuwen.core.foundation.store.vector.chroma_vector_store.get_task_manager') as mock_get_tm:
            mock_task_manager = MagicMock()
            mock_task = MagicMock()
            mock_task.result = mock_collection
            mock_task_manager.task_group.return_value.__aenter__ = AsyncMock()
            mock_task_manager.task_group.return_value.__aexit__ = AsyncMock()
            mock_task_manager.create_task = AsyncMock(return_value=mock_task)
            mock_get_tm.return_value = mock_task_manager

            store = ChromaVectorStore(persist_directory=tempfile.mkdtemp())

            schema = CollectionSchema()
            schema.add_field(
                FieldSchema(name="id", dtype=VectorDataType.VARCHAR, max_length=256, is_primary=True)
            )
            schema.add_field(FieldSchema(name="embedding", dtype=VectorDataType.FLOAT_VECTOR, dim=768))

            await store.create_collection(
                "test_collection", schema, distance_metric="l2"
            )

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.chroma_vector_store.chromadb")
    async def test_create_collection_with_dot_metric(self, mock_chromadb):
        """Test creating a collection with dot (inner product) distance metric."""
        mock_client = MagicMock()
        mock_chromadb.PersistentClient.return_value = mock_client
        mock_collection = MagicMock(metadata={})
        mock_client.get_or_create_collection.return_value = mock_collection


        # Mock the task manager
        with patch('openjiuwen.core.foundation.store.vector.chroma_vector_store.get_task_manager') as mock_get_tm:
            mock_task_manager = MagicMock()
            mock_task = MagicMock()
            mock_task.result = mock_collection
            mock_task_manager.task_group.return_value.__aenter__ = AsyncMock()
            mock_task_manager.task_group.return_value.__aexit__ = AsyncMock()
            mock_task_manager.create_task = AsyncMock(return_value=mock_task)
            mock_get_tm.return_value = mock_task_manager

            store = ChromaVectorStore(persist_directory=tempfile.mkdtemp())

            schema = CollectionSchema()
            schema.add_field(
                FieldSchema(name="id", dtype=VectorDataType.VARCHAR, max_length=256, is_primary=True)
            )
            schema.add_field(FieldSchema(name="embedding", dtype=VectorDataType.FLOAT_VECTOR, dim=768))

            await store.create_collection(
                "test_collection", schema, distance_metric="dot"
            )

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.chroma_vector_store.chromadb")
    async def test_create_collection_missing_primary_key(self, mock_chromadb):
        """Test creating a collection without primary key raises BaseError."""
        mock_client = MagicMock()
        mock_chromadb.PersistentClient.return_value = mock_client
        mock_collection = MagicMock(metadata={})
        mock_client.get_or_create_collection.return_value = mock_collection
        # Execute the actual function passed to to_thread to trigger validation

        # Mock the task manager
        with patch('openjiuwen.core.foundation.store.vector.chroma_vector_store.get_task_manager') as mock_get_tm:
            mock_task_manager = MagicMock()
            mock_task = MagicMock()
            mock_task_manager.task_group.return_value.__aenter__ = AsyncMock()
            mock_task_manager.task_group.return_value.__aexit__ = AsyncMock()
            mock_task_manager.create_task = AsyncMock(return_value=mock_task)
            mock_get_tm.return_value = mock_task_manager

            store = ChromaVectorStore(persist_directory=tempfile.mkdtemp())

            schema = CollectionSchema()
            schema.add_field(FieldSchema(name="embedding", dtype=VectorDataType.FLOAT_VECTOR, dim=768))

            with pytest.raises(BaseError):
                await store.create_collection("test_collection", schema)

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.chroma_vector_store.chromadb")
    async def test_create_collection_missing_vector_field(self, mock_chromadb):
        """Test creating a collection without vector field raises BaseError."""
        mock_client = MagicMock()
        mock_chromadb.PersistentClient.return_value = mock_client
        mock_collection = MagicMock(metadata={})
        mock_client.get_or_create_collection.return_value = mock_collection
        # Execute the actual function passed to to_thread to trigger validation

        # Mock the task manager
        with patch('openjiuwen.core.foundation.store.vector.chroma_vector_store.get_task_manager') as mock_get_tm:
            mock_task_manager = MagicMock()
            mock_task = MagicMock()
            mock_task_manager.task_group.return_value.__aenter__ = AsyncMock()
            mock_task_manager.task_group.return_value.__aexit__ = AsyncMock()
            mock_task_manager.create_task = AsyncMock(return_value=mock_task)
            mock_get_tm.return_value = mock_task_manager

            store = ChromaVectorStore(persist_directory=tempfile.mkdtemp())

            schema = CollectionSchema()
            schema.add_field(
                FieldSchema(name="id", dtype=VectorDataType.VARCHAR, max_length=256, is_primary=True)
            )

            with pytest.raises(BaseError):
                await store.create_collection("test_collection", schema)


class TestChromaVectorStoreDeleteCollection:
    """Tests for delete_collection method."""

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.chroma_vector_store.chromadb")
    async def test_delete_collection_success(self, mock_chromadb):
        """Test successful deletion of a collection."""
        mock_client = MagicMock()
        mock_chromadb.PersistentClient.return_value = mock_client

        # Mock the task manager
        with patch('openjiuwen.core.foundation.store.vector.chroma_vector_store.get_task_manager') as mock_get_tm:
            mock_task_manager = MagicMock()
            mock_task = MagicMock()
            mock_task_manager.task_group.return_value.__aenter__ = AsyncMock()
            mock_task_manager.task_group.return_value.__aexit__ = AsyncMock()
            mock_task_manager.create_task = AsyncMock(return_value=mock_task)
            mock_get_tm.return_value = mock_task_manager

            store = ChromaVectorStore(persist_directory=tempfile.mkdtemp())

            await store.delete_collection("test_collection")

            assert mock_task_manager.create_task.call_count >= 1

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.chroma_vector_store.chromadb")
    async def test_delete_collection_failure(self, mock_chromadb):
        """Test deletion failure raises an exception."""
        mock_client = MagicMock()
        mock_chromadb.PersistentClient.return_value = mock_client

        # Mock the task manager
        with patch('openjiuwen.core.foundation.store.vector.chroma_vector_store.get_task_manager') as mock_get_tm:
            mock_task_manager = MagicMock()
            mock_task = MagicMock()
            mock_task_manager.task_group.return_value.__aenter__ = AsyncMock()
            mock_task_manager.task_group.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_task_manager.create_task = AsyncMock(side_effect=RuntimeError("Delete failed"))
            mock_get_tm.return_value = mock_task_manager

            store = ChromaVectorStore(persist_directory=tempfile.mkdtemp())

            with pytest.raises(RuntimeError, match="Delete failed"):
                await store.delete_collection("test_collection")


class TestChromaVectorStoreCollectionExists:
    """Tests for collection_exists method."""

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.chroma_vector_store.chromadb")
    async def test_collection_exists_true(self, mock_chromadb):
        """Test collection_exists returns True when collection exists."""
        mock_client = MagicMock()
        mock_chromadb.PersistentClient.return_value = mock_client
        mock_collection = MagicMock()
        mock_client.get_collection.return_value = mock_collection

        # Mock the task manager
        with patch('openjiuwen.core.foundation.store.vector.chroma_vector_store.get_task_manager') as mock_get_tm:
            mock_task_manager = MagicMock()
            mock_task = MagicMock()
            mock_task.result = True
            mock_task_manager.task_group.return_value.__aenter__ = AsyncMock()
            mock_task_manager.task_group.return_value.__aexit__ = AsyncMock()
            mock_task_manager.create_task = AsyncMock(return_value=mock_task)
            mock_get_tm.return_value = mock_task_manager

            store = ChromaVectorStore(persist_directory=tempfile.mkdtemp())

            result = await store.collection_exists("test_collection")

            assert result is True

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.chroma_vector_store.chromadb")
    async def test_collection_exists_false(self, mock_chromadb):
        """Test collection_exists returns False when collection does not exist."""
        mock_client = MagicMock()
        mock_chromadb.PersistentClient.return_value = mock_client
        mock_client.get_collection.side_effect = Exception("Collection not found")

        # Mock the task manager
        with patch('openjiuwen.core.foundation.store.vector.chroma_vector_store.get_task_manager') as mock_get_tm:
            mock_task_manager = MagicMock()
            mock_task = MagicMock()
            mock_task.result = False
            mock_task_manager.task_group.return_value.__aenter__ = AsyncMock()
            mock_task_manager.task_group.return_value.__aexit__ = AsyncMock()
            mock_task_manager.create_task = AsyncMock(return_value=mock_task)
            mock_get_tm.return_value = mock_task_manager

            store = ChromaVectorStore(persist_directory=tempfile.mkdtemp())

            result = await store.collection_exists("test_collection")

            assert result is False


class TestChromaVectorStoreGetSchema:
    """Tests for get_schema method."""

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.chroma_vector_store.chromadb")
    async def test_get_schema_from_metadata(self, mock_chromadb):
        """Test getting schema from collection metadata."""
        mock_client = MagicMock()
        mock_chromadb.PersistentClient.return_value = mock_client

        # Mock collection with schema in metadata
        mock_collection = MagicMock()
        schema = CollectionSchema(
            fields=[
                FieldSchema(name="id", dtype=VectorDataType.VARCHAR, max_length=256, is_primary=True),
                FieldSchema(name="embedding", dtype=VectorDataType.FLOAT_VECTOR, dim=768),
                FieldSchema(name="text", dtype=VectorDataType.VARCHAR, max_length=65535),
            ],
            description="Test collection",
            enable_dynamic_field=False,
        )
        mock_collection.metadata = {"schema": json.dumps(schema.model_dump())}
        mock_client.get_collection.return_value = mock_collection

        # Mock the task manager
        with patch('openjiuwen.core.foundation.store.vector.chroma_vector_store.get_task_manager') as mock_get_tm:
            mock_task_manager = MagicMock()
            mock_task = MagicMock()
            mock_task.result = schema
            mock_task_manager.task_group.return_value.__aenter__ = AsyncMock()
            mock_task_manager.task_group.return_value.__aexit__ = AsyncMock()
            mock_task_manager.create_task = AsyncMock(return_value=mock_task)
            mock_get_tm.return_value = mock_task_manager

            store = ChromaVectorStore(persist_directory=tempfile.mkdtemp())

            schema = await store.get_schema("test_collection")

            assert len(schema.fields) == 3
            assert schema.fields[0].name == "id"
            assert schema.fields[0].dtype == VectorDataType.VARCHAR
            assert schema.fields[0].is_primary is True

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.chroma_vector_store.chromadb")
    async def test_get_schema_default_fallback(self, mock_chromadb):
        """Test getting schema returns default when metadata not available."""
        mock_client = MagicMock()
        mock_chromadb.PersistentClient.return_value = mock_client

        # Mock collection without schema metadata
        mock_collection = MagicMock()
        mock_collection.metadata = {}
        mock_client.get_collection.return_value = mock_collection

        schema = CollectionSchema(
            fields=[
                FieldSchema(name="id", dtype=VectorDataType.VARCHAR, max_length=256, is_primary=True),
                FieldSchema(name="embedding", dtype=VectorDataType.FLOAT_VECTOR, dim=768),
                FieldSchema(name="text", dtype=VectorDataType.VARCHAR, max_length=65535),
            ],
            description="Test collection",
            enable_dynamic_field=False,
        )
        # Mock the task manager
        with patch('openjiuwen.core.foundation.store.vector.chroma_vector_store.get_task_manager') as mock_get_tm:
            mock_task_manager = MagicMock()
            mock_task = MagicMock()
            mock_task.result = schema
            mock_task_manager.task_group.return_value.__aenter__ = AsyncMock()
            mock_task_manager.task_group.return_value.__aexit__ = AsyncMock()
            mock_task_manager.create_task = AsyncMock(return_value=mock_task)
            mock_get_tm.return_value = mock_task_manager

            store = ChromaVectorStore(persist_directory=tempfile.mkdtemp())
            schema = await store.get_schema("test_collection")
            # Should return default schema with id, embedding, text, metadata fields
            assert len(schema.fields) >= 3
            assert schema.has_field("id")
            assert schema.has_field("embedding")
            assert schema.has_field("text")

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.chroma_vector_store.chromadb")
    async def test_get_schema_collection_not_exists(self, mock_chromadb):
        """Test getting schema for non-existent collection raises BaseError."""
        mock_client = MagicMock()
        mock_chromadb.PersistentClient.return_value = mock_client
        mock_client.get_collection.side_effect = Exception("Collection not found")

        store = ChromaVectorStore(persist_directory=tempfile.mkdtemp())

        with pytest.raises(ValidationError):
            await store.get_schema("non_existent_collection")


class TestChromaVectorStoreAddDocs:
    """Tests for add_docs method."""

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.chroma_vector_store.chromadb")
    async def test_add_docs_success(self, mock_chromadb):
        """Test adding documents successfully."""
        mock_client = MagicMock()
        mock_chromadb.PersistentClient.return_value = mock_client

        # Mock collection with field_mapping metadata
        mock_collection = MagicMock()
        field_mapping = {
            "primary_key": "id",
            "vector_field": "embedding",
            "text_field": "text",
        }
        mock_collection.metadata = {"field_mapping": json.dumps(field_mapping)}
        mock_client.get_collection.return_value = mock_collection

        # Mock the task manager
        with patch('openjiuwen.core.foundation.store.vector.chroma_vector_store.get_task_manager') as mock_get_tm:
            mock_task_manager = MagicMock()
            mock_task = MagicMock()
            mock_task_manager.task_group.return_value.__aenter__ = AsyncMock()
            mock_task_manager.task_group.return_value.__aexit__ = AsyncMock()
            mock_task_manager.create_task = AsyncMock(return_value=mock_task)
            mock_get_tm.return_value = mock_task_manager

            store = ChromaVectorStore(persist_directory=tempfile.mkdtemp())

            docs = [
                {
                    "id": "doc1",
                    "embedding": [0.1, 0.2, 0.3],
                    "text": "Test document 1",
                    "metadata": {"source": "test1"},
                },
                {
                    "id": "doc2",
                    "embedding": [0.4, 0.5, 0.6],
                    "text": "Test document 2",
                    "metadata": {"source": "test2"},
                },
            ]

            await store.add_docs("test_collection", docs)

            assert mock_task_manager.create_task.call_count >= 1

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.chroma_vector_store.chromadb")
    async def test_add_docs_missing_id(self, mock_chromadb):
        """Test adding a document without an id raises BaseError."""
        mock_client = MagicMock()
        mock_chromadb.PersistentClient.return_value = mock_client

        # Mock collection with field_mapping metadata
        mock_collection = MagicMock()
        field_mapping = {
            "primary_key": "id",
            "vector_field": "embedding",
            "text_field": "text",
        }
        mock_collection.metadata = {"field_mapping": json.dumps(field_mapping)}
        mock_client.get_collection.return_value = mock_collection

        # Mock the task manager
        with patch('openjiuwen.core.foundation.store.vector.chroma_vector_store.get_task_manager') as mock_get_tm:
            mock_task_manager = MagicMock()
            mock_task = MagicMock()
            mock_task_manager.task_group.return_value.__aenter__ = AsyncMock()
            mock_task_manager.task_group.return_value.__aexit__ = AsyncMock()
            mock_task_manager.create_task = AsyncMock(return_value=mock_task)
            mock_get_tm.return_value = mock_task_manager

            store = ChromaVectorStore(persist_directory=tempfile.mkdtemp())

            docs = [
                {
                    "embedding": [0.1, 0.2, 0.3],
                    "text": "Test document",
                },
            ]

            with pytest.raises(BaseError, match="primary field"):
                await store.add_docs("test_collection", docs)

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.chroma_vector_store.chromadb")
    async def test_add_docs_missing_embedding(self, mock_chromadb):
        """Test adding a document without an embedding raises BaseError."""
        mock_client = MagicMock()
        mock_chromadb.PersistentClient.return_value = mock_client

        # Mock collection with field_mapping metadata
        mock_collection = MagicMock()
        field_mapping = {
            "primary_key": "id",
            "vector_field": "embedding",
            "text_field": "text",
        }
        mock_collection.metadata = {"field_mapping": json.dumps(field_mapping)}
        mock_client.get_collection.return_value = mock_collection

        # Mock the task manager
        with patch('openjiuwen.core.foundation.store.vector.chroma_vector_store.get_task_manager') as mock_get_tm:
            mock_task_manager = MagicMock()
            mock_task = MagicMock()
            mock_task_manager.task_group.return_value.__aenter__ = AsyncMock()
            mock_task_manager.task_group.return_value.__aexit__ = AsyncMock()
            mock_task_manager.create_task = AsyncMock(return_value=mock_task)
            mock_get_tm.return_value = mock_task_manager

            store = ChromaVectorStore(persist_directory=tempfile.mkdtemp())

            docs = [
                {
                    "id": "doc1",
                    "text": "Test document",
                },
            ]

            with pytest.raises(BaseError, match="vector field"):
                await store.add_docs("test_collection", docs)

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.chroma_vector_store.chromadb")
    async def test_add_docs_with_batch_size(self, mock_chromadb):
        """Test adding documents with custom batch size."""
        mock_client = MagicMock()
        mock_chromadb.PersistentClient.return_value = mock_client

        # Mock collection with field_mapping metadata
        mock_collection = MagicMock()
        field_mapping = {
            "primary_key": "id",
            "vector_field": "embedding",
            "text_field": "text",
        }
        mock_collection.metadata = {"field_mapping": json.dumps(field_mapping)}
        mock_client.get_collection.return_value = mock_collection

        # Mock the task manager
        with patch('openjiuwen.core.foundation.store.vector.chroma_vector_store.get_task_manager') as mock_get_tm:
            mock_task_manager = MagicMock()
            mock_task = MagicMock()
            mock_task_manager.task_group.return_value.__aenter__ = AsyncMock()
            mock_task_manager.task_group.return_value.__aexit__ = AsyncMock()
            mock_task_manager.create_task = AsyncMock(return_value=mock_task)
            mock_get_tm.return_value = mock_task_manager

            store = ChromaVectorStore(persist_directory=tempfile.mkdtemp())

            docs = [
                {
                    "id": f"doc{i}",
                    "embedding": [0.1, 0.2, 0.3],
                    "text": f"Test document {i}",
                }
                for i in range(10)
            ]

            await store.add_docs("test_collection", docs, batch_size=3)

            # Should process in 4 batches: 3, 3, 3, 1
            assert mock_task_manager.create_task.call_count >= 4

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.chroma_vector_store.chromadb")
    async def test_add_docs_with_list_metadata(self, mock_chromadb):
        """Test adding documents with list metadata (should be JSON serialized)."""
        mock_client = MagicMock()
        mock_chromadb.PersistentClient.return_value = mock_client

        # Mock collection with field_mapping metadata
        mock_collection = MagicMock()
        field_mapping = {
            "primary_key": "id",
            "vector_field": "embedding",
            "text_field": "text",
        }
        mock_collection.metadata = {"field_mapping": json.dumps(field_mapping)}
        mock_client.get_collection.return_value = mock_collection

        # Mock the task manager
        with patch('openjiuwen.core.foundation.store.vector.chroma_vector_store.get_task_manager') as mock_get_tm:
            mock_task_manager = MagicMock()
            mock_task = MagicMock()
            mock_task_manager.task_group.return_value.__aenter__ = AsyncMock()
            mock_task_manager.task_group.return_value.__aexit__ = AsyncMock()
            mock_task_manager.create_task = AsyncMock(return_value=mock_task)
            mock_get_tm.return_value = mock_task_manager

            store = ChromaVectorStore(persist_directory=tempfile.mkdtemp())

            docs = [
                {
                    "id": "doc1",
                    "embedding": [0.1, 0.2, 0.3],
                    "text": "Test document",
                    "tags": ["tag1", "tag2"],
                },
            ]

            await store.add_docs("test_collection", docs)

            assert mock_task_manager.create_task.call_count >= 1

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.chroma_vector_store.chromadb")
    async def test_add_docs_zero_batch_size(self, mock_chromadb):
        """Test adding documents with zero batch size uses default."""
        mock_client = MagicMock()
        mock_chromadb.PersistentClient.return_value = mock_client

        # Mock collection with field_mapping metadata
        mock_collection = MagicMock()
        field_mapping = {
            "primary_key": "id",
            "vector_field": "embedding",
            "text_field": "text",
        }
        mock_collection.metadata = {"field_mapping": json.dumps(field_mapping)}
        mock_client.get_collection.return_value = mock_collection

        # Mock the task manager
        with patch('openjiuwen.core.foundation.store.vector.chroma_vector_store.get_task_manager') as mock_get_tm:
            mock_task_manager = MagicMock()
            mock_task = MagicMock()
            mock_task_manager.task_group.return_value.__aenter__ = AsyncMock()
            mock_task_manager.task_group.return_value.__aexit__ = AsyncMock()
            mock_task_manager.create_task = AsyncMock(return_value=mock_task)
            mock_get_tm.return_value = mock_task_manager

            store = ChromaVectorStore(persist_directory=tempfile.mkdtemp())

            docs = [
                {
                    "id": "doc1",
                    "embedding": [0.1, 0.2, 0.3],
                    "text": "Test document",
                },
            ]

            await store.add_docs("test_collection", docs, batch_size=0)

            assert mock_task_manager.create_task.call_count >= 1


class TestChromaVectorStoreSearch:
    """Tests for search method."""

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.chroma_vector_store.chromadb")
    async def test_search_success(self, mock_chromadb):
        """Test successful vector search."""
        mock_client = MagicMock()
        mock_chromadb.PersistentClient.return_value = mock_client

        # Mock collection with field_mapping metadata
        mock_collection = MagicMock()
        field_mapping = {
            "primary_key": "id",
            "vector_field": "embedding",
            "text_field": "text",
        }
        mock_collection.metadata = {
            "distance_metric": "cosine",
            "field_mapping": json.dumps(field_mapping)
        }
        mock_collection.query.return_value = {
            "ids": [["doc1", "doc2"]],
            "documents": [["Text 1", "Text 2"]],
            "metadatas": [[{"source": "test1"}, {"source": "test2"}]],
            "distances": [[0.1, 0.3]],
        }
        mock_client.get_collection.return_value = mock_collection

        # Mock the task manager
        with patch('openjiuwen.core.foundation.store.vector.chroma_vector_store.get_task_manager') as mock_get_tm:
            mock_task_manager = MagicMock()
            mock_task = MagicMock()
            mock_task.result = mock_collection.query.return_value
            mock_task_manager.task_group.return_value.__aenter__ = AsyncMock()
            mock_task_manager.task_group.return_value.__aexit__ = AsyncMock()
            mock_task_manager.create_task = AsyncMock(return_value=mock_task)
            mock_get_tm.return_value = mock_task_manager

            store = ChromaVectorStore(persist_directory=tempfile.mkdtemp())

            results = await store.search(
                "test_collection", [0.1, 0.2, 0.3], "embedding", top_k=5
            )

            assert len(results) == 2
            assert results[0].fields["id"] == "doc1"
            assert results[0].fields["text"] == "Text 1"
            assert results[0].score > 0

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.chroma_vector_store.chromadb")
    async def test_search_with_filters(self, mock_chromadb):
        """Test search with filters."""
        mock_client = MagicMock()
        mock_chromadb.PersistentClient.return_value = mock_client

        # Mock collection with field_mapping metadata
        mock_collection = MagicMock()
        field_mapping = {
            "primary_key": "id",
            "vector_field": "embedding",
            "text_field": "text",
        }
        mock_collection.metadata = {
            "distance_metric": "cosine",
            "field_mapping": json.dumps(field_mapping)
        }
        mock_collection.query.return_value = {
            "ids": [["doc1"]],
            "documents": [["Text 1"]],
            "metadatas": [[{"source": "test1"}]],
            "distances": [[0.1]],
        }
        mock_client.get_collection.return_value = mock_collection

        # Mock the task manager
        with patch('openjiuwen.core.foundation.store.vector.chroma_vector_store.get_task_manager') as mock_get_tm:
            mock_task_manager = MagicMock()
            mock_task = MagicMock()
            mock_task.result = mock_collection.query.return_value
            mock_task_manager.task_group.return_value.__aenter__ = AsyncMock()
            mock_task_manager.task_group.return_value.__aexit__ = AsyncMock()
            mock_task_manager.create_task = AsyncMock(return_value=mock_task)
            mock_get_tm.return_value = mock_task_manager

            store = ChromaVectorStore(persist_directory=tempfile.mkdtemp())

            results = await store.search(
                "test_collection",
                [0.1, 0.2, 0.3],
                "embedding",
                top_k=5,
                filters={"source": "test1"},
            )

            assert len(results) == 1

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.chroma_vector_store.chromadb")
    async def test_search_cosine_distance_conversion(self, mock_chromadb):
        """Test cosine distance to similarity score conversion."""
        mock_client = MagicMock()
        mock_chromadb.PersistentClient.return_value = mock_client

        # Mock collection with field_mapping metadata
        mock_collection = MagicMock()
        field_mapping = {
            "primary_key": "id",
            "vector_field": "embedding",
            "text_field": "text",
        }
        mock_collection.metadata = {
            "distance_metric": "cosine",
            "field_mapping": json.dumps(field_mapping)
        }
        # Cosine distance of 0.0 should give similarity of 1.0
        # Cosine distance of 2.0 should give similarity of 0.0
        mock_collection.query.return_value = {
            "ids": [["doc1", "doc2"]],
            "documents": [["Text 1", "Text 2"]],
            "metadatas": [[{}, {}]],
            "distances": [[0.0, 2.0]],
        }
        mock_client.get_collection.return_value = mock_collection

        # Mock the task manager
        with patch('openjiuwen.core.foundation.store.vector.chroma_vector_store.get_task_manager') as mock_get_tm:
            mock_task_manager = MagicMock()
            mock_task = MagicMock()
            mock_task.result = mock_collection.query.return_value
            mock_task_manager.task_group.return_value.__aenter__ = AsyncMock()
            mock_task_manager.task_group.return_value.__aexit__ = AsyncMock()
            mock_task_manager.create_task = AsyncMock(return_value=mock_task)
            mock_get_tm.return_value = mock_task_manager

            store = ChromaVectorStore(persist_directory=tempfile.mkdtemp())

            results = await store.search(
                "test_collection", [0.1, 0.2, 0.3], "embedding", top_k=5
            )

            assert results[0].score == 1.0
            assert results[1].score == 0.0

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.chroma_vector_store.chromadb")
    async def test_search_l2_distance_conversion(self, mock_chromadb):
        """Test L2 distance to similarity score conversion."""
        mock_client = MagicMock()
        mock_chromadb.PersistentClient.return_value = mock_client

        # Mock collection with field_mapping metadata
        mock_collection = MagicMock()
        field_mapping = {
            "primary_key": "id",
            "vector_field": "embedding",
            "text_field": "text",
        }
        mock_collection.metadata = {
            "distance_metric": "l2",
            "field_mapping": json.dumps(field_mapping)
        }
        # L2 distance of 0.0 should give similarity of 1.0
        mock_collection.query.return_value = {
            "ids": [["doc1"]],
            "documents": [["Text 1"]],
            "metadatas": [[{}]],
            "distances": [[0.0]],
        }
        mock_client.get_collection.return_value = mock_collection

        # Mock the task manager
        with patch('openjiuwen.core.foundation.store.vector.chroma_vector_store.get_task_manager') as mock_get_tm:
            mock_task_manager = MagicMock()
            mock_task = MagicMock()
            mock_task.result = mock_collection.query.return_value
            mock_task_manager.task_group.return_value.__aenter__ = AsyncMock()
            mock_task_manager.task_group.return_value.__aexit__ = AsyncMock()
            mock_task_manager.create_task = AsyncMock(return_value=mock_task)
            mock_get_tm.return_value = mock_task_manager

            store = ChromaVectorStore(persist_directory=tempfile.mkdtemp())

            results = await store.search(
                "test_collection", [0.1, 0.2, 0.3], "embedding", top_k=5
            )

            assert results[0].score == 1.0

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.chroma_vector_store.chromadb")
    async def test_search_ip_distance_conversion(self, mock_chromadb):
        """Test IP (inner product) distance to similarity score conversion."""
        mock_client = MagicMock()
        mock_chromadb.PersistentClient.return_value = mock_client

        # Mock collection with field_mapping metadata
        mock_collection = MagicMock()
        field_mapping = {
            "primary_key": "id",
            "vector_field": "embedding",
            "text_field": "text",
        }
        mock_collection.metadata = {
            "distance_metric": "ip",
            "field_mapping": json.dumps(field_mapping)
        }
        mock_collection.query.return_value = {
            "ids": [["doc1"]],
            "documents": [["Text 1"]],
            "metadatas": [[{}]],
            "distances": [[0.5]],
        }
        mock_client.get_collection.return_value = mock_collection

        # Mock the task manager
        with patch('openjiuwen.core.foundation.store.vector.chroma_vector_store.get_task_manager') as mock_get_tm:
            mock_task_manager = MagicMock()
            mock_task = MagicMock()
            mock_task.result = mock_collection.query.return_value
            mock_task_manager.task_group.return_value.__aenter__ = AsyncMock()
            mock_task_manager.task_group.return_value.__aexit__ = AsyncMock()
            mock_task_manager.create_task = AsyncMock(return_value=mock_task)
            mock_get_tm.return_value = mock_task_manager

            store = ChromaVectorStore(persist_directory=tempfile.mkdtemp())

            results = await store.search(
                "test_collection", [0.1, 0.2, 0.3], "embedding", top_k=5
            )

            assert 0.0 <= results[0].score <= 1.0

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.chroma_vector_store.chromadb")
    async def test_search_empty_results(self, mock_chromadb):
        """Test search with no results."""
        mock_client = MagicMock()
        mock_chromadb.PersistentClient.return_value = mock_client

        # Mock collection with field_mapping metadata
        mock_collection = MagicMock()
        field_mapping = {
            "primary_key": "id",
            "vector_field": "embedding",
            "text_field": "text",
        }
        mock_collection.metadata = {
            "distance_metric": "cosine",
            "field_mapping": json.dumps(field_mapping)
        }
        mock_collection.query.return_value = {
            "ids": [],
            "documents": [],
            "metadatas": [],
            "distances": [],
        }
        mock_client.get_collection.return_value = mock_collection

        # Mock the task manager
        with patch('openjiuwen.core.foundation.store.vector.chroma_vector_store.get_task_manager') as mock_get_tm:
            mock_task_manager = MagicMock()
            mock_task = MagicMock()
            mock_task.result = mock_collection.query.return_value
            mock_task_manager.task_group.return_value.__aenter__ = AsyncMock()
            mock_task_manager.task_group.return_value.__aexit__ = AsyncMock()
            mock_task_manager.create_task = AsyncMock(return_value=mock_task)
            mock_get_tm.return_value = mock_task_manager

            store = ChromaVectorStore(persist_directory=tempfile.mkdtemp())

            results = await store.search(
                "test_collection", [0.1, 0.2, 0.3], "embedding", top_k=5
            )

            assert len(results) == 0

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.chroma_vector_store.chromadb")
    async def test_search_with_json_metadata(self, mock_chromadb):
        """Test search with JSON strings in metadata are parsed."""
        mock_client = MagicMock()
        mock_chromadb.PersistentClient.return_value = mock_client

        # Mock collection with field_mapping metadata
        mock_collection = MagicMock()
        field_mapping = {
            "primary_key": "id",
            "vector_field": "embedding",
            "text_field": "text",
        }
        mock_collection.metadata = {
            "distance_metric": "cosine",
            "field_mapping": json.dumps(field_mapping)
        }
        mock_collection.query.return_value = {
            "ids": [["doc1"]],
            "documents": [["Text 1"]],
            "metadatas": [[{"tags": json.dumps(["tag1", "tag2"])}]],
            "distances": [[0.1]],
        }
        mock_client.get_collection.return_value = mock_collection

        # Mock the task manager
        with patch('openjiuwen.core.foundation.store.vector.chroma_vector_store.get_task_manager') as mock_get_tm:
            mock_task_manager = MagicMock()
            mock_task = MagicMock()
            mock_task.result = mock_collection.query.return_value
            mock_task_manager.task_group.return_value.__aenter__ = AsyncMock()
            mock_task_manager.task_group.return_value.__aexit__ = AsyncMock()
            mock_task_manager.create_task = AsyncMock(return_value=mock_task)
            mock_get_tm.return_value = mock_task_manager

            store = ChromaVectorStore(persist_directory=tempfile.mkdtemp())

            results = await store.search(
                "test_collection", [0.1, 0.2, 0.3], "embedding", top_k=5
            )

            assert results[0].fields["tags"] == ["tag1", "tag2"]

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.chroma_vector_store.chromadb")
    async def test_search_with_invalid_json_metadata(self, mock_chromadb):
        """Test search with invalid JSON strings in metadata keeps original value."""
        mock_client = MagicMock()
        mock_chromadb.PersistentClient.return_value = mock_client

        # Mock collection with field_mapping metadata
        mock_collection = MagicMock()
        field_mapping = {
            "primary_key": "id",
            "vector_field": "embedding",
            "text_field": "text",
        }
        mock_collection.metadata = {
            "distance_metric": "cosine",
            "field_mapping": json.dumps(field_mapping)
        }
        mock_collection.query.return_value = {
            "ids": [["doc1"]],
            "documents": [["Text 1"]],
            "metadatas": [[{"tags": "invalid json"}]],
            "distances": [[0.1]],
        }
        mock_client.get_collection.return_value = mock_collection

        # Mock the task manager
        with patch('openjiuwen.core.foundation.store.vector.chroma_vector_store.get_task_manager') as mock_get_tm:
            mock_task_manager = MagicMock()
            mock_task = MagicMock()
            mock_task.result = mock_collection.query.return_value
            mock_task_manager.task_group.return_value.__aenter__ = AsyncMock()
            mock_task_manager.task_group.return_value.__aexit__ = AsyncMock()
            mock_task_manager.create_task = AsyncMock(return_value=mock_task)
            mock_get_tm.return_value = mock_task_manager

            store = ChromaVectorStore(persist_directory=tempfile.mkdtemp())

            results = await store.search(
                "test_collection", [0.1, 0.2, 0.3], "embedding", top_k=5
            )

            assert results[0].fields["tags"] == "invalid json"


class TestChromaVectorStoreDeleteDocsByIds:
    """Tests for delete_docs_by_ids method."""

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.chroma_vector_store.chromadb")
    async def test_delete_docs_by_ids_success(self, mock_chromadb):
        """Test deleting documents by ids successfully."""
        mock_client = MagicMock()
        mock_chromadb.PersistentClient.return_value = mock_client

        # Mock the task manager
        with patch('openjiuwen.core.foundation.store.vector.chroma_vector_store.get_task_manager') as mock_get_tm:
            mock_task_manager = MagicMock()
            mock_task = MagicMock()
            mock_task_manager.task_group.return_value.__aenter__ = AsyncMock()
            mock_task_manager.task_group.return_value.__aexit__ = AsyncMock()
            mock_task_manager.create_task = AsyncMock(return_value=mock_task)
            mock_get_tm.return_value = mock_task_manager

            store = ChromaVectorStore(persist_directory=tempfile.mkdtemp())

            await store.delete_docs_by_ids("test_collection", ["doc1", "doc2"])

            assert mock_task_manager.create_task.call_count >= 1


class TestChromaVectorStoreDeleteDocsByFilters:
    """Tests for delete_docs_by_filters method."""

    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.store.vector.chroma_vector_store.chromadb")
    async def test_delete_docs_by_filters_success(self, mock_chromadb):
        """Test deleting documents by filters successfully."""
        mock_client = MagicMock()
        mock_chromadb.PersistentClient.return_value = mock_client


        # Mock the task manager
        with patch('openjiuwen.core.foundation.store.vector.chroma_vector_store.get_task_manager') as mock_get_tm:
            mock_task_manager = MagicMock()
            mock_task = MagicMock()
            mock_task_manager.task_group.return_value.__aenter__ = AsyncMock()
            mock_task_manager.task_group.return_value.__aexit__ = AsyncMock()
            mock_task_manager.create_task = AsyncMock(return_value=mock_task)
            mock_get_tm.return_value = mock_task_manager

            store = ChromaVectorStore(persist_directory=tempfile.mkdtemp())

            await store.delete_docs_by_filters("test_collection", {"source": "test"})
            assert mock_task_manager.create_task.call_count >= 1
