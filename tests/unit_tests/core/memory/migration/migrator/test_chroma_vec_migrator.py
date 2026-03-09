# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import shutil
import tempfile
import logging
from typing import Dict, List

import pytest
import pytest_asyncio
from openjiuwen.core.foundation.store.base_vector_store import (
    CollectionSchema, FieldSchema, VectorDataType
)
from openjiuwen.core.foundation.store.vector.chroma_vector_store import ChromaVectorStore
from openjiuwen.core.memory.migration.migrator.vector_migrator import VectorMigrator
from openjiuwen.core.memory.migration.operation.operations import (
    AddScalarFieldOperation,
    RenameScalarFieldOperation,
    UpdateScalarFieldTypeOperation,
    UpdateEmbeddingDimensionOperation,
)
from openjiuwen.core.memory.migration.operation.base_operation import OperationMetadata


@pytest_asyncio.fixture(name="test_fixture")
async def chroma_test_fixture():
    """Set up test environment before each test."""
    # Create a temporary directory for ChromaDB storage
    temp_dir = tempfile.mkdtemp()
    
    # Create test collections with different memory types
    collection_names = {
        "summary": "user1_scope1_summary",
        "user_profile": "user2_scope2_user_profile"
    }

    # Create initial schema
    initial_schema = CollectionSchema.from_fields([
        FieldSchema(
            name="id",
            dtype=VectorDataType.VARCHAR,
            max_length=256,
            is_primary=True,
        ),
        FieldSchema(
            name="embedding",
            dtype=VectorDataType.FLOAT_VECTOR,
            dim=4,
        ),
        FieldSchema(
            name="text",
            dtype=VectorDataType.VARCHAR,
            max_length=65535,
        ),
        FieldSchema(
            name="count",
            dtype=VectorDataType.INT32,
        ),
    ])

    # Create initial test data
    initial_docs = [
        {
            "id": "doc_1",
            "embedding": [0.1, 0.2, 0.3, 0.4],
            "text": "First document",
            "count": 1,
        },
        {
            "id": "doc_2",
            "embedding": [0.5, 0.6, 0.7, 0.8],
            "text": "Second document",
            "count": 2,
        },
    ]
    
    async def _setup_test_collections() -> ChromaVectorStore:
        """Set up test collections with initial data."""
        store = ChromaVectorStore(persist_directory=temp_dir)

        # Create collections
        for collection_name in collection_names.values():
            await store.create_collection(collection_name, initial_schema)
            await store.add_docs(collection_name, initial_docs)

        return store
    
    async def _get_all_documents(store: ChromaVectorStore, collection_name: str) -> List[Dict[str, any]]:
        """Get all documents from the collection for verification."""
        # This is a helper to access the internal method for testing
        return await store.get_all_documents(collection_name)
    
    yield {
        "temp_dir": temp_dir,
        "collection_names": collection_names,
        "initial_schema": initial_schema,
        "initial_docs": initial_docs,
        "setup_test_collections": _setup_test_collections,
        "get_all_documents": _get_all_documents
    }
    try:
        # Clean up after test
        shutil.rmtree(temp_dir)
    except Exception as e:
        # Log the exception but don't fail the test
        logging.info(f"Failed to clean up temporary directory {temp_dir}: {e}")


@pytest.mark.asyncio
async def test_try_migrate_same_version_multiple_operations(test_fixture):
    """Test migrating with multiple operations at the same schema version."""
    # Setup
    env = test_fixture
    store = await env["setup_test_collections"]()
    migrator = VectorMigrator(store)

    # Create multiple operations with the same version
    operations = [
        # Version 2: Add category field
        AddScalarFieldOperation(
            metadata=OperationMetadata(schema_version=2),
            data_type="vector_summary",
            field_name="category",
            field_type="varchar",
            default_value="general"
        ),
        # Version 2: Add author field
        AddScalarFieldOperation(
            metadata=OperationMetadata(schema_version=2),
            data_type="vector_summary",
            field_name="author",
            field_type="varchar",
            default_value="unknown"
        ),
        # Version 2: Rename count to view_count
        RenameScalarFieldOperation(
            metadata=OperationMetadata(schema_version=2),
            data_type="vector_summary",
            old_field_name="count",
            new_field_name="view_count"
        )
    ]

    # First, update to version 1
    version1_op = AddScalarFieldOperation(
        metadata=OperationMetadata(schema_version=1),
        data_type="vector_summary",
        field_name="version1_field",
        field_type="varchar",
        default_value="v1"
    )
    await migrator.try_migrate("vector_summary", [version1_op])

    # Verify we're at version 1
    summary_collection = env["collection_names"]["summary"]
    user_profile_collection = env["collection_names"]["user_profile"]
    assert (await store.get_collection_metadata(summary_collection))["schema_version"] == 1
    assert (await store.get_collection_metadata(user_profile_collection))["schema_version"] == 0

    # Execute migration with multiple version 2 operations
    await migrator.try_migrate("vector_summary", operations)

    # Verify all operations were applied
    schema = await store.get_schema(summary_collection)
    assert schema.has_field("category")
    assert schema.has_field("author")
    assert schema.has_field("view_count")
    assert not schema.has_field("count")

    # Verify all fields were added to documents
    docs = await env["get_all_documents"](store, summary_collection)
    for doc in docs:
        assert doc["category"] == "general"
        assert doc["author"] == "unknown"
        assert "view_count" in doc
        assert "count" not in doc

    # Verify schema version was only updated to 2 once
    assert (await store.get_collection_metadata(summary_collection))["schema_version"] == 2


