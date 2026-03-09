# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import copy
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.foundation.store.base_db_store import BaseDbStore
from openjiuwen.core.foundation.store.base_kv_store import BaseKVStore, BasedKVStorePipeline
from openjiuwen.core.foundation.store.base_vector_store import BaseVectorStore, CollectionSchema
from openjiuwen.core.foundation.store.kv.in_memory_kv_store import InMemoryKVStore
from openjiuwen.core.memory.manage.mem_model.sql_db_store import SqlDbStore
from openjiuwen.core.memory.migration.migration_plan import kv_registry, sql_registry, vector_registry
from openjiuwen.core.memory.migration.operation.base_operation import OperationMetadata
from openjiuwen.core.memory.migration.operation.operations import (
    AddColumnOperation,
    RenameScalarFieldOperation,
    UpdateKVOperation
)
from openjiuwen.core.memory.migration.run_migrations import (
    run_kv_migrations,
    run_sql_migrations,
    run_vector_migrations
)


class MockVectorStore(BaseVectorStore):
    def __init__(self):
        self._collections = {}
        self._metadata = {}

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
        pass

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


class MockKVStore(BaseKVStore):
    def __init__(self):
        self._store = {}

    async def set(self, key: str, value: str | bytes):
        self._store[key] = value

    async def get(self, key: str) -> str | bytes | None:
        return self._store.get(key)

    async def delete(self, key: str):
        self._store.pop(key, None)

    async def exclusive_set(self, key: str, value: str | bytes, expiry: int | None = None) -> bool:
        if key in self._store:
            return False
        self._store[key] = value
        return True

    async def exists(self, key: str) -> bool:
        return key in self._store

    async def get_by_prefix(self, prefix: str) -> dict[str, str | bytes]:
        return {k: v for k, v in self._store.items() if k.startswith(prefix)}

    async def delete_by_prefix(self, prefix: str, batch_size=None):
        keys_to_delete = [k for k in self._store if k.startswith(prefix)]
        for k in keys_to_delete:
            del self._store[k]

    async def mget(self, keys: list) -> list:
        return [self._store.get(k) for k in keys]

    async def batch_delete(self, keys: list, batch_size=None) -> int:
        deleted = 0
        for k in keys:
            if k in self._store:
                del self._store[k]
                deleted += 1
        return deleted

    def pipeline(self):
        async def execute(operations):
            results = []
            for op in operations:
                if op[0] == 'set':
                    self._store[op[1]] = op[2]
                    results.append(None)
                elif op[0] == 'get':
                    results.append(self._store.get(op[1]))
                elif op[0] == 'exists':
                    results.append(op[1] in self._store)
            return results
        return BasedKVStorePipeline(execute)

    def is_empty(self) -> bool:
        return len(self._store) == 0


@pytest.fixture(scope="function", autouse=True)
def registry_backup_and_restore():
    """
    Function-level fixture to backup and restore registry state
    Ensures test isolation by clearing registries before each test
    and restoring original state after each test completes.
    """
    original_kv_registry = copy.deepcopy(kv_registry.get_all_operations())
    original_sql_registry = copy.deepcopy(sql_registry.get_all_operations())
    original_vector_registry = copy.deepcopy(vector_registry.get_all_operations())

    kv_registry.clear()
    sql_registry.clear()
    vector_registry.clear()

    yield

    kv_registry.clear()
    kv_registry.set_operations(original_kv_registry)
    sql_registry.clear()
    sql_registry.set_operations(original_sql_registry)
    vector_registry.clear()
    vector_registry.set_operations(original_vector_registry)


