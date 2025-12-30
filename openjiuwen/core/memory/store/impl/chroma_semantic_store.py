# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import threading
from typing import List, Tuple, Any
from chromadb import PersistentClient
from chromadb.api.types import QueryResult

from openjiuwen.core.common.logging import logger
from openjiuwen.core.memory.store.base_semantic_store import BaseSemanticStore


class ChromaSemanticStore(BaseSemanticStore):
    def __init__(self, persist_directory: str, embed_model: Any):
        self.embed_model = embed_model
        self.persist_directory = persist_directory
        self.client = PersistentClient(path=persist_directory)
        self.collection_cache = {}

    def _get_collection(self, table_name: str):
        """get or create collection"""
        if table_name in self.collection_cache:
            return self.collection_cache[table_name]

        collection = self.client.get_or_create_collection(
            name=table_name,
            metadata={"hnsw:space": "ip"},
        )
        self.collection_cache[table_name] = collection

        return collection

    def _remove_collection_from_cache(self, table_name: str):
        """remove collection from cache"""
        if table_name in self.collection_cache:
            del self.collection_cache[table_name]

    async def add_docs(self, docs: List[Tuple[str, str]], table_name: str) -> bool:
        memory_ids, memories = zip(*docs)
        memory_ids = list(memory_ids)
        memories = list(memories)
        embeddings = await self.embed_model.embed_queries(texts=memories)
        if len(memory_ids) != len(embeddings):
            raise ValueError(f"memory_ids and embeddings must have same length")
        collection = self._get_collection(table_name)
        vectors_arr = [
            [float(x) for x in vec]
            for vec in embeddings
        ]
        collection.add(ids=memory_ids, embeddings=vectors_arr)
        return True

    async def delete_docs(self, ids: List[str], table_name: str) -> bool:
        if not ids:
            return True
        try:
            collection = self._get_collection(table_name)
            collection.delete(ids=ids)
        except Exception as e:
            logger.error(f"ChromaSemanticStore delete_docs failed: {e}")
            return False
        return True

    async def search(self, query: str, table_name: str, top_k: int) -> List[Tuple[str, float]]:
        query_embedding = await self.embed_model.embed_queries(texts=[query])
        query_vector = [float(x) for x in query_embedding[0]]
        try:
            collection = self._get_collection(table_name)
            results: QueryResult = collection.query(
                query_embeddings=[query_vector],
                n_results=top_k
            )

            if results['ids'] and len(results['ids']) > 0 and results['ids'][0]:
                ids = results['ids'][0]
                distances = results['distances'][0] if results['distances'] else [1.0] * len(ids)
                distances = [1 - dis for dis in distances]
                return list(zip(ids, distances))
            else:
                return []
        except Exception as e:
            logger.error(f"ChromaSemanticStore search failed: {e}")
            return []

    async def delete_table(self, table_name: str) -> bool:
        try:
            self.client.delete_collection(name=table_name)
            self._remove_collection_from_cache(table_name)
        except Exception as e:
            logger.error(f"ChromaSemanticStore delete_table failed: {e}")
            return False
        return True
