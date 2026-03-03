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

        # Create resource directory if it doesn't exist
        if not os.path.exists(self.resource_dir):
            os.makedirs(self.resource_dir)

        # ---------- Embedding Configuration ----------
        embed_config = EmbeddingConfig(
            model_name=os.getenv("EMBED_MODEL_NAME", "xx"),
            api_key=os.getenv("EMBED_API_KEY", "xx"),
            base_url=os.getenv("EMBED_API_BASE", "xx"),
        )

        # ---------- Store Configuration ----------
        # KV Store: InMemoryKVStore
        self.kv_store = InMemoryKVStore()

        # Vector Store: Chroma (persist to resource_dir)
        self.vector_store = create_vector_store(
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
        self.db_store = DefaultDbStore(sqlite_engine)

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
        self.embedding_model = APIEmbedding(config=embed_config)

        # ---------- Register Stores ----------
        await self.engine.register_store(
            kv_store=self.kv_store,
            vector_store=self.vector_store,
            db_store=self.db_store,
            embedding_model=self.embedding_model
        )

        # ---------- Set Engine Config ----------
        self.engine.set_config(self.memory_engine_config)

        logger.info("LongTermMemory initialized successfully with Chroma + SQLite + InMemoryKVStore")

    async def asyncTearDown(self):
        """Clean up after each test."""
        # Clean up test data
        test_scope_id = "test_memory_scope"
        test_user_id = "test_user_workflow"

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
            enable_fragment_memory=True,
            enable_summary_memory=True
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

    async def test_scope_config_workflow(self):
        """
        Test the complete workflow with scope-specific configurations.

        This test demonstrates:
        1. Setting scope-specific configuration using set_scope_config
        2. Adding messages and verifying memory operations with scope config
        3. Deleting scope configuration using delete_scope_config
        4. Verifying memory operations work after deleting scope config
        """
        scope_id = "test_memory_scope"
        user_id = "test_user_workflow"

        # ---------- 1. Set Scope Configuration ----------
        logger.info("Step 1: Setting scope configuration")

        scope_config = MemoryScopeConfig(
            model_cfg=ModelRequestConfig(
                model=os.getenv("LLM_MODEL_NAME", ""),
                temperature=0.3,
                top_p=0.8
            ),
            model_client_cfg=ModelClientConfig(
                client_id="scope_test_client",
                client_provider=os.getenv("LLM_PROVIDER", "xx"),
                api_key=os.getenv("LLM_API_KEY", "xx"),
                api_base=os.getenv("LLM_API_BASE", "xx"),
                verify_ssl=False
            ),
            embedding_cfg=EmbeddingConfig(
                model_name=os.getenv("EMBED_MODEL_NAME", "xx"),
                api_key=os.getenv("EMBED_API_KEY", "xx"),
                base_url=os.getenv("EMBED_API_BASE", "xx"),
            )
        )
        # no default embedding model
        await self.engine.register_store(
            kv_store=self.kv_store,
            vector_store=self.vector_store,
            db_store=self.db_store,
        )
        set_success = await self.engine.set_scope_config(scope_id, scope_config)
        self.assertTrue(set_success, "Failed to set scope configuration")
        logger.info("Scope configuration set successfully")

        # ---------- 2. Test Memory Operations with Scope Config ----------
        logger.info("\nStep 2: Testing memory operations with scope config")

        agent_cfg = AgentMemoryConfig(
            mem_variables=[
                Param.string("姓名", "用户姓名", required=False),
                Param.string("爱好", "用户爱好", required=False),
            ],
            enable_long_term_mem=True,
        )

        messages = [
            BaseMessage(role="user", content="你好，我是Alice，我喜欢旅行"),
            BaseMessage(role="assistant", content="你好Alice，旅行是个很棒的爱好！"),
        ]

        await self.engine.add_messages(
            user_id=user_id,
            scope_id=scope_id,
            session_id="scope_test_session_1",
            messages=messages,
            agent_config=agent_cfg
        )
        logger.info("Messages added successfully with scope config")

        memories_page = await self.engine.get_user_mem_by_page(
            user_id=user_id,
            scope_id=scope_id,
            page_size=10,
            page_idx=1
        )
        self.assertGreater(len(memories_page), 0, "Should get memories with scope config")
        logger.info(f"Get {len(memories_page)} memories with scope config")

        # Test search_user_mem
        search_results = await self.engine.search_user_mem(
            query="用户的爱好",
            num=3,
            user_id=user_id,
            scope_id=scope_id,
            threshold=0.3
        )
        self.assertGreater(len(search_results), 0, "Should search results with scope config")
        logger.info(f"Found {len(search_results)} search results with scope config")

        # ---------- 3. Delete Scope Configuration ----------
        logger.info("\nStep 3: Deleting scope configuration")

        delete_success = await self.engine.delete_scope_config(scope_id)
        self.assertTrue(delete_success, "Failed to delete scope configuration")
        logger.info("Scope configuration deleted successfully")

        # ---------- 4. Test Memory Operations without Scope Config ----------
        logger.info("\nStep 4: Testing memory operations without scope config")

        memories_page2 = await self.engine.get_user_mem_by_page(
            user_id=user_id,
            scope_id=scope_id,
            page_size=20,
            page_idx=1
        )
        self.assertGreater(len(memories_page2), 0, "Should get memories with scope configg")
        logger.info(f"Get {len(memories_page2)} memories without scope config")

        search_results2 = await self.engine.search_user_mem(
            query="用户的爱好",
            num=5,
            user_id=user_id,
            scope_id=scope_id,
            threshold=0.3
        )
        self.assertEqual(len(search_results2), 0, "Should not search results without scope config")
        logger.info(f"Found {len(search_results2)} search results without scope config")

        logger.info("Test test_scope_config_workflow passed")

    async def test_update_mem_by_id(self):
        """
        Test the update_mem_by_id functionality.

        This test demonstrates:
        1. Adding a memory
        2. Getting the memory to obtain its ID
        3. Updating the memory using update_mem_by_id
        4. Verifying the update was successful
        """
        scope_id = "test_memory_scope"
        user_id = "test_user_workflow"
        # ---------- 1. Add a memory to update ----------
        logger.info("Step 1: Adding a memory to update")
        agent_cfg = AgentMemoryConfig(
            mem_variables=[
                Param.string("姓名", "用户姓名", required=False),
                Param.string("职业", "用户职业", required=False),
            ],
            enable_long_term_mem=True,
        )
        messages = [
            BaseMessage(role="user", content="你好，我是Bob，我是一名医生"),
            BaseMessage(role="assistant", content="你好Bob，医生是个很有意义的职业！"),
        ]
        scope_config = MemoryScopeConfig(
            model_cfg=ModelRequestConfig(
                model=os.getenv("LLM_MODEL_NAME", ""),
                temperature=0.3,
                top_p=0.8
            ),
            model_client_cfg=ModelClientConfig(
                client_id="scope_test_client",
                client_provider=os.getenv("LLM_PROVIDER", "xx"),
                api_key=os.getenv("LLM_API_KEY", "xx"),
                api_base=os.getenv("LLM_API_BASE", "xx"),
                verify_ssl=False
            ),
            embedding_cfg=EmbeddingConfig(
                model_name=os.getenv("EMBED_MODEL_NAME", "xx"),
                api_key=os.getenv("EMBED_API_KEY", "xx"),
                base_url=os.getenv("EMBED_API_BASE", "xx"),
            )
        )
        await self.engine.set_scope_config(scope_id, scope_config)
        await self.engine.add_messages(
            user_id=user_id,
            scope_id=scope_id,
            session_id="update_test_session",
            messages=messages,
            agent_config=agent_cfg
        )
        logger.info("Initial message added successfully")
        # ---------- 2. Get the memory to update ----------
        logger.info("Step 2: Getting the memory to update")
        memories = await self.engine.get_user_mem_by_page(
            user_id=user_id,
            scope_id=scope_id,
            page_size=10,
            page_idx=1
        )

        self.assertGreater(len(memories), 0, "Should have at least one memory")
        mem_to_update = memories[0]
        original_content = mem_to_update.content
        logger.info(f"Found memory to update: ID={mem_to_update.mem_id}, Content={original_content}")
        # ---------- 3. Update the memory ----------
        logger.info("Step 3: Updating the memory")
        new_content = "你好，我是Bob，我是一名外科医生"
        await self.engine.update_mem_by_id(
            user_id=user_id,
            scope_id=scope_id,
            mem_id=mem_to_update.mem_id,
            memory=new_content
        )
        logger.info(f"Memory updated successfully to: {new_content}")
        # ---------- 4. Verify the update was successful ----------
        logger.info("Step 4: Verifying the update was successful")
        updated_memories = await self.engine.get_user_mem_by_page(
            user_id=user_id,
            scope_id=scope_id,
            page_size=10,
            page_idx=1
        )
        self.assertGreater(len(updated_memories), 0, "Should have memories after update")
        updated_mem = next((m for m in updated_memories if m.mem_id == mem_to_update.mem_id), None)
        self.assertIsNotNone(updated_mem, "Should find the updated memory")
        self.assertEqual(updated_mem.content, new_content, "Memory content should be updated")



def run_tests():
    """Run all tests."""
    unittest.main(argv=[__file__], verbosity=2, exit=False)


if __name__ == "__main__":
    run_tests()
