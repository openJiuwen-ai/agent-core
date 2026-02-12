# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
Example script demonstrating QueryExpr usage with ChromaDB
"""

import asyncio
import os
import shutil

from utils.output import write_output

from openjiuwen.core.foundation.store.query import (
    MatchExpr,
    eq,
    gt,
    gte,
    in_list,
    lt,
    lte,
    ne,
)
from openjiuwen.core.retrieval.common.config import VectorStoreConfig
from openjiuwen.core.retrieval.vector_store.chroma_store import ChromaVectorStore


async def create_test_data(store: ChromaVectorStore, num_docs: int = 10) -> None:
    """Create test data with various metadata fields"""
    write_output("\n%s", "=" * 70)
    write_output("Creating test data...")
    write_output("%s", "=" * 70)

    embedding_dim = 384
    test_data = []
    categories = ["tech", "science", "business", "health"]
    authors = ["Alice", "Bob", "Charlie", "Diana"]

    for i in range(num_docs):
        doc_id = f"doc_{i + 1}"
        category = categories[i % len(categories)]
        author = authors[i % len(authors)]
        score = 50 + (i * 5)
        year = 2020 + (i % 5)

        embedding = [0.1 * (i + j) for j in range(embedding_dim)]
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

    await store.add(test_data, batch_size=5)
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


async def test_comparison_operators(store: ChromaVectorStore) -> None:
    """Test comparison operators: eq, ne, gt, lt, gte, lte"""
    write_output("\n%s", "=" * 70)
    write_output("Test 1: Comparison Operators")
    write_output("%s", "=" * 70)

    query_vector = [0.1] * 384

    write_output("\n[1.1] Testing eq() - Find documents where category == 'tech'")
    filter_expr = eq("category", "tech")
    results = await store.search(query_vector, top_k=10, filters=filter_expr)
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
    results = await store.search(query_vector, top_k=10, filters=filter_expr)
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
    results = await store.search(query_vector, top_k=10, filters=filter_expr)
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
    results = await store.search(query_vector, top_k=10, filters=filter_expr)
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
    results = await store.search(query_vector, top_k=10, filters=filter_expr)
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
    results = await store.search(query_vector, top_k=10, filters=filter_expr)
    write_output("  Found %d documents", len(results))
    for r in results[:3]:
        write_output(
            "    - ID: %s, Score: %s, Score: %.4f",
            r.id,
            r.metadata.get("score"),
            r.score,
        )


async def test_range_operators(store: ChromaVectorStore) -> None:
    """Test range operators: in_list"""
    write_output("\n%s", "=" * 70)
    write_output("Test 2: Range Operators")
    write_output("%s", "=" * 70)

    query_vector = [0.1] * 384

    write_output("\n[2.1] Testing in_list() - Find documents where category in ['tech', 'science']")
    filter_expr = in_list("category", ["tech", "science"])
    results = await store.search(query_vector, top_k=10, filters=filter_expr)
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
    results = await store.search(query_vector, top_k=10, filters=filter_expr)
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
    results = await store.search(query_vector, top_k=10, filters=filter_expr)
    write_output("  Found %d documents", len(results))
    for r in results[:3]:
        write_output(
            "    - ID: %s, Year: %s, Score: %.4f",
            r.id,
            r.metadata.get("year"),
            r.score,
        )


async def test_logical_operators(store: ChromaVectorStore) -> None:
    """Test logical operators: and, or"""
    write_output("\n%s", "=" * 70)
    write_output("Test 3: Logical Operators")
    write_output("%s", "=" * 70)

    query_vector = [0.1] * 384

    write_output("\n[3.1] Testing AND operator - category == 'tech' AND score > 70")
    filter_expr = eq("category", "tech") & gt("score", 70)
    results = await store.search(query_vector, top_k=10, filters=filter_expr)
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
    results = await store.search(query_vector, top_k=10, filters=filter_expr)
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
    results = await store.search(query_vector, top_k=10, filters=filter_expr)
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
    results = await store.search(query_vector, top_k=10, filters=filter_expr)
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
    results = await store.search(query_vector, top_k=10, filters=filter_expr)
    write_output("  Found %d documents", len(results))
    for r in results:
        write_output(
            "    - ID: %s, Category: %s, Score: %s, Score: %.4f",
            r.id,
            r.metadata.get("category"),
            r.metadata.get("score"),
            r.score,
        )


async def test_text_matching(store: ChromaVectorStore) -> None:
    """Test text matching with MatchExpr"""
    write_output("\n%s", "=" * 70)
    write_output("Test 4: Text Matching (MatchExpr)")
    write_output("%s", "=" * 70)

    query_vector = [0.1] * 384

    write_output("\n[4.1] Testing MatchExpr with exact mode - Find documents containing 'tech'")
    match_expr = MatchExpr(field="content", value="tech", match_mode="exact")
    results = await store.search(query_vector, top_k=10, filters=match_expr)
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
    results = await store.search(query_vector, top_k=10, filters=match_expr)
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
    results = await store.search(query_vector, top_k=10, filters=match_expr)
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
    results = await store.search(query_vector, top_k=10, filters=match_expr)
    write_output("  Found %d documents", len(results))
    for r in results:
        write_output(
            "    - ID: %s, Text: %s..., Score: %.4f",
            r.id,
            r.text[:50],
            r.score,
        )


async def test_sparse_search_with_filters(store: ChromaVectorStore) -> None:
    """Test sparse search (text search) with QueryExpr filters"""
    write_output("\n%s", "=" * 70)
    write_output("Test 5: Sparse Search with QueryExpr Filters")
    write_output("%s", "=" * 70)

    write_output("\n[5.1] Testing sparse_search with category filter - Search 'tech' in category='tech' documents")
    filter_expr = eq("category", "tech")
    results = await store.sparse_search("tech", top_k=5, filters=filter_expr)
    write_output("  Found %d documents", len(results))
    for r in results:
        write_output(
            "    - ID: %s, Category: %s, Text: %s..., Score: %.4f",
            r.id,
            r.metadata.get("category"),
            r.text[:50],
            r.score,
        )

    write_output("\n[5.2] Testing sparse_search with score filter - Search 'document' in score > 70 documents")
    filter_expr = gt("score", 70)
    results = await store.sparse_search("document", top_k=5, filters=filter_expr)
    write_output("  Found %d documents", len(results))
    for r in results:
        write_output(
            "    - ID: %s, Score: %s, Text: %s..., Score: %.4f",
            r.id,
            r.metadata.get("score"),
            r.text[:50],
            r.score,
        )


async def test_hybrid_search_with_filters(store: ChromaVectorStore) -> None:
    """Test hybrid search with QueryExpr filters"""
    write_output("\n%s", "=" * 70)
    write_output("Test 6: Hybrid Search with QueryExpr Filters")
    write_output("%s", "=" * 70)

    query_vector = [0.1] * 384

    write_output("\n[6.1] Testing hybrid_search with category filter")
    filter_expr = eq("category", "tech")
    results = await store.hybrid_search(
        query_text="tech document",
        query_vector=query_vector,
        top_k=5,
        filters=filter_expr,
    )
    write_output("  Found %d documents", len(results))
    for r in results:
        write_output(
            "    - ID: %s, Category: %s, Text: %s..., Score: %.4f",
            r.id,
            r.metadata.get("category"),
            r.text[:50],
            r.score,
        )


async def test_delete_with_filters(store: ChromaVectorStore) -> None:
    """Test delete operation with QueryExpr filters"""
    write_output("\n%s", "=" * 70)
    write_output("Test 7: Delete with QueryExpr Filters")
    write_output("%s", "=" * 70)

    query_vector = [0.1] * 384
    all_results = await store.search(query_vector, top_k=100)
    write_output("\n[7.1] Total documents before delete: %d", len(all_results))

    write_output("\n[7.2] Testing delete with filter - Delete documents where category == 'health'")
    filter_expr = eq("category", "health")
    success = await store.delete(filter_expr=filter_expr)
    write_output("  Delete operation %s", "succeeded" if success else "failed")

    all_results_after = await store.search(query_vector, top_k=100)
    write_output("\n[7.3] Total documents after delete: %d", len(all_results_after))
    write_output("  Deleted %d documents", len(all_results) - len(all_results_after))

    filter_expr = eq("category", "health")
    health_results = await store.search(query_vector, top_k=10, filters=filter_expr)
    write_output("\n[7.4] Remaining 'health' documents: %d", len(health_results))


async def demonstrate_unsupported_operations() -> None:
    """Demonstrate operations that are not supported by ChromaDB"""
    write_output("\n%s", "=" * 70)
    write_output("Test 8: Unsupported Operations (Expected to Fail)")
    write_output("%s", "=" * 70)

    from openjiuwen.core.foundation.store.query import (
        ArithmeticExpr,
        ArrayExpr,
        JSONExpr,
        NullExpr,
    )

    write_output("\n[8.1] Testing ArithmeticExpr (not supported by ChromaDB)")
    try:
        arith_expr = ArithmeticExpr(
            field="score",
            arithmetic_operator="+",
            arithmetic_value=10,
            comparison_operator=">",
            comparison_value=80,
        )
        result = arith_expr.to_expr("chroma")
        write_output("  Unexpected success: %s", result)
    except Exception as e:
        write_output("  Expected error: %s: %s", type(e).__name__, e)

    write_output("\n[8.2] Testing NullExpr (not supported by ChromaDB)")
    try:
        null_expr = NullExpr(field="category", is_null=False)
        result = null_expr.to_expr("chroma")
        write_output("  Unexpected success: %s", result)
    except Exception as e:
        write_output("  Expected error: %s: %s", type(e).__name__, e)

    write_output("\n[8.3] Testing JSONExpr (not supported by ChromaDB)")
    try:
        json_expr = JSONExpr(field="metadata", key="category", operator="==", value="tech")
        result = json_expr.to_expr("chroma")
        write_output("  Unexpected success: %s", result)
    except Exception as e:
        write_output("  Expected error: %s: %s", type(e).__name__, e)

    write_output("\n[8.4] Testing ArrayExpr (not supported by ChromaDB)")
    try:
        array_expr = ArrayExpr(field="tags", index=0, operator="==", value="python")
        result = array_expr.to_expr("chroma")
        write_output("  Unexpected success: %s", result)
    except Exception as e:
        write_output("  Expected error: %s: %s", type(e).__name__, e)


async def main() -> None:
    """Main example demonstrating QueryExpr usage with ChromaDB"""
    write_output("%s", "=" * 70)
    write_output("ChromaDB QueryExpr Test Script")
    write_output("%s", "=" * 70)
    write_output("\nThis script demonstrates QueryExpr usage with ChromaVectorStore")
    write_output("QueryExpr types are defined in openjiuwen/core/foundation/store/query/base.py")

    chroma_path = "./test-chroma-query-expr-store"

    if os.path.exists(chroma_path):
        write_output("\nCleaning up existing test store at %s...", chroma_path)
        shutil.rmtree(chroma_path)

    try:
        config = VectorStoreConfig(
            collection_name="test_query_expr_collection",
            distance_metric="cosine",
            database_name="test_db",
        )

        store = ChromaVectorStore(
            config=config,
            chroma_path=chroma_path,
        )

        write_output("\nCreated ChromaVectorStore at %s", chroma_path)
        write_output("  Collection: %s", config.collection_name)
        write_output("  Database: %s", config.database_name)

        await create_test_data(store, num_docs=10)

        await test_comparison_operators(store)
        await test_range_operators(store)
        await test_logical_operators(store)
        await test_text_matching(store)
        await test_sparse_search_with_filters(store)
        await test_hybrid_search_with_filters(store)
        await test_delete_with_filters(store)
        await demonstrate_unsupported_operations()

        write_output("\n%s", "=" * 70)
        write_output("All tests completed!")
        write_output("%s", "=" * 70)

    finally:
        if os.path.exists(chroma_path):
            write_output("\nCleaning up test store at %s...", chroma_path)
            shutil.rmtree(chroma_path)
            write_output("Cleanup complete")


if __name__ == "__main__":
    asyncio.run(main())
