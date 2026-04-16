# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for ElasticsearchVectorStore."""

import json
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.store.base_vector_store import (
    CollectionSchema,
    VectorDataType,
    FieldSchema,
    VectorSearchResult,
)
from openjiuwen.extensions.store.vector.es_vector_store import ElasticsearchVectorStore, _get_primary_key_field


class TestElasticsearchVectorStoreInit:
    """Tests for ElasticsearchVectorStore initialization."""

    def test_init_with_defaults(self):
        """Test initialization with default parameters."""
        mock_es = AsyncMock()
        store = ElasticsearchVectorStore(es=mock_es)
        assert store._es is mock_es
        assert store._index_prefix == "agent_vector"
        assert store._metadata_cache == {}

    def test_init_with_custom_prefix(self):
        """Test initialization with custom index prefix."""
        mock_es = AsyncMock()
        store = ElasticsearchVectorStore(es=mock_es, index_prefix="custom_prefix")
        assert store._es is mock_es
        assert store._index_prefix == "custom_prefix"

    def test_index_name(self):
        """Test index name generation."""
        mock_es = AsyncMock()
        store = ElasticsearchVectorStore(es=mock_es, index_prefix="my_prefix")
        assert store._index_name("test_coll") == "my_prefix__test_coll"

    def test_map_es_type_vector(self):
        """Test mapping FLOAT_VECTOR type to ES dense_vector."""
        mock_es = AsyncMock()
        store = ElasticsearchVectorStore(es=mock_es)
        field = FieldSchema(name="embedding", dtype=VectorDataType.FLOAT_VECTOR, dim=768)
        result = ElasticsearchVectorStore._map_es_type(field)
        assert result["type"] == "dense_vector"
        assert result["dims"] == 768
        assert result["index"] is True
        assert result["similarity"] == "cosine"

    def test_map_es_type_varchar(self):
        """Test mapping VARCHAR type to ES keyword."""
        mock_es = AsyncMock()
        store = ElasticsearchVectorStore(es=mock_es)
        field = FieldSchema(name="text", dtype=VectorDataType.VARCHAR)
        result = ElasticsearchVectorStore._map_es_type(field)
        assert result["type"] == "keyword"

    def test_map_es_type_int64(self):
        """Test mapping INT64 type to ES long."""
        mock_es = AsyncMock()
        store = ElasticsearchVectorStore(es=mock_es)
        field = FieldSchema(name="count", dtype=VectorDataType.INT64)
        result = ElasticsearchVectorStore._map_es_type(field)
        assert result["type"] == "long"

    def test_map_es_type_int32(self):
        """Test mapping INT32 type to ES integer."""
        mock_es = AsyncMock()
        store = ElasticsearchVectorStore(es=mock_es)
        field = FieldSchema(name="age", dtype=VectorDataType.INT32)
        result = ElasticsearchVectorStore._map_es_type(field)
        assert result["type"] == "integer"

    def test_map_es_type_float(self):
        """Test mapping FLOAT type to ES float."""
        mock_es = AsyncMock()
        store = ElasticsearchVectorStore(es=mock_es)
        field = FieldSchema(name="score", dtype=VectorDataType.FLOAT)
        result = ElasticsearchVectorStore._map_es_type(field)
        assert result["type"] == "float"

    def test_map_es_type_double(self):
        """Test mapping DOUBLE type to ES double."""
        mock_es = AsyncMock()
        store = ElasticsearchVectorStore(es=mock_es)
        field = FieldSchema(name="value", dtype=VectorDataType.DOUBLE)
        result = ElasticsearchVectorStore._map_es_type(field)
        assert result["type"] == "double"

    def test_map_es_type_bool(self):
        """Test mapping BOOL type to ES boolean."""
        mock_es = AsyncMock()
        store = ElasticsearchVectorStore(es=mock_es)
        field = FieldSchema(name="is_active", dtype=VectorDataType.BOOL)
        result = ElasticsearchVectorStore._map_es_type(field)
        assert result["type"] == "boolean"

    def test_map_es_type_json(self):
        """Test mapping JSON type to ES object."""
        mock_es = AsyncMock()
        store = ElasticsearchVectorStore(es=mock_es)
        field = FieldSchema(name="metadata", dtype=VectorDataType.JSON)
        result = ElasticsearchVectorStore._map_es_type(field)
        assert result["type"] == "object"
        assert result["enabled"] is True