class TestRunMigrations:
    """
    Test cases for run_kv_migrations function
    """

    @pytest.mark.asyncio
    async def test_schema_version_behind_upgrade(self):
        """
        Test schema version behind scenario - verify upgrade logic executes correctly
        This test simulates a scenario where current schema version is behind
        latest version, and verifies that all pending operations are executed.
        """
        kv_store = InMemoryKVStore()

        from openjiuwen.core.memory.migration.migrator.kv_migrator import KV_SCHEMA_VERSION
        await kv_store.set(KV_SCHEMA_VERSION, "1")

        await kv_store.set("old_key_v1", "value_v1")
        await kv_store.set("old_key_v2", "value_v2")

        async def update_func_v2(store):
            old_value = await store.get("old_key_v1")
            if old_value:
                await store.set("new_key_v1", old_value)
                await store.delete("old_key_v1")

        async def update_func_v3(store):
            old_value = await store.get("old_key_v2")
            if old_value:
                await store.set("new_key_v2", old_value)
                await store.delete("old_key_v2")

        kv_registry.register(
            "kv_global",
            UpdateKVOperation(
                metadata=OperationMetadata(schema_version=2, description="Migrate v2"),
                update_func=update_func_v2
            )
        )
        kv_registry.register(
            "kv_global",
            UpdateKVOperation(
                metadata=OperationMetadata(schema_version=3, description="Migrate v3"),
                update_func=update_func_v3
            )
        )

        await run_kv_migrations(kv_store)

        assert await kv_store.get("old_key_v1") is None
        assert await kv_store.get("old_key_v2") is None
        assert await kv_store.get("new_key_v1") == "value_v1"
        assert await kv_store.get("new_key_v2") == "value_v2"
        assert await kv_store.get(KV_SCHEMA_VERSION) == "3"

    @pytest.mark.asyncio
    async def test_schema_version_up_to_date(self):
        """
        Test schema version up-to-date scenario - verify no operations execute
        This test simulates a scenario where current schema version is already
        at the latest version, and verifies that no operations are executed.
        """
        kv_store = InMemoryKVStore()

        from openjiuwen.core.memory.migration.migrator.kv_migrator import KV_SCHEMA_VERSION
        await kv_store.set(KV_SCHEMA_VERSION, "3")

        await kv_store.set("key1", "value1")

        async def update_func(store):
            await store.set("should_not_execute", "true")

        kv_registry.register(
            "kv_global",
            UpdateKVOperation(
                metadata=OperationMetadata(schema_version=1, description="Migrate v1"),
                update_func=update_func
            )
        )
        kv_registry.register(
            "kv_global",
            UpdateKVOperation(
                metadata=OperationMetadata(schema_version=2, description="Migrate v2"),
                update_func=update_func
            )
        )
        kv_registry.register(
            "kv_global",
            UpdateKVOperation(
                metadata=OperationMetadata(schema_version=3, description="Migrate v3"),
                update_func=update_func
            )
        )

        await run_kv_migrations(kv_store)

        assert await kv_store.get("key1") == "value1"
        assert await kv_store.get("should_not_execute") is None
        assert await kv_store.get(KV_SCHEMA_VERSION) == "3"

    @pytest.mark.asyncio
    async def test_no_schema_version_first_time(self):
        """
        Test no schema version scenario (first time installation)
        This test simulates a scenario where there is no existing schema version,
        and verifies that all operations are executed.
        """
        from openjiuwen.core.memory.common.kv_prefix_registry import kv_prefix_registry

        kv_store = InMemoryKVStore()

        test_prefix = "TEST_PREFIX_INIT"
        kv_prefix_registry.register_current(test_prefix)

        try:
            await kv_store.set(f"{test_prefix}/existing_data", "value")

            from openjiuwen.core.memory.migration.migrator.kv_migrator import KV_SCHEMA_VERSION
            await kv_store.set(KV_SCHEMA_VERSION, "0")

            async def update_func_v1(store):
                await store.set(f"{test_prefix}/initialized_v1", "true")

            async def update_func_v2(store):
                await store.set(f"{test_prefix}/initialized_v2", "true")

            kv_registry.register(
                "kv_global",
                UpdateKVOperation(
                    metadata=OperationMetadata(schema_version=1, description="Initialize v1"),
                    update_func=update_func_v1
                )
            )
            kv_registry.register(
                "kv_global",
                UpdateKVOperation(
                    metadata=OperationMetadata(schema_version=2, description="Initialize v2"),
                    update_func=update_func_v2
                )
            )

            await run_kv_migrations(kv_store)

            assert await kv_store.get(f"{test_prefix}/initialized_v1") == "true"
            assert await kv_store.get(f"{test_prefix}/initialized_v2") == "true"
            assert await kv_store.get(KV_SCHEMA_VERSION) == "2"
        finally:
            kv_prefix_registry.unregister(test_prefix)

    @pytest.mark.asyncio
    async def test_empty_migration_plan(self):
        """
        Test empty migration plan scenario
        This test verifies that migration process handles empty migration
        plans gracefully without errors.
        """
        kv_store = MockKVStore()

        await run_kv_migrations(kv_store)

        assert kv_store.is_empty()

    @pytest.mark.asyncio
    async def test_migration_failure_handling(self):
        """
        Test migration failure handling
        This test verifies that migration failures are handled gracefully
        and appropriate error messages are logged.
        """
        kv_store = InMemoryKVStore()

        from openjiuwen.core.memory.migration.migrator.kv_migrator import KV_SCHEMA_VERSION
        await kv_store.set(KV_SCHEMA_VERSION, "1")

        async def failing_update_func(store):
            raise Exception("Simulated migration failure")

        kv_registry.register(
            "kv_global",
            UpdateKVOperation(
                metadata=OperationMetadata(schema_version=2, description="Failing migration"),
                update_func=failing_update_func
            )
        )

        with pytest.raises(Exception) as exc_info:
            await run_kv_migrations(kv_store)

        assert "failed to migrate memory" in str(exc_info.value)
        assert "kv store migrations failed for entity: kv_global" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_multiple_operations_migration(self):
        """
        Test migration with multiple operations
        This test verifies that migration works correctly when multiple
        operations are registered for the same entity.
        """
        kv_store = InMemoryKVStore()

        from openjiuwen.core.memory.migration.migrator.kv_migrator import KV_SCHEMA_VERSION
        await kv_store.set(KV_SCHEMA_VERSION, "1")

        async def update_v2(store):
            await store.set("v2_migrated", "true")

        async def update_v3(store):
            await store.set("v3_migrated", "true")

        kv_registry.register(
            "kv_global",
            UpdateKVOperation(
                metadata=OperationMetadata(schema_version=2, description="Migrate v2"),
                update_func=update_v2
            )
        )
        kv_registry.register(
            "kv_global",
            UpdateKVOperation(
                metadata=OperationMetadata(schema_version=3, description="Migrate v3"),
                update_func=update_v3
            )
        )

        await run_kv_migrations(kv_store)

        v2_migrated = await kv_store.get("v2_migrated")
        v3_migrated = await kv_store.get("v3_migrated")

        assert v2_migrated == "true"
        assert v3_migrated == "true"
        assert await kv_store.get(KV_SCHEMA_VERSION) == "3"

    @pytest.mark.asyncio
    async def test_invalid_version_format(self):
        """
        Test invalid version format handling
        This test verifies that the migration process raises an exception when
        SCHEMA_VERSION field contains invalid format (non-numeric string).
        """
        kv_store = InMemoryKVStore()

        from openjiuwen.core.memory.migration.migrator.kv_migrator import KV_SCHEMA_VERSION
        await kv_store.set(KV_SCHEMA_VERSION, "invalid_version")

        async def update_func(store):
            await store.set("migrated", "true")

        kv_registry.register(
            "kv_global",
            UpdateKVOperation(
                metadata=OperationMetadata(schema_version=1, description="Migrate"),
                update_func=update_func
            )
        )

        with pytest.raises(Exception) as exc_info:
            await run_kv_migrations(kv_store)

        assert "failed to migrate memory" in str(exc_info.value)
        assert "kv store migrations failed for entity: kv_global" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_kv_store_connection_interrupted(self):
        """
        Test KV store connection interrupted scenario
        This test verifies that migration process handles connection errors
        gracefully and propagates the error.
        """
        class MockKVStoreWithConnectionError(InMemoryKVStore):
            async def get(self, key):
                if key == "test_key":
                    raise ConnectionError("Connection interrupted")
                return await super().get(key)

        kv_store = MockKVStoreWithConnectionError()

        from openjiuwen.core.memory.migration.migrator.kv_migrator import KV_SCHEMA_VERSION
        await kv_store.set(KV_SCHEMA_VERSION, "1")

        async def update_func(store):
            await store.get("test_key")
            await store.set("migrated", "true")

        kv_registry.register(
            "kv_global",
            UpdateKVOperation(
                metadata=OperationMetadata(schema_version=2, description="Migrate"),
                update_func=update_func
            )
        )

        with pytest.raises(Exception) as exc_info:
            await run_kv_migrations(kv_store)

        assert "failed to migrate memory" in str(exc_info.value)
        assert "kv store migrations failed for entity" in str(exc_info.value)


