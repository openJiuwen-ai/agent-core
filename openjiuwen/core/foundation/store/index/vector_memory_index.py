# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Default vector index implementation for memory storage and retrieval.

This module provides VectorMemoryIndex, which adapts BaseVectorStore
for memory document management with vector-based similarity search,
replacing the functionality of SemanticStore and UserMemStore.
"""
import uuid
from datetime import datetime, timezone
from typing import Any

from openjiuwen.core.common.logging import memory_logger
from openjiuwen.core.common.logging.events import LogEventType
from openjiuwen.core.foundation.store.base_memory_index import BaseMemoryIndex, MemoryDoc
from openjiuwen.core.foundation.store.base_vector_store import (
    BaseVectorStore,
    CollectionSchema,
    FieldSchema,
    VectorDataType,
)

DEFAULT_EMBEDDING_DIMENSION = 768
_DEFAULT_ZERO_EMBEDDING: list[float] = [0.0] * DEFAULT_EMBEDDING_DIMENSION


class VectorMemoryIndex(BaseMemoryIndex):
    """
    Default vector index implementation, adapting to BaseVectorStore.
    This implementation replaces the functionality of SemanticStore and UserMemStore.
    """

    def __init__(self, vector_store: BaseVectorStore, embedding_model=None):
        """
        Initialize VectorMemoryIndex with a BaseVectorStore instance.

        Args:
            vector_store: BaseVectorStore instance to use as the backend.
            embedding_model: Embedding model for generating vectors.
        """
        self._vector_store = vector_store
        self._embedding_model = embedding_model
        self._created_collections: set[str] = set()
        self._schema_version = 0
        self._backups: dict[str, list[dict[str, Any]]] = {}

    def _get_collection_name(self, user_id: str, scope_id: str, mem_type: str) -> str:
        """Generate collection name based on user_id, scope_id, and memory type."""
        return f"memory_{user_id}_{scope_id}_{mem_type}"

    @staticmethod
    def _parse_timestamp(v: Any) -> datetime:
        """Parse stored timestamp value to datetime."""
        if isinstance(v, datetime):
            return v
        if isinstance(v, str) and v:
            try:
                return datetime.fromisoformat(v)
            except ValueError:
                pass
            for fmt in ("%Y-%m-%d %H-%M-%S", "%Y-%m-%d %H:%M:%S"):
                try:
                    return datetime.strptime(v, fmt).replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v, tz=timezone.utc)
        return datetime.now(timezone.utc).astimezone()

    @staticmethod
    def _fields_to_memory_doc(fields: dict[str, Any]) -> MemoryDoc:
        """Convert vector store result fields to MemoryDoc."""
        return MemoryDoc(
            id=fields["id"],
            text=fields["text"],
            type=fields["type"],
            timestamp=VectorMemoryIndex._parse_timestamp(fields.get("timestamp", "")),
            fields=fields.get("fields", {}),
        )

    async def _ensure_collection(self, collection_name: str) -> None:
        """Ensure the collection exists, create it if not."""
        if collection_name in self._created_collections:
            return

        if not await self._vector_store.collection_exists(collection_name):
            schema = CollectionSchema(
                description=f"Memory collection for {collection_name}",
                enable_dynamic_field=True,
            )
            schema.add_field(FieldSchema(
                name="id",
                dtype=VectorDataType.VARCHAR,
                max_length=256,
                is_primary=True,
            ))
            schema.add_field(FieldSchema(
                name="embedding",
                dtype=VectorDataType.FLOAT_VECTOR,
                dim=DEFAULT_EMBEDDING_DIMENSION,
            ))
            schema.add_field(FieldSchema(
                name="text",
                dtype=VectorDataType.VARCHAR,
                max_length=65535,
            ))
            schema.add_field(FieldSchema(
                name="type",
                dtype=VectorDataType.VARCHAR,
                max_length=128,
            ))
            schema.add_field(FieldSchema(
                name="timestamp",
                dtype=VectorDataType.VARCHAR,
                max_length=64,
            ))
            schema.add_field(FieldSchema(
                name="fields",
                dtype=VectorDataType.JSON,
            ))
            schema.add_field(FieldSchema(
                name="user_id",
                dtype=VectorDataType.VARCHAR,
                max_length=256,
            ))
            schema.add_field(FieldSchema(
                name="scope_id",
                dtype=VectorDataType.VARCHAR,
                max_length=256,
            ))

            await self._vector_store.create_collection(collection_name, schema)

        self._created_collections.add(collection_name)

    async def add_memories(self, user_id: str, scope_id: str, memories: list[MemoryDoc]) -> None:
        """Batch add memory documents. Internally generates vectors if needed."""
        if not self._vector_store:
            memory_logger.error(
                "Vector store not initialized.",
                event_type=LogEventType.MEMORY_STORE,
                scope_id=scope_id,
                metadata={"user_id": user_id}
            )
            return False

        if not memories:
            return

        memories_by_type: dict[str, list[MemoryDoc]] = {}
        for memory in memories:
            if memory.type not in memories_by_type:
                memories_by_type[memory.type] = []
            memories_by_type[memory.type].append(memory)

        for mem_type, type_memories in memories_by_type.items():
            collection_name = self._get_collection_name(user_id, scope_id, mem_type)
            await self._ensure_collection(collection_name)

            texts = [memory.text for memory in type_memories]
            if self._embedding_model:
                embeddings = await self._embedding_model.embed_documents(texts)
            else:
                memory_logger.error(
                    "Embedding model not initialized, please call initialize_embedding_model first.",
                    event_type=LogEventType.MEMORY_STORE,
                    scope_id=scope_id,
                    metadata={"collection_name": collection_name}
                )
                return False

            docs = []
            for memory, embedding in zip(type_memories, embeddings):
                docs.append({
                    "id": memory.id,
                    "embedding": embedding,
                    "text": memory.text,
                    "type": memory.type,
                    "timestamp": memory.timestamp.isoformat(),
                    "fields": memory.fields,
                    "user_id": user_id,
                    "scope_id": scope_id,
                })

            await self._vector_store.add_docs(collection_name, docs)

    async def search(
            self,
            user_id: str,
            scope_id: str,
            query: str,
            mem_type: str,
            top_k: int = 10) -> list[tuple[MemoryDoc, float]]:
        """Search for memory documents matching a query."""
        if not self._vector_store:
            memory_logger.error(
                "Vector store not initialized.",
                event_type=LogEventType.MEMORY_STORE,
                scope_id=scope_id,
                metadata={"user_id": user_id, "mem_type": mem_type}
            )
            return []

        collection_name = self._get_collection_name(user_id, scope_id, mem_type)

        if not await self._vector_store.collection_exists(collection_name):
            return []

        if self._embedding_model:
            query_vector = await self._embedding_model.embed_query(query)
        else:
            memory_logger.error(
                    "Embedding model not initialized, please call initialize_embedding_model first.",
                    event_type=LogEventType.MEMORY_STORE,
                    scope_id=scope_id,
                    metadata={"collection_name": collection_name}
                )
            return False

        results = await self._vector_store.search(
            collection_name=collection_name,
            query_vector=query_vector,
            vector_field="embedding",
            top_k=top_k,
            output_fields=["id", "text", "type", "timestamp", "fields"]
        )

        memory_results = []
        seen_ids: dict[str, int] = {}
        for idx, result in enumerate(results):
            fields = result.fields
            mem_id = fields["id"]
            if mem_id in seen_ids:
                prev_idx = seen_ids[mem_id]
                if result.score > memory_results[prev_idx][1]:
                    memory_results[prev_idx] = (self._fields_to_memory_doc(fields), result.score)
                continue
            seen_ids[mem_id] = len(memory_results)
            memory_results.append((self._fields_to_memory_doc(fields), result.score))

        return memory_results

    async def update_memories(self, user_id: str, scope_id: str, memories: list[MemoryDoc]) -> None:
        """Update memories by deleting old ones then adding new ones."""
        if not memories:
            return
        ids = [m.id for m in memories]
        await self.delete_memories(user_id, scope_id, ids)
        await self.add_memories(user_id, scope_id, memories)

    async def delete_memories(self, user_id: str, scope_id: str, ids: list[str]) -> None:
        """Batch delete memory documents by their IDs."""
        if not self._vector_store:
            memory_logger.error(
                "Vector store not initialized.",
                event_type=LogEventType.MEMORY_STORE,
                scope_id=scope_id,
                metadata={"user_id": user_id}
            )
            return

        if not ids:
            return

        collection_names = await self._vector_store.list_collection_names()
        target_collections = [name for name in collection_names
                            if name.startswith(f"memory_{user_id}_{scope_id}_")]

        for collection_name in target_collections:
            await self._vector_store.delete_docs_by_ids(collection_name, ids)

    async def delete_by_user(self, user_id: str) -> None:
        """Delete all memory documents for a specific user."""
        if not self._vector_store:
            memory_logger.error(
                "Vector store not initialized.",
                event_type=LogEventType.MEMORY_STORE,
                metadata={"user_id": user_id}
            )
            return

        collection_names = await self._vector_store.list_collection_names()
        user_collections = [name for name in collection_names
                          if name.startswith(f"memory_{user_id}_")]

        for collection_name in user_collections:
            await self._vector_store.delete_docs_by_filters(
                collection_name=collection_name,
                filters={"user_id": user_id}
            )

    async def delete_by_scope(self, scope_id: str) -> None:
        """Delete all memory documents for a specific scope."""
        if not self._vector_store:
            memory_logger.error(
                "Vector store not initialized.",
                event_type=LogEventType.MEMORY_STORE,
                scope_id=scope_id,
            )
            return

        collection_names = await self._vector_store.list_collection_names()
        memory_collections = [name for name in collection_names
                            if name.startswith("memory_")]

        for collection_name in memory_collections:
            await self._vector_store.delete_docs_by_filters(
                collection_name=collection_name,
                filters={"scope_id": scope_id}
            )

    async def delete_by_user_and_scope(self, user_id: str, scope_id: str) -> None:
        """Delete all memory documents for a specific user and scope combination."""
        if not self._vector_store:
            memory_logger.error(
                "Vector store not initialized.",
                event_type=LogEventType.MEMORY_STORE,
                scope_id=scope_id,
                metadata={"user_id": user_id}
            )
            return

        collection_names = await self._vector_store.list_collection_names()
        target_collections = [name for name in collection_names
                            if name.startswith(f"memory_{user_id}_{scope_id}_")]

        for collection_name in target_collections:
            await self._vector_store.delete_docs_by_filters(
                collection_name=collection_name,
                filters={"user_id": user_id, "scope_id": scope_id}
            )

    async def get_by_id(self, user_id: str, scope_id: str, mem_id: str) -> MemoryDoc | None:
        """Get a single memory document by its ID."""
        if not self._vector_store:
            memory_logger.error(
                "Vector store not initialized.",
                event_type=LogEventType.MEMORY_STORE,
                scope_id=scope_id,
                metadata={"user_id": user_id, "mem_id": mem_id}
            )
            return None

        collection_names = await self._vector_store.list_collection_names()
        target_collections = [name for name in collection_names
                            if name.startswith(f"memory_{user_id}_{scope_id}_")]

        for collection_name in target_collections:
            results = await self._vector_store.search(
                collection_name=collection_name,
                query_vector=list(_DEFAULT_ZERO_EMBEDDING),
                vector_field="embedding",
                top_k=1,
                filters={"id": mem_id, "user_id": user_id, "scope_id": scope_id},
                output_fields=["id", "text", "type", "timestamp", "fields"]
            )

            if results:
                return self._fields_to_memory_doc(results[0].fields)

        return None

    async def list_memories(self, user_id: str, scope_id: str, offset: int, limit: int) -> list[MemoryDoc]:
        """List memory documents with pagination."""
        if not self._vector_store:
            memory_logger.error(
                "Vector store not initialized.",
                event_type=LogEventType.MEMORY_STORE,
                scope_id=scope_id,
                metadata={"user_id": user_id}
            )
            return []

        collection_names = await self._vector_store.list_collection_names()
        target_collections = [name for name in collection_names
                            if name.startswith(f"memory_{user_id}_{scope_id}_")]

        all_memories = []
        for collection_name in target_collections:
            results = await self._vector_store.search(
                collection_name=collection_name,
                query_vector=list(_DEFAULT_ZERO_EMBEDDING),
                vector_field="embedding",
                top_k=offset + limit,
                output_fields=["id", "text", "type", "timestamp", "fields"]
            )

            for result in results:
                all_memories.append(self._fields_to_memory_doc(result.fields))

        all_memories.sort(key=lambda x: x.timestamp, reverse=True)

        start = offset
        end = offset + limit
        return all_memories[start:end]

    def get_schema_version(self) -> int:
        """Get the current schema version."""
        return self._schema_version

    def update_schema_version(self, version: int) -> None:
        """Update the schema version."""
        self._schema_version = version

    async def create_backup(self) -> str:
        """Create a backup of the current data."""
        if not self._vector_store:
            memory_logger.error(
                "Vector store not initialized.",
                event_type=LogEventType.MEMORY_STORE,
            )
            return ""

        backup_id = str(uuid.uuid4())
        collection_names = await self._vector_store.list_collection_names()
        backup_data = {
            "schema_version": self._schema_version,
            "collection_names": collection_names
        }
        self._backups[backup_id] = backup_data
        return backup_id

    async def restore_backup(self, backup_id: str) -> None:
        """Restore data from a backup."""
        if backup_id not in self._backups:
            raise ValueError(f"Backup with id {backup_id} not found")
        backup_data = self._backups[backup_id]
        self._schema_version = backup_data["schema_version"]

    async def cleanup_backup(self, backup_id: str) -> None:
        """Clean up a backup."""
        if backup_id in self._backups:
            del self._backups[backup_id]

    async def list_user_scopes(self) -> list[tuple[str, str]]:
        """List all user-scope combinations in the index."""
        if not self._vector_store:
            memory_logger.error(
                "Vector store not initialized.",
                event_type=LogEventType.MEMORY_STORE,
            )
            return []

        collection_names = await self._vector_store.list_collection_names()
        scopes = set()

        for name in collection_names:
            if name.startswith("memory_"):
                parts = name.split("_")
                if len(parts) >= 4:
                    user_id = parts[1]
                    scope_id = parts[2]
                    scopes.add((user_id, scope_id))

        return list(scopes)
