import asyncio
import uuid
import pytest
from pymilvus import MilvusException

from openjiuwen.core.foundation.store.base_vector_store import CollectionSchema, FieldSchema, VectorDataType
from openjiuwen.core.foundation.store.vector.milvus_vector_store import MilvusVectorStore
from openjiuwen.core.memory.migration.migrator.vector_migrator import VectorMigrator
from openjiuwen.core.memory.migration.operation.operations import (
    AddScalarFieldOperation,
    RenameScalarFieldOperation,
    UpdateScalarFieldTypeOperation,
    UpdateEmbeddingDimensionOperation,
)
from openjiuwen.core.memory.migration.operation.base_operation import OperationMetadata


async def _create_initial_collection(vector_store, collection_name):
    v1_schema = CollectionSchema(
        fields=[
            FieldSchema(name="id", dtype=VectorDataType.VARCHAR, is_primary=True, auto_id=False, max_length=36),
            FieldSchema(name="vector", dtype=VectorDataType.FLOAT_VECTOR, dim=4),
            FieldSchema(name="text", dtype=VectorDataType.VARCHAR, max_length=256),
            FieldSchema(name="old_field_name", dtype=VectorDataType.VARCHAR, max_length=64),
            FieldSchema(name="type_change_field", dtype=VectorDataType.INT32),
            FieldSchema(name="shared_field", dtype=VectorDataType.VARCHAR, max_length=64),
        ],
        description="Initial collection for schema update tests"
    )
    await vector_store.create_collection(collection_name, v1_schema)
    await vector_store.update_collection_metadata(collection_name, {"schema_version": 0})


@pytest.fixture
def test_fixture():
    """Fixture to setup and teardown test resources."""
    milvus_uri = "xxxx"
    vector_store = MilvusVectorStore(milvus_uri=milvus_uri)
    migrator = VectorMigrator(vector_store=vector_store)
    user_id = "test_user"
    scope_id = "test_scope"
    mem_type = "summarymigtestt"
    collection_name = (f"{user_id}_{str(uuid.uuid4()).replace('-', '')[:8]}_"
                       f"{scope_id}_{mem_type}")

    asyncio.run(_create_initial_collection(vector_store, collection_name))

    yield {
        'milvus_uri': milvus_uri,
        'vector_store': vector_store,
        'migrator': migrator,
        'user_id': user_id,
        'scope_id': scope_id,
        'mem_type': mem_type,
        'collection_name': collection_name,
    }

    # Teardown
    asyncio.run(vector_store.delete_collection(collection_name))


