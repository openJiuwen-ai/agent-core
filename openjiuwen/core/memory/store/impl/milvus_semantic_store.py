# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import threading
from typing import List, Tuple, Any
from pymilvus import MilvusClient, FieldSchema, CollectionSchema, DataType, Collection, connections, utility
from openjiuwen.core.common.logging import logger
from openjiuwen.core.memory.store.base_semantic_store import BaseSemanticStore


TABLE_NAME_LENGTH = 128
MEMORY_ID_LENGTH = 36


def convert_milvus_result(results) -> List[List[Tuple[str, float]]]:
    final_results = []
    for hits_per_query in results:
        hits = []
        for hit in hits_per_query:
            memory_id = hit.entity.get("memory_id")  # 获取字段
            distance = hit.distance
            hits.append((memory_id, distance))
        final_results.append(hits)
    return final_results


class MilvusSemanticStore(BaseSemanticStore):
    def __init__(self, milvus_host: str, milvus_port: str, token: str | None,
                collection_name: str, embed_model: Any, embedding_dims: int):
        self.embed_model = embed_model
        self.embedding_dims = embedding_dims
        self.token = token
        self.collection_name = collection_name
        self.milvus_host = milvus_host
        self.milvus_port = milvus_port
        self.time_out = 3
        self.collections = {}
        self.complete_init = False

    def _init_collection(self):
        """
        create Milvus collection(if not exist)
        """
        if self.complete_init:
            return
        connections.connect(host=self.milvus_host, port=self.milvus_port,
                            alias="default", token=self.token, timeout=self.time_out)
        existing_collections = utility.list_collections()
        if self.collection_name not in existing_collections:
            logger.info(f"Collection {self.collection_name} not found, creating...")
            fields = [
                FieldSchema(name="memory_id", dtype=DataType.VARCHAR, is_primary=True,
                            max_length=MEMORY_ID_LENGTH),
                FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR,
                            dim=self.embedding_dims),
                FieldSchema(name="table_name", dtype=DataType.VARCHAR,
                            max_length=TABLE_NAME_LENGTH)
            ]
            schema = CollectionSchema(fields, description="embedding collection")
            collection = Collection(
                name=self.collection_name,
                schema=schema,
                using="default"
            )
            index_params = {
                "index_type": "IVF_FLAT",
                "metric_type": "IP",
                "params": {"nlist": 128}
            }
            collection.create_index(field_name="embedding", index_params=index_params)
            logger.info(f"Index created for collection {self.collection_name}")
        self.complete_init = True

    def get_collection(self, table_name: str) -> Collection:
        if table_name in self.collections:
            return self.collections[table_name]
        connections.connect(host=self.milvus_host, port=self.milvus_port, alias="default")
        if not utility.has_collection(table_name):
            fields = [
                FieldSchema(name="memory_id", dtype=DataType.VARCHAR, is_primary=True, max_length=36),
                FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=self.embedding_dims),
                FieldSchema(name="table_name", dtype=DataType.VARCHAR, max_length=64)
            ]
            schema = CollectionSchema(fields, description="embedding collection")
            collection = Collection(name=table_name, schema=schema, using="default")
            index_params = {"index_type": "IVF_FLAT", "metric_type": "IP", "params": {"nlist": 128}}
            collection.create_index("embedding", index_params)
        else:
            collection = Collection(name=table_name, using="default")
        collection.load()
        self.collections[table_name] = collection
        return collection

    async def add_docs(self, docs: List[Tuple[str, str]], table_name: str) -> bool:
        self._init_collection()
        memory_ids, memories = zip(*docs)
        memory_ids = list(memory_ids)
        memories = list(memories)
        embeddings = await self.embed_model.embed_queries(texts=memories)
        if len(memory_ids) != len(embeddings):
            raise ValueError(f"memory_ids and embeddings must have same length")
        collection = self.get_collection(self.collection_name)
        vectors_arr = [
            [float(x) for x in vec]
            for vec in embeddings
        ]
        collection.insert([
            memory_ids,
            vectors_arr,
            [table_name] * len(memory_ids)
        ], timeout=self.time_out)
        return True

    async def delete_docs(self, ids: List[str], table_name: str) -> bool:
        self._init_collection()
        if self.collection_name not in self.collections:
            return True  # collection not exist
        collection = self.get_collection(self.collection_name)
        ids_str = ','.join([f'"{i}"' for i in ids])
        expr = f'memory_id in [{ids_str}] && table_name == "{table_name}"'
        collection.delete(expr, timeout=self.time_out)
        return True

    async def search(self, query: str, table_name: str, top_k: int) -> List[Tuple[str, float]]:
        self._init_collection()
        collection = self.get_collection(self.collection_name)
        query_vector = await self.embed_model.embed_queries(texts=[query])
        expr = f'table_name == "{table_name}"'
        results = collection.search(
            data=query_vector,
            anns_field="embedding",
            param={"metric_type": "IP", "params": {"nprobe": 10}},
            limit=top_k,
            expr=expr,
            timeout=self.time_out
        )
        parsed_results = convert_milvus_result(results)
        return parsed_results[0] if parsed_results else []

    async def delete_table(self, table_name: str) -> bool:
        self._init_collection()
        if self.collection_name not in self.collections:
            return True
        collection = self.get_collection(self.collection_name)
        expr = f'table_name == "{table_name}"'
        collection.delete(expr, timeout=self.time_out)
        return True