class TestElasticsearchVectorStoreCreateCollection:
    """Tests for create_collection method."""

    @pytest.mark.asyncio
    async def test_create_collection_with_schema_object(self):
        """Test creating a collection with a CollectionSchema object."""
        mock_es = AsyncMock()
        exists_resp = MagicMock()
        exists_resp.body = False
        mock_es.indices.exists = AsyncMock(return_value=exists_resp)
        mock_es.indices.create = AsyncMock()
        mock_es.index = AsyncMock()
        store = ElasticsearchVectorStore(es=mock_es)

        schema = CollectionSchema(description="Test collection")
        schema.add_field(FieldSchema(name="id", dtype=VectorDataType.VARCHAR, is_primary=True))
        schema.add_field(FieldSchema(name="embedding", dtype=VectorDataType.FLOAT_VECTOR, dim=768))
        schema.add_field(FieldSchema(name="text", dtype=VectorDataType.VARCHAR))

        await store.create_collection("test_collection", schema)

        mock_es.indices.exists.assert_called_once_with(index="agent_vector__test_collection")
        mock_es.indices.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_collection_with_dict_schema(self):
        """Test creating a collection with a schema dictionary."""
        mock_es = AsyncMock()
        exists_resp = MagicMock()
        exists_resp.body = False
        mock_es.indices.exists = AsyncMock(return_value=exists_resp)
        mock_es.indices.create = AsyncMock()
        mock_es.index = AsyncMock()
        store = ElasticsearchVectorStore(es=mock_es)

        schema_dict = {
            "fields": [
                {"name": "id", "type": "VARCHAR", "is_primary": True},
                {"name": "embedding", "type": "FLOAT_VECTOR", "dim": 768},
                {"name": "text", "type": "VARCHAR"},
            ],
            "description": "Test collection",
        }

        await store.create_collection("test_collection", schema_dict)

        mock_es.indices.exists.assert_called_once()
        mock_es.indices.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_collection_already_exists(self):
        """Test creating a collection that already exists does nothing."""
        mock_es = AsyncMock()
        exists_resp = MagicMock()
        exists_resp.body = True
        mock_es.indices.exists = AsyncMock(return_value=exists_resp)
        mock_es.indices.create = AsyncMock()
        store = ElasticsearchVectorStore(es=mock_es)

        schema = CollectionSchema()
        schema.add_field(FieldSchema(name="id", dtype=VectorDataType.VARCHAR, is_primary=True))
        schema.add_field(FieldSchema(name="embedding", dtype=VectorDataType.FLOAT_VECTOR, dim=768))

        await store.create_collection("test_collection", schema)

        mock_es.indices.exists.assert_called_once()
        mock_es.indices.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_collection_missing_vector_field(self):
        """Test creating a collection without FLOAT_VECTOR field raises BaseError."""
        mock_es = AsyncMock()
        exists_resp = MagicMock()
        exists_resp.body = False
        mock_es.indices.exists = AsyncMock(return_value=exists_resp)
        store = ElasticsearchVectorStore(es=mock_es)

        schema = CollectionSchema()
        schema.add_field(FieldSchema(name="id", dtype=VectorDataType.VARCHAR, is_primary=True))

        with pytest.raises(BaseError, match="must contain at least one FLOAT_VECTOR field"):
            await store.create_collection("test_collection", schema)

    @pytest.mark.asyncio
    async def test_create_collection_with_custom_metric(self):
        """Test creating a collection with custom distance metric."""
        mock_es = AsyncMock()
        exists_resp = MagicMock()
        exists_resp.body = False
        mock_es.indices.exists = AsyncMock(return_value=exists_resp)
        mock_es.indices.create = AsyncMock()
        mock_es.index = AsyncMock()
        store = ElasticsearchVectorStore(es=mock_es)

        schema = CollectionSchema()
        schema.add_field(FieldSchema(name="id", dtype=VectorDataType.VARCHAR, is_primary=True))
        schema.add_field(FieldSchema(name="embedding", dtype=VectorDataType.FLOAT_VECTOR, dim=768))

        await store.create_collection("test_collection", schema, distance_metric="L2")

        call_args = mock_es.indices.create.call_args
        body = call_args[1]["body"]
        mapping = body["mappings"]["properties"]["embedding"]
        assert mapping["similarity"] == "l2_norm"


