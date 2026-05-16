# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
from datetime import datetime, timezone

import pytest

from openjiuwen.core.foundation.store.base_memory_index import MemoryDoc
from openjiuwen.core.foundation.store.base_embedding import Embedding
from openjiuwen.core.foundation.store.base_vector_store import BaseVectorStore, VectorSearchResult
from openjiuwen.core.foundation.store.base_kv_store import BaseKVStore
from openjiuwen.core.foundation.store.index.simple_memory_index import SimpleMemoryIndex
from openjiuwen.core.memory.migration.migrator.index_version_migrator import IndexVersionMigrator
from openjiuwen.core.memory.migration.operation.operations import (
    RenameMemoryDocFieldOperation,
    TransformMemoryDocFieldOperation,
    AddMemoryDocFieldOperation
)
from openjiuwen.core.memory.migration.operation.base_operation import OperationMetadata


_EMBEDDING_DIM = 768


class _MockEmbedding(Embedding):
    limiter = asyncio.Semaphore(10)

    async def embed_query(self, text, **kwargs):
        return [0.0] * _EMBEDDING_DIM

    async def embed_documents(self, texts, batch_size=None, **kwargs):
        return [[0.0] * _EMBEDDING_DIM for _ in texts]

    @property
    def dimension(self):
        return _EMBEDDING_DIM


class _InMemoryKVStore(BaseKVStore):

    def __init__(self):
        self._data: dict[str, str] = {}

    async def get(self, key, **_kw):
        return self._data.get(key)

    async def set(self, key, value, **_kw):
        self._data[key] = value

    async def delete(self, key, **_kw):
        self._data.pop(key, None)

    async def mget(self, keys, **_kw):
        return [self._data.get(k) for k in keys]

    async def batch_delete(self, keys, **_kw):
        for k in keys:
            self._data.pop(k, None)

    async def get_by_prefix(self, prefix, **_kw):
        return [k for k in self._data if k.startswith(prefix)]

    async def delete_by_prefix(self, prefix, **_kw):
        keys_to_delete = [k for k in self._data if k.startswith(prefix)]
        for k in keys_to_delete:
            del self._data[k]

    async def exists(self, key, **_kw):
        return key in self._data

    async def exclusive_set(self, key, value, **_kw):
        if key not in self._data:
            self._data[key] = value
            return True
        return False

    async def pipeline(self, operations, **_kw):
        for op in operations:
            method = op[0]
            args = op[1:]
            await getattr(self, method)(*args)


class _InMemoryVectorStore(BaseVectorStore):

    def __init__(self):
        self._cols: dict[str, dict] = {}
        self._docs: dict[str, list[dict]] = {}

    async def create_collection(self, collection_name, schema, **_kw):
        self._cols[collection_name] = {}
        self._docs.setdefault(collection_name, [])

    async def delete_collection(self, collection_name, **_kw):
        self._cols.pop(collection_name, None)
        self._docs.pop(collection_name, None)

    async def collection_exists(self, collection_name, **_kw) -> bool:
        return collection_name in self._cols

    async def get_schema(self, collection_name, **_kw):
        return None

    async def add_docs(self, collection_name, docs, **_kw):
        bucket = self._docs.setdefault(collection_name, [])
        for doc in docs:
            idx = next(
                (i for i, d in enumerate(bucket) if d.get("id") == doc.get("id")),
                None,
            )
            if idx is not None:
                bucket[idx] = doc
            else:
                bucket.append(doc)

    async def search(
        self, collection_name, query_vector, vector_field, top_k=5,
        filters=None, output_fields=None, **_kw,
    ):
        bucket = self._docs.get(collection_name, [])
        results: list[VectorSearchResult] = []
        for doc in bucket:
            if filters and not all(doc.get(k) == v for k, v in filters.items()):
                continue
            score = 1.0
            fields = {k: v for k, v in doc.items() if k != vector_field}
            results.append(VectorSearchResult(score=score, fields=fields))
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]

    async def delete_docs_by_ids(self, collection_name, ids, **_kw):
        if collection_name in self._docs:
            self._docs[collection_name] = [d for d in self._docs[collection_name] if d.get("id") not in ids]

    async def delete_docs_by_filters(self, collection_name, filters, **_kw):
        if collection_name in self._docs:
            self._docs[collection_name] = [
                d for d in self._docs[collection_name]
                if not all(d.get(k) == v for k, v in filters.items())
            ]

    async def list_collection_names(self) -> list[str]:
        return list(self._cols)

    async def get_collection_metadata(self, collection_name) -> dict:
        return self._cols.get(collection_name, {})

    async def update_collection_metadata(self, collection_name, metadata):
        if collection_name in self._cols:
            self._cols[collection_name].update(metadata)

    async def update_schema(self, collection_name, operations):
        pass


