# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
import pytest
import pytest_asyncio

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.store.kv.in_memory_kv_store import InMemoryKVStore
from openjiuwen.core.memory.migration.operation.base_operation import OperationMetadata
from openjiuwen.core.memory.migration.operation.operations import UpdateKVOperation
from openjiuwen.core.memory.migration.migrator.kv_migrator import KVMigrator, KV_SCHEMA_VERSION, KV_ENTITY_KEY


class TestKVMigrator:
    """
    Test cases for KVMigrator class
    """

    @pytest.mark.asyncio
    async def test_basic_migration(self):
        """
        Test basic KV migration functionality for data compatibility upgrade

        This test simulates migrating existing old data to new format by
        transforming old keys to new keys and removing old ones.
        """
        kv_store = InMemoryKVStore()
        migrator = KVMigrator(kv_store)

        # Simulate existing old data in store (before migration)
        await kv_store.set("old_key_v1", "old_value_v1")
        await kv_store.set("old_key_v2", "old_value_v2")

        async def update_func_v1(store):
            """
            Migration operation v1: Transform old_key_v1 to new_key_v1
            """
            old_value = await store.get("old_key_v1")
            if old_value:
                await store.set("new_key_v1", old_value)
                await store.delete("old_key_v1")

        async def update_func_v2(store):
            """
            Migration operation v2: Transform old_key_v2 to new_key_v2
            """
            old_value = await store.get("old_key_v2")
            if old_value:
                await store.set("new_key_v2", old_value)
                await store.delete("old_key_v2")

        operations = [
            UpdateKVOperation(
                metadata=OperationMetadata(
                    schema_version=1, description="Migrate old_key_v1 to new_key_v1"
                ),
                update_func=update_func_v1
            ),
            UpdateKVOperation(
                metadata=OperationMetadata(
                    schema_version=2, description="Migrate old_key_v2 to new_key_v2"
                ),
                update_func=update_func_v2
            ),
        ]

        # Execute migration
        result = await migrator.try_migrate(KV_ENTITY_KEY, operations)
        assert result

        # Verify migration results - old keys should be removed, new keys should exist
        assert await kv_store.get("old_key_v1") is None
        assert await kv_store.get("old_key_v2") is None
        assert await kv_store.get("new_key_v1") == "old_value_v1"
        assert await kv_store.get("new_key_v2") == "old_value_v2"
        assert await kv_store.get(KV_SCHEMA_VERSION) == "2"

        # Test idempotency - running again should not change anything
        result = await migrator.try_migrate(KV_ENTITY_KEY, operations)
        assert result

    @pytest.mark.asyncio
    async def test_key_rename_functionality(self):
        """
        Test key rename functionality for data compatibility upgrade

        This test verifies ability to rename keys with specific format while
        protecting keys with different formats from being modified.
        """
        kv_store = InMemoryKVStore()
        migrator = KVMigrator(kv_store)

        # Define key formats
        key_prefix1 = "KEY_PREFIX1"
        key_prefix2 = "KEY_PREFIX2"

        # Prepare test data
        test_data = {
            # Keys to be renamed (KEY_PREFIX1/<user_id>/<scope_id>)
            f"{key_prefix1}/user1/scope1": "value1",
            f"{key_prefix1}/user2/scope2": "value2",
            f"{key_prefix1}/user3/scope3": "value3",
            # Protected keys (KEY_PREFIX2/<user_id>/<scope_id>)
            f"{key_prefix2}/user1/scope1": "protected_value1",
            f"{key_prefix2}/user2/scope2": "protected_value2",
            f"{key_prefix2}/user3/scope3": "protected_value3",
            # Other format keys (should not be affected)
            "other_key1": "other_value1",
            "other_key2": "other_value2"
        }

        # Insert initial test data
        for key, value in test_data.items():
            await kv_store.set(key, value)

        # Define key rename operation
        async def rename_keys(store):
            """
            Rename keys from KEY_PREFIX1/<user_id>/<scope_id> to
            KEY_PREFIX1/<scope_id>/<user_id>
            """
            try:
                # Get all keys starting with KEY_PREFIX1
                keys_to_rename = await store.get_by_prefix(key_prefix1)

                # Execute rename operation
                for old_key, value in keys_to_rename.items():
                    # Parse old key format
                    parts = old_key.split("/")
                    if len(parts) == 3:
                        prefix, user_id, scope_id = parts

                        # Build new key format
                        new_key = f"{prefix}/{scope_id}/{user_id}"

                        # Set new key and delete old key
                        await store.set(new_key, value)
                        await store.delete(old_key)
            except Exception as e:
                logger.error(f"Error during key rename: {e}")
                raise

        operations = [
            UpdateKVOperation(
                metadata=OperationMetadata(
                    schema_version=1,
                    description="Rename keys from KEY_PREFIX1/user_id/scope_id to "
                    "KEY_PREFIX1/scope_id/user_id"
                ),
                update_func=rename_keys
            ),
        ]

        # Execute migration
        result = await migrator.try_migrate(KV_ENTITY_KEY, operations)
        assert result

        # Verify rename results
        # Verify old keys no longer exist
        assert await kv_store.get(f"{key_prefix1}/user1/scope1") is None
        assert await kv_store.get(f"{key_prefix1}/user2/scope2") is None
        assert await kv_store.get(f"{key_prefix1}/user3/scope3") is None

        # Verify new keys are created with correct values
        assert await kv_store.get(f"{key_prefix1}/scope1/user1") == "value1"
        assert await kv_store.get(f"{key_prefix1}/scope2/user2") == "value2"
        assert await kv_store.get(f"{key_prefix1}/scope3/user3") == "value3"

        # Verify protected keys remain unchanged
        assert await kv_store.get(f"{key_prefix2}/user1/scope1") == "protected_value1"
        assert await kv_store.get(f"{key_prefix2}/user2/scope2") == "protected_value2"
        assert await kv_store.get(f"{key_prefix2}/user3/scope3") == "protected_value3"

        # Verify other format keys are not affected
        assert await kv_store.get("other_key1") == "other_value1"
        assert await kv_store.get("other_key2") == "other_value2"

        # Verify version number is updated
        assert await kv_store.get(KV_SCHEMA_VERSION) == "1"

    @pytest.mark.asyncio
    async def test_key_value_merge_migration(self):
        """
        Test key value merge migration functionality for data compatibility upgrade

        This test verifies ability to merge values from multiple source keys
        into a single target key, preserving all data during migration process.
        """
        kv_store = InMemoryKVStore()
        migrator = KVMigrator(kv_store)

        # Define key formats
        key_prefix1 = "KEY_PREFIX1"
        key_prefix2 = "KEY_PREFIX2"
        key_prefix3 = "KEY_PREFIX3"

        # Prepare test data - multiple entries for same user_id and scope_id
        test_data = {
            f"{key_prefix1}/user1/scope1": json.dumps(
                {"key1": "value1", "key2": "value2"}
            ),
            f"{key_prefix1}/user2/scope2": json.dumps(
                {"key1": "value5", "key2": "value6"}
            ),
            f"{key_prefix2}/user1/scope1": json.dumps(
                {"key3": "value3", "key4": "value4"}
            ),
            f"{key_prefix2}/user2/scope2": json.dumps(
                {"key3": "value7", "key4": "value8"}
            ),
            # Add another entry for same user_id and scope_id with different prefix
            f"{key_prefix2}/user1/scope1/extra": json.dumps(
                {"key5": "value5"}
            )  # This should not be merged
        }

        # Insert initial test data
        for key, value in test_data.items():
            await kv_store.set(key, value)

        # Define key value merge operation using closure to capture prefixes
        async def merge_key_values(store):
            """
            Merge key-value pairs from KEY_PREFIX1 and KEY_PREFIX2 into KEY_PREFIX3

            Args:
                store: KV store instance
            """
            try:
                # Get all keys starting with KEY_PREFIX1
                keys1 = await store.get_by_prefix(key_prefix1)

                for key1, value1 in keys1.items():
                    try:
                        # Parse key structure
                        parts = key1.split("/")
                        if len(parts) != 3:
                            continue
                        _, user_id, scope_id = parts

                        # Build corresponding KEY_PREFIX2 key
                        key2 = f"{key_prefix2}/{user_id}/{scope_id}"
                        value2 = await store.get(key2)

                        # Merge values
                        merged_data = {}

                        # Process first value
                        if value1:
                            if isinstance(value1, bytes):
                                value1 = value1.decode('utf-8')
                            merged_data.update(json.loads(value1))

                        # Process second value
                        if value2:
                            if isinstance(value2, bytes):
                                value2 = value2.decode('utf-8')
                            merged_data.update(json.loads(value2))

                        # Save merged value to new key
                        target_key = f"{key_prefix3}/{user_id}/{scope_id}"
                        await store.set(target_key, json.dumps(merged_data))

                    except json.JSONDecodeError as e:
                        logger.error(f"Error decoding JSON for keys {key1}/{key2}: {e}")
                        # Skip data with format errors, continue processing other keys
                        continue
                    except Exception as e:
                        logger.error(f"Error merging values for keys {key1}/{key2}: {e}")
                        raise
            except Exception as e:
                logger.error(f"Error during key value merge: {e}")
                raise

        operations = [
            UpdateKVOperation(
                metadata=OperationMetadata(
                    schema_version=1,
                    description="Merge values from KEY_PREFIX1 and KEY_PREFIX2 to "
                    "KEY_PREFIX3"
                ),
                update_func=merge_key_values
            ),
        ]

        # Execute migration
        result = await migrator.try_migrate(KV_ENTITY_KEY, operations)
        assert result

        # Verify merge results
        # Verify new keys are created with correctly merged values
        merged_value1 = await kv_store.get(f"{key_prefix3}/user1/scope1")
        assert merged_value1 is not None
        merged_dict1 = json.loads(merged_value1)
        assert merged_dict1 == {
            "key1": "value1", "key2": "value2", "key3": "value3", "key4": "value4"
        }

        merged_value2 = await kv_store.get(f"{key_prefix3}/user2/scope2")
        assert merged_value2 is not None
        merged_dict2 = json.loads(merged_value2)
        assert merged_dict2 == {
            "key1": "value5", "key2": "value6", "key3": "value7", "key4": "value8"
        }

        # Verify original keys still exist (default: do not delete original keys)
        assert await kv_store.get(f"{key_prefix1}/user1/scope1") == test_data[
            f"{key_prefix1}/user1/scope1"
        ]
        assert await kv_store.get(f"{key_prefix2}/user1/scope1") == test_data[
            f"{key_prefix2}/user1/scope1"
        ]

        # Verify version number is updated
        assert await kv_store.get(KV_SCHEMA_VERSION) == "1"

    @pytest.mark.asyncio
    async def test_version_control(self):
        """
        Test version control functionality for incremental data upgrades

        This test verifies that migration operations execute selectively based on
        current schema version, supporting incremental upgrades from old data formats.
        Scenario:
        - Start with version 2 (simulating v1 already executed)
        - Operations: v1, v2, v3
        - Expected: Only v2 and v3 operations are executed (version > 2)
        """
        kv_store = InMemoryKVStore()
        migrator = KVMigrator(kv_store)

        # Set initial version in KV store to simulate existing schema version
        # This simulates that operation v1 has already been executed
        await kv_store.set(KV_SCHEMA_VERSION, "1")

        # Set up initial data as if v1 was already executed
        # v1 renamed key1 to key2
        await kv_store.set("key2", "value1")
        await kv_store.set("key4", "value4")

        async def update_func_v1(store):
            """
            Operation v1: Rename key1 to key2
            """
            old_value = await store.get("key1")
            if old_value:
                await store.set("key2", old_value)
                await store.delete("key1")

        async def update_func_v2(store):
            """
            Operation v2: Rename key2 to key3
            """
            old_value = await store.get("key2")
            if old_value:
                await store.set("key3", old_value)
                await store.delete("key2")

        async def update_func_v3(store):
            """
            Operation v3: Merge key3 and key4 into key5
            """
            value3 = await store.get("key3")
            value4 = await store.get("key4")

            merged_data = {}
            if value3:
                if isinstance(value3, bytes):
                    value3 = value3.decode('utf-8')
                merged_data["key3"] = value3
            if value4:
                if isinstance(value4, bytes):
                    value4 = value4.decode('utf-8')
                merged_data["key4"] = value4

            if merged_data:
                await store.set("key5", json.dumps(merged_data))
                await store.delete("key3")
                await store.delete("key4")

        # Define incremental migration operations
        operations = [
            UpdateKVOperation(
                metadata=OperationMetadata(
                    schema_version=1, description="Rename key1 to key2"
                ),
                update_func=update_func_v1
            ),
            UpdateKVOperation(
                metadata=OperationMetadata(
                    schema_version=2, description="Rename key2 to key3"
                ),
                update_func=update_func_v2
            ),
            UpdateKVOperation(
                metadata=OperationMetadata(
                    schema_version=3, description="Merge key3 and key4 into key5"
                ),
                update_func=update_func_v3
            ),
        ]

        # Execute migration
        result = await migrator.try_migrate(KV_ENTITY_KEY, operations)
        assert result

        # Verify only v2 and v3 were executed
        # key1 should not exist (never existed in this scenario)
        assert await kv_store.get("key1") is None

        # key2 should be deleted by v2
        assert await kv_store.get("key2") is None

        # key3 and key4 should be deleted by v3
        assert await kv_store.get("key3") is None
        assert await kv_store.get("key4") is None

        # Final merged key should exist with correct value (created by v3)
        merged_value = await kv_store.get("key5")
        assert merged_value is not None
        merged_dict = json.loads(merged_value)
        assert merged_dict == {"key3": "value1", "key4": "value4"}

        # Verify version number is updated to highest executed version
        assert await kv_store.get(KV_SCHEMA_VERSION) == "3"

    @pytest.mark.asyncio
    async def test_invalid_version_format_raises_exception(self):
        """
        Test that invalid version format raises exception
        This test verifies that when SCHEMA_VERSION field contains a non-numeric
        string, an exception is raised to prevent risky migration operations.
        """
        kv_store = InMemoryKVStore()
        migrator = KVMigrator(kv_store)

        # Set invalid version format (non-numeric string)
        await kv_store.set(KV_SCHEMA_VERSION, "invalid_version")

        async def update_func(store):
            await store.set("migrated", "true")

        operations = [
            UpdateKVOperation(
                metadata=OperationMetadata(schema_version=1, description="Migrate"),
                update_func=update_func
            ),
        ]

        # Execute migration - should raise exception
        with pytest.raises(ValueError) as exc_info:
            await migrator.try_migrate(KV_ENTITY_KEY, operations)

        assert "Invalid SCHEMA_VERSION format" in str(exc_info.value)
        assert "invalid_version" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_valid_numeric_string_version(self):
        """
        Test valid numeric string version format
        This test verifies that SCHEMA_VERSION field with valid numeric string
        is handled correctly and migration proceeds as expected.
        """
        kv_store = InMemoryKVStore()
        migrator = KVMigrator(kv_store)

        # Set valid numeric string version
        await kv_store.set(KV_SCHEMA_VERSION, "1")
        await kv_store.set("old_key", "old_value")

        async def update_func(store):
            old_value = await store.get("old_key")
            if old_value:
                await store.set("new_key", old_value)
                await store.delete("old_key")

        operations = [
            UpdateKVOperation(
                metadata=OperationMetadata(schema_version=2, description="Migrate"),
                update_func=update_func
            ),
        ]

        # Execute migration - should succeed
        result = await migrator.try_migrate(KV_ENTITY_KEY, operations)
        assert result

        # Verify migration executed
        assert await kv_store.get("old_key") is None
        assert await kv_store.get("new_key") == "old_value"
        assert await kv_store.get(KV_SCHEMA_VERSION) == "2"

    @pytest.mark.asyncio
    async def test_integer_version_type(self):
        """
        Test integer version type
        This test verifies that SCHEMA_VERSION field with integer type
        is handled correctly and migration proceeds as expected.
        """
        kv_store = InMemoryKVStore()
        migrator = KVMigrator(kv_store)

        # Set integer version type
        await kv_store.set(KV_SCHEMA_VERSION, 1)
        await kv_store.set("old_key", "old_value")

        async def update_func(store):
            old_value = await store.get("old_key")
            if old_value:
                await store.set("new_key", old_value)
                await store.delete("old_key")

        operations = [
            UpdateKVOperation(
                metadata=OperationMetadata(schema_version=2, description="Migrate"),
                update_func=update_func
            ),
        ]

        # Execute migration - should succeed
        result = await migrator.try_migrate(KV_ENTITY_KEY, operations)
        assert result

        # Verify migration executed
        assert (await kv_store.get("old_key")) is None
        assert await kv_store.get("new_key") == "old_value"
        assert await kv_store.get(KV_SCHEMA_VERSION) == "2"

    @pytest.mark.asyncio
    async def test_version_field_not_exists(self):
        """
        Test version field not exists scenario
        This test verifies that when SCHEMA_VERSION field does not exist,
        all migration operations are executed (first time installation).
        """
        kv_store = InMemoryKVStore()
        migrator = KVMigrator(kv_store)

        # No version field set (first time installation)

        async def update_func(store):
            await store.set("initialized", "true")

        operations = [
            UpdateKVOperation(
                metadata=OperationMetadata(schema_version=1, description="Initialize"),
                update_func=update_func
            ),
        ]

        # Execute migration - should succeed
        result = await migrator.try_migrate(KV_ENTITY_KEY, operations)
        assert result

        # Verify operation executed
        assert await kv_store.get("initialized") == "true"
        assert await kv_store.get(KV_SCHEMA_VERSION) == "1"

    @pytest.mark.asyncio
    async def test_schema_version_zero(self):
        """
        Test schema version zero scenario
        This test verifies that schema version 0 is handled correctly
        and migration proceeds as expected.
        """
        kv_store = InMemoryKVStore()
        migrator = KVMigrator(kv_store)

        # Set schema version to 0
        await kv_store.set(KV_SCHEMA_VERSION, 0)

        async def update_func(store):
            await store.set("migrated", "true")

        operations = [
            UpdateKVOperation(
                metadata=OperationMetadata(schema_version=1, description="Migrate"),
                update_func=update_func
            ),
        ]

        # Execute migration - should succeed
        result = await migrator.try_migrate(KV_ENTITY_KEY, operations)
        assert result

        # Verify migration executed
        assert await kv_store.get("migrated") == "true"
        assert await kv_store.get(KV_SCHEMA_VERSION) == "1"

    @pytest.mark.asyncio
    async def test_schema_version_large_number(self):
        """
        Test schema version with large number
        This test verifies that schema version with large number (999999)
        is handled correctly.
        """
        kv_store = InMemoryKVStore()
        migrator = KVMigrator(kv_store)

        # Set schema version to large number
        await kv_store.set(KV_SCHEMA_VERSION, 999999)

        async def update_func(store):
            await store.set("migrated", "true")

        operations = [
            UpdateKVOperation(
                metadata=OperationMetadata(
                    schema_version=1000000, description="Migrate"
                ),
                update_func=update_func
            ),
        ]

        # Execute migration - should succeed
        result = await migrator.try_migrate(KV_ENTITY_KEY, operations)
        assert result

        # Verify migration executed
        assert await kv_store.get("migrated") == "true"
        assert await kv_store.get(KV_SCHEMA_VERSION) == "1000000"

    @pytest.mark.asyncio
    async def test_schema_version_negative_number_raises_exception(self):
        """
        Test that negative schema version raises exception
        This test verifies that negative schema version is rejected
        to prevent invalid migration operations.
        """
        kv_store = InMemoryKVStore()
        migrator = KVMigrator(kv_store)

        # Set negative schema version
        await kv_store.set(KV_SCHEMA_VERSION, -1)

        async def update_func(store):
            await store.set("migrated", "true")

        operations = [
            UpdateKVOperation(
                metadata=OperationMetadata(schema_version=1, description="Migrate"),
                update_func=update_func
            ),
        ]

        # Execute migration - should succeed (negative numbers are allowed)
        result = await migrator.try_migrate(KV_ENTITY_KEY, operations)
        assert result

        # Verify migration executed
        assert await kv_store.get("migrated") == "true"
        assert await kv_store.get(KV_SCHEMA_VERSION) == "1"

    @pytest.mark.asyncio
    async def test_schema_version_float_raises_exception(self):
        """
        Test that float schema version raises exception
        This test verifies that float schema version is rejected
        to prevent invalid migration operations.
        """
        kv_store = InMemoryKVStore()
        migrator = KVMigrator(kv_store)

        # Set float schema version
        await kv_store.set(KV_SCHEMA_VERSION, 1.5)

        async def update_func(store):
            await store.set("migrated", "true")

        operations = [
            UpdateKVOperation(
                metadata=OperationMetadata(schema_version=2, description="Migrate"),
                update_func=update_func
            ),
        ]

        # Execute migration - should raise exception
        with pytest.raises(ValueError) as exc_info:
            await migrator.try_migrate(KV_ENTITY_KEY, operations)

        assert "Invalid SCHEMA_VERSION type" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_special_character_key_names(self):
        """
        Test special character key names handling
        This test verifies that migration operations correctly handle
        keys with special characters.
        """
        kv_store = InMemoryKVStore()
        migrator = KVMigrator(kv_store)

        # Set up test data with special characters
        special_keys = [
            "key/with/slashes",
            "key:with:colons",
            "key-with-dashes",
            "key_with_underscores",
            "key.with.dots",
            "key@with@at",
            "key#with#hash"
        ]

        for key in special_keys:
            await kv_store.set(key, f"value_{key}")

        # Define migration operation to rename keys
        async def rename_special_keys(store):
            for old_key in special_keys:
                old_value = await store.get(old_key)
                if old_value:
                    new_key = f"renamed_{old_key}"
                    await store.set(new_key, old_value)
                    await store.delete(old_key)

        operations = [
            UpdateKVOperation(
                metadata=OperationMetadata(
                    schema_version=1, description="Rename special character keys"
                ),
                update_func=rename_special_keys
            ),
        ]

        # Execute migration
        result = await migrator.try_migrate(KV_ENTITY_KEY, operations)
        assert result

        # Verify rename results
        for old_key in special_keys:
            assert await kv_store.get(old_key) is None
            new_key = f"renamed_{old_key}"
            assert await kv_store.get(new_key) == f"value_{old_key}"

        # Verify version number is updated
        assert await kv_store.get(KV_SCHEMA_VERSION) == "1"

    @pytest.mark.asyncio
    async def test_migration_interrupt_and_resume(self):
        """
        Test migration interrupt and resume scenario
        This test verifies that migration can resume after interruption
        by checking current schema version.
        """
        kv_store = InMemoryKVStore()
        migrator = KVMigrator(kv_store)

        # Set schema version to 1 (simulating v1 already executed)
        await kv_store.set(KV_SCHEMA_VERSION, "1")
        await kv_store.set("key1", "value1")

        # Define migration operations
        async def update_func_v2(store):
            await store.set("key2", "value2")

        async def update_func_v3(store):
            await store.set("key3", "value3")

        operations = [
            UpdateKVOperation(
                metadata=OperationMetadata(schema_version=2, description="Migrate v2"),
                update_func=update_func_v2
            ),
            UpdateKVOperation(
                metadata=OperationMetadata(schema_version=3, description="Migrate v3"),
                update_func=update_func_v3
            ),
        ]

        # Execute migration (simulating resume from version 1)
        result = await migrator.try_migrate(KV_ENTITY_KEY, operations)
        assert result

        # Verify v2 and v3 executed
        assert await kv_store.get("key2") == "value2"
        assert await kv_store.get("key3") == "value3"
        assert await kv_store.get(KV_SCHEMA_VERSION) == "3"

    @pytest.mark.asyncio
    async def test_partial_migration_continue_upgrade(self):
        """
        Test partial migration continue upgrade scenario
        This test verifies that migration can continue from partial state
        by executing only remaining operations.
        """
        kv_store = InMemoryKVStore()
        migrator = KVMigrator(kv_store)

        # Set schema version to 2 (simulating v1 and v2 already executed)
        await kv_store.set(KV_SCHEMA_VERSION, "2")
        await kv_store.set("key2", "value2")

        # Define migration operations
        async def update_func_v1(store):
            await store.set("key1", "value1")

        async def update_func_v2(store):
            await store.set("key2", "value2")

        async def update_func_v3(store):
            await store.set("key3", "value3")

        operations = [
            UpdateKVOperation(
                metadata=OperationMetadata(schema_version=1, description="Migrate v1"),
                update_func=update_func_v1
            ),
            UpdateKVOperation(
                metadata=OperationMetadata(schema_version=2, description="Migrate v2"),
                update_func=update_func_v2
            ),
            UpdateKVOperation(
                metadata=OperationMetadata(schema_version=3, description="Migrate v3"),
                update_func=update_func_v3
            ),
        ]

        # Execute migration
        result = await migrator.try_migrate(KV_ENTITY_KEY, operations)
        assert result

        # Verify only v3 executed
        assert await kv_store.get("key1") is None
        assert await kv_store.get("key2") == "value2"
        assert await kv_store.get("key3") == "value3"
        assert await kv_store.get(KV_SCHEMA_VERSION) == "3"

   

    @pytest.mark.asyncio
    async def test_migration_invalid_entity_key(self):
        """
        Test that invalid entity_key returns False
        This test verifies that when entity_key is not KV_ENTITY_KEY,
        the method returns False instead of raising exception.
        """
        kv_store = InMemoryKVStore()
        migrator = KVMigrator(kv_store)

        async def update_func(store):
            await store.set("migrated", "true")

        operations = [
            UpdateKVOperation(
                metadata=OperationMetadata(schema_version=1, description="Migrate"),
                update_func=update_func
            ),
        ]

        # Execute migration with invalid entity_key - should return False
        result = await migrator.try_migrate("invalid_entity_key", operations)
        assert result is False

    @pytest.mark.asyncio
    async def test_migration_failure_rolls_back_to_initial(self):
        """
        Test that migration failure rolls back data to initial state
        This test verifies that when a migration operation fails during execution,
        all changes are rolled back to state before migration started,
        including both data and schema version.
        """
        from openjiuwen.core.memory.common.kv_prefix_registry import kv_prefix_registry

        kv_store = InMemoryKVStore()
        migrator = KVMigrator(kv_store)

        # Register a prefix for testing
        test_prefix = "TEST_PREFIX_ROLLBACK"
        kv_prefix_registry.register_current(test_prefix)

        try:
            # Set initial state
            await kv_store.set(KV_SCHEMA_VERSION, "1")
            await kv_store.set(f"{test_prefix}/initial_key1", "initial_value1")
            await kv_store.set(f"{test_prefix}/initial_key2", "initial_value2")

            # Define migration operations where v3 will fail
            async def update_func_v2(store):
                await store.set(f"{test_prefix}/v2_key", "v2_value")
                await store.delete(f"{test_prefix}/initial_key1")

            async def update_func_v3(store):
                await store.set(f"{test_prefix}/v3_key", "v3_value")
                await store.delete(f"{test_prefix}/initial_key2")
                raise Exception("Migration v3 failed")

            operations = [
                UpdateKVOperation(
                    metadata=OperationMetadata(
                        schema_version=2, description="Migrate v2"
                    ),
                    update_func=update_func_v2
                ),
                UpdateKVOperation(
                    metadata=OperationMetadata(
                        schema_version=3, description="Migrate v3"
                    ),
                    update_func=update_func_v3
                ),
            ]

            # Execute migration - should fail and rollback
            result = await migrator.try_migrate(KV_ENTITY_KEY, operations)
            assert result is False

            # Verify rollback to initial state
            assert await kv_store.get(
                f"{test_prefix}/initial_key1"
            ) == "initial_value1"
            assert await kv_store.get(
                f"{test_prefix}/initial_key2"
            ) == "initial_value2"
            assert await kv_store.get(KV_SCHEMA_VERSION) == "1"

            # Verify v2 changes were rolled back
            assert await kv_store.get(f"{test_prefix}/v2_key") is None
            assert await kv_store.get(f"{test_prefix}/v3_key") is None
        finally:
            kv_prefix_registry.unregister(test_prefix)

    @pytest.mark.asyncio
    async def test_migration_failure_rolls_back_with_prefix_data(self):
        """
        Test that migration failure rolls back prefix-based data correctly
        This test verifies rollback functionality with data using (test_prefix)
        """
        from openjiuwen.core.memory.common.kv_prefix_registry import kv_prefix_registry

        kv_store = InMemoryKVStore()
        migrator = KVMigrator(kv_store)

        # Register a prefix for testing
        test_prefix = "TEST_PREFIX"
        kv_prefix_registry.register_current(test_prefix)

        try:
            # Set initial state with prefix-based keys
            await kv_store.set(KV_SCHEMA_VERSION, "1")
            await kv_store.set(f"{test_prefix}/key1", "value1")
            await kv_store.set(f"{test_prefix}/key2", "value2")
            await kv_store.set("other_key", "other_value")

            # Define migration operations where v2 will fail
            async def update_func_v2(store):
                await store.set(f"{test_prefix}/new_key", "new_value")
                await store.delete(f"{test_prefix}/key1")
                raise Exception("Migration v2 failed")

            operations = [
                UpdateKVOperation(
                    metadata=OperationMetadata(
                        schema_version=2, description="Migrate v2"
                    ),
                    update_func=update_func_v2
                ),
            ]

            # Execute migration - should fail and rollback
            result = await migrator.try_migrate(KV_ENTITY_KEY, operations)
            assert result is False

            # Verify rollback to initial state
            assert await kv_store.get(f"{test_prefix}/key1") == "value1"
            assert await kv_store.get(f"{test_prefix}/key2") == "value2"
            assert await kv_store.get("other_key") == "other_value"
            assert await kv_store.get(KV_SCHEMA_VERSION) == "1"

            # Verify v2 changes were rolled back
            assert await kv_store.get(f"{test_prefix}/new_key") is None
        finally:
            kv_prefix_registry.unregister(test_prefix)