class TestElasticsearchVectorStoreDeleteCollection:
    """Tests for delete_collection method."""

    @pytest.mark.asyncio
    async def test_delete_collection_success(self):
        """Test successful deletion of a collection."""
        mock_es = AsyncMock()
        exists_resp = MagicMock()
        exists_resp.body = True
        mock_es.indices.exists = AsyncMock(return_value=exists_resp)
        mock_es.indices.delete = AsyncMock()
        store = ElasticsearchVectorStore(es=mock_es)

        await store.delete_collection("test_collection")

        mock_es.indices.exists.assert_called_once_with(index="agent_vector__test_collection")
        mock_es.indices.delete.assert_called_once_with(index="agent_vector__test_collection")

    @pytest.mark.asyncio
    async def test_delete_collection_not_exists(self):
        """Test deleting a collection that doesn't exist."""
        mock_es = AsyncMock()
        exists_resp = MagicMock()
        exists_resp.body = False
        mock_es.indices.exists = AsyncMock(return_value=exists_resp)
        mock_es.indices.delete = AsyncMock()
        store = ElasticsearchVectorStore(es=mock_es)

        await store.delete_collection("test_collection")

        mock_es.indices.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_collection_error(self):
        """Test deletion with error raises exception."""
        mock_es = AsyncMock()
        exists_resp = MagicMock()
        exists_resp.body = True
        mock_es.indices.exists = AsyncMock(return_value=exists_resp)
        mock_es.indices.delete = AsyncMock(side_effect=Exception("Delete failed"))
        store = ElasticsearchVectorStore(es=mock_es)

        with pytest.raises(Exception, match="Delete failed"):
            await store.delete_collection("test_collection")


class TestElasticsearchVectorStoreCollectionExists:
    """Tests for collection_exists method."""

    @pytest.mark.asyncio
    async def test_collection_exists_true(self):
        """Test collection_exists returns True when collection exists."""
        mock_es = AsyncMock()
        exists_resp = MagicMock()
        exists_resp.body = True
        mock_es.indices.exists = AsyncMock(return_value=exists_resp)
        store = ElasticsearchVectorStore(es=mock_es)

        result = await store.collection_exists("test_collection")

        assert result is True

    @pytest.mark.asyncio
    async def test_collection_exists_false(self):
        """Test collection_exists returns False when collection does not exist."""
        mock_es = AsyncMock()
        exists_resp = MagicMock()
        exists_resp.body = False
        mock_es.indices.exists = AsyncMock(return_value=exists_resp)
        store = ElasticsearchVectorStore(es=mock_es)

        result = await store.collection_exists("test_collection")

        assert result is False

    @pytest.mark.asyncio
    async def test_collection_exists_error(self):
        """Test collection_exists returns False on error."""
        mock_es = AsyncMock()
        mock_es.indices.exists = AsyncMock(side_effect=Exception("ES error"))
        store = ElasticsearchVectorStore(es=mock_es)

        result = await store.collection_exists("test_collection")

        assert result is False