@pytest.mark.asyncio
async def test_try_migrate_multi_version_multi_operations(test_fixture):
    """Test migrating with multiple versions, each having multiple operations."""
    # Setup
    env = test_fixture
    store = await env["setup_test_collections"]()
    migrator = VectorMigrator(store)
    summary_collection = env["collection_names"]["summary"]

    # Define operations for multiple versions
    operations = []

    # Version 1: Add two fields
    operations.append(AddScalarFieldOperation(
        metadata=OperationMetadata(schema_version=1),
        data_type="vector_summary",
        field_name="version1_field1",
        field_type="varchar",
        default_value="v1_f1"
    ))
    operations.append(AddScalarFieldOperation(
        metadata=OperationMetadata(schema_version=1),
        data_type="vector_summary",
        field_name="version1_field2",
        field_type="int32",
        default_value=1
    ))

    # Version 2: Rename a field, add a field, update a field type
    operations.append(RenameScalarFieldOperation(
        metadata=OperationMetadata(schema_version=2),
        data_type="vector_summary",
        old_field_name="count",
        new_field_name="view_count"
    ))
    operations.append(AddScalarFieldOperation(
        metadata=OperationMetadata(schema_version=2),
        data_type="vector_summary",
        field_name="version2_field",
        field_type="double",
        default_value=2.0
    ))
    operations.append(UpdateScalarFieldTypeOperation(
        metadata=OperationMetadata(schema_version=2),
        data_type="vector_summary",
        field_name="version1_field2",
        new_field_type="int64"
    ))

    # Version 3: Update vector dimension, add a field
    def expand_embedding(doc):
        old_embedding = doc["embedding"]
        return old_embedding + [0.1, 0.2]  # Expand from 4D to 6D
    
    operations.append(UpdateEmbeddingDimensionOperation(
        metadata=OperationMetadata(schema_version=3),
        data_type="vector_summary",
        field_name="embedding",
        new_dimension=6,
        recompute_embedding_func=expand_embedding
    ))
    operations.append(AddScalarFieldOperation(
        metadata=OperationMetadata(schema_version=3),
        data_type="vector_summary",
        field_name="version3_field",
        field_type="bool",
        default_value=True
    ))

    # Execute all operations at once
    await migrator.try_migrate("vector_summary", operations)

    # Verify final schema version is 3
    assert (await store.get_collection_metadata(summary_collection))["schema_version"] == 3

    # Verify all operations were applied correctly
    updated_schema = await store.get_schema(summary_collection)
    
    # Check version 1 operations
    assert updated_schema.has_field("version1_field1")
    assert updated_schema.has_field("version1_field2")
    
    # Check version 2 operations
    assert updated_schema.has_field("view_count")
    assert not updated_schema.has_field("count")
    assert updated_schema.has_field("version2_field")
    version1_field2 = next(f for f in updated_schema.fields if f.name == "version1_field2")
    assert version1_field2.dtype == VectorDataType.INT64
    
    # Check version 3 operations
    vector_field = next(f for f in updated_schema.fields if f.name == "embedding")
    assert vector_field.dim == 6
    assert updated_schema.has_field("version3_field")

    # Verify all fields are present in documents
    docs = await env["get_all_documents"](store, summary_collection)
    for doc in docs:
        # Check version 1 fields
        assert doc["version1_field1"] == "v1_f1"
        assert isinstance(doc["version1_field2"], int)
        
        # Check version 2 fields
        assert "view_count" in doc
        assert "count" not in doc
        assert doc["version2_field"] == 2.0
        
        # Check version 3 fields
        assert len(doc["embedding"]) == 6
        assert doc["version3_field"] is True


