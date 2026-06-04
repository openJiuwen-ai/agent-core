# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from unittest.mock import AsyncMock, Mock, patch
import pytest

from openjiuwen.core.common.security.crypt_utils import (
    AesGcmCrypt,
    CryptUtils,
)
from openjiuwen.core.common.utils.singleton import Singleton
from openjiuwen.core.foundation.store.base_kv_store import BaseKVStore
from openjiuwen.core.foundation.store.base_memory_index import MemoryDoc
from openjiuwen.core.foundation.store.index.simple_memory_index import SimpleMemoryIndex
from openjiuwen.core.memory.config.config import MemoryEngineConfig
from openjiuwen.core.memory.long_term_memory import LongTermMemory


_VALID_KEY = b"0123456789abcdef0123456789abcdef"


@pytest.fixture(autouse=True)
def _clean_global_state():
    Singleton._instances.pop(AesGcmCrypt, None)
    Singleton._instances.pop(LongTermMemory, None)
    CryptUtils._CRYPT_REGISTRY.clear()
    yield
    Singleton._instances.pop(AesGcmCrypt, None)
    Singleton._instances.pop(LongTermMemory, None)
    CryptUtils._CRYPT_REGISTRY.clear()


def _setup_minimal_ltm(ltm, crypto_key):
    from openjiuwen.core.foundation.store.kv.in_memory_kv_store import InMemoryKVStore
    kv = InMemoryKVStore()
    vs = Mock()
    vs.collection_exists = AsyncMock(return_value=False)
    vs.add_docs = AsyncMock()
    vs.list_collection_names = AsyncMock(return_value=[])
    vs.create_collection = AsyncMock()
    vs.delete_collection = AsyncMock()
    vs.search = AsyncMock(return_value=[])
    vs.delete_docs_by_ids = AsyncMock()
    emb = Mock()
    emb.dimension = 8
    emb.limiter = AsyncMock()
    emb.embed_documents = AsyncMock(return_value=[[0.1] * 8])
    emb.embed_query = AsyncMock(return_value=[0.1] * 8)

    ltm.kv_store = kv
    ltm.vector_store = vs
    ltm.db_store = AsyncMock()
    ltm.memory_index = SimpleMemoryIndex(kv_store=kv, vector_store=vs, embedding_model=emb)
    ltm._sys_mem_config = MemoryEngineConfig(crypto_key=crypto_key)

    from openjiuwen.core.memory.codec.aes_storage_codec import AesStorageCodec
    codec = AesStorageCodec(crypto_key)
    if crypto_key:
        crypt = AesGcmCrypt()
        CryptUtils.register_crypt(CryptUtils.AES_GCM_CRYPT_NAME, crypt)
    ltm.memory_index.set_storage_codec(codec)

    return ltm


class TestLongTermMemoryCodecInjection:
    def test_set_config_injects_codec(self):
        crypt = AesGcmCrypt()
        CryptUtils.register_crypt(CryptUtils.AES_GCM_CRYPT_NAME, crypt)
        ltm = LongTermMemory()
        _setup_minimal_ltm(ltm, _VALID_KEY)

        assert ltm.memory_index is not None
        assert ltm.memory_index._codec is not None

    def test_set_config_empty_key_codec_still_present(self):
        ltm = LongTermMemory()
        _setup_minimal_ltm(ltm, b"")

        assert ltm.memory_index is not None
        assert ltm.memory_index._codec is not None
        assert ltm.memory_index._codec.encode("hello") == "hello"
        assert ltm.memory_index._codec.decode("hello") == "hello"

    @pytest.mark.asyncio
    async def test_full_write_read_cycle(self):
        from datetime import datetime, timezone

        crypt = AesGcmCrypt()
        CryptUtils.register_crypt(CryptUtils.AES_GCM_CRYPT_NAME, crypt)
        ltm = LongTermMemory()
        ltm = _setup_minimal_ltm(ltm, _VALID_KEY)
        idx = ltm.memory_index

        assert idx._codec is not None

        doc = MemoryDoc(
            id="test_m1",
            text="encrypted memory content",
            type="user_profile",
            timestamp=datetime.now(timezone.utc).astimezone(),
        )
        await idx.add_memories("u1", "s1", [doc])

        result = await idx.get_by_id("u1", "s1", "test_m1")
        assert result is not None
        assert result.text == "encrypted memory content"

        raw_data = await ltm.kv_store.get_by_prefix("UMD")
        raw_val = list(raw_data.values())[0]
        decoded = raw_val.decode("utf-8") if isinstance(raw_val, bytes) else raw_val
        import json
        kv_json = json.loads(decoded)
        assert kv_json["mem"] != "encrypted memory content"
