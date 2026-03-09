# coding: utf-8
# Copyright (c) Huawei Technologies by Co., Ltd. 2025. All rights reserved.

import copy
import pytest

from openjiuwen.core.memory.migration.migration_plan import sql_registry, vector_registry, kv_registry
from openjiuwen.core.memory.migration.operation.base_operation import OperationMetadata
from openjiuwen.core.memory.migration.operation.operations import UpdateKVOperation
from openjiuwen.core.foundation.store.kv.in_memory_kv_store import InMemoryKVStore


@pytest.fixture(scope="function", autouse=True)
def registry_backup_and_restore():
    """
    Function-level fixture to backup and restore registry state
    Ensures test isolation by clearing registries before each test
    and restoring original state after each test completes.
    """
    original_sql_registry = copy.deepcopy(sql_registry.get_all_operations())
    original_vector_registry = copy.deepcopy(vector_registry.get_all_operations())
    original_kv_registry = copy.deepcopy(kv_registry.get_all_operations())

    sql_registry.clear()
    vector_registry.clear()
    kv_registry.clear()

    yield

    sql_registry.clear()
    sql_registry.set_operations(original_sql_registry)
    vector_registry.clear()
    vector_registry.set_operations(original_vector_registry)
    kv_registry.clear()
    kv_registry.set_operations(original_kv_registry)