@pytest.mark.asyncio
async def test_try_migrate_update_field_type_normal(test_fixture):
    """Test normal field type update operation."""
    # Setup
    env = test_fixture
    store = await env["setup_test_collections"]()
    migrator = VectorMigrator(store)

    # Create update field type operation
    update_field_type_op = UpdateScalarFieldTypeOperation(
        metadata=OperationMetadata(schema_version=1),
        data_type="vector_summary",
        field_name="count",
        new_field_type="double"
    )

    # Execute migration
    await migrator.try_migrate("vector_summary", [update_field_type_op])

    # Verify the migration was successful
    updated_schema = await store.get_schema(env["collection_names"]["summary"])
    count_field = next(f for f in updated_schema.fields if f.name == "count")
    assert count_field.dtype == VectorDataType.DOUBLE


@pytest.mark.asyncio
async def test_try_migrate_update_nonexistent_field_type(test_fixture):
    """Test that updating type of a nonexistent field raises appropriate error."""
    # Setup
    env = test_fixture
    store = await env["setup_test_collections"]()
    migrator = VectorMigrator(store)

    # Create update field type operation for a nonexistent field
    update_nonexistent_op = UpdateScalarFieldTypeOperation(
        metadata=OperationMetadata(schema_version=1),
        data_type="vector_summary",
        field_name="nonexistent_field",
        new_field_type="float64"
    )

    # Execute migration and expect error
    with pytest.raises(Exception) as excinfo:
        await migrator.try_migrate("vector_summary", [update_nonexistent_op])

    # Verify the error message contains relevant information
    assert "does not exist" in str(excinfo.value).lower()


@pytest.mark.asyncio
async def test_try_migrate_update_vector_field_type(test_fixture):
    """Test that updating type of a vector field raises appropriate error."""
    # Setup
    env = test_fixture
    store = await env["setup_test_collections"]()
    migrator = VectorMigrator(store)

    # Create update field type operation for a vector field
    update_vector_field_op = UpdateScalarFieldTypeOperation(
        metadata=OperationMetadata(schema_version=1),
        data_type="vector_summary",
        field_name="embedding",
        new_field_type="float64"
    )

    # Execute migration and expect error
    with pytest.raises(Exception) as excinfo:
        await migrator.try_migrate("vector_summary", [update_vector_field_op])

    # Verify the error message contains relevant information
    assert "cannot update type of vector field" in str(excinfo.value).lower()


@pytest.mark.asyncio
async def test_try_migrate_with_exception_during_migration(test_fixture):
    """Test that migration handles exceptions gracefully."""
    # Setup
    env = test_fixture
    store = await env["setup_test_collections"]()
    migrator = VectorMigrator(store)

    # Define a function that raises an exception during embedding recomputation
    def failing_recompute_embedding(doc):
        raise ValueError("Intentional failure during embedding recomputation")

    # Create update dimension operation that will fail
    update_dim_op = UpdateEmbeddingDimensionOperation(
        metadata=OperationMetadata(schema_version=1),
        data_type="vector_summary",
        field_name="embedding",
        new_dimension=8,
        recompute_embedding_func=failing_recompute_embedding
    )

    summary_collection = env["collection_names"]["summary"]
    
    # Verify initial state
    initial_docs = await env["get_all_documents"](store, summary_collection)
    initial_schema = await store.get_schema(summary_collection)
    initial_version = (await store.get_collection_metadata(summary_collection))["schema_version"]

    # Execute migration and expect error
    with pytest.raises(ValueError, match="Intentional failure during embedding recomputation"):
        await migrator.try_migrate("vector_summary", [update_dim_op])

    # Verify original collection remains unchanged
    final_docs = await env["get_all_documents"](store, summary_collection)
    final_schema = await store.get_schema(summary_collection)
    final_version = (await store.get_collection_metadata(summary_collection))["schema_version"]

    # Verify schema is unchanged
    assert final_schema.to_dict() == initial_schema.to_dict()
    assert final_version == initial_version

    # Verify data is unchanged
    assert len(final_docs) == len(initial_docs)
    for final_doc, initial_doc in zip(final_docs, initial_docs):
        assert final_doc == initial_doc

    # Verify no temporary collections remain
    collections = await store.list_collection_names()
    for collection in collections:
        assert not collection.startswith(f"{summary_collection}_migration_")