class TestElasticsearchVectorStoreGetSchema:
    """Tests for get_schema method."""

    @pytest.mark.asyncio
    async def test_get_schema_from_metadata(self):
        """Test getting schema from stored metadata."""
        mock_es = AsyncMock()
        mock_es.get = AsyncMock(
            return_value={
                "found": True,
                "_source": {
                    "_meta": {
                        "schema": {
                            "fields": [
                                {"name": "id", "type": "VARCHAR", "is_primary": True},
                                {"name": "embedding", "type": "FLOAT_VECTOR", "dim": 768},
                            ]
                        }
                    }
                },
            }
        )
        store = ElasticsearchVectorStore(es=mock_es)

        schema = await store.get_schema("test_collection")

        assert len(schema.fields) == 2
        assert schema.fields[0].name == "id"
        assert schema.fields[0].dtype == VectorDataType.VARCHAR
        assert schema.fields[1].name == "embedding"
        assert schema.fields[1].dtype == VectorDataType.FLOAT_VECTOR

    @pytest.mark.asyncio
    async def test_get_schema_from_mapping(self):
        """Test getting schema from index mapping when metadata not found."""
        mock_es = AsyncMock()
        mock_es.get = AsyncMock(return_value={"found": False})
        mock_es.indices.get_mapping = AsyncMock(
            return_value={
                "agent_vector__test_collection": {
                    "mappings": {
                        "properties": {
                            "id": {"type": "keyword"},
                            "embedding": {"type": "dense_vector", "dims": 768},
                            "text": {"type": "keyword"},
                        }
                    }
                }
            }
        )
        store = ElasticsearchVectorStore(es=mock_es)

        schema = await store.get_schema("test_collection")

        assert len(schema.fields) == 3
        assert schema.fields[0].name == "id"
        assert schema.fields[0].dtype == VectorDataType.VARCHAR
        assert schema.fields[1].name == "embedding"
        assert schema.fields[1].dtype == VectorDataType.FLOAT_VECTOR

    @pytest.mark.asyncio
    async def test_get_schema_not_found(self):
        """Test getting schema for non-existent collection raises BaseError."""
        mock_es = AsyncMock()
        mock_es.get = AsyncMock(return_value={"found": False})
        mock_es.indices.get_mapping = AsyncMock(side_effect=Exception("Not found"))
        store = ElasticsearchVectorStore(es=mock_es)

        with pytest.raises(BaseError):
            await store.get_schema("non_existent_collection")


class TestElasticsearchVectorStoreAddDocs:
    """Tests for add_docs method."""

    @pytest.mark.asyncio
    @patch("openjiuwen.extensions.store.vector.es_vector_store.async_bulk")
    async def test_add_docs_success(self, mock_bulk):
        """Test adding documents successfully."""
        mock_es = AsyncMock()
        mock_es.get = AsyncMock(
            return_value={
                "found": True,
                "_source": {
                    "_meta": {
                        "schema": {
                            "fields": [
                                {"name": "id", "type": "VARCHAR", "is_primary": True},
                                {"name": "embedding", "type": "FLOAT_VECTOR", "dim": 3},
                            ]
                        }
                    }
                },
            }
        )
        mock_es.indices.refresh = AsyncMock()
        mock_bulk.return_value = (2, [])
        store = ElasticsearchVectorStore(es=mock_es)

        docs = [
            {"id": "doc1", "embedding": [0.1, 0.2, 0.3], "text": "Test 1"},
            {"id": "doc2", "embedding": [0.4, 0.5, 0.6], "text": "Test 2"},
        ]

        await store.add_docs("test_collection", docs)

        mock_bulk.assert_called_once()
        mock_es.indices.refresh.assert_called_once_with(index="agent_vector__test_collection")

    @pytest.mark.asyncio
    @patch("openjiuwen.extensions.store.vector.es_vector_store.async_bulk")
    async def test_add_docs_with_batch_size(self, mock_bulk):
        """Test adding documents with custom batch size."""
        mock_es = AsyncMock()
        mock_es.get = AsyncMock(
            return_value={
                "found": True,
                "_source": {
                    "_meta": {
                        "schema": {
                            "fields": [
                                {"name": "id", "type": "VARCHAR", "is_primary": True},
                            ]
                        }
                    }
                },
            }
        )
        mock_es.indices.refresh = AsyncMock()
        mock_bulk.return_value = (10, [])
        store = ElasticsearchVectorStore(es=mock_es)

        docs = [{"id": f"doc{i}", "text": f"Text {i}"} for i in range(10)]

        await store.add_docs("test_collection", docs, batch_size=3)

        assert mock_bulk.call_count == 4

    @pytest.mark.asyncio
    async def test_add_docs_empty_list(self):
        """Test adding empty list returns early."""
        mock_es = AsyncMock()
        store = ElasticsearchVectorStore(es=mock_es)

        await store.add_docs("test_collection", [])


