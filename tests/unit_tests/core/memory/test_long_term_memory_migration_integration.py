# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import copy
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.foundation.store.base_db_store import BaseDbStore
from openjiuwen.core.foundation.store.base_vector_store import BaseVectorStore, CollectionSchema
from openjiuwen.core.foundation.store.kv.in_memory_kv_store import InMemoryKVStore
from openjiuwen.core.memory.long_term_memory import LongTermMemory
from openjiuwen.core.memory.migration.migration_plan import kv_registry, sql_registry, vector_registry
from openjiuwen.core.memory.migration.operation.base_operation import OperationMetadata
from openjiuwen.core.memory.migration.operation.operations import (
    AddColumnOperation,
    RenameScalarFieldOperation,
    UpdateKVOperation
)


class MockVectorStore(BaseVectorStore):
    def __init__(self):
        self._collections = {}
        self._metadata = {}
        self.update_schema_called = False

    async def create_collection(self, collection_name: str, schema, **kwargs):
        self._collections[collection_name] = schema

    async def delete_collection(self, collection_name: str, **kwargs):
        self._collections.pop(collection_name, None)

    async def collection_exists(self, collection_name: str, **kwargs) -> bool:
        return collection_name in self._collections

    async def get_schema(self, collection_name: str, **kwargs) -> CollectionSchema:
        return CollectionSchema()

    async def add_docs(self, collection_name: str, docs, **kwargs):
        pass

    async def search(self, collection_name: str, query_vector, vector_field, top_k=5, filters=None, **kwargs):
        return []

    async def delete_docs_by_ids(self, collection_name: str, ids, **kwargs):
        pass

    async def delete_docs_by_filters(self, collection_name: str, filters, **kwargs):
        pass

    async def list_collection_names(self):
        return list(self._collections.keys())

    async def update_schema(self, collection_name: str, operations):
        self.update_schema_called = True

    async def get_collection_schema_version(self, collection_name: str, **kwargs) -> int | None:
        return None

    async def update_collection_schema_version(self, collection_name: str, schema_version: int, **kwargs):
        pass

    async def update_collection_metadata(self, collection_name: str, metadata: dict) -> None:
        self._metadata[collection_name] = metadata

    async def get_collection_metadata(self, collection_name: str) -> dict:
        return self._metadata.get(collection_name, {"schema_version": 0})


class MockDbStore(BaseDbStore):
    async def execute_sql(self, sql: str, params=None):
        pass

    async def execute_sql_batch(self, sql_list: list, params_list=None):
        pass

    def get_async_engine(self):
        mock_engine = MagicMock()
        mock_engine.begin = MagicMock(return_value=AsyncMock())
        return mock_engine


@pytest.fixture(scope="function", autouse=True)
def registry_backup_and_restore():
    """
    Function-level fixture to backup and restore registry state
    Ensures test isolation by clearing registries before each test
    and restoring original state after each test completes.
    """
    original_kv_registry = copy.deepcopy(kv_registry.get_all_operations())
    original_vector_registry = copy.deepcopy(vector_registry.get_all_operations())
    original_sql_registry = copy.deepcopy(sql_registry.get_all_operations())

    kv_registry.clear()
    vector_registry.clear()
    sql_registry.clear()

    yield

    kv_registry.clear()
    kv_registry.set_operations(original_kv_registry)
    vector_registry.clear()
    vector_registry.set_operations(original_vector_registry)
    sql_registry.clear()
    sql_registry.set_operations(original_sql_registry)


