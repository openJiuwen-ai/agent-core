# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
System tests for LongTermMemory with Chroma vector store, SQLite db_store, and InMemoryKVStore.

This test file demonstrates a complete LongTermMemory workflow including:
- Initialization
- register_store (Chroma + SQLite + InMemoryKVStore)
- set_config
- set_scope_config
- add_messages
- search

Environment variables can be set via tests/system_tests/memory/memory_env file.
"""

import base64
import os
import unittest
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine

from openjiuwen.core.memory.long_term_memory import LongTermMemory
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.schema.message import BaseMessage
from openjiuwen.core.foundation.store import create_vector_store
from openjiuwen.core.foundation.store.kv.in_memory_kv_store import InMemoryKVStore
from openjiuwen.core.foundation.llm.schema.config import ModelRequestConfig, ModelClientConfig
from openjiuwen.core.foundation.store.db.default_db_store import DefaultDbStore
from openjiuwen.core.memory.config.config import MemoryEngineConfig, AgentMemoryConfig, MemoryScopeConfig
from openjiuwen.core.common.schema.param import Param
from openjiuwen.core.retrieval import APIEmbedding
from openjiuwen.core.retrieval.common.config import EmbeddingConfig


# Load environment variables from memory_env file using dotenv
env_file = Path(__file__).parent / "memory_env"
load_dotenv(env_file)


@unittest.skip("skip system test")
class TestLongTermMemory(unittest.IsolatedAsyncioTestCase):
    """
    Comprehensive system tests for LongTermMemory using Chroma vector store,
    SQLite database, and InMemoryKVStore.
    """

    async def asyncSetUp(self):
        """Set up test environment before each test."""
        # Reset singleton
        self.engine = LongTermMemory()

        # Get crypto key from environment or use empty string for testing
        try:
            crypto_key = base64.b64decode(os.getenv("MEMORY_CRYPTO_KEY", ""))
        except Exception:
            crypto_key = b""

        # Get resource directory for Chroma persistence
        self.resource_dir = os.getenv("MEMORY_RESOURCE_DIR", "./resource_dir")

        # ---------- Embedding Configuration ----------
        embed_config = EmbeddingConfig(
            model_name=os.getenv("EMBED_MODEL_NAME", "xx"),
            api_key=os.getenv("EMBED_API_KEY", "xx"),
            base_url=os.getenv("EMBED_API_BASE", "xx"),
        )

        # ---------- Store Configuration ----------
        # KV Store: InMemoryKVStore
        kv_store = InMemoryKVStore()

        # Vector Store: Chroma (persist to resource_dir)
        vector_store = create_vector_store(
            "chroma",
            persist_directory=self.resource_dir
        )

        # Database Store: SQLite
        sqlite_db_path = os.path.join(self.resource_dir, "mem_test.db")
        sqlite_engine = create_async_engine(
            f"sqlite+aiosqlite:///{sqlite_db_path}",
            pool_pre_ping=True,
            echo=False,
        )
        db_store = DefaultDbStore(sqlite_engine)

        # ---------- Memory Engine Configuration ----------
        default_model_cfg = ModelRequestConfig(
            model=os.getenv("LLM_MODEL_NAME", ""),
            temperature=0.2,
            top_p=0.7
        )
        default_model_client_cfg = ModelClientConfig(
            client_id="memory_test_client",
            client_provider=os.getenv("LLM_PROVIDER", "xx"),
            api_key=os.getenv("LLM_API_KEY", "xx"),
            api_base=os.getenv("LLM_API_BASE", "xx"),
            verify_ssl=False
        )

        self.memory_engine_config = MemoryEngineConfig(
            default_model_cfg=default_model_cfg,
            default_model_client_cfg=default_model_client_cfg,
            crypto_key=crypto_key,
            input_msg_max_len=int(os.getenv("MEMORY_INPUT_MSG_MAX_LEN", "8192")),
            single_turn_history_summary_max_token=int(os.getenv("MEMORY_SUMMARY_MAX_TOKEN", "128")),
        )

        # Create embedding model
        embedding_model = APIEmbedding(config=embed_config)

        # ---------- Register Stores ----------
        await self.engine.register_store(
            kv_store=kv_store,
            vector_store=vector_store,
            db_store=db_store,
            embedding_model=embedding_model
        )

        # ---------- Set Engine Config ----------
        self.engine.set_config(self.memory_engine_config)

        logger.info("LongTermMemory initialized successfully with Chroma + SQLite + InMemoryKVStore")

    async def asyncTearDown(self):
        """Clean up after each test."""
        # Clean up test data
        test_scope_id = "test_memory_scope"
        test_user_id = "test_user_001"

        try:
            # Delete test user's memories
            await self.engine.delete_mem_by_user_id(
                user_id=test_user_id,
                scope_id=test_scope_id
            )

        except Exception as e:
            logger.warning(f"Error cleaning up test data: {e}")


    async def test_memory_sample(self):
        """
        Test a complete end-to-end workflow combining all operations.

        This test simulates a real-world scenario:
        1. User has multiple conversations
        2. System extracts and stores memories
        3. User asks questions that require memory retrieval
        4. System searches and retrieves relevant memories
        """
        scope_id = "test_memory_scope"
        user_id = "test_user_workflow"

        agent_cfg = AgentMemoryConfig(
            mem_variables=[
                Param.string("姓名", "用户姓名", required=False),
                Param.string("职业", "用户职业", required=False),
            ],
            enable_long_term_mem=True,
        )

        # Conversation 1: Introduction
        messages1 = [
            BaseMessage(role="user", content="你好，我是Tom"),
            BaseMessage(role="assistant", content="你好Tom，很高兴认识你"),
        ]

        await self.engine.add_messages(
            user_id=user_id,
            scope_id=scope_id,
            session_id="workflow_session_1",
            messages=messages1,
            agent_config=agent_cfg
        )

        # Conversation 2: More details
        messages2 = [
            BaseMessage(role="user", content="我是一名数据分析师"),
            BaseMessage(role="assistant", content="数据分析是个很有前景的领域"),
        ]

        await self.engine.add_messages(
            user_id=user_id,
            scope_id=scope_id,
            session_id="workflow_session_2",
            messages=messages2,
            agent_config=agent_cfg
        )

        # Conversation 3: Interests
        messages3 = [
            BaseMessage(role="user", content="业余时间我喜欢阅读"),
            BaseMessage(role="assistant", content="阅读都是很好的爱好"),
        ]

        await self.engine.add_messages(
            user_id=user_id,
            scope_id=scope_id,
            session_id="workflow_session_3",
            messages=messages3,
            agent_config=agent_cfg
        )

        # Verify all memories were captured
        all_memories = await self.engine.get_user_mem_by_page(
            user_id=user_id,
            scope_id=scope_id,
            page_size=50,
            page_idx=1
        )

        logger.info(f"Total memories captured: {len(all_memories)}")
        for mem in all_memories:
            logger.info(f"  - {mem.content}")

        # Search for different types of information
        search_tests = [
            ("用户的姓名", "Tom"),
            ("用户的工作", "数据分析师"),
            ("用户的兴趣爱好", "阅读"),
        ]

        for query, expected_context in search_tests:
            results = await self.engine.search_user_mem(
                query=query,
                num=3,
                user_id=user_id,
                scope_id=scope_id,
                threshold=0.3
            )

            logger.info(f"Search: '{query}' ({expected_context})")
            self.assertGreater(len(results), 0, f"Should find results for: {query}")
            mem_found = False
            for result in results:
                logger.info(f"  - Score: {result.score:.4f}, Content: {result.mem_info.content}")
                if not mem_found and expected_context in result.mem_info.content:
                    mem_found = True

            self.assertTrue(mem_found, f"Can not find {query}-{expected_context} in memory")

        # Verify variables
        variables = await self.engine.get_variables(
            user_id=user_id,
            scope_id=scope_id
        )

        logger.info(f"Extracted variables: {variables}")
        self.assertIn("姓名", variables)
        self.assertEqual(variables["姓名"], "Tom")
        self.assertEqual(variables["职业"], "数据分析师")

        logger.info("Test test_memory_sample passed")


def run_tests():
    """Run all tests."""
    unittest.main(argv=[__file__], verbosity=2, exit=False)


if __name__ == "__main__":
    run_tests()
