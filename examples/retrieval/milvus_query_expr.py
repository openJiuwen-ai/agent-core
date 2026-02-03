# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
Example script demonstrating QueryExpr usage with Milvus using MilvusClient directly
"""

import asyncio
from typing import Optional

from pymilvus import DataType, MilvusClient
from utils.output import write_output

from openjiuwen.core.foundation.store.query import (
    ArithmeticExpr,
    MatchExpr,
    QueryExpr,
    eq,
    gt,
    gte,
    in_list,
    is_not_null,
    is_null,
    json_key,
    lt,
    lte,
    ne,
)
from openjiuwen.core.retrieval.common.retrieval_result import SearchResult

# Collection configuration
MILVUS_URI = "http://localhost:19530"
DATABASE_NAME = "test_query_expr"
COLLECTION_NAME = "test_query_expr_collection"
EMBEDDING_DIM = 384


async def ensure_collection(client: MilvusClient, collection_name: str) -> None:
    """Create collection if it doesn't exist"""
    if client.has_collection(collection_name):
        write_output("Collection %s already exists", collection_name)
        return

    # Create schema
    schema = client.create_schema(
        auto_id=False,
        enable_dynamic_field=False,
    )

    # Add fields
    schema.add_field(field_name="id", datatype=DataType.VARCHAR, max_length=256, is_primary=True)
    schema.add_field(
        field_name="content", datatype=DataType.VARCHAR, max_length=65535, enable_analyzer=True, enable_match=True
    )
    schema.add_field(field_name="embedding", datatype=DataType.FLOAT_VECTOR, dim=EMBEDDING_DIM)
    schema.add_field(field_name="metadata", datatype=DataType.JSON)
    # Add top-level fields for easier filtering
    schema.add_field(field_name="category", datatype=DataType.VARCHAR, max_length=256)
    schema.add_field(field_name="author", datatype=DataType.VARCHAR, max_length=256)
    schema.add_field(field_name="score", datatype=DataType.INT64)
    schema.add_field(field_name="year", datatype=DataType.INT64)
    # Add optional field for null filtering
    schema.add_field(field_name="optional_field", datatype=DataType.JSON, nullable=True)

    # Prepare index parameters
    index_params = client.prepare_index_params()
    index_params.add_index(field_name="embedding", index_type="AUTOINDEX", metric_type="COSINE")
    index_params.add_index(field_name="category", index_type="INVERTED")
    index_params.add_index(field_name="author", index_type="INVERTED")
    index_params.add_index(field_name="score", index_type="STL_SORT")
    index_params.add_index(field_name="year", index_type="STL_SORT")

    # Create collection
    client.create_collection(
        collection_name=collection_name,
        schema=schema,
        index_params=index_params,
    )
    write_output("Created collection: %s", collection_name)


async def insert_data(client: MilvusClient, collection_name: str, data: list[dict], batch_size: int = 5) -> None:
    """Insert data into collection"""
    for i in range(0, len(data), batch_size):
        j = i + batch_size
        batch = data[i:j]
        # Prepare data in Milvus format - list of entities (each entity is a dict)
        insert_data_list = []
        for doc in batch:
            entity = {
                "id": doc["id"],
                "content": doc["content"],
                "embedding": doc["embedding"],
                "metadata": doc["metadata"],
                "category": doc["metadata"]["category"],
                "author": doc["metadata"]["author"],
                "score": doc["metadata"]["score"],
                "year": doc["metadata"]["year"],
                "optional_field": 1 if i % 2 else None,
            }
            insert_data_list.append(entity)
        await asyncio.to_thread(client.insert, collection_name=collection_name, data=insert_data_list)
    await asyncio.to_thread(client.flush, collection_name=collection_name)