class TestLongTermMemoryMigrationIntegration:
    """
    Integration tests for KV migration in LongTermMemory
    """

    @pytest.mark.asyncio
    async def test_migration_during_register_store(self):
        """
        Test that migration is automatically triggered during register_store
        This integration test verifies that when LongTermMemory.register_store
        is called, KV migrations are automatically executed.
        """
        kv_store = InMemoryKVStore()
        vector_store = MockVectorStore()
        db_store = MockDbStore()

        from openjiuwen.core.memory.migration.migrator.kv_migrator import KV_SCHEMA_VERSION
        await kv_store.set(KV_SCHEMA_VERSION, "1")

        await kv_store.set("old_key", "old_value")

        async def update_func(store):
            old_value = await store.get("old_key")
            if old_value:
                await store.set("new_key", old_value)
                await store.delete("old_key")

        kv_registry.register(
            "kv_global",
            UpdateKVOperation(
                metadata=OperationMetadata(schema_version=2, description="Migrate v2"),
                update_func=update_func
            )
        )

        ltm = LongTermMemory()
        await ltm.register_store(kv_store=kv_store, vector_store=vector_store, db_store=db_store)

        assert await kv_store.get("old_key") is None
        assert await kv_store.get("new_key") == "old_value"
        assert await kv_store.get(KV_SCHEMA_VERSION) == "2"

    @pytest.mark.asyncio
    async def test_migration_failure_prevents_initialization(self):
        """
        Test that migration failure prevents LongTermMemory initialization
        This integration test verifies that if migration fails, error
        is properly propagated and prevents system from starting with
        inconsistent state.
        """
        kv_store = InMemoryKVStore()
        vector_store = MockVectorStore()
        db_store = MockDbStore()

        from openjiuwen.core.memory.migration.migrator.kv_migrator import KV_SCHEMA_VERSION
        await kv_store.set(KV_SCHEMA_VERSION, "1")

        async def failing_migration(store):
            raise Exception("Migration failed due to data corruption")

        kv_registry.register(
            "kv_global",
            UpdateKVOperation(
                metadata=OperationMetadata(schema_version=2, description="Failing migration"),
                update_func=failing_migration
            )
        )

        ltm = LongTermMemory()

        with pytest.raises(Exception) as exc_info:
            await ltm.register_store(kv_store=kv_store, vector_store=vector_store, db_store=db_store)

        assert "failed to migrate memory" in str(exc_info.value)
        assert "kv store migrations failed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_idempotent_migration(self):
        """
        Test that migration is idempotent
        This integration test verifies that running migration multiple times
        does not cause issues or duplicate data.
        """
        from openjiuwen.core.memory.common.kv_prefix_registry import kv_prefix_registry

        kv_store = InMemoryKVStore()
        vector_store = MockVectorStore()
        db_store = MockDbStore()

        test_prefix = "TEST_PREFIX_IDEMPOTENT"
        kv_prefix_registry.register_current(test_prefix)

        try:
            await kv_store.set(f"{test_prefix}/existing_data", "value")

            from openjiuwen.core.memory.migration.migrator.kv_migrator import KV_SCHEMA_VERSION
            await kv_store.set(KV_SCHEMA_VERSION, "0")

            async def migrate(store):
                await store.set(f"{test_prefix}/initialized", "true")
                await store.set(f"{test_prefix}/data", "value")

            kv_registry.register(
                "kv_global",
                UpdateKVOperation(
                    metadata=OperationMetadata(schema_version=1, description="Initialize"),
                    update_func=migrate
                )
            )

            ltm1 = LongTermMemory()
            await ltm1.register_store(kv_store=kv_store, vector_store=vector_store, db_store=db_store)

            assert await kv_store.get(f"{test_prefix}/initialized") == "true"
            assert await kv_store.get(f"{test_prefix}/data") == "value"

            ltm2 = LongTermMemory()
            await ltm2.register_store(kv_store=kv_store, vector_store=vector_store, db_store=db_store)

            assert await kv_store.get(f"{test_prefix}/initialized") == "true"
            assert await kv_store.get(f"{test_prefix}/data") == "value"
        finally:
            kv_prefix_registry.unregister(test_prefix)

    @pytest.mark.asyncio
    async def test_empty_migration_plan(self):
        """
        Test that empty migration plan is handled gracefully
        This integration test verifies that when no migration operations
        are registered, register_store still succeeds without errors.
        """
        kv_store = InMemoryKVStore()
        vector_store = MockVectorStore()
        db_store = MockDbStore()

        ltm = LongTermMemory()
        await ltm.register_store(kv_store=kv_store, vector_store=vector_store, db_store=db_store)

        from openjiuwen.core.memory.migration.migrator.kv_migrator import KV_SCHEMA_VERSION
        assert await kv_store.get(KV_SCHEMA_VERSION) is None

    @pytest.mark.asyncio
    async def test_vector_migration_during_register_store(self):
        """
        Test that vector migration is automatically triggered during register_store
        This integration test verifies that when LongTermMemory.register_store
        is called with a vector_store, vector migrations are automatically executed.
        """
        kv_store = InMemoryKVStore()
        vector_store = MockVectorStore()
        db_store = MockDbStore()

        await vector_store.create_collection("user_scope_summary", CollectionSchema())
        await vector_store.update_collection_metadata("user_scope_summary", {"schema_version": 0})

        vector_registry.register(
            "summary",
            RenameScalarFieldOperation(
                metadata=OperationMetadata(schema_version=1, description="Rename field"),
                data_type="summary",
                old_field_name="old_field",
                new_field_name="new_field"
            )
        )

        ltm = LongTermMemory()
        await ltm.register_store(kv_store=kv_store, vector_store=vector_store, db_store=db_store)

        metadata = await vector_store.get_collection_metadata("user_scope_summary")
        assert metadata.get("schema_version") == 1

    @pytest.mark.asyncio
    async def test_sql_migration_during_register_store(self):
        """
        Test that SQL migration is automatically triggered during register_store
        This integration test verifies that when LongTermMemory.register_store
        is called with a db_store, SQL migrations are automatically executed.
        """
        kv_store = InMemoryKVStore()
        vector_store = MockVectorStore()
        db_store = MockDbStore()

        sql_registry.register(
            "test_table",
            AddColumnOperation(
                metadata=OperationMetadata(schema_version=1, description="Add column"),
                table="test_table",
                column_name="new_column",
                column_type="STRING",
                nullable=True
            )
        )

        ltm = LongTermMemory()
        await ltm.register_store(kv_store=kv_store, vector_store=vector_store, db_store=db_store)

    @pytest.mark.asyncio
    async def test_all_migrations_together(self):
        """
        Test that all three types of migrations work together
        This integration test verifies that KV, vector, and SQL migrations
        can all be executed during a single register_store call.
        """
        kv_store = InMemoryKVStore()
        vector_store = MockVectorStore()
        db_store = MockDbStore()

        from openjiuwen.core.memory.migration.migrator.kv_migrator import KV_SCHEMA_VERSION
        await kv_store.set(KV_SCHEMA_VERSION, "0")

        await kv_store.set("old_key", "old_value")

        async def update_func(store):
            old_value = await store.get("old_key")
            if old_value:
                await store.set("new_key", old_value)
                await store.delete("old_key")

        kv_registry.register(
            "kv_global",
            UpdateKVOperation(
                metadata=OperationMetadata(schema_version=1, description="Migrate v1"),
                update_func=update_func
            )
        )

        await vector_store.create_collection("user_scope_summary", CollectionSchema())
        await vector_store.update_collection_metadata("user_scope_summary", {"schema_version": 0})

        vector_registry.register(
            "summary",
            RenameScalarFieldOperation(
                metadata=OperationMetadata(schema_version=1, description="Rename field"),
                data_type="summary",
                old_field_name="old_field",
                new_field_name="new_field"
            )
        )

        sql_registry.register(
            "test_table",
            AddColumnOperation(
                metadata=OperationMetadata(schema_version=1, description="Add column"),
                table="test_table",
                column_name="new_column",
                column_type="STRING",
                nullable=True
            )
        )

        ltm = LongTermMemory()
        await ltm.register_store(kv_store=kv_store, vector_store=vector_store, db_store=db_store)

        assert await kv_store.get("old_key") is None
        assert await kv_store.get("new_key") == "old_value"
        assert await kv_store.get(KV_SCHEMA_VERSION) == "1"

        metadata = await vector_store.get_collection_metadata("user_scope_summary")
        assert metadata.get("schema_version") == 1

    @pytest.mark.asyncio
    async def test_vector_migration_failure_prevents_initialization(self):
        """
        Test that vector migration failure prevents LongTermMemory initialization
        This integration test verifies that if vector migration fails, error
        is properly propagated.
        """
        kv_store = InMemoryKVStore()
        vector_store = MockVectorStore()
        db_store = MockDbStore()

        class FailingVectorStore(MockVectorStore):
            async def update_schema(self, collection_name: str, operations):
                raise Exception("Vector migration failed")

        failing_vector_store = FailingVectorStore()
        await failing_vector_store.create_collection("user_scope_summary", CollectionSchema())
        await failing_vector_store.update_collection_metadata("user_scope_summary", {"schema_version": 0})

        vector_registry.register(
            "summary",
            RenameScalarFieldOperation(
                metadata=OperationMetadata(schema_version=1, description="Rename field"),
                data_type="summary",
                old_field_name="old_field",
                new_field_name="new_field"
            )
        )

        ltm = LongTermMemory()

        with pytest.raises(Exception) as exc_info:
            await ltm.register_store(kv_store=kv_store, vector_store=failing_vector_store, db_store=db_store)

        assert "failed to migrate memory" in str(exc_info.value)
        assert "vector store migrations failed" in str(exc_info.value)