class TestMigrationPlan:
    """
    Test cases for migration_plan.py registry objects
    Tests verify the behavior of registry objects and their operations
    """

    @staticmethod
    def test_kv_registry_initialization():
        """
        Test that kv_registry is properly initialized
        """
        assert kv_registry is not None
        assert isinstance(kv_registry.get_all_operations(), dict)

    @staticmethod
    def test_sql_registry_initialization():
        """
        Test that sql_registry is properly initialized
        """
        assert sql_registry is not None
        assert isinstance(sql_registry.get_all_operations(), dict)

    @staticmethod
    def test_vector_registry_initialization():
        """
        Test that vector_registry is properly initialized
        """
        assert vector_registry is not None
        assert isinstance(vector_registry.get_all_operations(), dict)

    @staticmethod
    def test_kv_registry_register_operation():
        """
        Test registering an operation to kv_registry
        """
        async def update_func(store):
            await store.set("test_key", "test_value")

        operation = UpdateKVOperation(
            metadata=OperationMetadata(schema_version=1, description="Test operation"),
            update_func=update_func
        )

        kv_registry.register("test_entity", operation)

        operations = kv_registry.get_operations("test_entity", 1, 1)
        assert len(operations) == 1
        assert operations[0].schema_version == 1

    @staticmethod
    def test_kv_registry_get_operations_operations_empty():
        """
        Test getting operations when none are registered
        """
        operations = kv_registry.get_operations("nonexistent_entity", 1, 10)
        assert len(operations) == 0

    @staticmethod
    def test_kv_registry_get_current_version_empty():
        """
        Test getting current version when no operations are registered
        """
        version = kv_registry.get_current_version("nonexistent_entity")
        assert version == 0

    @staticmethod
    def test_kv_registry_get_all_entities_empty():
        """
        Test getting all entities when none are registered
        """
        entities = kv_registry.get_all_entities()
        assert len(entities) == 0

    @staticmethod
    def test_kv_registry_get_all_operations_empty():
        """
        Test getting all operations when none are registered
        """
        all_ops = kv_registry.get_all_operations()
        assert len(all_ops) == 0

    @staticmethod
    def test_kv_registry_multiple_operations_same_entity():
        """
        Test registering multiple operations for the same entity
        """
        async def update_func_v1(store):
            await store.set("key_v1", "value_v1")

        async def update_func_v2(store):
            await store.set("key_v2", "value_v2")

        kv_registry.register(
            "test_entity",
            UpdateKVOperation(
                metadata=OperationMetadata(schema_version=1, description="Test v1"),
                update_func=update_func_v1
            )
        )
        kv_registry.register(
            "test_entity",
            UpdateKVOperation(
                metadata=OperationMetadata(schema_version=2, description="Test v2"),
                update_func=update_func_v2
            )
        )

        operations = kv_registry.get_operations("test_entity", 1, 2)
        assert len(operations) == 2
        assert operations[0].schema_version == 1
        assert operations[1].schema_version == 2

    @staticmethod
    def test_kv_registry_get_operations_version_range():
        """
        Test getting operations within a specific version range
        """
        async def update_func(store):
            await store.set("test_key", "test_value")

        for version in range(1, 6):
            kv_registry.register(
                "test_entity",
                UpdateKVOperation(
                    metadata=OperationMetadata(schema_version=version, description=f"Test v{version}"),
                    update_func=update_func
                )
            )

        operations = kv_registry.get_operations("test_entity", 2, 4)
        assert len(operations) == 3
        assert operations[0].schema_version == 2
        assert operations[1].schema_version == 3
        assert operations[2].schema_version == 4

    @staticmethod
    def test_kv_registry_get_operations_invalid_range():
        """
        Test getting operations with invalid version range (from > to)
        """
        async def update_func(store):
            await store.set("test_key", "test_value")

        kv_registry.register(
            "test_entity",
            UpdateKVOperation(
                metadata=OperationMetadata(schema_version=1, description="Test"),
                update_func=update_func
            )
        )

        operations = kv_registry.get_operations("test_entity", 5, 1)
        assert len(operations) == 0

    @staticmethod
    def test_kv_registry_get_current_version():
        """
        Test getting the current version for an entity
        """
        async def update_func(store):
            await store.set("test_key", "test_value")

        kv_registry.register(
            "test_entity",
            UpdateKVOperation(
                metadata=OperationMetadata(schema_version=3, description="Test"),
                update_func=update_func
            )
        )

        version = kv_registry.get_current_version("test_entity")
        assert version == 3

    @staticmethod
    def test_kv_registry_get_all_entities():
        """
        Test getting all registered entities
        """
        async def update_func(store):
            await store.set("test_key", "test_value")

        kv_registry.register(
            "entity1",
            UpdateKVOperation(
                metadata=OperationMetadata(schema_version=1, description="Test"),
                update_func=update_func
            )
        )
        kv_registry.register(
            "entity2",
            UpdateKVOperation(
                metadata=OperationMetadata(schema_version=1, description="Test"),
                update_func=update_func
            )
        )

        entities = kv_registry.get_all_entities()
        assert len(entities) == 2
        assert "entity1" in entities
        assert "entity2" in entities

    @staticmethod
    def test_kv_registry_get_all_operations():
        """
        Test getting all operations from the registry
        """
        async def update_func(store):
            await store.set("test_key", "test_value")

        kv_registry.register(
            "entity1",
            UpdateKVOperation(
                metadata=OperationMetadata(schema_version=1, description="Test"),
                update_func=update_func
            )
        )
        kv_registry.register(
            "entity2",
            UpdateKVOperation(
                metadata=OperationMetadata(schema_version=2, description="Test"),
                update_func=update_func
            )
        )

        all_ops = kv_registry.get_all_operations()
        assert len(all_ops) == 2
        assert "entity1" in all_ops
        assert "entity2" in all_ops
        assert len(all_ops["entity1"]) == 1
        assert len(all_ops["entity2"]) == 1

    @staticmethod
    def test_kv_registry_register_same_version_raises_error():
        """
        Test that registering multiple operations with the same version raises an error
        """
        async def update_func1(store):
            await store.set("key1", "value1")

        async def update_func2(store):
            await store.set("key2", "value2")

        kv_registry.register(
            "test_entity",
            UpdateKVOperation(
                metadata=OperationMetadata(schema_version=1, description="Test 1"),
                update_func=update_func1
            )
        )

        with pytest.raises(Exception) as exc_info:
            kv_registry.register(
                "test_entity",
                UpdateKVOperation(
                    metadata=OperationMetadata(schema_version=1, description="Test 2"),
                    update_func=update_func2
                )
            )

        assert "failed to register operation" in str(exc_info.value)
        assert "schema number of the new operation must be greater than the current maximum" in str(exc_info.value)

    @staticmethod
    def test_kv_registry_register_lower_version_raises_error():
        """
        Test that registering a lower version raises an error
        """
        async def update_func(store):
            await store.set("test_key", "test_value")

        kv_registry.register(
            "test_entity",
            UpdateKVOperation(
                metadata=OperationMetadata(schema_version=3, description="Test v3"),
                update_func=update_func
            )
        )

        with pytest.raises(Exception) as exc_info:
            kv_registry.register(
                "test_entity",
                UpdateKVOperation(
                    metadata=OperationMetadata(schema_version=1, description="Test v1"),
                    update_func=update_func
                )
            )

        assert "failed to register operation" in str(exc_info.value)
        assert "schema number of the new operation must be greater than the current maximum" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_kv_registry_operation_execution(self):
        """
        Test that registered operations can be executed correctly
        """
        kv_store = InMemoryKVStore()

        async def update_func(store):
            await store.set("test_key", "test_value")
            await store.set("another_key", "another_value")

        kv_registry.register(
            "test_entity",
            UpdateKVOperation(
                metadata=OperationMetadata(schema_version=1, description="Test"),
                update_func=update_func
            )
        )

        operations = kv_registry.get_operations("test_entity", 1, 1)
        assert len(operations) == 1

        await operations[0].update_func(kv_store)

        assert await kv_store.get("test_key") == "test_value"
        assert await kv_store.get("another_key") == "another_value"

    @pytest.mark.asyncio
    async def test_kv_registry_multiple_operations_execution_order(self):
        """
        Test that multiple operations execute in the correct order
        """
        kv_store = InMemoryKVStore()

        execution_order = []

        async def update_func_v1(store):
            execution_order.append(1)
            await store.set("order", "v1")

        async def update_func_v2(store):
            execution_order.append(2)
            await store.set("order", "v2")

        async def update_func_v3(store):
            execution_order.append(3)
            await store.set("order", "v3")

        kv_registry.register(
            "test_entity",
            UpdateKVOperation(
                metadata=OperationMetadata(schema_version=1, description="Test v1"),
                update_func=update_func_v1
            )
        )
        kv_registry.register(
            "test_entity",
            UpdateKVOperation(
                metadata=OperationMetadata(schema_version=2, description="Test v2"),
                update_func=update_func_v2
            )
        )
        kv_registry.register(
            "test_entity",
            UpdateKVOperation(
                metadata=OperationMetadata(schema_version=3, description="Test v3"),
                update_func=update_func_v3
            )
        )

        operations = kv_registry.get_operations("test_entity", 1, 3)
        assert len(operations) == 3

        for op in operations:
            await op.update_func(kv_store)

        assert execution_order == [1, 2, 3]
        assert await kv_store.get("order") == "v3"

    @staticmethod
    def test_sql_registry_operations():
        """
        Test that sql_registry works correctly
        """
        assert sql_registry is not None
        entities = sql_registry.get_all_entities()
        assert isinstance(entities, list)

    @staticmethod
    def test_vector_registry_operations():
        """
        Test that vector_registry works correctly
        """
        assert vector_registry is not None
        entities = vector_registry.get_all_entities()
        assert isinstance(entities, list)

    @staticmethod
    def test_registry_cleanup_and_restore():
        """
        Test registry cleanup and restore functionality
        This test verifies that registry can be cleared and restored
        to its original state, ensuring test isolation.
        """
        # Register some operations
        async def update_func(store):
            await store.set("test_key", "test_value")

        kv_registry.register(
            "test_entity1",
            UpdateKVOperation(
                metadata=OperationMetadata(schema_version=1, description="Test 1"),
                update_func=update_func
            )
        )
        kv_registry.register(
            "test_entity2",
            UpdateKVOperation(
                metadata=OperationMetadata(schema_version=2, description="Test 2"),
                update_func=update_func
            )
        )

        # Save current state
        current_state = copy.deepcopy(kv_registry.get_all_operations())

        # Clear registry
        kv_registry.clear()

        # Verify registry is empty
        assert len(kv_registry.get_all_entities()) == 0

        # Restore registry
        kv_registry.set_operations(current_state)

        # Verify registry is restored
        entities = kv_registry.get_all_entities()
        assert len(entities) == 2
        assert "test_entity1" in entities
        assert "test_entity2" in entities