class TestRunVectorMigrations:
    """
    Test cases for run_vector_migrations function
    """

    @pytest.mark.asyncio
    async def test_vector_migration_with_matching_collections(self):
        """
        Test vector migration with matching collections
        This test verifies that vector migrations are applied to collections
        that match the registered memory type.
        """
        vector_store = MockVectorStore()
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

        await run_vector_migrations(vector_store)

        metadata = await vector_store.get_collection_metadata("user_scope_summary")
        assert metadata.get("schema_version") == 1

    @pytest.mark.asyncio
    async def test_vector_migration_empty_registry(self):
        """
        Test vector migration with empty registry
        This test verifies that migration handles empty registry gracefully.
        """
        vector_store = MockVectorStore()

        await run_vector_migrations(vector_store)

        assert len(await vector_store.list_collection_names()) == 0

    @pytest.mark.asyncio
    async def test_vector_migration_no_matching_collections(self):
        """
        Test vector migration with no matching collections
        This test verifies that migration handles case where no collections
        match the registered memory type.
        """
        vector_store = MockVectorStore()
        await vector_store.create_collection("other_collection", CollectionSchema())

        vector_registry.register(
            "summary",
            RenameScalarFieldOperation(
                metadata=OperationMetadata(schema_version=1, description="Rename field"),
                data_type="summary",
                old_field_name="old_field",
                new_field_name="new_field"
            )
        )

        await run_vector_migrations(vector_store)

        assert "other_collection" in await vector_store.list_collection_names()