@pytest.mark.asyncio
async def test_try_migrate_add_existing_field(test_fixture):
    """Test that adding an existing field raises appropriate error."""
    # Setup
    env = test_fixture
    store = await env["setup_test_collections"]()
    migrator = VectorMigrator(store)

    # Create add field operation for a field that already exists
    add_existing_field_op = AddScalarFieldOperation(
        metadata=OperationMetadata(schema_version=1),
        data_type="vector_summary",
        field_name="count",  # This field already exists in the initial schema
        field_type="int32",
        default_value=0
    )

    # Execute migration and expect error
    with pytest.raises(Exception) as excinfo:
        await migrator.try_migrate("vector_summary", [add_existing_field_op])
    # Verify the error message contains relevant information
    assert "already exists" in str(excinfo.value).lower() or "duplicate" in str(excinfo.value).lower()


@pytest.mark.asyncio
async def test_try_migrate_rename_field_normal(test_fixture):
    """Test normal field rename operation."""
    # Setup
    env = test_fixture
    store = await env["setup_test_collections"]()
    migrator = VectorMigrator(store)

    # Create rename field operation to a new field name
    rename_op = RenameScalarFieldOperation(
        metadata=OperationMetadata(schema_version=1),
        data_type="vector_summary",
        old_field_name="count",
        new_field_name="view_count"  # This field doesn't exist yet
    )

    # Execute migration
    await migrator.try_migrate("vector_summary", [rename_op])

    # Verify the operation was applied correctly
    schema = await store.get_schema(env["collection_names"]["summary"])
    assert not schema.has_field("count")
    assert schema.has_field("view_count")

    # Verify the field was renamed in all documents
    docs = await env["get_all_documents"](store, env["collection_names"]["summary"])
    for i, doc in enumerate(docs):
        assert "count" not in doc
        assert doc["view_count"] == i + 1


@pytest.mark.asyncio
async def test_try_migrate_rename_nonexistent_field(test_fixture):
    """Test that renaming a nonexistent field raises appropriate error."""
    # Setup
    env = test_fixture
    store = await env["setup_test_collections"]()
    migrator = VectorMigrator(store)

    # Create rename field operation for a nonexistent field
    rename_nonexistent_op = RenameScalarFieldOperation(
        metadata=OperationMetadata(schema_version=1),
        data_type="vector_summary",
        old_field_name="nonexistent_field",
        new_field_name="new_field"
    )

    # Execute migration and expect error
    with pytest.raises(Exception) as excinfo:
        await migrator.try_migrate("vector_summary", [rename_nonexistent_op])

    # Verify the error message contains relevant information
    assert "does not exist" in str(excinfo.value).lower()


@pytest.mark.asyncio
async def test_try_migrate_rename_to_existing_field(test_fixture):
    """Test that renaming to an existing field raises appropriate error."""
    # Setup
    env = test_fixture
    store = await env["setup_test_collections"]()
    migrator = VectorMigrator(store)

    # Create rename field operation to an existing field name
    rename_to_existing_op = RenameScalarFieldOperation(
        metadata=OperationMetadata(schema_version=1),
        data_type="vector_summary",
        old_field_name="count",
        new_field_name="text"  # This field already exists
    )

    # Execute migration and expect error
    with pytest.raises(Exception) as excinfo:
        await migrator.try_migrate("vector_summary", [rename_to_existing_op])

    # Verify the error message contains relevant information
    assert "already exists" in str(excinfo.value).lower()


