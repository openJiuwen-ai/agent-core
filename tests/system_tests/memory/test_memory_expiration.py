# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
System tests for memory expiration functionality.

Tests the MemoryExpirationService integrated with LongTermMemory,
covering end-to-end cleanup of expired memories across real backends
(Chroma vector store, SQLite db_store, InMemoryKVStore).

Environment variables can be set via tests/system_tests/memory/memory_env file.
"""

import asyncio
import base64
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine

from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.schema.param import Param
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.llm.schema.message import BaseMessage
from openjiuwen.core.foundation.store import create_vector_store
from openjiuwen.core.foundation.store.db.default_db_store import DefaultDbStore
from openjiuwen.core.foundation.store.kv.in_memory_kv_store import InMemoryKVStore
from openjiuwen.core.memory.config.config import AgentMemoryConfig, MemoryEngineConfig
from openjiuwen.core.memory.long_term_memory import LongTermMemory
from openjiuwen.core.memory.manage.expiration.memory_expiration_service import MemoryExpirationService
from openjiuwen.core.retrieval import APIEmbedding
from openjiuwen.core.retrieval.common.config import EmbeddingConfig


class MockMemoryExpirationService(MemoryExpirationService):
    """Mock service with short check interval for testing periodic cleanup."""

    _CHECK_INTERVAL_SECONDS: int = 10


# Load environment variables from memory_env file using dotenv
env_file = Path(__file__).parent / "memory_env"
load_dotenv(env_file)

SCOPE_ID = "test_expiration_scope"
USER_ID = "test_user_expiration"


# ---------- Fixtures ----------


@pytest.fixture
def memory_engine_config():
    try:
        crypto_key = base64.b64decode(os.getenv("MEMORY_CRYPTO_KEY", ""))
    except Exception:
        crypto_key = b""

    return MemoryEngineConfig(
        default_model_cfg=ModelRequestConfig(
            model=os.getenv("LLM_MODEL_NAME", ""),
            temperature=0.2,
            top_p=0.7,
        ),
        default_model_client_cfg=ModelClientConfig(
            client_id="memory_expiration_test_client",
            client_provider=os.getenv("LLM_PROVIDER", "xx"),
            api_key=os.getenv("LLM_API_KEY", "xx"),
            api_base=os.getenv("LLM_API_BASE", "xx"),
            verify_ssl=False,
        ),
        crypto_key=crypto_key,
        input_msg_max_len=int(os.getenv("MEMORY_INPUT_MSG_MAX_LEN", "8192")),
        single_turn_history_summary_max_token=int(os.getenv("MEMORY_SUMMARY_MAX_TOKEN", "128")),
        enable_memory_expiration=True,
        memory_expiration_seconds=5,
    )


@pytest_asyncio.fixture
async def engine(tmp_path, memory_engine_config):
    """Set up LongTermMemory engine with real backends."""
    engine = LongTermMemory()

    embed_config = EmbeddingConfig(
        model_name=os.getenv("EMBED_MODEL_NAME", "xx"),
        api_key=os.getenv("EMBED_API_KEY", "xx"),
        base_url=os.getenv("EMBED_API_BASE", "xx"),
    )

    kv_store = InMemoryKVStore()
    vector_store = create_vector_store("chroma", persist_directory=str(tmp_path))
    sqlite_db_path = str(tmp_path / "mem_expiration_test.db")
    sqlite_engine = create_async_engine(
        f"sqlite+aiosqlite:///{sqlite_db_path}",
        pool_pre_ping=True,
        echo=False,
    )
    db_store = DefaultDbStore(sqlite_engine)
    embedding_model = APIEmbedding(config=embed_config)

    await engine.register_store(
        kv_store=kv_store,
        vector_store=vector_store,
        db_store=db_store,
        embedding_model=embedding_model,
    )
    engine.set_config(memory_engine_config)

    logger.info("MemoryExpiration test setup completed")

    yield engine


async def add_conversation(engine, user_content, assistant_content, session_id):
    agent_cfg = AgentMemoryConfig(
        mem_variables=[
            Param.string("姓名", "用户姓名", required=False),
            Param.string("职业", "用户职业", required=False),
        ],
        enable_long_term_mem=True,
        enable_user_profile=True,
        enable_semantic_memory=True,
        enable_episodic_memory=True,
        enable_summary_memory=True,
    )
    messages = [
        BaseMessage(role="user", content=user_content),
        BaseMessage(role="assistant", content=assistant_content),
    ]
    await engine.add_messages(
        user_id=USER_ID,
        scope_id=SCOPE_ID,
        session_id=session_id,
        messages=messages,
        agent_config=agent_cfg,
    )


# ---------- Tests ----------


@pytest.mark.asyncio
@pytest.mark.skip(reason="skip system test")
async def test_cleanup_all_expired_memories(engine):
    """
    Add memories, then cleanup with a future cutoff time.
    All memories should be treated as expired and deleted.
    """
    await add_conversation(engine, "你好，我是Tom，我是一名工程师", "你好Tom，很高兴认识你", "expiration_session_1")
    await add_conversation(engine, "业余时间我喜欢打篮球", "打篮球是很好的运动", "expiration_session_2")

    memories_before = await engine.get_user_mem_by_page(user_id=USER_ID, scope_id=SCOPE_ID, page_size=50, page_idx=1)
    assert len(memories_before) > 0
    logger.info(f"Memories before cleanup: {len(memories_before)}")

    service = engine._expiration_service
    assert service is not None

    future_cutoff = datetime.now(timezone.utc).astimezone() + timedelta(days=1)
    await service.cleanup_all_users(cutoff_time=future_cutoff)

    memories_after = await engine.get_user_mem_by_page(user_id=USER_ID, scope_id=SCOPE_ID, page_size=50, page_idx=1)
    assert len(memories_after) == 0
    logger.info("test_cleanup_all_expired_memories passed")


@pytest.mark.asyncio
@pytest.mark.skip(reason="skip system test")
async def test_cleanup_preserves_recent_memories(engine):
    """
    Add memories, then cleanup with a very old cutoff time.
    No memories should be deleted.
    """
    await add_conversation(engine, "你好，我是Jerry，我喜欢阅读", "你好Jerry，阅读是很好的爱好", "expiration_session_3")

    memories_before = await engine.get_user_mem_by_page(user_id=USER_ID, scope_id=SCOPE_ID, page_size=50, page_idx=1)
    assert len(memories_before) > 0
    logger.info(f"Memories before cleanup: {len(memories_before)}")

    service = engine._expiration_service
    assert service is not None

    old_cutoff = datetime.now(timezone.utc).astimezone() - timedelta(days=365)
    await service.cleanup_all_users(cutoff_time=old_cutoff)

    memories_after = await engine.get_user_mem_by_page(user_id=USER_ID, scope_id=SCOPE_ID, page_size=50, page_idx=1)
    assert len(memories_after) == len(memories_before)
    logger.info("test_cleanup_preserves_recent_memories passed")


@pytest.mark.asyncio
@pytest.mark.skip(reason="skip system test")
async def test_expiration_service_lifecycle(engine):
    """Test start/stop lifecycle of the expiration service."""
    service = engine._expiration_service
    assert service is not None
    assert service._running is True

    await service.stop()
    assert service._running is False
    assert service._task is None

    await service.start()
    assert service._running is True
    assert service._task is not None

    await service.stop()
    logger.info("test_expiration_service_lifecycle passed")


@pytest.mark.asyncio
@pytest.mark.skip(reason="skip system test")
async def test_cleanup_with_no_users(engine):
    """Call cleanup when there are no user mappings. Should complete without errors."""
    service = engine._expiration_service
    assert service is not None

    await engine.delete_mem_by_user_id(user_id=USER_ID, scope_id=SCOPE_ID)

    cutoff = datetime.now(timezone.utc).astimezone() + timedelta(days=1)
    await service.cleanup_all_users(cutoff_time=cutoff)
    logger.info("test_cleanup_with_no_users passed")


@pytest.mark.asyncio
@pytest.mark.skip(reason="skip system test")
async def test_periodic_cleanup_expiration(engine, memory_engine_config):
    """
    Test automatic periodic cleanup with short intervals.
    Uses MockMemoryExpirationService (10s check interval) and 5s retention.
    """
    # Stop the default service
    default_service = engine._expiration_service
    if default_service:
        await default_service.stop()

    short_config = MemoryEngineConfig(
        default_model_cfg=memory_engine_config.default_model_cfg,
        default_model_client_cfg=memory_engine_config.default_model_client_cfg,
        crypto_key=memory_engine_config.crypto_key,
        input_msg_max_len=memory_engine_config.input_msg_max_len,
        single_turn_history_summary_max_token=memory_engine_config.single_turn_history_summary_max_token,
        enable_memory_expiration=True,
        memory_expiration_seconds=5,
    )

    mock_service = MockMemoryExpirationService(
        kv_store=engine.kv_store,
        config=short_config,
        scope_user_mapping_manager=engine.scope_user_mapping_manager,
        write_manager=engine.write_manager,
        search_manager=engine.search_manager,
        semantic_store_factory=engine._create_semantic_store_with_embedding,
    )

    engine._expiration_service = mock_service
    await mock_service.start()
    logger.info("MockMemoryExpirationService started (10s interval, 5s retention)")

    await add_conversation(engine, "你好，我是Spike，我是一名测试工程师", "你好Spike，欢迎", "periodic_session_1")

    memories_before = await engine.get_user_mem_by_page(user_id=USER_ID, scope_id=SCOPE_ID, page_size=50, page_idx=1)
    assert len(memories_before) > 0
    logger.info(f"Memories before periodic cleanup: {len(memories_before)}")

    # Sleep: > 10s (check interval) + 5s (retention) → memories should expire
    logger.info("Sleeping 20s to wait for periodic cleanup...")
    await asyncio.sleep(20)

    memories_after = await engine.get_user_mem_by_page(user_id=USER_ID, scope_id=SCOPE_ID, page_size=50, page_idx=1)
    assert len(memories_after) == 0
    logger.info("test_periodic_cleanup_expiration passed")
