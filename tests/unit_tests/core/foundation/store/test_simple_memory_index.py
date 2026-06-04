# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for SimpleMemoryIndex: old-framework compatibility and operations.

The phases tested are:
  1. Write data using the old framework (UserMemStore + SemanticStore).
  2. Operate on that data through SimpleMemoryIndex.
"""

import asyncio
import json
import math
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from openjiuwen.core.common.security.crypt_utils import (
    AesGcmCrypt,
    CryptUtils,
)
from openjiuwen.core.common.utils.singleton import Singleton
from openjiuwen.core.foundation.store.base_memory_index import MemoryDoc
from openjiuwen.core.foundation.store.base_vector_store import (
    BaseVectorStore,
    VectorSearchResult,
)
from openjiuwen.core.foundation.store.index.simple_memory_index import SimpleMemoryIndex
from openjiuwen.core.foundation.store.kv.in_memory_kv_store import InMemoryKVStore
from openjiuwen.core.memory.codec.aes_storage_codec import AesStorageCodec
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


@pytest.fixture(autouse=True)
def _clean_crypt():
    Singleton._instances.pop(AesGcmCrypt, None)
    CryptUtils._CRYPT_REGISTRY.clear()
    yield
    Singleton._instances.pop(AesGcmCrypt, None)
    CryptUtils._CRYPT_REGISTRY.clear()


@pytest.fixture
def crypto_key():
    return b"0123456789abcdef0123456789abcdef"


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

        results = await idx.search(_UID, _SID, "programming", mem_types=[_MEM_TYPE], top_k=5)
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
#  Phase 3: SimpleMemoryIndex with AesStorageCodec
# ===========================================================================

_CODEC_UID = "codec_user"
_CODEC_SID = "codec_scope"
_CODEC_TYPE = "user_profile"


def _register_codec(codec_key):
    crypt = AesGcmCrypt()
    CryptUtils.register_crypt(CryptUtils.AES_GCM_CRYPT_NAME, crypt)
    return AesStorageCodec(codec_key)


class TestSimpleMemoryIndexWithCodec:
    """Verify SimpleMemoryIndex encrypts/decrypts ``text`` field via codec."""

    @staticmethod
    @pytest.mark.asyncio
    async def test_add_then_search_with_codec(kv, vec, emb, crypto_key):
        codec = _register_codec(crypto_key)
        idx = SimpleMemoryIndex(kv, vec, emb)
        idx.set_storage_codec(codec)

        doc = MemoryDoc(id="m1", text="sensitive data", type=_CODEC_TYPE,
                        timestamp=datetime.now(timezone.utc).astimezone())
        await idx.add_memories(_CODEC_UID, _CODEC_SID, [doc])

        results = await idx.search(_CODEC_UID, _CODEC_SID, "sensitive", top_k=1)
        assert len(results) == 1
        assert results[0][0].text == "sensitive data"

    @staticmethod
    @pytest.mark.asyncio
    async def test_add_then_get_by_id_with_codec(kv, vec, emb, crypto_key):
        codec = _register_codec(crypto_key)
        idx = SimpleMemoryIndex(kv, vec, emb)
        idx.set_storage_codec(codec)

        doc = MemoryDoc(id="m1", text="confidential", type=_CODEC_TYPE,
                        timestamp=datetime.now(timezone.utc).astimezone())
        await idx.add_memories(_CODEC_UID, _CODEC_SID, [doc])

        result = await idx.get_by_id(_CODEC_UID, _CODEC_SID, "m1")
        assert result is not None
        assert result.text == "confidential"

    @staticmethod
    @pytest.mark.asyncio
    async def test_add_then_list_memories_with_codec(kv, vec, emb, crypto_key):
        codec = _register_codec(crypto_key)
        idx = SimpleMemoryIndex(kv, vec, emb)
        idx.set_storage_codec(codec)

        ids = [f"test{i:022d}" for i in range(3)]
        ts = datetime.now(timezone.utc).astimezone()
        docs = [
            MemoryDoc(id=ids[i], text=f"data_{i}", type=_CODEC_TYPE, timestamp=ts)
            for i in range(3)
        ]
        await idx.add_memories(_CODEC_UID, _CODEC_SID, docs)

        for i in range(3):
            doc = await idx.get_by_id(_CODEC_UID, _CODEC_SID, ids[i])
            assert doc is not None
            assert doc.text == f"data_{i}"

    @staticmethod
    @pytest.mark.asyncio
    async def test_kv_stores_encrypted_with_codec(kv, vec, emb, crypto_key):
        codec = _register_codec(crypto_key)
        idx = SimpleMemoryIndex(kv, vec, emb)
        idx.set_storage_codec(codec)

        plaintext = "top secret"
        doc = MemoryDoc(id="m1", text=plaintext, type=_CODEC_TYPE,
                        timestamp=datetime.now(timezone.utc).astimezone())
        await idx.add_memories(_CODEC_UID, _CODEC_SID, [doc])

        raw_data = await kv.get_by_prefix("UMD")
        raw_bytes = None
        for raw in raw_data.values():
            raw_bytes = raw
            break
        assert raw_bytes is not None
        decoded = raw_bytes.decode("utf-8") if isinstance(raw_bytes, bytes) else raw_bytes
        kv_json = json.loads(decoded)
        assert kv_json["mem"] != plaintext

    @staticmethod
    @pytest.mark.asyncio
    async def test_other_fields_not_encrypted(kv, vec, emb, crypto_key):
        codec = _register_codec(crypto_key)
        idx = SimpleMemoryIndex(kv, vec, emb)
        idx.set_storage_codec(codec)

        doc = MemoryDoc(id="m1", text="secret", type=_CODEC_TYPE,
                        timestamp=datetime.now(timezone.utc).astimezone(),
                        fields={"source_id": "src_1"})
        await idx.add_memories(_CODEC_UID, _CODEC_SID, [doc])

        raw_data = await kv.get_by_prefix("UMD")
        raw_bytes = None
        for raw in raw_data.values():
            raw_bytes = raw
            break
        decoded = raw_bytes.decode("utf-8") if isinstance(raw_bytes, bytes) else raw_bytes
        kv_json = json.loads(decoded)
        assert kv_json["id"] == "m1"
        assert kv_json["mem_type"] == _CODEC_TYPE

    @staticmethod
    @pytest.mark.asyncio
    async def test_id_tracking_plaintext(kv, vec, emb, crypto_key):
        codec = _register_codec(crypto_key)
        idx = SimpleMemoryIndex(kv, vec, emb)
        idx.set_storage_codec(codec)

        doc = MemoryDoc(id="m1", text="data", type=_CODEC_TYPE,
                        timestamp=datetime.now(timezone.utc).astimezone())
        await idx.add_memories(_CODEC_UID, _CODEC_SID, [doc])

        ids_raw = await kv.get(f"UMD/{_CODEC_UID}/{_CODEC_SID}/ids")
        assert ids_raw is not None

    @staticmethod
    @pytest.mark.asyncio
    async def test_without_codec_plaintext(kv, vec, emb):
        idx = SimpleMemoryIndex(kv, vec, emb)

        doc = MemoryDoc(id="m1", text="plain data", type=_CODEC_TYPE,
                        timestamp=datetime.now(timezone.utc).astimezone())
        await idx.add_memories(_CODEC_UID, _CODEC_SID, [doc])

        raw_data = await kv.get_by_prefix("UMD")
        raw_bytes = None
        for raw in raw_data.values():
            raw_bytes = raw
            break
        decoded = raw_bytes.decode("utf-8") if isinstance(raw_bytes, bytes) else raw_bytes
        kv_json = json.loads(decoded)
        assert kv_json["mem"] == "plain data"

    @staticmethod
    @pytest.mark.asyncio
    async def test_search_without_codec_still_works(kv, vec, emb):
        idx = SimpleMemoryIndex(kv, vec, emb)

        doc = MemoryDoc(id="m1", text="open data", type=_CODEC_TYPE,
                        timestamp=datetime.now(timezone.utc).astimezone())
        await idx.add_memories(_CODEC_UID, _CODEC_SID, [doc])

        results = await idx.search(_CODEC_UID, _CODEC_SID, "open", top_k=1)
        assert len(results) == 1
        assert results[0][0].text == "open data"

    @staticmethod
    @pytest.mark.asyncio
    async def test_update_memories_with_codec(kv, vec, emb, crypto_key):
        codec = _register_codec(crypto_key)
        idx = SimpleMemoryIndex(kv, vec, emb)
        idx.set_storage_codec(codec)

        doc = MemoryDoc(id="m1", text="old text", type=_CODEC_TYPE,
                        timestamp=datetime.now(timezone.utc).astimezone())
        await idx.add_memories(_CODEC_UID, _CODEC_SID, [doc])

        updated = MemoryDoc(id="m1", text="new text", type=_CODEC_TYPE,
                            timestamp=datetime.now(timezone.utc).astimezone())
        await idx.update_memories(_CODEC_UID, _CODEC_SID, [updated])

        result = await idx.get_by_id(_CODEC_UID, _CODEC_SID, "m1")
        assert result.text == "new text"

    @staticmethod
    @pytest.mark.asyncio
    async def test_delete_memories_with_codec(kv, vec, emb, crypto_key):
        codec = _register_codec(crypto_key)
        idx = SimpleMemoryIndex(kv, vec, emb)
        idx.set_storage_codec(codec)

        doc = MemoryDoc(id="m1", text="to delete", type=_CODEC_TYPE,
                        timestamp=datetime.now(timezone.utc).astimezone())
        await idx.add_memories(_CODEC_UID, _CODEC_SID, [doc])
        await idx.delete_memories(_CODEC_UID, _CODEC_SID, ["m1"])

        result = await idx.get_by_id(_CODEC_UID, _CODEC_SID, "m1")
        assert result is None

    @staticmethod
    @pytest.mark.asyncio
    async def test_delete_memories_extracts_mem_type_from_encrypted(kv, vec, emb, crypto_key):
        codec = _register_codec(crypto_key)
        idx = SimpleMemoryIndex(kv, vec, emb)
        idx.set_storage_codec(codec)

        doc = MemoryDoc(id="m1", text="delete me", type=_CODEC_TYPE,
                        timestamp=datetime.now(timezone.utc).astimezone())
        await idx.add_memories(_CODEC_UID, _CODEC_SID, [doc])

        await idx.delete_memories(_CODEC_UID, _CODEC_SID, ["m1"])
        result = await idx.get_by_id(_CODEC_UID, _CODEC_SID, "m1")
        assert result is None

    @staticmethod
    @pytest.mark.asyncio
    async def test_codec_decode_failure_fallback(kv, vec, emb, crypto_key):
        crypt = AesGcmCrypt()
        CryptUtils.register_crypt(CryptUtils.AES_GCM_CRYPT_NAME, crypt)
        codec = AesStorageCodec(crypto_key)
        idx = SimpleMemoryIndex(kv, vec, emb)
        idx.set_storage_codec(codec)

        doc = MemoryDoc(id="m1", text="legacy plain", type=_CODEC_TYPE,
                        timestamp=datetime.now(timezone.utc).astimezone())
        await idx.add_memories(_CODEC_UID, _CODEC_SID, [doc])

        original_decrypt = crypt.decrypt
        crypt.decrypt = MagicMock(side_effect=RuntimeError("decrypt failure"))
        try:
            result = await idx.search(_CODEC_UID, _CODEC_SID, "legacy", top_k=1)
            assert len(result) == 1
        finally:
            crypt.decrypt = original_decrypt


if __name__ == "__main__":
    pytest.main([__file__])