async def search_vectors(
    client: MilvusClient,
    collection_name: str,
    query_vector: list[float],
    top_k: int = 5,
    filters: Optional[QueryExpr] = None,
) -> list[SearchResult]:
    """Search vectors with optional filters"""
    filter_expr = None
    if filters is not None:
        filter_expr = filters.to_expr("milvus")

    results = await asyncio.to_thread(
        client.search,
        collection_name=collection_name,
        data=[query_vector],
        anns_field="embedding",
        limit=top_k,
        output_fields=["id", "content", "metadata", "category", "author", "score", "year"],
        search_params={"metric_type": "COSINE", "params": {}},
        filter=filter_expr,
    )

    if not results or len(results) == 0:
        return []

    search_results = []
    for item in results[0]:
        result_id = str(item.get("id", ""))
        text = item.get("content", "")
        metadata = item.get("metadata", {})
        if isinstance(metadata, str):
            import json

            try:
                metadata = json.loads(metadata)
            except Exception:
                metadata = {}

        # Get score (distance)
        raw_score = item.get("distance", 0.0)
        # Convert cosine distance to similarity score (Milvus returns distance, we want similarity)
        score = (1.0 - float(raw_score) + 1.0) / 2.0 if raw_score is not None else 0.0

        search_result = SearchResult(
            id=result_id,
            text=text,
            score=score,
            metadata=metadata,
        )
        search_results.append(search_result)

    return search_results


async def create_test_data(client: MilvusClient, collection_name: str, num_docs: int = 10) -> None:
    """Create test data with various metadata fields"""
    write_output("\n%s", "=" * 70)
    write_output("Creating test data...")
    write_output("%s", "=" * 70)

    test_data = []
    categories = ["tech", "science", "business", "health"]
    authors = ["Alice", "Bob", "Charlie", "Diana"]

    for i in range(num_docs):
        doc_id = f"doc_{i + 1}"
        category = categories[i % len(categories)]
        author = authors[i % len(authors)]
        score = 50 + (i * 5)
        year = 2020 + (i % 5)

        embedding = [0.1 * (i + j) for j in range(EMBEDDING_DIM)]
        text = f"This is document {i + 1} about {category} written by {author} in {year} with score {score}."

        data = {
            "id": doc_id,
            "content": text,
            "embedding": embedding,
            "metadata": {
                "category": category,
                "author": author,
                "score": score,
                "year": year,
                "document_id": doc_id,
                "chunk_id": f"chunk_{i + 1}",
            },
        }
        test_data.append(data)

    await insert_data(client, collection_name, test_data, batch_size=5)
    write_output("Added %d documents to the collection", len(test_data))

    write_output("\nSample documents:")
    for i, doc in enumerate(test_data):
        write_output(
            "  %d. ID: %s, Category: %s, Author: %s, Score: %d",
            i + 1,
            doc["id"],
            doc["metadata"]["category"],
            doc["metadata"]["author"],
            doc["metadata"]["score"],
        )


