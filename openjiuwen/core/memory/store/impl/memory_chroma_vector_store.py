# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
from typing import Any, List, Optional
from chromadb import PersistentClient
from chromadb.api.types import QueryResult
from chromadb.errors import NotFoundError

from openjiuwen.core.retrieval.vector_store.base import VectorStore
from openjiuwen.core.retrieval.common.retrieval_result import SearchResult
from openjiuwen.core.common.logging import logger


class MemoryChromaVectorStore(VectorStore):
    """Chroma vector store implementation"""

    def __init__(self, persist_directory: str):
        """ Initialize Chroma vector store"""
        self.client = PersistentClient(path=persist_directory)

        self.collection_cache = {}  # Cache for collections

    @staticmethod
    def create_client(database_name: str, path_or_uri: str, token: str = "", **kwargs) -> Any:
        logger.error("create_client not implemented in MemoryChromaVectorStore")
        pass

    async def get_collection(self, table_name: str):
        """Get or create collection by table name"""
        if table_name in self.collection_cache:
            return self.collection_cache[table_name]

        collection = await asyncio.to_thread(
            self.client.get_or_create_collection,
            name=table_name,
            metadata={"hnsw:space": "ip"},
        )
        self.collection_cache[table_name] = collection

        return collection

    def remove_collection_from_cache(self, table_name: str):
        """remove collection from cache"""
        if table_name in self.collection_cache:
            del self.collection_cache[table_name]

    def check_table_name(self, table_name: Optional[str] = None, operation: Optional[str] = None):
        """check table name"""
        if table_name is None or table_name.strip() == "":
            raise ValueError(f"Chroma collection name is required for {operation}")

    async def is_collection_exists(self, table_name: str) -> bool:
        """Check whether the collection exists"""
        try:
            await asyncio.to_thread(
                self.client.get_collection,
                name=table_name
            )
            return True
        except NotFoundError:
            return False

    async def add(
        self,
        data: dict | List[dict],
        batch_size: int | None = 128,
        **kwargs: Any,
    ) -> None:
        """Add vectors to the vector store"""
        table_name = kwargs.get("table_name")
        self.check_table_name(table_name, "add")

        collection = await self.get_collection(table_name)

        # Convert single dict to list
        if isinstance(data, dict):
            data = [data]

        # Process in batches
        for i in range(0, len(data), batch_size):
            batch = data[i:i + batch_size]
            ids, embeddings, metadatas = [], [], []
            for item in batch:
                ids.append(item["id"])
                embeddings.append(item["embedding"])
                metadatas.append({"scope_id": item["scope_id"] if item.get("scope_id") else ""})

            await asyncio.to_thread(
                collection.add,
                ids=ids,
                embeddings=embeddings,
                metadatas=metadatas
            )

    async def search(
        self,
        query_vector: List[float],
        top_k: int = 5,
        filters: Optional[dict] = None,
        **kwargs: Any,
    ) -> List[SearchResult]:
        """Vector search"""
        table_name = kwargs.get("table_name")
        scope_id = kwargs.get("scope_id")
        self.check_table_name(table_name, "search")

        collection = await self.get_collection(table_name)

        results: QueryResult = await asyncio.to_thread(
            collection.query,
            query_embeddings=[query_vector],
            n_results=top_k,
            where={"scope_id": scope_id} if scope_id is not None else None,
        )

        search_results = []
        if len(results['ids']) > 0 and results['ids'][0]:
            ids = results['ids'][0]
            distances = results['distances'][0] if results.get('distances') else [1.0] * len(ids)
            metadatas = results['metadatas'][0] if results.get('metadatas') else [{}] * len(ids)

            # Convert distances to scores (higher is better)
            for item_id, distance, metadata in zip(ids, distances, metadatas):
                score = 1 - distance
                search_results.append(SearchResult(
                    id=item_id,
                    text="",
                    score=score,
                    metadata=metadata or {}
                ))

        return search_results

    async def delete(
        self,
        ids: Optional[List[str]] = None,
        filter_expr: Optional[str] = None,
        **kwargs: Any,
    ) -> bool:
        """Delete vectors"""
        table_name = kwargs.get("table_name")
        self.check_table_name(table_name, "delete")
        collection_is_exists = await self.is_collection_exists(table_name)
        if not collection_is_exists:
            logger.debug(f"Chroma Collection {table_name} does not exist, skip delete vector")
            return True
        if not ids:
            logger.debug(f"ids is {ids}, skip delete vector")
            return True
        collection = await self.get_collection(table_name)
        await asyncio.to_thread(
            collection.delete,
            ids=ids,
        )
        return True

    async def delete_table(self, table_name: str) -> bool:
        collection_is_exists = await self.is_collection_exists(table_name)
        if not collection_is_exists:
            logger.debug(f"Chroma Collection {table_name} does not exist, skip delete collection")
            return True
        await asyncio.to_thread(
            self.client.delete_collection,
            name=table_name
        )
        self.remove_collection_from_cache(table_name)
        return True

    async def sparse_search(
        self,
        query_text: str,
        top_k: int = 5,
        filters: Optional[dict] = None,
        **kwargs: Any,
    ) -> List[SearchResult]:
        pass

    async def hybrid_search(
        self,
        query_text: str,
        query_vector: Optional[List[float]] = None,
        top_k: int = 5,
        alpha: float = 0.5,
        filters: Optional[dict] = None,
        **kwargs: Any,
    ) -> List[SearchResult]:
        pass