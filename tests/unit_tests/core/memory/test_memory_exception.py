from unittest.mock import MagicMock, patch, AsyncMock
import pytest

from openjiuwen.core.memory.long_term_memory import LongTermMemory
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.common.exception.codes import StatusCode

from openjiuwen.core.foundation.store.base_kv_store import BaseKVStore
from openjiuwen.core.retrieval.vector_store.base import VectorStore


@pytest.mark.asyncio
async def test_register_store_kv_store_none():
    mem = LongTermMemory()

    with pytest.raises(BaseError) as e:
        await mem.register_store(kv_store=None)

    err = e.value
    assert err.status == StatusCode.MEMORY_REGISTER_STORE_EXECUTION_ERROR
    assert err.code == StatusCode.MEMORY_REGISTER_STORE_EXECUTION_ERROR.code


@pytest.mark.asyncio
async def test_register_store_semantic_store_wrong_type():
    mem = LongTermMemory()

    fake_kv = MagicMock(spec=BaseKVStore)
    wrong_vector_store = object()  # 不是 BaseSemanticStore

    with pytest.raises(BaseError) as e:
        await mem.register_store(
            kv_store=fake_kv,
            vector_store=wrong_vector_store
        )

    assert e.value.status == StatusCode.MEMORY_REGISTER_STORE_EXECUTION_ERROR


@pytest.mark.asyncio
async def test_register_store_db_store_wrong_type():
    mem = LongTermMemory()

    fake_kv = MagicMock(spec=BaseKVStore)
    fake_semantic = MagicMock(spec=VectorStore)
    wrong_db = object()

    with pytest.raises(BaseError) as e:
        await mem.register_store(
            kv_store=fake_kv,
            vector_store=fake_semantic,
            db_store=wrong_db
        )

    assert e.value.status == StatusCode.MEMORY_REGISTER_STORE_EXECUTION_ERROR


def test_set_scope_config_llm_init_failed():
    mem = LongTermMemory()
    mem.kv_store = MagicMock()
    mem.db_store = MagicMock()
    mem.vector_store = MagicMock()

    fake_scope_cfg = MagicMock()
    fake_scope_cfg.default_model_cfg = MagicMock()
    fake_scope_cfg.default_model_client_cfg = MagicMock()
    fake_scope_cfg.crypto_key = b""

    with patch(
        "openjiuwen.core.memory.long_term_memory.LongTermMemory._get_llm_from_config",
        side_effect=Exception("llm init failed")
    ):
        with pytest.raises(Exception) as e:
            mem.set_config(fake_scope_cfg)

    assert str(e.value) == "llm init failed"


@pytest.mark.asyncio
async def test_delete_mem_by_id_write_manager_not_init():
    mem = LongTermMemory()
    mem.write_manager = None
    mem.kv_store = MagicMock()

    mem.kv_store.exclusive_set = AsyncMock(return_value=True)

    with pytest.raises(BaseError) as e:
        await mem.delete_mem_by_id(mem_id="mem123", user_id="u1", scope_id="s1")

    err = e.value
    assert err.status == StatusCode.MEMORY_DELETE_MEMORY_EXECUTION_ERROR