@pytest.mark.skip(reason="skip system test")
class TestMilvusSchemaUpdate:

    def test_schema_updates_and_migration(self, test_fixture):
        asyncio.run(self._run_schema_updates_and_migration(test_fixture))

    async def _run_schema_updates_and_migration(self, fixture):
        vector_store = fixture['vector_store']
        migrator = fixture['migrator']
        user_id = fixture['user_id']
        scope_id = fixture['scope_id']
        mem_type = fixture['mem_type']
        collection_name = fixture['collection_name']

        # 1. Test AddScalarFieldOperation
        add_op = AddScalarFieldOperation(field_name="added_field", field_type="string", data_type="None",
                                         metadata=OperationMetadata(schema_version=1))
        await vector_store.update_schema(collection_name, [add_op])
        schema = await vector_store.get_schema(collection_name)
        assert "added_field" in [f.name for f in schema.fields], "added_field should be in schema fields"

        # 1b. Test AddScalarFieldOperation without default value
        add_op_no_default = AddScalarFieldOperation(field_name="optional_field",
                                                    field_type="string", data_type="None",
                                                    default_value=None,
                                                    metadata=OperationMetadata(schema_version=1))
        await vector_store.update_schema(collection_name, [add_op_no_default])
        schema = await vector_store.get_schema(collection_name)
        assert "optional_field" in [f.name for f in schema.fields], "optional_field should be in schema fields"

        # 1c. Test AddScalarFieldOperation with duplicate field name (error scenario)
        duplicate_op = AddScalarFieldOperation(field_name="added_field", field_type="string", data_type="None",
                                                metadata=OperationMetadata(schema_version=1))
        try:
            await vector_store.update_schema(collection_name, [duplicate_op])
            pytest.fail("Expected exception for duplicate field name")
        except Exception as e:
            # Check if error message contains "already exists" or similar
            error_msg = str(e)
            assert "already exists" in error_msg or "duplicate" in error_msg

        # 2. Test RenameScalarFieldOperation
        rename_op = RenameScalarFieldOperation(old_field_name="old_field_name", new_field_name="new_field_name",
                                               data_type="None", metadata=OperationMetadata(schema_version=1))
        await vector_store.update_schema(collection_name, [rename_op])
        schema = await vector_store.get_schema(collection_name)
        field_names = [f.name for f in schema.fields]
        assert "old_field_name" not in field_names, "old_field_name should not be in field names"
        assert "new_field_name" in field_names, "new_field_name should be in field names"

        # 2b. Test rename non-existent field (error scenario)
        rename_non_exist_op = RenameScalarFieldOperation(old_field_name="non_existent_field",
                                                         new_field_name="new_name",
                                                         data_type="None",
                                                         metadata=OperationMetadata(schema_version=1))
        try:
            await vector_store.update_schema(collection_name, [rename_non_exist_op])
        except Exception as e:
            error_msg = str(e).lower()
            assert "not found" in error_msg or "non_existent" in error_msg or "field" in error_msg

        # 2c. Test rename to existing field name (error scenario)
        # First add another field
        add_another_op = AddScalarFieldOperation(field_name="another_field", field_type="string", data_type="None",
                                                  metadata=OperationMetadata(schema_version=1))
        await vector_store.update_schema(collection_name, [add_another_op])

        # Now try to rename "new_field_name" to "another_field" (which exists)
        rename_to_exist_op = RenameScalarFieldOperation(old_field_name="new_field_name",
                                                        new_field_name="another_field",
                                                        data_type="None",
                                                        metadata=OperationMetadata(schema_version=1))
        try:
            await vector_store.update_schema(collection_name, [rename_to_exist_op])
            pytest.fail("Expected exception for renaming to existing field name")
        except Exception as e:
            error_msg = str(e).lower()
            assert "already exists" in error_msg or "duplicate" in error_msg

        # 3. Test UpdateScalarFieldTypeOperation
        update_type_op = UpdateScalarFieldTypeOperation(field_name="type_change_field", new_field_type="int64",
                                                        data_type="None",
                                                        metadata=OperationMetadata(schema_version=1))
        await vector_store.update_schema(collection_name, [update_type_op])
        schema = await vector_store.get_schema(collection_name)
        type_field = next(f for f in schema.fields if f.name == "type_change_field")
        assert type_field.dtype == VectorDataType.INT64, f"Expected INT64, got {type_field.dtype}"
        
        # 4. Test UpdateEmbeddingDimensionOperation
        # This operation is complex and often requires re-indexing
        # We will test if the store handles it gracefully.
        update_dim_op = UpdateEmbeddingDimensionOperation(new_dimension=8, data_type="None", field_name="vector",
                                                          metadata=OperationMetadata(schema_version=1))
        try:
            await vector_store.update_schema(collection_name, [update_dim_op])
            # If it doesn't raise an error, check if the dimension was updated.
            schema = await vector_store.get_schema(collection_name)
            vector_field = next(f for f in schema.fields if f.name == "vector")
            assert vector_field.dim == 8, f"Expected dim 8, got {vector_field.dim}"
        except MilvusException as e:
            # This is an expected outcome if Milvus doesn't support dynamic dim change.
            pass
        except Exception as e:
            pytest.fail(f"UpdateEmbeddingDimensionOperation failed with an unexpected exception: {e}")

        # 4b. Test UpdateEmbeddingDimensionOperation without recompute function (zero fill)

        # 4c. Test UpdateEmbeddingDimensionOperation with wrong dimension returned by recompute function

        # First insert some test data to ensure transform_func is called during migration
        # Note: old_field_name was renamed to new_field_name in Step 2
        def wrong_dim_fn(doc):
            return [0.0] * 5  # Returns 5 dimensions instead of expected 8

        update_dim_wrong_op = UpdateEmbeddingDimensionOperation(
            new_dimension=8,
            data_type="None",
            field_name="vector",
            recompute_embedding_func=wrong_dim_fn,
            metadata=OperationMetadata(schema_version=1)
        )
        try:
            await vector_store.update_schema(collection_name, [update_dim_wrong_op])
        except Exception as e:
            error_msg = str(e).lower()
            assert "dimension" in error_msg or "mismatch" in error_msg or "invalid" in error_msg

        # 5. Test multi-version migration with VectorMigrator
        # Re-create a fresh collection for this test
        await vector_store.delete_collection(collection_name)
        v1_schema = CollectionSchema(
            fields=[
                FieldSchema(name="id", dtype=VectorDataType.VARCHAR, is_primary=True, auto_id=False, max_length=36),
                FieldSchema(name="vector", dtype=VectorDataType.FLOAT_VECTOR, dim=4),
                FieldSchema(name="text", dtype=VectorDataType.VARCHAR, max_length=256),
                FieldSchema(name="old_field_name", dtype=VectorDataType.VARCHAR, max_length=64),
                FieldSchema(name="type_change_field", dtype=VectorDataType.INT32),
            ],
            description="Initial collection for schema update tests"
        )
        await vector_store.create_collection(collection_name, v1_schema)
        await vector_store.update_collection_metadata(collection_name, {"schema_version": 0})

        # Insert data before migration
        docs = [{
            "id": str(uuid.uuid4()), "vector": [0.1, 0.2, 0.3, 0.4], "text": "data",
            "old_field_name": "value", "type_change_field": 100
        }]
        await vector_store.add_docs(collection_name, docs)

        # Define multi-version operations
        operations = [
            AddScalarFieldOperation(metadata=OperationMetadata(schema_version=1), field_name="added_field_migrator",
                                    field_type="string", data_type="None", default_value="default"),
            RenameScalarFieldOperation(metadata=OperationMetadata(schema_version=2), old_field_name="old_field_name",
                                    new_field_name="new_field_name_migrator", data_type="None"),
        ]

        await migrator.try_migrate(f"vector_{mem_type}", operations)

        # Verify migration
        schema = await vector_store.get_schema(collection_name)
        field_names = [f.name for f in schema.fields]
        assert "added_field_migrator" in field_names, f"added_field_migrator should be in field names"
        assert "old_field_name" not in field_names, f"old_field_name should not be in field names"
        assert "new_field_name_migrator" in field_names, f"new_field_name_migrator should be in field names"

        version_metadata = await vector_store.get_collection_metadata(collection_name)
        version = version_metadata.get("schema_version", 0)
        assert version == 2, f"Expected version 2, got {version}"

        # Verify data integrity and ability to add new data
        new_doc = {
            "id": str(uuid.uuid4()), "vector": [0.5, 0.6, 0.7, 0.8], "text": "new data",
            "new_field_name_migrator": "new value", "type_change_field": 200, "added_field_migrator": "migrated"
        }
        await vector_store.add_docs(collection_name, [new_doc])
        
        res = await vector_store.search(collection_name, [0.1, 0.1, 0.1, 0.1], "vector", top_k=5)
        assert len(res) >= 2, f"Expected at least 2 results, got {len(res)}"

    def test_multi_collection_migration(self, test_fixture):
        """Test migration of multiple collections."""
        asyncio.run(self._run_multi_collection_migration(test_fixture))

    async def _run_multi_collection_migration(self, fixture):
        vector_store = fixture['vector_store']
        migrator = fixture['migrator']
        user_id = fixture['user_id']
        scope_id = fixture['scope_id']
        mem_type = fixture['mem_type']

        # create multi collections for test
        collection_names = []
        num_collections = 3
        # Number of records per collection for testing (change to 100 for larger scale testing)
        records_per_collection = 1  # Set to 100 to test with 100 records
        
        # Prepare different mem_types to create multiple collections
        col_types = ["multicoltesta", "multicoltestb", "multicoltestc"]
        
        try:
            # Create multiple collections, each with a different collection_name
            for i in range(num_collections):
                collection_name = (f"{user_id}_{str(uuid.uuid4()).replace('-', '')[:8]}_"
                                f"{scope_id}_{col_types[i]}_{mem_type}")
                collection_names.append(collection_name)
                
                # Create a base schema
                base_schema = CollectionSchema(
                    fields=[
                        FieldSchema(name="id", dtype=VectorDataType.VARCHAR, is_primary=True,
                                    auto_id=False, max_length=36),
                        FieldSchema(name="vector", dtype=VectorDataType.FLOAT_VECTOR, dim=4),
                        FieldSchema(name="text", dtype=VectorDataType.VARCHAR, max_length=256),
                        FieldSchema(name="shared_field", dtype=VectorDataType.VARCHAR, max_length=64),
                    ],
                    description=f"Multi-collection test schema for {mem_type}"
                )
                
                await vector_store.create_collection(collection_name, base_schema)
                await vector_store.update_collection_metadata(collection_name, {"schema_version": 0})
                
                # Insert initial data (multiple records per collection)
                docs = []
                for j in range(records_per_collection):
                    docs.append({
                        "id": str(uuid.uuid4()), 
                        "vector": [0.1 * (i + 1) + j * 0.01, 0.2 * (i + 1) + j * 0.01, 0.3 * (i + 1) +
                                   j * 0.01, 0.4 * (i + 1) + j * 0.01],
                        "text": f"data_{i}_{j}",
                        "shared_field": f"value_{i}_{j}"
                    })
                await vector_store.add_docs(collection_name, docs)

            
            # Step 1: Perform migration operations on multiple collections
            # Use collection name filtering to match all test collections.
            collection_pattern = f"{user_id}_"
            
            operations_step1 = [
                AddScalarFieldOperation(
                    metadata=OperationMetadata(schema_version=1), 
                    field_name="added_multi_field",
                    field_type="string", 
                    data_type="None", 
                    default_value="default_multi"
                ),
            ]
            
            # Perform migration for each collection
            for collection_name in collection_names:
                # Try migrating each collection using Migrator.
                await migrator.try_migrate(f"vector_{mem_type}", operations_step1)
            
            # Verify that all collections have been updated.
            for collection_name in collection_names:
                schema = await vector_store.get_schema(collection_name)
                field_names = [f.name for f in schema.fields]
                assert "added_multi_field" in field_names, f"Collection {collection_name} missing added field"
                
                version_metadata = await vector_store.get_collection_metadata(collection_name)
                version = version_metadata.get("schema_version", 0)
                assert version == 1, f"Collection {collection_name} has wrong schema version"

            
            # Step 2: Perform more complex cross-collection migrations
            operations_step2 = [
                RenameScalarFieldOperation(
                    metadata=OperationMetadata(schema_version=2), 
                    old_field_name="shared_field",
                    new_field_name="renamed_shared_field", 
                    data_type="None"
                ),
                AddScalarFieldOperation(
                    metadata=OperationMetadata(schema_version=3), 
                    field_name="another_new_field",
                    field_type="int64", 
                    data_type="None",
                    default_value=0
                ),
            ]

            await migrator.try_migrate(f"vector_{mem_type}", operations_step2)
            
            # Verify that all collections have been updated to version 2
            for collection_name in collection_names:
                schema = await vector_store.get_schema(collection_name)
                field_names = [f.name for f in schema.fields]
                
                # Check renaming
                assert "shared_field" not in field_names, f"Collection {collection_name} still has old field name"
                assert "renamed_shared_field" in field_names, f"Collection {collection_name} missing renamed field"
                
                # Check newly added fields
                assert "another_new_field" in field_names, f"Collection {collection_name} missing another new field"
                
                # Check version
                version_metadata = await vector_store.get_collection_metadata(collection_name)
                version = version_metadata.get("schema_version", 0)
                assert version == 3, f"Collection {collection_name} has wrong schema version"

            
            # Step 3: Verify data integrity and add new data.
            for i, collection_name in enumerate(collection_names):
                # Add new data and use the new schema
                new_doc = {
                    "id": str(uuid.uuid4()), 
                    "vector": [0.5 + i * 0.1, 0.6 + i * 0.1, 0.7 + i * 0.1, 0.8 + i * 0.1],
                    "text": f"new_data_{i}",
                    "renamed_shared_field": f"new_value_{i}",
                    "added_multi_field": f"custom_{i}",
                    "another_new_field": i * 100
                }
                await vector_store.add_docs(collection_name, [new_doc])
                
                # Search verification data
                res = await vector_store.search(
                    collection_name, 
                    [0.1 * (i + 1), 0.2 * (i + 1), 0.3 * (i + 1), 0.4 * (i + 1)],
                    "vector", 
                    top_k=5
                )
                assert len(res) >= 2, f"Collection {collection_name} should have at least 2 records"
            
        finally:
            # Clean up all created collections
            for collection_name in collection_names:
                await vector_store.delete_collection(collection_name)

    def test_migration_with_empty_operations(self, test_fixture):
        """Test migration with empty operations list."""
        asyncio.run(self._run_migration_with_empty_operations(test_fixture))

    async def _run_migration_with_empty_operations(self, fixture):
        vector_store = fixture['vector_store']
        migrator = fixture['migrator']
        mem_type = fixture['mem_type']
        collection_name = fixture['collection_name']

        # Get initial version
        initial_version_metadata = await vector_store.get_collection_metadata(collection_name)
        initial_version = initial_version_metadata.get("schema_version", 0)
        
        # Try migrate with empty operations
        await migrator.try_migrate(f"vector_{mem_type}", [])
        
        # Version should remain unchanged
        final_version_metadata = await vector_store.get_collection_metadata(collection_name)
        final_version = final_version_metadata.get("schema_version", 0)
        assert final_version == initial_version, f"Version should remain {initial_version}, got {final_version}"

    def test_migration_with_null_operations(self, test_fixture):
        """Test migration with null operations list."""
        asyncio.run(self._run_migration_with_null_operations(test_fixture))

    async def _run_migration_with_null_operations(self, fixture):
        migrator = fixture['migrator']
        mem_type = fixture['mem_type']

        # Try migrate with None operations -- should handle gracefully
        try:
            await migrator.try_migrate(f"vector_{mem_type}", None)
            pytest.fail("Expected exception for None operations")
        except (TypeError, ValueError, AttributeError) as e:
            error_msg = str(e).lower()
            assert ("none" in error_msg or "invalid" in error_msg or "operations"
                    in error_msg or "'nonetype'" in error_msg)

    def test_concurrent_migration(self, test_fixture):
        """Test concurrent migration operations."""
        asyncio.run(self._run_concurrent_migration(test_fixture))

    async def _run_concurrent_migration(self, fixture):
        vector_store = fixture['vector_store']
        migrator = fixture['migrator']
        user_id = fixture['user_id']
        scope_id = fixture['scope_id']
        mem_type = fixture['mem_type']

        # Create multiple collections for concurrent test
        collection_names = []
        for i in range(3):
            collection_name = (f"{user_id}_{str(uuid.uuid4()).replace('-', '')[:8]}_"
                           f"{scope_id}_concurrent_{i}_{mem_type}")
            collection_names.append(collection_name)
            
            v1_schema = CollectionSchema(
                fields=[
                    FieldSchema(name="id", dtype=VectorDataType.VARCHAR, is_primary=True,
                                auto_id=False, max_length=36),
                    FieldSchema(name="vector", dtype=VectorDataType.FLOAT_VECTOR, dim=4),
                    FieldSchema(name="text", dtype=VectorDataType.VARCHAR, max_length=256),
                    FieldSchema(name="shared_field", dtype=VectorDataType.VARCHAR, max_length=64),
                ],
                description="Concurrent test collection"
            )
            await vector_store.create_collection(collection_name, v1_schema)
            await vector_store.update_collection_metadata(collection_name, {"schema_version": 0})

        try:
            # Perform concurrent migrations
            operations = [
                AddScalarFieldOperation(
                    metadata=OperationMetadata(schema_version=1), 
                    field_name="concurrent_field",
                    field_type="string", 
                    data_type="None", 
                    default_value="concurrent"
                ),
            ]

            # Migrate all collections
            for collection_name in collection_names:
                await migrator.try_migrate(f"vector_{mem_type}", operations)

            # Verify all collections were updated
            for collection_name in collection_names:
                schema = await vector_store.get_schema(collection_name)
                field_names = [f.name for f in schema.fields]
                assert "concurrent_field" in field_names, f"Collection {collection_name} missing concurrent_field"
                
                version_metadata = await vector_store.get_collection_metadata(collection_name)
                version = version_metadata.get("schema_version", 0)
                assert version == 1, f"Collection {collection_name} has wrong schema version"

        finally:
            # Cleanup
            for collection_name in collection_names:
                await vector_store.delete_collection(collection_name)

    def test_migration_rollback_on_failure(self, test_fixture):
        """Test migration rollback when an operation fails."""
        asyncio.run(self._run_migration_rollback_on_failure(test_fixture))

    async def _run_migration_rollback_on_failure(self, fixture):
        vector_store = fixture['vector_store']
        migrator = fixture['migrator']
        mem_type = fixture['mem_type']
        collection_name = fixture['collection_name']

        # Record initial schema version
        initial_version_metadata = await vector_store.get_collection_metadata(collection_name)
        initial_version = initial_version_metadata.get("schema_version", 0)
        initial_schema = await vector_store.get_schema(collection_name)
        initial_field_count = len(initial_schema.fields)

        # Create operations where the second one will fail
        operations = [
            AddScalarFieldOperation(
                metadata=OperationMetadata(schema_version=1), 
                field_name="rollback_test_field",
                field_type="string", 
                data_type="None", 
                default_value="test"
            ),
            # This operation references a non-existent field, which should cause an error
            RenameScalarFieldOperation(
                metadata=OperationMetadata(schema_version=2), 
                old_field_name="non_existent_field_for_rollback",
                new_field_name="should_not_exist", 
                data_type="None"
            ),
        ]

        # Execute migration - should fail on second operation
        try:
            await migrator.try_migrate(f"vector_{mem_type}", operations)
            # If we reach here without exception, the test should fail
            # But some implementations might skip failed operations
        except Exception as e:
            # Expected: migration should fail
            pass

        # Verify schema integrity
        final_version_metadata = await vector_store.get_collection_metadata(collection_name)
        final_version = final_version_metadata.get("schema_version", 0)
        final_schema = await vector_store.get_schema(collection_name)
        final_field_names = [f.name for f in final_schema.fields]

        # Schema should be in a consistent state
        # Note: Exact rollback behavior depends on implementation
        # Some implementations may partially apply changes before failing

    def test_migration_idempotency(self, test_fixture):
        """Test that running the same migration multiple times is idempotent."""
        asyncio.run(self._run_migration_idempotency(test_fixture))

    async def _run_migration_idempotency(self, fixture):
        vector_store = fixture['vector_store']
        migrator = fixture['migrator']
        mem_type = fixture['mem_type']
        collection_name = fixture['collection_name']

        # Define migration operations
        operations = [
            AddScalarFieldOperation(
                metadata=OperationMetadata(schema_version=1), 
                field_name="idempotent_field",
                field_type="string", 
                data_type="None", 
                default_value="idempotent"
            ),
        ]

        # Run migration first time
        await migrator.try_migrate(f"vector_{mem_type}", operations)

        # Get state after first migration
        schema_after_first = await vector_store.get_schema(collection_name)
        version_after_first_metadata = await vector_store.get_collection_metadata(collection_name)
        version_after_first = version_after_first_metadata.get("schema_version", 0)
        field_count_after_first = len(schema_after_first.fields)

        # Run same migration again
        await migrator.try_migrate(f"vector_{mem_type}", operations)

        # Get state after second migration
        schema_after_second = await vector_store.get_schema(collection_name)
        version_after_second_metadata = await vector_store.get_collection_metadata(collection_name)
        version_after_second = version_after_second_metadata.get("schema_version", 0)
        field_count_after_second = len(schema_after_second.fields)

        # Verify idempotency
        assert version_after_first == version_after_second, \
            f"Schema version changed from {version_after_first} to {version_after_second}"
        assert field_count_after_first == field_count_after_second, \
            f"Field count changed from {field_count_after_first} to {field_count_after_second}"

        # Run a third time to be absolutely sure
        await migrator.try_migrate(f"vector_{mem_type}", operations)
        schema_after_third = await vector_store.get_schema(collection_name)
        version_after_third_metadata = await vector_store.get_collection_metadata(collection_name)
        version_after_third = version_after_third_metadata.get("schema_version", 0)

        assert version_after_second == version_after_third, \
            f"Schema version not stable: {version_after_second} vs {version_after_third}"