async def test_comparison_operators(client: MilvusClient, collection_name: str) -> None:
    """Test comparison operators: eq, ne, gt, lt, gte, lte"""
    write_output("\n%s", "=" * 70)
    write_output("Test 1: Comparison Operators")
    write_output("%s", "=" * 70)

    query_vector = [0.1] * EMBEDDING_DIM

    write_output("\n[1.1] Testing eq() - Find documents where category == 'tech'")
    filter_expr = eq("category", "tech")
    results = await search_vectors(client, collection_name, query_vector, top_k=10, filters=filter_expr)
    write_output("  Found %d documents", len(results))
    for r in results[:3]:
        write_output(
            "    - ID: %s, Category: %s, Score: %.4f",
            r.id,
            r.metadata.get("category"),
            r.score,
        )

    write_output("\n[1.2] Testing ne() - Find documents where category != 'tech'")
    filter_expr = ne("category", "tech")
    results = await search_vectors(client, collection_name, query_vector, top_k=10, filters=filter_expr)
    write_output("  Found %d documents", len(results))
    for r in results[:3]:
        write_output(
            "    - ID: %s, Category: %s, Score: %.4f",
            r.id,
            r.metadata.get("category"),
            r.score,
        )

    write_output("\n[1.3] Testing gt() - Find documents where score > 70")
    filter_expr = gt("score", 70)
    results = await search_vectors(client, collection_name, query_vector, top_k=10, filters=filter_expr)
    write_output("  Found %d documents", len(results))
    for r in results[:3]:
        write_output(
            "    - ID: %s, Score: %s, Score: %.4f",
            r.id,
            r.metadata.get("score"),
            r.score,
        )

    write_output("\n[1.4] Testing lt() - Find documents where score < 70")
    filter_expr = lt("score", 70)
    results = await search_vectors(client, collection_name, query_vector, top_k=10, filters=filter_expr)
    write_output("  Found %d documents", len(results))
    for r in results[:3]:
        write_output(
            "    - ID: %s, Score: %s, Score: %.4f",
            r.id,
            r.metadata.get("score"),
            r.score,
        )

    write_output("\n[1.5] Testing gte() - Find documents where score >= 80")
    filter_expr = gte("score", 80)
    results = await search_vectors(client, collection_name, query_vector, top_k=10, filters=filter_expr)
    write_output("  Found %d documents", len(results))
    for r in results[:3]:
        write_output(
            "    - ID: %s, Score: %s, Score: %.4f",
            r.id,
            r.metadata.get("score"),
            r.score,
        )

    write_output("\n[1.6] Testing lte() - Find documents where score <= 70")
    filter_expr = lte("score", 70)
    results = await search_vectors(client, collection_name, query_vector, top_k=10, filters=filter_expr)
    write_output("  Found %d documents", len(results))
    for r in results[:3]:
        write_output(
            "    - ID: %s, Score: %s, Score: %.4f",
            r.id,
            r.metadata.get("score"),
            r.score,
        )


async def test_range_operators(client: MilvusClient, collection_name: str) -> None:
    """Test range operators: in_list"""
    write_output("\n%s", "=" * 70)
    write_output("Test 2: Range Operators")
    write_output("%s", "=" * 70)

    query_vector = [0.1] * EMBEDDING_DIM

    write_output("\n[2.1] Testing in_list() - Find documents where category in ['tech', 'science']")
    filter_expr = in_list("category", ["tech", "science"])
    results = await search_vectors(client, collection_name, query_vector, top_k=10, filters=filter_expr)
    write_output("  Found %d documents", len(results))
    for r in results[:3]:
        write_output(
            "    - ID: %s, Category: %s, Score: %.4f",
            r.id,
            r.metadata.get("category"),
            r.score,
        )

    write_output("\n[2.2] Testing in_list() - Find documents where score in [70, 80, 90]")
    filter_expr = in_list("score", [70, 80, 90])
    results = await search_vectors(client, collection_name, query_vector, top_k=10, filters=filter_expr)
    write_output("  Found %d documents", len(results))
    for r in results[:3]:
        write_output(
            "    - ID: %s, Score: %s, Score: %.4f",
            r.id,
            r.metadata.get("score"),
            r.score,
        )

    write_output("\n[2.3] Testing in_list() - Find documents where year in [2020, 2021, 2022]")
    filter_expr = in_list("year", [2020, 2021, 2022])
    results = await search_vectors(client, collection_name, query_vector, top_k=10, filters=filter_expr)
    write_output("  Found %d documents", len(results))
    for r in results[:3]:
        write_output(
            "    - ID: %s, Year: %s, Score: %.4f",
            r.id,
            r.metadata.get("year"),
            r.score,
        )