class TestElasticsearchVectorStoreSearch:
    """Tests for search method."""

    @pytest.mark.asyncio
    async def test_search_success(self):
        """Test successful vector search."""
        mock_es = AsyncMock()
        mock_es.get = AsyncMock(
            return_value={
                "found": True,
                "_source": {
                    "_meta": {
                        "distance_metric": "COSINE",
                    }
                },
            }
        )
        mock_es.search = AsyncMock(
            return_value={
                "hits": {
                    "hits": [
                        {
                            "_id": "doc1",
                            "_score": 0.95,
                            "_source": {"text": "Text 1", "source": "test1"},
                        },
                        {
                            "_id": "doc2",
                            "_score": 0.85,
                            "_source": {"text": "Text 2", "source": "test2"},
                        },
                    ]
                }
            }
        )
        store = ElasticsearchVectorStore(es=mock_es)

        results = await store.search("test_collection", [0.1, 0.2, 0.3], "embedding", top_k=5)

        assert len(results) == 2
        assert results[0].fields["id"] == "doc1"
        assert results[0].fields["text"] == "Text 1"
        assert results[0].score == 0.95

    @pytest.mark.asyncio
    async def test_search_with_filters(self):
        """Test search with filters."""
        mock_es = AsyncMock()
        mock_es.get = AsyncMock(
            return_value={
                "found": True,
                "_source": {"_meta": {}},
            }
        )
        mock_es.search = AsyncMock(
            return_value={
                "hits": {
                    "hits": [
                        {
                            "_id": "doc1",
                            "_score": 0.9,
                            "_source": {"category": "tech"},
                        }
                    ]
                }
            }
        )
        store = ElasticsearchVectorStore(es=mock_es)

        results = await store.search(
            "test_collection",
            [0.1, 0.2, 0.3],
            "embedding",
            top_k=5,
            filters={"category": "tech"},
        )

        assert len(results) == 1
        call_args = mock_es.search.call_args
        body = call_args[1]["body"]
        assert "filter" in body["knn"]

    @pytest.mark.asyncio
    async def test_search_with_list_filters(self):
        """Test search with list filter values."""
        mock_es = AsyncMock()
        mock_es.get = AsyncMock(
            return_value={
                "found": True,
                "_source": {"_meta": {}},
            }
        )
        mock_es.search = AsyncMock(
            return_value={
                "hits": {"hits": []}
            }
        )
        store = ElasticsearchVectorStore(es=mock_es)

        await store.search(
            "test_collection",
            [0.1, 0.2, 0.3],
            "embedding",
            top_k=5,
            filters={"category": ["tech", "science"]},
        )

        call_args = mock_es.search.call_args
        body = call_args[1]["body"]
        assert "filter" in body["knn"]
        filter_clause = body["knn"]["filter"]
        assert filter_clause["bool"]["filter"][0]["terms"]["category"] == ["tech", "science"]

    @pytest.mark.asyncio
    async def test_search_error(self):
        """Test search with error raises exception."""
        mock_es = AsyncMock()
        mock_es.get = AsyncMock(
            return_value={
                "found": True,
                "_source": {"_meta": {}},
            }
        )
        mock_es.search = AsyncMock(side_effect=Exception("Search failed"))
        store = ElasticsearchVectorStore(es=mock_es)

        with pytest.raises(Exception, match="Search failed"):
            await store.search("test_collection", [0.1, 0.2, 0.3], "embedding")


