# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
from typing import List, Any, Optional
from pymilvus import FieldSchema, CollectionSchema, DataType, Collection, connections, utility
from openjiuwen.core.common.logging import logger
from openjiuwen.core.retrieval.vector_store.base import VectorStore
from openjiuwen.core.retrieval.common.retrieval_result import SearchResult

MEMORY_ID_LENGTH = 36
SCOPE_ID_LENGTH = 64


def convert_milvus_result(results) -> List[SearchResult]:
    final_results: List[SearchResult] = []
    for hits_per_query in results:
        for hit in hits_per_query:
            memory_id = hit.entity.get("id")
            distance = hit.distance
            final_results.append(
                SearchResult(
                    id=str(memory_id),
                    score=distance,
                    text="",
                    metadata={}
                )
            )
    return final_results


class MilvusVectorStore(VectorStore):

    def __init__(self, milvus_host: str, milvus_port: str, token: str | None, embedding_dims: int):
        self.embedding_dims = embedding_dims
        self.token = token
        self.milvus_host = milvus_host
        self.milvus_port = milvus_port
        self.timeout = 3
        self.collections = {}

    async def _ensure_connection(self):
        try:
            await asyncio.to_thread(
                connections.connect,
                host=self.milvus_host,
                port=self.milvus_port,
                alias="default",
                token=self.token,
                timeout=self.timeout
            )
        except Exception as e:
            raise RuntimeError(f"milvus connect error: {str(e)}") from e

    async def _get_collection(self, collection_name: str) -> Collection:
        await self._ensure_connection()
        if collection_name in self.collections:
            return self.collections[collection_name]
        if not utility.has_collection(collection_name):
            logger.info(f"Collection {collection_name} not found, creating...")
            fields = [
                FieldSchema(name="id", dtype=DataType.VARCHAR,
                            is_primary=True, max_length=MEMORY_ID_LENGTH),
                FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR,
                            dim=self.embedding_dims),
                FieldSchema(name="scope_id", dtype=DataType.VARCHAR,
                    max_length=SCOPE_ID_LENGTH)
            ]
            schema = CollectionSchema(fields, description="embedding collection")
            collection = Collection(name=collection_name, schema=schema, using="default")
            index_params = {"index_type": "IVF_FLAT", "metric_type": "IP", "params": {"nlist": 128}}
            collection.create_index("embedding", index_params)
            logger.info(f"Index created for collection {collection_name}")
        else:
            logger.info(f"milvus collection already exists: {collection_name}")
            collection = Collection(name=collection_name, using="default")
        collection.load()
        self.collections[collection_name] = collection
        return collection

    async def add(self, data: dict | List[dict], batch_size: int | None = 128, **kwargs: Any):
        table_name = kwargs.get("table_name")
        if table_name is None:
            raise ValueError("table_name is required")
        if isinstance(data, dict):
            data = [data]
        collection = await self._get_collection(collection_name=table_name)
        embeddings = [d["embedding"] for d in data]
        memory_ids = [d["id"] for d in data]
        scope_id = [d.get("scope_id", "") or "" for d in data]
        await asyncio.to_thread(
            collection.insert,
            [
                memory_ids,
                embeddings,
                scope_id,
            ],
            timeout=self.timeout
        )

    async def search(self, query_vector: List[float], top_k: int = 5,
                     filters: Optional[dict] = None, **kwargs: Any) -> List[SearchResult]:
        table_name = kwargs.get("table_name")
        if table_name is None:
            raise ValueError("table_name is required")
        scope_id = kwargs.get("scope_id")
        expr_filters = None
        if scope_id:
            expr_filters = f"scope_id == '{scope_id}'"
        collection = await self._get_collection(table_name)
        results = await asyncio.to_thread(
            collection.search,
            data=[query_vector],
            anns_field="embedding",
            param={"metric_type": "IP", "params": {"nprobe": 10}},
            limit=top_k,
            expr=expr_filters,
            timeout=self.timeout,
        )
        parsed_results = convert_milvus_result(results)
        return parsed_results if parsed_results else []

    async def sparse_search(self, query_text: str, top_k: int = 5,
                            filters: Optional[dict] = None, **kwargs: Any) -> List[SearchResult]:
        pass

    async def hybrid_search(self, query_text: str, query_vector: Optional[List[float]] = None,
                            top_k: int = 5, alpha: float = 0.5, filters: Optional[dict] = None,
                            **kwargs: Any,
    ) -> List[SearchResult]:
        pass

    async def delete(self, ids: Optional[List[str]] = None,
                     filter_expr: Optional[str] = None, **kwargs: Any) -> bool:
        table_name = kwargs.get("table_name")
        if table_name is None:
            raise ValueError("table_name is required")
        await self._ensure_connection()
        if not utility.has_collection(table_name, using="default"):
            logger.debug(f"Milvus Collection {table_name} does not exist, skip delete vector")
            return True
        collection = await self._get_collection(table_name)
        ids_str = ", ".join(f'"{i}"' for i in ids)
        expr = f'id in [{ids_str}]'
        await asyncio.to_thread(
            collection.delete,
            expr,
            timeout=self.timeout,
        )
        return True

    async def delete_table(self, table_name: str) -> bool:
        await self._ensure_connection()
        if not utility.has_collection(table_name, using="default"):
            logger.debug(f"Milvus Collection {table_name} does not exist, skip delete collection")
            return True
        await asyncio.to_thread(
            utility.drop_collection,
            table_name,
            timeout=self.timeout,
        )
        self.collections.pop(table_name, None)
        return True