class TestRunSQLMigrations:
    """
    Test cases for run_sql_migrations function
    """

    @pytest.mark.asyncio
    async def test_sql_migration_empty_registry(self):
        """
        Test SQL migration with empty registry
        This test verifies that migration handles empty registry gracefully.
        """
        db_store = MockDbStore()
        sql_db_store = SqlDbStore(db_store)

        await run_sql_migrations(sql_db_store)

    @pytest.mark.asyncio
    async def test_sql_migration_with_operations(self):
        """
        Test SQL migration with registered operations
        This test verifies that SQL migrations are executed when operations
        are registered.
        """
        db_store = MockDbStore()
        sql_db_store = SqlDbStore(db_store)

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

        await run_sql_migrations(sql_db_store)

    @pytest.mark.asyncio
    async def test_sql_migration_multiple_tables(self):
        """
        Test SQL migration for multiple tables
        This test verifies that migration works correctly when multiple
        tables have registered operations.
        """
        db_store = MockDbStore()
        sql_db_store = SqlDbStore(db_store)

        sql_registry.register(
            "table1",
            AddColumnOperation(
                metadata=OperationMetadata(schema_version=1, description="Add column to table1"),
                table="table1",
                column_name="col1",
                column_type="STRING"
            )
        )
        sql_registry.register(
            "table2",
            AddColumnOperation(
                metadata=OperationMetadata(schema_version=1, description="Add column to table2"),
                table="table2",
                column_name="col2",
                column_type="INTEGER"
            )
        )

        await run_sql_migrations(sql_db_store)
