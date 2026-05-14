# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Backward-compatible memory index for legacy KV + Vector store data.

.. deprecated::
    This module provides ``SimpleMemoryIndex`` for operating on data created by
    the old SemanticStore + UserMemStore architecture.  It may be removed in a
    future release once migration to VectorMemoryIndex is complete.
    Do **not** build new features on this class — use VectorMemoryIndex instead.

Legacy data layout
------------------
- KV store key:    ``UMD/{user_id}/{scope_id}/{mem_id}``
- KV store value:  JSON dict with ``id``, ``mem``, ``mem_type``, ``timestamp``, …
- Vector collection: ``uid_{user_id}_gid_{scope_id}_mtype_{mem_type}``
- Vector schema:   ``id`` (VARCHAR) + ``embedding`` (FLOAT_VECTOR)
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from openjiuwen.core.common.logging import memory_logger
from openjiuwen.core.common.logging.events import LogEventType
from openjiuwen.core.foundation.store.base_kv_store import BaseKVStore
from openjiuwen.core.foundation.store.base_memory_index import BaseMemoryIndex, MemoryDoc
from openjiuwen.core.foundation.store.base_vector_store import (
    BaseVectorStore,
    CollectionSchema,
    FieldSchema,
    VectorDataType,
)


class SimpleMemoryIndex(BaseMemoryIndex):
    """
    Backward-compatible memory index for legacy KV + Vector store data.

    WARNING: This class exists solely for backward compatibility with data written
    by the old SemanticStore + UserMemStore architecture.  It may be removed in a
    future version.  New code should use VectorMemoryIndex instead.  Do not build
    new features or long-lived components on top of this class.
    """

    _KV_PREFIX = "UMD"
    _KV_SEP = "/"
    _IDS_SUFFIX = "ids"

    _BYTE_NUM_PER_ID = 24

    def __init__(
        self,
        kv_store: BaseKVStore,
        vector_store: BaseVectorStore,
        embedding_model: Any = None,
    ):
        self._kv_store = kv_store
        self._vector_store = vector_store
        self._embedding_model = embedding_model
        self._created_collections: set[str] = set()
        self._schema_version = 0
        self._backups: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------ #
    #  KV helpers                                                         #
    # ------------------------------------------------------------------ #

    def _kv_mem_key(self, user_id: str, scope_id: str, mem_id: str) -> str:
        return f"{self._KV_PREFIX}{self._KV_SEP}{user_id}{self._KV_SEP}{scope_id}{self._KV_SEP}{mem_id}"

    def _kv_ids_key(self, user_id: str, scope_id: str, mem_type: str | None = None) -> str:
        if mem_type is None:
            return (f"{self._KV_PREFIX}{self._KV_SEP}{user_id}{self._KV_SEP}"
                    f"{scope_id}{self._KV_SEP}{self._IDS_SUFFIX}")
        return (f"{self._KV_PREFIX}{self._KV_SEP}{user_id}{self._KV_SEP}"
                f"{scope_id}{self._KV_SEP}{mem_type}{self._KV_SEP}{self._IDS_SUFFIX}")

    @staticmethod
    def _decode(raw: str | bytes | None) -> str | None:
        if raw is None:
            return None
        return raw.decode("utf-8") if isinstance(raw, bytes) else raw

    @staticmethod
    def _parse_all_ids(raw: str) -> list[str]:
        n = len(raw) // SimpleMemoryIndex._BYTE_NUM_PER_ID
        return [raw[i * SimpleMemoryIndex._BYTE_NUM_PER_ID:(i + 1) * SimpleMemoryIndex._BYTE_NUM_PER_ID]
                for i in range(n)]

    @staticmethod
    def _append_id(raw: str, mem_id: str) -> str:
        return raw + mem_id

    @staticmethod
    def _remove_id(raw: str, mem_id: str) -> str:
        bpid = SimpleMemoryIndex._BYTE_NUM_PER_ID
        total = len(raw) // bpid
        for i in range(total):
            s, e = i * bpid, (i + 1) * bpid
            if raw[s:e] == mem_id:
                return raw[:s] + raw[e:]
        return raw

    async def _add_id_to_tracking(
        self, user_id: str, scope_id: str, mem_id: str, mem_type: str,
    ) -> None:
        # Global IDs
        key = self._kv_ids_key(user_id, scope_id)
        val = self._decode(await self._kv_store.get(key)) or ""
        if mem_id not in self._parse_all_ids(val):
            await self._kv_store.set(key, self._append_id(val, mem_id))
        # Type-specific IDs
        tkey = self._kv_ids_key(user_id, scope_id, mem_type)
        tval = self._decode(await self._kv_store.get(tkey)) or ""
        if mem_id not in self._parse_all_ids(tval):
            await self._kv_store.set(tkey, self._append_id(tval, mem_id))

    async def _remove_id_from_tracking(
        self, user_id: str, scope_id: str, mem_id: str, mem_type: str | None,
    ) -> None:
        # Global IDs
        key = self._kv_ids_key(user_id, scope_id)
        val = self._decode(await self._kv_store.get(key)) or ""
        new_val = self._remove_id(val, mem_id)
        if new_val:
            await self._kv_store.set(key, new_val)
        else:
            await self._kv_store.delete(key)
        if not mem_type:
            return
        # Type-specific IDs
        tkey = self._kv_ids_key(user_id, scope_id, mem_type)
        tval = self._decode(await self._kv_store.get(tkey)) or ""
        new_tval = self._remove_id(tval, mem_id)
        if new_tval:
            await self._kv_store.set(tkey, new_tval)
        else:
            await self._kv_store.delete(tkey)

    # ------------------------------------------------------------------ #
    #  Data conversion                                                    #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _kv_data_to_memory_doc(data: dict[str, Any], mem_id: str) -> MemoryDoc:
        skip = {"id", "mem", "mem_type", "timestamp", "user_id", "scope_id"}
        extra = {k: v for k, v in data.items() if k not in skip}

        ts_raw = data.get("timestamp", "")
        if isinstance(ts_raw, datetime):
            timestamp = ts_raw
        elif isinstance(ts_raw, str) and ts_raw:
            timestamp = None
            for fmt in ("%Y-%m-%d %H-%M-%S", "%Y-%m-%d %H:%M:%S"):
                try:
                    timestamp = datetime.strptime(ts_raw, fmt).replace(tzinfo=timezone.utc)
                    break
                except ValueError:
                    continue
            if timestamp is None:
                try:
                    timestamp = datetime.fromisoformat(ts_raw)
                except ValueError:
                    timestamp = datetime.now(timezone.utc).astimezone()
        elif isinstance(ts_raw, (int, float)):
            timestamp = datetime.fromtimestamp(ts_raw, tz=timezone.utc)
        else:
            timestamp = datetime.now(timezone.utc).astimezone()

        return MemoryDoc(
            id=mem_id,
            text=data.get("mem", ""),
            type=data.get("mem_type", ""),
            timestamp=timestamp,
            fields=extra,
        )

    @staticmethod
    def _memory_doc_to_kv_data(doc: MemoryDoc, user_id: str, scope_id: str) -> dict[str, Any]:
        ts = doc.timestamp.strftime("%Y-%m-%d %H-%M-%S") if doc.timestamp else datetime.now(
            timezone.utc).astimezone().strftime("%Y-%m-%d %H-%M-%S")
        return {
            "id": doc.id,
            "user_id": user_id,
            "scope_id": scope_id,
            "mem": doc.text,
            "mem_type": doc.type,
            "timestamp": ts,
            **doc.fields,
        }

    # ------------------------------------------------------------------ #
    #  Vector helpers                                                     #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _get_collection_name(user_id: str, scope_id: str, mem_type: str) -> str:
        return f"uid_{user_id}_gid_{scope_id}_mtype_{mem_type}"

    @staticmethod
    def _parse_mem_type_from_collection(name: str) -> str | None:
        if "_mtype_" in name:
            return name.rsplit("_mtype_", 1)[-1]
        return None

    async def _ensure_collection(self, name: str, dim: int) -> None:
        if name in self._created_collections:
            return
        if await self._vector_store.collection_exists(name):
            self._created_collections.add(name)
            return
        schema = CollectionSchema(description="Semantic memory collection", enable_dynamic_field=False)
        schema.add_field(FieldSchema(name="id", dtype=VectorDataType.VARCHAR, max_length=256, is_primary=True))
        schema.add_field(FieldSchema(name="embedding", dtype=VectorDataType.FLOAT_VECTOR, dim=dim))
        await self._vector_store.create_collection(name, schema)
        self._created_collections.add(name)

    async def _collections_for(self, user_id: str, scope_id: str) -> list[str]:
        prefix = f"uid_{user_id}_gid_{scope_id}_mtype_"
        names = await self._vector_store.list_collection_names()
        return [n for n in names if n.startswith(prefix)]

    # ------------------------------------------------------------------ #
    #  BaseMemoryIndex implementation                                     #
    # ------------------------------------------------------------------ #

    async def add_memories(self, user_id: str, scope_id: str, memories: list[MemoryDoc]) -> None:
        """Add or update memory documents (writes to both KV and vector stores)."""
        if not memories:
            return

        by_type: dict[str, list[MemoryDoc]] = {}
        for m in memories:
            by_type.setdefault(m.type, []).append(m)

        for mem_type, docs in by_type.items():
            col = self._get_collection_name(user_id, scope_id, mem_type)
            texts = [d.text for d in docs]

            if self._embedding_model:
                embeddings = await self._embedding_model.embed_documents(texts)
            else:
                memory_logger.error(
                    "Embedding model not initialized.",
                    event_type=LogEventType.MEMORY_STORE,
                    scope_id=scope_id,
                    metadata={"collection": col},
                )
                return

            if embeddings:
                await self._ensure_collection(col, len(embeddings[0]))

            await self._vector_store.add_docs(
                col, [{"id": d.id, "embedding": e} for d, e in zip(docs, embeddings)]
            )

            for doc in docs:
                kv_key = self._kv_mem_key(user_id, scope_id, doc.id)
                await self._kv_store.set(
                    kv_key, json.dumps(self._memory_doc_to_kv_data(doc, user_id, scope_id))
                )
                await self._add_id_to_tracking(user_id, scope_id, doc.id, mem_type)

    async def search(
        self,
        user_id: str,
        scope_id: str,
        query: str,
        mem_type: str | None = None,
        top_k: int = 10,
    ) -> list[tuple[MemoryDoc, float]]:
        """Search memories via vector similarity, then fetch content from KV store."""
        if not self._embedding_model:
            memory_logger.error(
                "Embedding model not initialized.",
                event_type=LogEventType.MEMORY_RETRIEVE,
                scope_id=scope_id,
            )
            return []

        query_vec = await self._embedding_model.embed_query(query)

        if mem_type:
            types = [mem_type]
        else:
            cols = await self._collections_for(user_id, scope_id)
            types = [t for t in (self._parse_mem_type_from_collection(c) for c in cols) if t]

        results: list[tuple[MemoryDoc, float]] = []
        for mt in types:
            col = self._get_collection_name(user_id, scope_id, mt)
            if not await self._vector_store.collection_exists(col):
                continue

            hits = await self._vector_store.search(
                collection_name=col,
                query_vector=query_vec,
                vector_field="embedding",
                top_k=top_k,
            )

            hit_ids: list[str] = []
            scores: dict[str, float] = {}
            for h in hits:
                mid = h.fields.get("id", "")
                if mid:
                    hit_ids.append(mid)
                    scores[mid] = h.score

            if not hit_ids:
                continue

            keys = [self._kv_mem_key(user_id, scope_id, mid) for mid in hit_ids]
            values = await self._kv_store.mget(keys)

            for mid, raw in zip(hit_ids, values):
                decoded = self._decode(raw)
                if decoded is None:
                    continue
                data = json.loads(decoded)
                results.append((self._kv_data_to_memory_doc(data, mid), scores.get(mid, 0.0)))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    async def update_memories(self, user_id: str, scope_id: str, memories: list[MemoryDoc]) -> None:
        """Update memories by deleting old ones then adding new ones."""
        if not memories:
            return
        ids = [m.id for m in memories]
        await self.delete_memories(user_id, scope_id, ids)
        await self.add_memories(user_id, scope_id, memories)

    async def delete_memories(self, user_id: str, scope_id: str, ids: list[str]) -> None:
        """Delete memory documents from both KV and vector stores."""
        if not ids:
            return

        for mid in ids:
            kv_key = self._kv_mem_key(user_id, scope_id, mid)
            raw = self._decode(await self._kv_store.get(kv_key))

            mem_type = None
            if raw:
                data = json.loads(raw)
                mem_type = data.get("mem_type")

            await self._kv_store.delete(kv_key)
            await self._remove_id_from_tracking(user_id, scope_id, mid, mem_type)

        cols = await self._collections_for(user_id, scope_id)
        for col in cols:
            await self._vector_store.delete_docs_by_ids(col, ids)

    async def delete_by_user(self, user_id: str) -> None:
        """Delete all memories for a user across all scopes."""
        kv_prefix = f"{self._KV_PREFIX}{self._KV_SEP}{user_id}{self._KV_SEP}"
        await self._kv_store.delete_by_prefix(kv_prefix)

        all_cols = await self._vector_store.list_collection_names()
        marker = f"uid_{user_id}_gid_"
        for col in all_cols:
            if col.startswith(marker):
                await self._vector_store.delete_collection(col)
                self._created_collections.discard(col)

    async def delete_by_scope(self, scope_id: str) -> None:
        """Delete all memories for a scope across all users."""
        kv_prefix = f"{self._KV_PREFIX}{self._KV_SEP}"
        all_kv = await self._kv_store.get_by_prefix(kv_prefix)
        to_delete = []
        for key in all_kv:
            parts = key.split(self._KV_SEP)
            if len(parts) >= 3 and parts[2] == scope_id:
                to_delete.append(key)
        if to_delete:
            await self._kv_store.batch_delete(to_delete)

        scope_marker = f"_gid_{scope_id}_mtype_"
        for col in await self._vector_store.list_collection_names():
            if col.startswith("uid_") and scope_marker in col:
                await self._vector_store.delete_collection(col)
                self._created_collections.discard(col)

    async def delete_by_user_and_scope(self, user_id: str, scope_id: str) -> None:
        """Delete all memories for a specific user and scope."""
        kv_prefix = f"{self._KV_PREFIX}{self._KV_SEP}{user_id}{self._KV_SEP}{scope_id}{self._KV_SEP}"
        await self._kv_store.delete_by_prefix(kv_prefix)

        for col in await self._collections_for(user_id, scope_id):
            await self._vector_store.delete_collection(col)
            self._created_collections.discard(col)

    async def get_by_id(self, user_id: str, scope_id: str, mem_id: str) -> MemoryDoc | None:
        """Retrieve a single memory document by ID from the KV store."""
        raw = self._decode(await self._kv_store.get(self._kv_mem_key(user_id, scope_id, mem_id)))
        if raw is None:
            return None
        return self._kv_data_to_memory_doc(json.loads(raw), mem_id)

    async def list_memories(self, user_id: str, scope_id: str, offset: int, limit: int) -> list[MemoryDoc]:
        """List memory documents with pagination, reading from the KV store."""
        ids_key = self._kv_ids_key(user_id, scope_id)
        raw = self._decode(await self._kv_store.get(ids_key)) or ""
        if not raw:
            return []

        all_ids = self._parse_all_ids(raw)
        if not all_ids:
            return []

        keys = [self._kv_mem_key(user_id, scope_id, mid) for mid in all_ids]
        values = await self._kv_store.mget(keys)

        docs: list[MemoryDoc] = []
        for mid, val in zip(all_ids, values):
            decoded = self._decode(val)
            if decoded is None:
                continue
            docs.append(self._kv_data_to_memory_doc(json.loads(decoded), mid))

        docs.sort(key=lambda d: d.timestamp, reverse=True)
        return docs[offset:offset + limit]

    def get_schema_version(self) -> int:
        return self._schema_version

    def update_schema_version(self, version: int) -> None:
        self._schema_version = version

    async def create_backup(self) -> str:
        bid = str(uuid.uuid4())
        self._backups[bid] = {"schema_version": self._schema_version}
        return bid

    async def restore_backup(self, backup_id: str) -> None:
        if backup_id not in self._backups:
            raise ValueError(f"Backup {backup_id} not found")
        self._schema_version = self._backups[backup_id]["schema_version"]

    async def cleanup_backup(self, backup_id: str) -> None:
        self._backups.pop(backup_id, None)

    async def list_user_scopes(self) -> list[tuple[str, str]]:
        """List all (user_id, scope_id) pairs by scanning KV keys."""
        kv_prefix = f"{self._KV_PREFIX}{self._KV_SEP}"
        all_kv = await self._kv_store.get_by_prefix(kv_prefix)
        scopes: set[tuple[str, str]] = set()
        for key in all_kv:
            parts = key.split(self._KV_SEP)
            if len(parts) >= 3:
                scopes.add((parts[1], parts[2]))
        return list(scopes)