@pytest.fixture()
def index_with_docs():
    kv_store = _InMemoryKVStore()
    vector_store = _InMemoryVectorStore()
    mock_embedding = _MockEmbedding()
    index = SimpleMemoryIndex(kv_store=kv_store, vector_store=vector_store, embedding_model=mock_embedding)

    test_docs = [
        MemoryDoc(
            id=f"{i:024d}",
            text=f"Test document {i}",
            type="fragment",
            timestamp=datetime.now(tz=timezone.utc).astimezone(),
            fields={"memory_text": f"Content {i}", "category": "test", "count": i}
        )
        for i in range(1, 4)
    ]

    return index, test_docs


class TestIndexMigrationIntegration:

    @staticmethod
    @pytest.mark.asyncio
    async def test_version_migration_rename_field(index_with_docs):
        index, test_docs = index_with_docs
        await index.add_memories("user1", "scope1", test_docs)

        initial_docs = await index.list_memories("user1", "scope1", 0, 10)
        assert len(initial_docs) == 3
        for doc in initial_docs:
            assert "memory_text" in doc.fields
            assert "text" not in doc.fields

        rename_operation = RenameMemoryDocFieldOperation(
            metadata=OperationMetadata(schema_version=1, description="Rename memory_text to text"),
            old_field_name="memory_text",
            new_field_name="text"
        )

        migrator = IndexVersionMigrator()
        result = await migrator.try_migrate(index, [rename_operation])

        assert result is True
        assert index.get_schema_version() == 1

        migrated_docs = await index.list_memories("user1", "scope1", 0, 10)
        assert len(migrated_docs) == 3
        for doc in migrated_docs:
            assert "memory_text" not in doc.fields
            assert "text" in doc.fields
            assert doc.fields["text"].startswith("Content ")

    @staticmethod
    @pytest.mark.asyncio
    async def test_version_migration_multiple_operations(index_with_docs):
        index, test_docs = index_with_docs
        await index.add_memories("user1", "scope1", test_docs)

        operations = [
            RenameMemoryDocFieldOperation(
                metadata=OperationMetadata(schema_version=1, description="Rename memory_text to content"),
                old_field_name="memory_text",
                new_field_name="content"
            ),
            AddMemoryDocFieldOperation(
                metadata=OperationMetadata(schema_version=2, description="Add processed field"),
                field_name="processed",
                default_value_or_func=True
            ),
            TransformMemoryDocFieldOperation(
                metadata=OperationMetadata(schema_version=3, description="Increment count by 10"),
                field_name="count",
                transform_func=lambda x: x + 10
            )
        ]

        migrator = IndexVersionMigrator()
        result = await migrator.try_migrate(index, operations)

        assert result is True
        assert index.get_schema_version() == 3

        migrated_docs = await index.list_memories("user1", "scope1", 0, 10)
        assert len(migrated_docs) == 3

        for doc in migrated_docs:
            assert "memory_text" not in doc.fields
            assert "content" in doc.fields
            assert doc.fields["content"].startswith("Content ")

            assert "processed" in doc.fields
            assert doc.fields["processed"] is True

            assert "count" in doc.fields
            expected_count = int(doc.fields["content"].split()[-1]) + 10
            assert doc.fields["count"] == expected_count