class TestElasticsearchVectorStoreDeleteDocsByIds:
    """Tests for delete_docs_by_ids method."""

    @pytest.mark.asyncio
    @patch("openjiuwen.extensions.store.vector.es_vector_store.async_bulk")
    async def test_delete_docs_by_ids_success(self, mock_bulk):
        """Test deleting documents by ids successfully."""
        mock_es = AsyncMock()
        mock_bulk.return_value = (2, [])
        store = ElasticsearchVectorStore(es=mock_es)

        await store.delete_docs_by_ids("test_collection", ["doc1", "doc2"])

        mock_bulk.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_docs_by_ids_empty_list(self):
        """Test deleting with empty id list returns early."""
        mock_es = AsyncMock()
        store = ElasticsearchVectorStore(es=mock_es)

        await store.delete_docs_by_ids("test_collection", [])


class TestElasticsearchVectorStoreDeleteDocsByFilters:
    """Tests for delete_docs_by_filters method."""

    @pytest.mark.asyncio
    async def test_delete_docs_by_filters_success(self):
        """Test deleting documents by filters successfully."""
        mock_es = AsyncMock()
        mock_es.delete_by_query = AsyncMock(return_value={"deleted": 5})
        store = ElasticsearchVectorStore(es=mock_es)

        await store.delete_docs_by_filters("test_collection", {"source": "test"})

        mock_es.delete_by_query.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_docs_by_filters_empty(self):
        """Test deleting with empty filters returns early."""
        mock_es = AsyncMock()
        store = ElasticsearchVectorStore(es=mock_es)

        await store.delete_docs_by_filters("test_collection", {})

        mock_es.delete_by_query.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_docs_by_filters_with_list(self):
        """Test deleting with list filter values."""
        mock_es = AsyncMock()
        mock_es.delete_by_query = AsyncMock(return_value={"deleted": 3})
        store = ElasticsearchVectorStore(es=mock_es)

        await store.delete_docs_by_filters("test_collection", {"category": ["tech", "science"]})

        call_args = mock_es.delete_by_query.call_args
        body = call_args[1]["body"]
        assert "terms" in body["query"]["bool"]["filter"][0]


class TestElasticsearchVectorStoreListCollectionNames:
    """Tests for list_collection_names method."""

    @pytest.mark.asyncio
    async def test_list_collection_names_success(self):
        """Test listing collection names successfully."""
        mock_es = AsyncMock()
        mock_es.indices.get = AsyncMock(
            return_value={
                "agent_vector__test1": {},
                "agent_vector__test2": {},
                "other_index": {},
            }
        )
        store = ElasticsearchVectorStore(es=mock_es)

        names = await store.list_collection_names()

        assert len(names) == 2
        assert "test1" in names
        assert "test2" in names

    @pytest.mark.asyncio
    async def test_list_collection_names_empty(self):
        """Test listing collection names when none exist."""
        mock_es = AsyncMock()
        mock_es.indices.get = AsyncMock(side_effect=Exception("Not found"))
        store = ElasticsearchVectorStore(es=mock_es)

        names = await store.list_collection_names()

        assert names == []


