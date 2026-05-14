# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for SimpleMemoryIndex: old-framework compatibility and migration.

The three phases tested are:
  1. Write data using the old framework (UserMemStore + SemanticStore).
  2. Operate on that data through SimpleMemoryIndex.
  3. Migrate data from SimpleMemoryIndex into VectorMemoryIndex.
"""

import asyncio
import math
from datetime import datetime, timezone

import pytest

from openjiuwen.core.foundation.store.base_memory_index import MemoryDoc
from openjiuwen.core.foundation.store.base_db_store import BaseDbStore
from openjiuwen.core.foundation.store.base_vector_store import (
    BaseVectorStore,
    VectorSearchResult,
)
from openjiuwen.core.foundation.store.index.simple_memory_index import SimpleMemoryIndex
from openjiuwen.core.foundation.store.index.vector_memory_index import VectorMemoryIndex
from openjiuwen.core.foundation.store.kv.in_memory_kv_store import InMemoryKVStore
from openjiuwen.core.memory.long_term_memory import LongTermMemory
from openjiuwen.core.memory.common.base import generate_idx_name
from openjiuwen.core.memory.manage.mem_model.semantic_store import SemanticStore
from openjiuwen.core.memory.manage.mem_model.user_mem_store import UserMemStore


# ---------------------------------------------------------------------------
#  Test helpers
# ---------------------------------------------------------------------------

_UID = "test_user"
_SID = "test_scope"
_DIM = 8


def _make_id(n: int) -> str:
    """Generate a 24-char zero-padded ID (matches old DataIdManager format)."""
    return f"{n:024d}"


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


class FakeEmbedding:
    """Deterministic embedding used across all tests."""

    def __init__(self, dim: int = _DIM):
        self._dim = dim
        self.limiter = asyncio.Semaphore(10)

    async def embed_query(self, text: str, **_kw) -> list[float]:
        return self._embed(text)

    async def embed_documents(self, texts: list[str], **_kw) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    @property
    def dimension(self) -> int:
        return self._dim

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self._dim
        for i, ch in enumerate(text):
            vec[i % self._dim] += ord(ch) * 0.01
        norm = math.sqrt(sum(v * v for v in vec))
        return [v / norm for v in vec] if norm else vec


class _MemVectorStore(BaseVectorStore):
    """Minimal in-memory vector store with cosine-similarity search."""

    def __init__(self):
        self._cols: dict[str, dict] = {}        # name -> metadata
        self._docs: dict[str, list[dict]] = {}   # name -> [doc, ...]

    async def create_collection(self, collection_name, schema, **_kw):
        self._cols[collection_name] = {}
        self._docs.setdefault(collection_name, [])

    async def delete_collection(self, collection_name, **_kw):
        self._cols.pop(collection_name, None)
        self._docs.pop(collection_name, None)

    async def collection_exists(self, collection_name, **_kw) -> bool:
        return collection_name in self._cols

    async def get_schema(self, collection_name, **_kw):
        return None

    async def add_docs(self, collection_name, docs, **_kw):
        bucket = self._docs.setdefault(collection_name, [])
        for doc in docs:
            idx = next(
                (i for i, d in enumerate(bucket) if d.get("id") == doc.get("id")),
                None,
            )
            if idx is not None:
                bucket[idx] = doc
            else:
                bucket.append(doc)

    async def search(
        self, collection_name, query_vector, vector_field, top_k=5,
        filters=None, output_fields=None, **_kw,
    ):
        bucket = self._docs.get(collection_name, [])
        results: list[VectorSearchResult] = []
        for doc in bucket:
            if filters and not all(doc.get(k) == v for k, v in filters.items()):
                continue
            score = _cosine(query_vector, doc.get(vector_field, []))
            fields = {k: v for k, v in doc.items() if k != vector_field}
            results.append(VectorSearchResult(score=score, fields=fields))
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]

    async def delete_docs_by_ids(self, collection_name, ids, **_kw):
        if collection_name in self._docs:
            self._docs[collection_name] = [d for d in self._docs[collection_name] if d.get("id") not in ids]

    async def delete_docs_by_filters(self, collection_name, filters, **_kw):
        if collection_name in self._docs:
            self._docs[collection_name] = [
                d for d in self._docs[collection_name]
                if not all(d.get(k) == v for k, v in filters.items())
            ]

    async def list_collection_names(self) -> list[str]:
        return list(self._cols)

    async def get_collection_metadata(self, collection_name) -> dict:
        return self._cols.get(collection_name, {})

    async def update_collection_metadata(self, collection_name, metadata):
        if collection_name in self._cols:
            self._cols[collection_name].update(metadata)

    async def update_schema(self, collection_name, operations):
        pass


# ---------------------------------------------------------------------------
#  Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def kv():
    return InMemoryKVStore()


@pytest.fixture()
def vec():
    return _MemVectorStore()


@pytest.fixture()
def emb():
    return FakeEmbedding()


# ---------------------------------------------------------------------------
#  Old-framework write helper
# ---------------------------------------------------------------------------

_OLD_RECORDS = [
    (_make_id(1), "Alice likes Python"),
    (_make_id(2), "Bob prefers Go"),
    (_make_id(3), "Charlie works on AI"),
]
_MEM_TYPE = "user_profile"


async def _write_via_old_framework(kv_store, vec_store, emb):
    """Write test data using the old SemanticStore + UserMemStore path."""
    user_mem = UserMemStore(kv_store)
    sem_store = SemanticStore(vec_store, emb)
    table = generate_idx_name(_UID, _SID, _MEM_TYPE)
    ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")

    for mem_id, text in _OLD_RECORDS:
        data = {
            "id": mem_id,
            "user_id": _UID,
            "scope_id": _SID,
            "mem": text,
            "source_id": "",
            "mem_type": _MEM_TYPE,
            "timestamp": ts,
        }
        await user_mem.write(_UID, _SID, mem_id, data)
        await sem_store.add_docs([(mem_id, text)], table, scope_id=_SID)


# ===========================================================================
#  Phase 1+2: old framework writes  →  SimpleMemoryIndex reads
# ===========================================================================


class TestSimpleMemoryIndexReadsOldData:
    """Verify SimpleMemoryIndex can read data written by the old framework."""

    @staticmethod
    @pytest.mark.asyncio
    async def test_get_by_id(kv, vec, emb):
        await _write_via_old_framework(kv, vec, emb)
        idx = SimpleMemoryIndex(kv, vec, emb)

        doc = await idx.get_by_id(_UID, _SID, _make_id(1))
        assert doc is not None
        assert doc.text == "Alice likes Python"
        assert doc.type == _MEM_TYPE
        assert doc.timestamp != ""

    @staticmethod
    @pytest.mark.asyncio
    async def test_get_by_id_not_found(kv, vec, emb):
        await _write_via_old_framework(kv, vec, emb)
        idx = SimpleMemoryIndex(kv, vec, emb)

        assert await idx.get_by_id(_UID, _SID, _make_id(99)) is None

    @staticmethod
    @pytest.mark.asyncio
    async def test_search_finds_relevant(kv, vec, emb):
        await _write_via_old_framework(kv, vec, emb)
        idx = SimpleMemoryIndex(kv, vec, emb)

        results = await idx.search(_UID, _SID, "Alice likes Python", top_k=3)
        assert len(results) >= 1
        top_doc, top_score = results[0]
        assert "Alice" in top_doc.text
        assert top_score > 0

    @staticmethod
    @pytest.mark.asyncio
    async def test_search_with_mem_type_filter(kv, vec, emb):
        await _write_via_old_framework(kv, vec, emb)
        idx = SimpleMemoryIndex(kv, vec, emb)

        results = await idx.search(_UID, _SID, "programming", mem_type=_MEM_TYPE, top_k=5)
        assert len(results) >= 1
        for doc, _ in results:
            assert doc.type == _MEM_TYPE

    @staticmethod
    @pytest.mark.asyncio
    async def test_search_no_type_discovers_collections(kv, vec, emb):
        await _write_via_old_framework(kv, vec, emb)
        idx = SimpleMemoryIndex(kv, vec, emb)

        # mem_type=None → auto-discover from existing collections
        results = await idx.search(_UID, _SID, "AI", top_k=5)
        assert len(results) >= 1
        texts = {doc.text for doc, _ in results}
        assert texts == {"Alice likes Python", "Bob prefers Go", "Charlie works on AI"}

    @staticmethod
    @pytest.mark.asyncio
    async def test_list_memories_returns_all(kv, vec, emb):
        await _write_via_old_framework(kv, vec, emb)
        idx = SimpleMemoryIndex(kv, vec, emb)

        docs = await idx.list_memories(_UID, _SID, 0, 100)
        assert len(docs) == 3
        texts = {d.text for d in docs}
        assert "Alice likes Python" in texts
        assert "Bob prefers Go" in texts
        assert "Charlie works on AI" in texts

    @staticmethod
    @pytest.mark.asyncio
    async def test_list_memories_pagination(kv, vec, emb):
        await _write_via_old_framework(kv, vec, emb)
        idx = SimpleMemoryIndex(kv, vec, emb)

        page1 = await idx.list_memories(_UID, _SID, 0, 2)
        page2 = await idx.list_memories(_UID, _SID, 2, 2)
        assert len(page1) == 2
        assert len(page2) == 1
        # No overlap
        ids_p1 = {d.id for d in page1}
        ids_p2 = {d.id for d in page2}
        assert ids_p1.isdisjoint(ids_p2)

    @staticmethod
    @pytest.mark.asyncio
    async def test_list_user_scopes(kv, vec, emb):
        await _write_via_old_framework(kv, vec, emb)
        idx = SimpleMemoryIndex(kv, vec, emb)

        scopes = await idx.list_user_scopes()
        assert (_UID, _SID) in scopes

    @staticmethod
    @pytest.mark.asyncio
    async def test_timestamp_string_to_float_conversion(kv, vec, emb):
        """Old KV stores timestamp as string, read back as datetime."""
        await _write_via_old_framework(kv, vec, emb)
        idx = SimpleMemoryIndex(kv, vec, emb)

        doc = await idx.get_by_id(_UID, _SID, _make_id(1))
        assert isinstance(doc.timestamp, datetime)
        assert doc.timestamp.tzinfo is not None


# ===========================================================================
#  Phase 2 continued: write / delete through SimpleMemoryIndex
# ===========================================================================


class TestSimpleMemoryIndexWriteOperations:
    """Verify SimpleMemoryIndex can add and delete old-format data."""

    @staticmethod
    @pytest.mark.asyncio
    async def test_add_new_memory(kv, vec, emb):
        await _write_via_old_framework(kv, vec, emb)
        idx = SimpleMemoryIndex(kv, vec, emb)

        new_doc = MemoryDoc(
            id=_make_id(4),
            text="Diana studies Rust",
            type=_MEM_TYPE,
            timestamp=datetime.now(timezone.utc).astimezone(),
            fields={"source_id": "msg_4"},
        )
        await idx.add_memories(_UID, _SID, [new_doc])

        doc = await idx.get_by_id(_UID, _SID, _make_id(4))
        assert doc is not None
        assert doc.text == "Diana studies Rust"
        assert doc.fields.get("source_id") == "msg_4"

    @staticmethod
    @pytest.mark.asyncio
    async def test_add_does_not_corrupt_existing(kv, vec, emb):
        await _write_via_old_framework(kv, vec, emb)
        idx = SimpleMemoryIndex(kv, vec, emb)

        await idx.add_memories(_UID, _SID, [MemoryDoc(
            id=_make_id(4), text="New", type=_MEM_TYPE,
            timestamp=datetime.now(timezone.utc).astimezone(),
        )])

        old = await idx.get_by_id(_UID, _SID, _make_id(1))
        assert old is not None
        assert old.text == "Alice likes Python"

    @staticmethod
    @pytest.mark.asyncio
    async def test_delete_single_memory(kv, vec, emb):
        await _write_via_old_framework(kv, vec, emb)
        idx = SimpleMemoryIndex(kv, vec, emb)

        await idx.delete_memories(_UID, _SID, [_make_id(1)])

        assert await idx.get_by_id(_UID, _SID, _make_id(1)) is None
        remaining = await idx.list_memories(_UID, _SID, 0, 100)
        assert len(remaining) == 2
        assert all(d.id != _make_id(1) for d in remaining)

    @staticmethod
    @pytest.mark.asyncio
    async def test_delete_multiple_memories(kv, vec, emb):
        await _write_via_old_framework(kv, vec, emb)
        idx = SimpleMemoryIndex(kv, vec, emb)

        await idx.delete_memories(_UID, _SID, [_make_id(1), _make_id(2)])

        remaining = await idx.list_memories(_UID, _SID, 0, 100)
        assert len(remaining) == 1
        assert remaining[0].text == "Charlie works on AI"

    @staticmethod
    @pytest.mark.asyncio
    async def test_delete_by_user_and_scope(kv, vec, emb):
        await _write_via_old_framework(kv, vec, emb)
        idx = SimpleMemoryIndex(kv, vec, emb)

        await idx.delete_by_user_and_scope(_UID, _SID)

        assert await idx.list_memories(_UID, _SID, 0, 100) == []
        assert await idx.get_by_id(_UID, _SID, _make_id(1)) is None

    @staticmethod
    @pytest.mark.asyncio
    async def test_delete_by_user(kv, vec, emb):
        await _write_via_old_framework(kv, vec, emb)
        idx = SimpleMemoryIndex(kv, vec, emb)

        await idx.delete_by_user(_UID)

        assert await idx.list_memories(_UID, _SID, 0, 100) == []
        # Vector collections should be gone
        cols = await vec.list_collection_names()
        assert not any(_UID in c for c in cols)

    @staticmethod
    @pytest.mark.asyncio
    async def test_delete_by_scope(kv, vec, emb):
        await _write_via_old_framework(kv, vec, emb)
        idx = SimpleMemoryIndex(kv, vec, emb)

        await idx.delete_by_scope(_SID)

        assert await idx.list_memories(_UID, _SID, 0, 100) == []
        cols = await vec.list_collection_names()
        assert not any(_SID in c for c in cols)

    @staticmethod
    @pytest.mark.asyncio
    async def test_upsert_via_delete_then_add(kv, vec, emb):
        """Updating a doc with an existing ID requires explicit delete then add."""
        await _write_via_old_framework(kv, vec, emb)
        idx = SimpleMemoryIndex(kv, vec, emb)

        updated = MemoryDoc(
            id=_make_id(1),
            text="Alice now prefers Rust",
            type=_MEM_TYPE,
            timestamp=datetime.now(timezone.utc).astimezone(),
        )
        await idx.delete_memories(_UID, _SID, [_make_id(1)])
        await idx.add_memories(_UID, _SID, [updated])

        doc = await idx.get_by_id(_UID, _SID, _make_id(1))
        assert doc.text == "Alice now prefers Rust"

        # Total count stays the same
        all_docs = await idx.list_memories(_UID, _SID, 0, 100)
        assert len(all_docs) == 3


# ===========================================================================
#  Phase 3: migrate SimpleMemoryIndex → VectorMemoryIndex
# ===========================================================================


class TestMigrationToVectorMemoryIndex:
    """Read all data via SimpleMemoryIndex, then write to VectorMemoryIndex."""

    @staticmethod
    @pytest.mark.asyncio
    async def test_migration_round_trip(kv, vec, emb):
        # 1. Write old data
        await _write_via_old_framework(kv, vec, emb)
        simple = SimpleMemoryIndex(kv, vec, emb)

        # 2. Read everything through SimpleMemoryIndex
        old_docs = await simple.list_memories(_UID, _SID, 0, 100)
        assert len(old_docs) == 3

        # 3. Write to a fresh VectorMemoryIndex (separate vec_store to avoid name clash)
        new_vec = _MemVectorStore()
        new_idx = VectorMemoryIndex(new_vec, emb)
        await new_idx.add_memories(_UID, _SID, old_docs)

        # 4. Verify search works in the new index
        results = await new_idx.search(
            _UID, _SID, "Alice likes Python", mem_type=_MEM_TYPE, top_k=3,
        )
        assert len(results) >= 1
        assert "Alice" in results[0][0].text

        # 5. Verify get_by_id works
        doc = await new_idx.get_by_id(_UID, _SID, _make_id(1))
        assert doc is not None
        assert doc.text == "Alice likes Python"

        # 6. Verify list_memories returns all migrated docs
        migrated = await new_idx.list_memories(_UID, _SID, 0, 100)
        assert len(migrated) == 3
        migrated_texts = {d.text for d in migrated}
        assert migrated_texts == {"Alice likes Python", "Bob prefers Go", "Charlie works on AI"}

    @staticmethod
    @pytest.mark.asyncio
    async def test_migration_preserves_type_and_timestamp(kv, vec, emb):
        await _write_via_old_framework(kv, vec, emb)
        simple = SimpleMemoryIndex(kv, vec, emb)

        old_docs = await simple.list_memories(_UID, _SID, 0, 100)

        new_vec = _MemVectorStore()
        new_idx = VectorMemoryIndex(new_vec, emb)
        await new_idx.add_memories(_UID, _SID, old_docs)

        for old_doc in old_docs:
            new_doc = await new_idx.get_by_id(_UID, _SID, old_doc.id)
            assert new_doc is not None
            assert new_doc.type == old_doc.type
            assert new_doc.timestamp == old_doc.timestamp

    @staticmethod
    @pytest.mark.asyncio
    async def test_migration_preserves_extra_fields(kv, vec, emb):
        """Fields like source_id survive the migration path."""
        await _write_via_old_framework(kv, vec, emb)
        simple = SimpleMemoryIndex(kv, vec, emb)

        old_docs = await simple.list_memories(_UID, _SID, 0, 100)

        new_vec = _MemVectorStore()
        new_idx = VectorMemoryIndex(new_vec, emb)
        await new_idx.add_memories(_UID, _SID, old_docs)

        doc = await new_idx.get_by_id(_UID, _SID, _make_id(1))
        assert doc is not None
        assert "source_id" in doc.fields

    @staticmethod
    @pytest.mark.asyncio
    async def test_migration_then_delete_in_new_index(kv, vec, emb):
        """After migration, new index supports full CRUD."""
        await _write_via_old_framework(kv, vec, emb)
        simple = SimpleMemoryIndex(kv, vec, emb)

        old_docs = await simple.list_memories(_UID, _SID, 0, 100)

        new_vec = _MemVectorStore()
        new_idx = VectorMemoryIndex(new_vec, emb)
        await new_idx.add_memories(_UID, _SID, old_docs)

        # Delete one doc in the new index
        await new_idx.delete_memories(_UID, _SID, [_make_id(2)])

        remaining = await new_idx.list_memories(_UID, _SID, 0, 100)
        assert len(remaining) == 2
        remaining_ids = {d.id for d in remaining}
        assert _make_id(2) not in remaining_ids

    @staticmethod
    @pytest.mark.asyncio
    async def test_old_and_new_coexist(kv, vec, emb):
        """Old SimpleMemoryIndex and new VectorMemoryIndex can coexist on
        different collection namespaces in the same vector store."""
        await _write_via_old_framework(kv, vec, emb)
        simple = SimpleMemoryIndex(kv, vec, emb)

        old_docs = await simple.list_memories(_UID, _SID, 0, 100)

        # Reuse the same vec_store — namespace difference prevents collision
        new_idx = VectorMemoryIndex(vec, emb)
        await new_idx.add_memories(_UID, _SID, old_docs)

        # Old collections still exist
        old_cols = [c for c in await vec.list_collection_names()
                    if c.startswith(f"uid_{_UID}_gid_{_SID}_")]
        assert len(old_cols) >= 1

        # New collections also exist
        new_cols = [c for c in await vec.list_collection_names()
                    if c.startswith(f"memory_{_UID}_{_SID}_")]
        assert len(new_cols) >= 1

        # Both can search independently
        old_results = await simple.search(_UID, _SID, "Python", top_k=3)
        new_results = await new_idx.search(
            _UID, _SID, "Python", mem_type=_MEM_TYPE, top_k=3,
        )
        assert len(old_results) >= 1
        assert len(new_results) >= 1


# ===========================================================================
#  Phase 4: LongTermMemory.register_store → SimpleMemoryIndex → migrate_from_index
# ===========================================================================


class _MockDbStore(BaseDbStore):
    """Minimal db_store mock satisfying register_store / create_tables."""

    def get_async_engine(self):
        from unittest.mock import AsyncMock, MagicMock
        mock_engine = MagicMock()
        mock_engine.begin = MagicMock(return_value=AsyncMock())
        return mock_engine


class TestMigrateFromIndexViaLongTermMemory:
    """End-to-end: register_store creates SimpleMemoryIndex, then
    migrate_from_index copies data to a fresh VectorMemoryIndex."""

    @staticmethod
    @pytest.mark.asyncio
    async def test_migrate_simple_to_vector_after_register_store():
        # -- 1. Set up stores ------------------------------------------------
        kv_store = InMemoryKVStore()
        vec_store = _MemVectorStore()
        embedding = FakeEmbedding()
        db_store = _MockDbStore()

        # -- 2. LongTermMemory.register_store auto-creates SimpleMemoryIndex -
        ltm = LongTermMemory()
        await ltm.register_store(
            kv_store=kv_store,
            vector_store=vec_store,
            db_store=db_store,
            embedding_model=embedding,
        )
        assert isinstance(ltm.memory_index, SimpleMemoryIndex)

        # -- 3. Write data directly to the SimpleMemoryIndex -----------------
        docs = [
            MemoryDoc(
                id=_make_id(1), text="Alice likes Python", type="user_profile",
                timestamp=datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc), fields={"source_id": "msg_1"},
            ),
            MemoryDoc(
                id=_make_id(2), text="Bob prefers Go", type="user_profile",
                timestamp=datetime(2025, 1, 1, 0, 0, 1, tzinfo=timezone.utc), fields={"source_id": "msg_2"},
            ),
            MemoryDoc(
                id=_make_id(3), text="Charlie works on AI", type="semantic_memory",
                timestamp=datetime(2025, 1, 1, 0, 0, 2, tzinfo=timezone.utc), fields={"source_id": "msg_3"},
            ),
        ]
        await ltm.memory_index.add_memories(_UID, _SID, docs)

        # Verify data landed in SimpleMemoryIndex
        source_docs = await ltm.memory_index.list_memories(_UID, _SID, 0, 100)
        assert len(source_docs) == 3

        # -- 4. Create a fresh VectorMemoryIndex ------------------------------
        new_vec = _MemVectorStore()
        vector_index = VectorMemoryIndex(new_vec, embedding)

        # -- 5. Migrate via LongTermMemory.migrate_from_index ----------------
        await LongTermMemory.migrate_between_indices(
            source_index=ltm.memory_index,
            target_index=vector_index,
        )

        # -- 6. Verify data in VectorMemoryIndex ------------------------------
        # 6a. get_by_id
        doc = await vector_index.get_by_id(_UID, _SID, _make_id(1))
        assert doc is not None
        assert doc.text == "Alice likes Python"
        assert doc.type == "user_profile"

        # 6b. list_memories
        all_docs = await vector_index.list_memories(_UID, _SID, 0, 100)
        assert len(all_docs) == 3
        texts = {d.text for d in all_docs}
        assert texts == {"Alice likes Python", "Bob prefers Go", "Charlie works on AI"}

        # 6c. search
        results = await vector_index.search(
            _UID, _SID, "Python programming",
            mem_type="user_profile", top_k=3,
        )
        assert len(results) >= 1
        assert "Alice" in results[0][0].text

        # -- 7. Source data is preserved --------------------------------------
        source_after = await ltm.memory_index.list_memories(_UID, _SID, 0, 100)
        assert len(source_after) == 3

    @staticmethod
    @pytest.mark.asyncio
    async def test_migrate_preserves_fields_and_timestamps():
        kv_store = InMemoryKVStore()
        vec_store = _MemVectorStore()
        embedding = FakeEmbedding()
        db_store = _MockDbStore()

        ltm = LongTermMemory()
        await ltm.register_store(
            kv_store=kv_store, vector_store=vec_store,
            db_store=db_store, embedding_model=embedding,
        )

        original = MemoryDoc(
            id=_make_id(10), text="Test with fields", type="user_profile",
            timestamp=datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
            fields={"source_id": "msg_10", "confidence": 0.95},
        )
        await ltm.memory_index.add_memories(_UID, _SID, [original])

        new_vec = _MemVectorStore()
        target = VectorMemoryIndex(new_vec, embedding)
        await LongTermMemory.migrate_between_indices(
            source_index=ltm.memory_index, target_index=target,
        )

        migrated = await target.get_by_id(_UID, _SID, _make_id(10))
        assert migrated is not None
        assert migrated.text == original.text
        assert migrated.type == original.type
        assert migrated.timestamp == original.timestamp
        assert migrated.fields.get("source_id") == "msg_10"
        assert migrated.fields.get("confidence") == 0.95


if __name__ == "__main__":
    pytest.main([__file__])