async def test_logical_operators(client: MilvusClient, collection_name: str) -> None:
    """Test logical operators: and, or"""
    write_output("\n%s", "=" * 70)
    write_output("Test 3: Logical Operators")
    write_output("%s", "=" * 70)

    query_vector = [0.1] * EMBEDDING_DIM

    write_output("\n[3.1] Testing AND operator - category == 'tech' AND score > 70")
    filter_expr = eq("category", "tech") & gt("score", 70)
    results = await search_vectors(client, collection_name, query_vector, top_k=10, filters=filter_expr)
    write_output("  Found %d documents", len(results))
    for r in results:
        write_output(
            "    - ID: %s, Category: %s, Score: %s, Score: %.4f",
            r.id,
            r.metadata.get("category"),
            r.metadata.get("score"),
            r.score,
        )

    write_output("\n[3.2] Testing OR operator - category == 'tech' OR category == 'science'")
    filter_expr = eq("category", "tech") | eq("category", "science")
    results = await search_vectors(client, collection_name, query_vector, top_k=10, filters=filter_expr)
    write_output("  Found %d documents", len(results))
    for r in results[:5]:
        write_output(
            "    - ID: %s, Category: %s, Score: %.4f",
            r.id,
            r.metadata.get("category"),
            r.score,
        )

    write_output("\n[3.3] Testing complex AND - category == 'tech' AND score >= 70 AND year >= 2022")
    filter_expr = eq("category", "tech") & gte("score", 70) & gte("year", 2022)
    results = await search_vectors(client, collection_name, query_vector, top_k=10, filters=filter_expr)
    write_output("  Found %d documents", len(results))
    for r in results:
        write_output(
            "    - ID: %s, Category: %s, Score: %s, Year: %s, Score: %.4f",
            r.id,
            r.metadata.get("category"),
            r.metadata.get("score"),
            r.metadata.get("year"),
            r.score,
        )

    write_output("\n[3.4] Testing complex OR - author == 'Alice' OR author == 'Bob'")
    filter_expr = eq("author", "Alice") | eq("author", "Bob")
    results = await search_vectors(client, collection_name, query_vector, top_k=10, filters=filter_expr)
    write_output("  Found %d documents", len(results))
    for r in results[:5]:
        write_output(
            "    - ID: %s, Author: %s, Score: %.4f",
            r.id,
            r.metadata.get("author"),
            r.score,
        )

    write_output("\n[3.5] Testing combined AND/OR - (category == 'tech' OR category == 'science') AND score > 70")
    filter_expr = (eq("category", "tech") | eq("category", "science")) & gt("score", 70)
    results = await search_vectors(client, collection_name, query_vector, top_k=10, filters=filter_expr)
    write_output("  Found %d documents", len(results))
    for r in results:
        write_output(
            "    - ID: %s, Category: %s, Score: %s, Score: %.4f",
            r.id,
            r.metadata.get("category"),
            r.metadata.get("score"),
            r.score,
        )


async def test_text_matching(client: MilvusClient, collection_name: str) -> None:
    """Test text matching with MatchExpr"""
    write_output("\n%s", "=" * 70)
    write_output("Test 4: Text Matching (MatchExpr)")
    write_output("%s", "=" * 70)

    query_vector = [0.1] * EMBEDDING_DIM

    write_output("\n[4.1] Testing MatchExpr with exact mode - Find documents containing 'tech'")
    match_expr = MatchExpr(field="content", value="tech", match_mode="exact")
    results = await search_vectors(client, collection_name, query_vector, top_k=10, filters=match_expr)
    write_output("  Found %d documents", len(results))
    for r in results[:3]:
        write_output(
            "    - ID: %s, Text: %s..., Score: %.4f",
            r.id,
            r.text[:50],
            r.score,
        )

    write_output("\n[4.2] Testing MatchExpr with prefix mode - Find documents starting with 'This is document 1'")
    match_expr = MatchExpr(field="content", value="This is document 1", match_mode="prefix")
    results = await search_vectors(client, collection_name, query_vector, top_k=10, filters=match_expr)
    write_output("  Found %d documents", len(results))
    for r in results[:3]:
        write_output(
            "    - ID: %s, Text: %s..., Score: %.4f",
            r.id,
            r.text[:50],
            r.score,
        )

    write_output("\n[4.3] Testing MatchExpr with infix mode - Find documents containing 'Alice'")
    match_expr = MatchExpr(field="content", value="Alice", match_mode="infix")
    results = await search_vectors(client, collection_name, query_vector, top_k=10, filters=match_expr)
    write_output("  Found %d documents", len(results))
    for r in results:
        write_output(
            "    - ID: %s, Text: %s..., Score: %.4f",
            r.id,
            r.text[:50],
            r.score,
        )

    write_output("\n[4.4] Testing MatchExpr with suffix mode - Find documents ending with 'score 70.'")
    match_expr = MatchExpr(field="content", value="score 70.", match_mode="suffix")
    results = await search_vectors(client, collection_name, query_vector, top_k=10, filters=match_expr)
    write_output("  Found %d documents", len(results))
    for r in results:
        write_output(
            "    - ID: %s, Text: %s..., Score: %.4f",
            r.id,
            r.text[:50],
            r.score,
        )


