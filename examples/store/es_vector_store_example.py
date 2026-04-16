# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
Example script demonstrating ElasticsearchVectorStore usage with local Elasticsearch.

This script requires:
1. A running Elasticsearch instance (default: http://localhost:9200)
2. The elasticsearch Python package installed

To run this example:
    python examples/es_vector_store_example.py

Or run with pytest to execute all tests:
    pytest tests/unit_tests/extensions/store/vector/test_es_vector_store.py -v
"""

import asyncio
import sys
from typing import List

from elasticsearch import AsyncElasticsearch

from openjiuwen.core.foundation.store.base_vector_store import (
    CollectionSchema,
    VectorDataType,
    FieldSchema,
    VectorSearchResult,
)
from openjiuwen.extensions.store.vector.es_vector_store import ElasticsearchVectorStore


async def main():
    """Main function demonstrating ElasticsearchVectorStore usage."""

    print("=" * 60)
    print("Elasticsearch Vector Store Example")
    print("=" * 60)
    print()

    # Connect to local Elasticsearch
    es_url = "http://localhost:9200"
    print(f"Connecting to Elasticsearch at {es_url}...")

    try:
        es = AsyncElasticsearch(es_url, verify_certs=False, request_timeout=30)
        # Test connection
        info = await es.info()
        print(f"[OK] Connected to Elasticsearch version: {info['version']['number']}")
        print()
    except Exception as e:
        print(f"[X] Failed to connect to Elasticsearch: {e}")
        print()
        print("Please make sure Elasticsearch is running at http://localhost:9200")
        print("You can start Elasticsearch using Docker:")
        print("  docker run -d -p 9200:9200 -p 9300:9300 \\")
        print("    -e 'discovery.type=single-node' \\")
        print("    -e 'xpack.security.enabled=false' \\")
        print("    docker.elastic.co/elasticsearch/elasticsearch:8.11.0")
        sys.exit(1)

    # Create vector store instance
    store = ElasticsearchVectorStore(es=es, index_prefix="example_vector")

    # Clean up any existing test collection
    collection_name = "test_documents"
    print(f"Cleaning up existing collection '{collection_name}' (if any)...")
    if await store.collection_exists(collection_name):
        await store.delete_collection(collection_name)
        print(f"[OK] Deleted existing collection")
    print()

    # Example 1: Create a collection
    print("-" * 60)
    print("Example 1: Create a collection")
    print("-" * 60)

    schema = CollectionSchema(description="Test document collection")
    schema.add_field(FieldSchema(name="id", dtype=VectorDataType.VARCHAR, is_primary=True))
    schema.add_field(FieldSchema(name="embedding", dtype=VectorDataType.FLOAT_VECTOR, dim=768))
    schema.add_field(FieldSchema(name="title", dtype=VectorDataType.VARCHAR))
    schema.add_field(FieldSchema(name="content", dtype=VectorDataType.VARCHAR))
    schema.add_field(FieldSchema(name="category", dtype=VectorDataType.VARCHAR))
    schema.add_field(FieldSchema(name="views", dtype=VectorDataType.INT64))

    await store.create_collection(collection_name, schema, distance_metric="COSINE")
    print(f"[OK] Created collection '{collection_name}'")
    print()

    # Example 2: Add documents
    print("-" * 60)
    print("Example 2: Add documents")
    print("-" * 60)

    documents = [
        {
            "id": "doc1",
            "embedding": [0.1] * 768,
            "title": "Introduction to Machine Learning",
            "content": "Machine learning is a subset of artificial intelligence...",
            "category": "AI",
            "views": 1000,
        },
        {
            "id": "doc2",
            "embedding": [0.2] * 768,
            "title": "Deep Learning Basics",
            "content": "Deep learning uses neural networks with multiple layers...",
            "category": "AI",
            "views": 800,
        },
        {
            "id": "doc3",
            "embedding": [0.3] * 768,
            "title": "Natural Language Processing",
            "content": "NLP deals with the interaction between computers and human language...",
            "category": "NLP",
            "views": 1200,
        },
    ]

    await store.add_docs(collection_name, documents)
    print(f"[OK] Added {len(documents)} documents to collection")
    print()

    # Example 3: Search documents
    print("-" * 60)
    print("Example 3: Search documents")
    print("-" * 60)

    query_vector = [0.15] * 768
    results = await store.search(
        collection_name,
        query_vector=query_vector,
        vector_field="embedding",
        top_k=3,
    )

    print(f"Found {len(results)} results:")
    for i, result in enumerate(results, 1):
        print(f"  {i}. ID: {result.fields.get('id')}, Score: {result.score:.4f}")
        print(f"     Title: {result.fields.get('title')}")
        print(f"     Category: {result.fields.get('category')}")
    print()

    # Example 4: Search with filters
    print("-" * 60)
    print("Example 4: Search with filters")
    print("-" * 60)

    results_filtered = await store.search(
        collection_name,
        query_vector=query_vector,
        vector_field="embedding",
        top_k=10,
        filters={"category": "AI"},
    )

    print(f"Found {len(results_filtered)} results with category='AI':")
    for i, result in enumerate(results_filtered, 1):
        print(f"  {i}. ID: {result.fields.get('id')}, Score: {result.score:.4f}")
    print()

    # Example 5: Get schema
    print("-" * 60)
    print("Example 5: Get collection schema")
    print("-" * 60)

    retrieved_schema = await store.get_schema(collection_name)
    print(f"Collection has {len(retrieved_schema.fields)} fields:")
    for field in retrieved_schema.fields:
        field_info = f"  - {field.name}: {field.dtype.value}"
        if field.dim:
            field_info += f" (dim={field.dim})"
        if field.is_primary:
            field_info += " [PRIMARY]"
        print(field_info)
    print()

    # Example 6: Get collection metadata
    print("-" * 60)
    print("Example 6: Get collection metadata")
    print("-" * 60)

    metadata = await store.get_collection_metadata(collection_name)
    print(f"Distance Metric: {metadata.get('distance_metric')}")
    print(f"Schema Version: {metadata.get('schema_version')}")
    print(f"Vector Field: {metadata.get('vector_field')}")
    print(f"Vector Dimension: {metadata.get('vector_dim')}")
    print()

    # Example 7: List all collections
    print("-" * 60)
    print("Example 7: List all collections")
    print("-" * 60)

    collections = await store.list_collection_names()
    print(f"Found {len(collections)} collection(s):")
    for coll_name in collections:
        print(f"  - {coll_name}")
    print()

    # Example 8: Delete documents by IDs
    print("-" * 60)
    print("Example 8: Delete documents by IDs")
    print("-" * 60)

    await store.delete_docs_by_ids(collection_name, ["doc3"])
    print("[OK] Deleted document with id='doc3'")
    print()

    # Verify deletion
    results_after_delete = await store.search(
        collection_name,
        query_vector=query_vector,
        vector_field="embedding",
        top_k=10,
    )
    print(f"[OK] Collection now has {len(results_after_delete)} documents")
    print()

    # Example 9: Delete documents by filters
    print("-" * 60)
    print("Example 9: Delete documents by filters")
    print("-" * 60)

    await store.delete_docs_by_filters(collection_name, {"category": "NLP"})
    print("[OK] Deleted documents with category='NLP'")
    print()

    # Example 10: Check collection exists
    print("-" * 60)
    print("Example 10: Check collection exists")
    print("-" * 60)

    exists = await store.collection_exists(collection_name)
    print(f"Collection '{collection_name}' exists: {exists}")
    print()

    # Example 11: Update collection metadata
    print("-" * 60)
    print("Example 11: Update collection metadata")
    print("-" * 60)

    await store.update_collection_metadata(collection_name, {"schema_version": 1})
    print("[OK] Updated schema_version to 1")

    updated_metadata = await store.get_collection_metadata(collection_name)
    print(f"Updated schema_version: {updated_metadata.get('schema_version')}")
    print()

    # Cleanup
    print("-" * 60)
    print("Cleanup")
    print("-" * 60)

    await store.delete_collection(collection_name)
    print(f"[OK] Deleted collection '{collection_name}'")
    print()

    # Close Elasticsearch connection
    await es.close()
    print("[OK] Closed Elasticsearch connection")
    print()

    print("=" * 60)
    print("All examples completed successfully!")
    print("=" * 60)


async def run_all_tests():
    """Run all unit tests for ElasticsearchVectorStore."""

    print("=" * 60)
    print("Running All Elasticsearch Vector Store Tests")
    print("=" * 60)
    print()

    # This would typically be run via pytest from command line
    # pytest tests/unit_tests/extensions/store/vector/test_es_vector_store.py -v

    print("To run all tests, execute:")
    print()
    print("  pytest tests/unit_tests/extensions/store/vector/test_es_vector_store.py -v")
    print()
    print("Or run with coverage:")
    print()
    print("  pytest tests/unit_tests/extensions/store/vector/test_es_vector_store.py -v --cov=openjiuwen.extensions.store.vector.es_vector_store")
    print()


if __name__ == "__main__":
    try:
        # Run the main example
        asyncio.run(main())

        # Show how to run tests
        print()
        asyncio.run(run_all_tests())

    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"\n\nError: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