class TestElasticsearchVectorStoreGetCollectionMetadata:
    """Tests for get_collection_metadata method."""

    @pytest.mark.asyncio
    async def test_get_collection_metadata_success(self):
        """Test getting collection metadata successfully."""
        mock_es = AsyncMock()
        mock_es.get = AsyncMock(
            return_value={
                "found": True,
                "_source": {
                    "_meta": {
                        "distance_metric": "L2",
                        "schema_version": 1,
                    }
                },
            }
        )
        store = ElasticsearchVectorStore(es=mock_es)

        metadata = await store.get_collection_metadata("test_collection")

        assert metadata["distance_metric"] == "L2"
        assert metadata["schema_version"] == 1

    @pytest.mark.asyncio
    async def test_get_collection_metadata_defaults(self):
        """Test getting collection metadata returns defaults when not found."""
        mock_es = AsyncMock()
        mock_es.get = AsyncMock(return_value={"found": False})
        store = ElasticsearchVectorStore(es=mock_es)

        metadata = await store.get_collection_metadata("test_collection")

        assert metadata.get("distance_metric") == "COSINE"
        assert metadata.get("schema_version") == 0


class TestElasticsearchVectorStoreUpdateCollectionMetadata:
    """Tests for update_collection_metadata method."""

    @pytest.mark.asyncio
    async def test_update_collection_metadata_success(self):
        """Test updating collection metadata successfully."""
        mock_es = AsyncMock()
        mock_es.get = AsyncMock(
            return_value={
                "found": True,
                "_source": {"_meta": {"schema_version": 0}},
            }
        )
        mock_es.index = AsyncMock()
        store = ElasticsearchVectorStore(es=mock_es)

        await store.update_collection_metadata("test_collection", {"schema_version": 1})

        mock_es.index.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_collection_metadata_empty(self):
        """Test updating with empty metadata returns early."""
        mock_es = AsyncMock()
        store = ElasticsearchVectorStore(es=mock_es)

        await store.update_collection_metadata("test_collection", {})

        mock_es.index.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_collection_metadata_invalid_version(self):
        """Test updating with invalid schema_version raises BaseError."""
        mock_es = AsyncMock()
        mock_es.get = AsyncMock(return_value={"found": True, "_source": {"_meta": {}}})
        store = ElasticsearchVectorStore(es=mock_es)

        with pytest.raises(BaseError, match="schema_version must be a non-negative integer"):
            await store.update_collection_metadata("test_collection", {"schema_version": -1})


class TestElasticsearchVectorStoreUpdateSchema:
    """Tests for update_schema method."""

    @pytest.mark.asyncio
    async def test_update_schema_empty_operations(self):
        """Test update_schema with empty operations returns early."""
        mock_es = AsyncMock()
        store = ElasticsearchVectorStore(es=mock_es)

        await store.update_schema("test_collection", [])


class TestGetPrimaryKeyField:
    """Tests for _get_primary_key_field helper function."""

    def test_get_primary_key_field_found(self):
        """Test finding primary key field in schema."""
        schema_dict = {
            "fields": [
                {"name": "id", "type": "VARCHAR", "is_primary": True},
                {"name": "embedding", "type": "FLOAT_VECTOR"},
            ]
        }
        result = _get_primary_key_field(schema_dict)
        assert result == "id"

    def test_get_primary_key_field_not_found(self):
        """Test when primary key field not found."""
        schema_dict = {
            "fields": [
                {"name": "embedding", "type": "FLOAT_VECTOR"},
            ]
        }
        result = _get_primary_key_field(schema_dict)
        assert result is None


class TestElasticsearchVectorStoreClose:
    """Tests for close method."""

    @pytest.mark.asyncio
    async def test_close_connection(self):
        """Test closing Elasticsearch connection."""
        mock_es = AsyncMock()
        mock_es.close = AsyncMock()

        store = ElasticsearchVectorStore(es=mock_es)
        store._metadata_cache["test_index"] = {"key": "value"}

        await store.close()

        mock_es.close.assert_called_once()
        assert len(store._metadata_cache) == 0

    @pytest.mark.asyncio
    async def test_close_logging(self):
        """Test that close method logs correctly."""
        mock_es = AsyncMock()
        mock_es.close = AsyncMock()
        store = ElasticsearchVectorStore(es=mock_es)

        await store.close()

        mock_es.close.assert_called_once()