async def test_arithmetic_operators(client: MilvusClient, collection_name: str) -> None:
    """Test arithmetic operators (supported by Milvus)"""
    write_output("\n%s", "=" * 70)
    write_output("Test 5: Arithmetic Operators")
    write_output("%s", "=" * 70)

    query_vector = [0.1] * EMBEDDING_DIM

    write_output("\n[5.1] Testing ArithmeticExpr - Find documents where score + 10 > 80")
    arith_expr = ArithmeticExpr(
        field="score",
        arithmetic_operator="+",
        arithmetic_value=10,
        comparison_operator=">",
        comparison_value=80,
    )
    results = await search_vectors(client, collection_name, query_vector, top_k=10, filters=arith_expr)
    write_output("  Found %d documents", len(results))
    for r in results[:3]:
        write_output(
            "    - ID: %s, Score: %s, Score: %.4f",
            r.id,
            r.metadata.get("score"),
            r.score,
        )

    write_output("\n[5.2] Testing ArithmeticExpr - Find documents where score * 2 >= 150")
    arith_expr = ArithmeticExpr(
        field="score",
        arithmetic_operator="*",
        arithmetic_value=2,
        comparison_operator=">=",
        comparison_value=150,
    )
    results = await search_vectors(client, collection_name, query_vector, top_k=10, filters=arith_expr)
    write_output("  Found %d documents", len(results))
    for r in results[:3]:
        write_output(
            "    - ID: %s, Score: %s, Score: %.4f",
            r.id,
            r.metadata.get("score"),
            r.score,
        )


async def test_null_operators(client: MilvusClient, collection_name: str) -> None:
    """Test null value checks (supported by Milvus)"""
    write_output("\n%s", "=" * 70)
    write_output("Test 6: Null Value Checks")
    write_output("%s", "=" * 70)

    query_vector = [0.1] * EMBEDDING_DIM

    write_output("\n[6.1] Testing is_not_null() - Find documents where category is not null")
    null_expr = is_not_null("category")
    results = await search_vectors(client, collection_name, query_vector, top_k=10, filters=null_expr)
    write_output("  Found %d documents", len(results))
    for r in results[:3]:
        write_output(
            "    - ID: %s, Category: %s, Score: %.4f",
            r.id,
            r.metadata.get("category"),
            r.score,
        )

    write_output("\n[6.2] Testing is_null() - Find documents where optional_field is null")
    null_expr = is_null("optional_field")
    results = await search_vectors(client, collection_name, query_vector, top_k=10, filters=null_expr)
    write_output("  Found %d documents", len(results))
    for r in results[:3]:
        write_output(
            "    - ID: %s, Score: %.4f",
            r.id,
            r.score,
        )


async def test_json_operators(client: MilvusClient, collection_name: str) -> None:
    """Test JSON field operations (supported by Milvus)"""
    write_output("\n%s", "=" * 70)
    write_output("Test 7: JSON Field Operations")
    write_output("%s", "=" * 70)

    query_vector = [0.1] * EMBEDDING_DIM

    write_output("\n[7.1] Testing JSONExpr - Find documents where metadata['category'] == 'tech'")
    json_expr = json_key(field="metadata", key="category", operator="==", value="tech")
    results = await search_vectors(client, collection_name, query_vector, top_k=10, filters=json_expr)
    write_output("  Found %d documents", len(results))
    for r in results[:3]:
        write_output(
            "    - ID: %s, Category: %s, Score: %.4f",
            r.id,
            r.metadata.get("category"),
            r.score,
        )

    write_output("\n[7.2] Testing JSONExpr - Find documents where metadata['score'] > 70")
    json_expr = json_key(field="metadata", key="score", operator=">", value=70)
    results = await search_vectors(client, collection_name, query_vector, top_k=10, filters=json_expr)
    write_output("  Found %d documents", len(results))
    for r in results[:3]:
        write_output(
            "    - ID: %s, Score: %s, Score: %.4f",
            r.id,
            r.metadata.get("score"),
            r.score,
        )