@pytest.mark.asyncio
async def test_try_migrate_update_embedding_dimension_expansion(test_fixture):
    """Test expanding embedding dimension with recompute function."""
    # Setup
    env = test_fixture
    store = await env["setup_test_collections"]()
    migrator = VectorMigrator(store)

    # Define a function that expands embedding from 4D to 8D
    def expand_embedding(doc):
        old_embedding = doc["embedding"]
        # Expand from 4D to 8D by duplicating values
        return old_embedding + old_embedding

    # Create update dimension operation
    update_dim_op = UpdateEmbeddingDimensionOperation(
        metadata=OperationMetadata(schema_version=1),
        data_type="vector_summary",
        field_name="embedding",
        new_dimension=8,
        recompute_embedding_func=expand_embedding
    )

    # Execute migration
    await migrator.try_migrate("vector_summary", [update_dim_op])

    # Verify the migration was successful
    updated_schema = await store.get_schema(env["collection_names"]["summary"])
    vector_field = next(f for f in updated_schema.fields if f.name == "embedding")
    assert vector_field.dim == 8

    # Verify all documents have 8D embeddings
    docs = await env["get_all_documents"](store, env["collection_names"]["summary"])
    for doc in docs:
        assert len(doc["embedding"]) == 8
        # Verify the expanded embedding is correct (original values duplicated)
        old_embedding = doc["embedding"][:4]
        new_embedding = doc["embedding"]
        assert new_embedding == old_embedding + old_embedding


@pytest.mark.asyncio
async def test_try_migrate_update_embedding_dimension_reduction(test_fixture):
    """Test reducing embedding dimension with recompute function."""
    # Setup
    env = test_fixture
    store = await env["setup_test_collections"]()
    migrator = VectorMigrator(store)

    # Define a function that reduces embedding from 4D to 2D
    def reduce_embedding(doc):
        old_embedding = doc["embedding"]
        # Reduce from 4D to 2D by taking first 2 values
        return old_embedding[:2]

    # Create update dimension operation
    update_dim_op = UpdateEmbeddingDimensionOperation(
        metadata=OperationMetadata(schema_version=1),
        data_type="vector_summary",
        field_name="embedding",
        new_dimension=2,
        recompute_embedding_func=reduce_embedding
    )

    # Execute migration
    await migrator.try_migrate("vector_summary", [update_dim_op])

    # Verify the migration was successful
    updated_schema = await store.get_schema(env["collection_names"]["summary"])
    vector_field = next(f for f in updated_schema.fields if f.name == "embedding")
    assert vector_field.dim == 2

    # Verify all documents have 2D embeddings
    docs = await env["get_all_documents"](store, env["collection_names"]["summary"])
    for doc in docs:
        assert len(doc["embedding"]) == 2
        # Verify the reduced embedding is correct (first 2 values)
        assert doc["embedding"] == doc["embedding"][:2]


@pytest.mark.asyncio
async def test_try_migrate_update_embedding_dimension_zero_padding(test_fixture):
    """Test updating embedding dimension without recompute function (uses zero padding)."""
    # Setup
    env = test_fixture
    store = await env["setup_test_collections"]()
    migrator = VectorMigrator(store)

    # Create update dimension operation without recompute function
    update_dim_op = UpdateEmbeddingDimensionOperation(
        metadata=OperationMetadata(schema_version=1),
        data_type="vector_summary",
        field_name="embedding",
        new_dimension=6
    )

    # Execute migration
    await migrator.try_migrate("vector_summary", [update_dim_op])

    # Verify the migration was successful
    updated_schema = await store.get_schema(env["collection_names"]["summary"])
    vector_field = next(f for f in updated_schema.fields if f.name == "embedding")
    assert vector_field.dim == 6

    # Verify all documents have 6D embeddings with zero padding
    docs = await env["get_all_documents"](store, env["collection_names"]["summary"])
    for doc in docs:
        assert len(doc["embedding"]) == 6
        assert doc["embedding"] == [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


@pytest.mark.asyncio
async def test_try_migrate_update_embedding_dimension_wrong_size(test_fixture):
    """Test that updating embedding dimension with wrong size from recompute function raises error."""
    # Setup
    env = test_fixture
    store = await env["setup_test_collections"]()
    migrator = VectorMigrator(store)

    # Define a function that returns wrong dimension embedding
    def wrong_dimension_embedding(doc):
        old_embedding = doc["embedding"]
        # Return 6D instead of requested 8D
        return old_embedding + [0.5, 0.6]

    # Create update dimension operation
    update_dim_op = UpdateEmbeddingDimensionOperation(
        metadata=OperationMetadata(schema_version=1),
        data_type="vector_summary",
        field_name="embedding",
        new_dimension=8,  # Request 8D
        recompute_embedding_func=wrong_dimension_embedding  # But return 6D
    )

    # Execute migration and expect error
    with pytest.raises(Exception) as excinfo:
        await migrator.try_migrate("vector_summary", [update_dim_op])

    # Verify the error message contains relevant information
    assert "vector length" in str(excinfo.value).lower()
    assert "does not match" in str(excinfo.value).lower()