async def test_delete_with_filters(client: MilvusClient, collection_name: str) -> None:
    """Test delete operation with QueryExpr filters"""
    write_output("\n%s", "=" * 70)
    write_output("Test 8: Delete with QueryExpr Filters")
    write_output("%s", "=" * 70)

    query_vector = [0.1] * EMBEDDING_DIM
    all_results = await search_vectors(client, collection_name, query_vector, top_k=100)
    write_output("\n[8.1] Total documents before delete: %d", len(all_results))

    write_output("\n[8.2] Testing delete with filter - Delete documents where category == 'health'")
    filter_expr = eq("category", "health")
    filter_str = filter_expr.to_expr("milvus")
    result = await asyncio.to_thread(client.delete, collection_name=collection_name, filter=filter_str)
    await asyncio.to_thread(client.flush, collection_name=collection_name)

    delete_count = result.get("delete_count", 0) if isinstance(result, dict) else 0
    write_output(
        "  Delete operation %s, deleted %d documents", "succeeded" if delete_count > 0 else "failed", delete_count
    )

    all_results_after = await search_vectors(client, collection_name, query_vector, top_k=100)
    write_output("\n[8.3] Total documents after delete: %d", len(all_results_after))
    write_output("  Deleted %d documents", len(all_results) - len(all_results_after))

    filter_expr = eq("category", "health")
    health_results = await search_vectors(client, collection_name, query_vector, top_k=10, filters=filter_expr)
    write_output("\n[8.4] Remaining 'health' documents: %d", len(health_results))


async def main() -> None:
    """Main example demonstrating QueryExpr usage with Milvus"""
    write_output("%s", "=" * 70)
    write_output("Milvus QueryExpr Test Script")
    write_output("%s", "=" * 70)
    write_output("\nThis script demonstrates QueryExpr usage with MilvusClient")
    write_output("QueryExpr types are defined in openjiuwen/core/foundation/store/query/base.py")

    milvus_uri = MILVUS_URI
    collection_name = COLLECTION_NAME

    try:
        client = MilvusClient(uri=milvus_uri)
        if DATABASE_NAME in client.list_databases(timeout=10):
            client.use_database(DATABASE_NAME)
            for col in client.list_collections():
                client.drop_collection(col)
            client.use_database("")
            client.drop_database(DATABASE_NAME, timeout=10)
        client.create_database(DATABASE_NAME, timeout=10)
        client.use_database(DATABASE_NAME)
        write_output("\nCreated MilvusClient at %s", milvus_uri)
        write_output("  Collection: %s", collection_name)
        write_output("  Database: test_query_expr")

        await ensure_collection(client, collection_name)
        await create_test_data(client, collection_name, num_docs=10)

        await test_comparison_operators(client, collection_name)
        await test_range_operators(client, collection_name)
        await test_logical_operators(client, collection_name)
        await test_text_matching(client, collection_name)
        await test_arithmetic_operators(client, collection_name)
        await test_null_operators(client, collection_name)
        await test_json_operators(client, collection_name)
        await test_delete_with_filters(client, collection_name)

        write_output("\n%s", "=" * 70)
        write_output("All tests completed!")
        write_output("%s", "=" * 70)

    finally:
        # Cleanup: close the client connection
        if "client" in locals():
            client.close()
            write_output("\nClosed Milvus connection")


if __name__ == "__main__":
    asyncio.run(main())
