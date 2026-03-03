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
class TestMemoryQuality(unittest.IsolatedAsyncioTestCase):
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
        kv_store = InMemoryKVStore()

        vector_db_type = os.getenv("VECTOR_DB_TYPE", "chroma")
        if vector_db_type == "chroma":
            # Vector Store: Chroma (persist to resource_dir)
            vector_store = create_vector_store(
                "chroma",
                persist_directory=self.resource_dir
            )
        else:
            # Vector Store: Milvus
            milvus_uri = os.getenv("MILVUS_URI", "http://localhost:19530")
            milvus_token = os.getenv("MILVUS_TOKEN", None)

            vector_store = create_vector_store(
                "milvus",
                milvus_uri=milvus_uri,
                milvus_token=milvus_token,
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
            temperature=0.3,
            top_p=0.9
        )
        default_model_client_cfg = ModelClientConfig(
            client_id="memory_test_client",
            client_provider=os.getenv("LLM_PROVIDER", "xx"),
            api_key=os.getenv("LLM_API_KEY", "xx"),
            api_base=os.getenv("LLM_API_BASE", "xx"),
            verify_ssl=False,
            timeout=120,
        )

        self.memory_engine_config = MemoryEngineConfig(
            default_model_cfg=default_model_cfg,
            default_model_client_cfg=default_model_client_cfg,
            crypto_key=crypto_key
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

        self.scope_id = "test_memory_scope"
        self.user_id = "test_user_id"

        logger.info("LongTermMemory initialized successfully with Chroma + SQLite + InMemoryKVStore")

    async def asyncTearDown(self):
        """Clean up after each test."""
        # Clean up test data
        try:
            # Delete test user's memories
            await self.engine.delete_mem_by_user_id(
                user_id=self.user_id,
                scope_id=self.scope_id
            )
            await self.engine.delete_messages_by_user_and_scope(
                user_id=self.user_id,
                scope_id=self.scope_id
            )

        except Exception as e:
            logger.warning(f"Error cleaning up test data: {e}")

    async def _user_mem_check(
        self,
        messages: list[BaseMessage],
        query_checklist: list[tuple[str, str | list[str] | tuple[str, bool] | list[tuple[str, bool]]]]
    ):
        agent_cfg = AgentMemoryConfig(
            mem_variables=[],
            enable_long_term_mem=True,
        )
        await self.engine.add_messages(
            user_id=self.user_id,
            scope_id=self.scope_id,
            messages=messages,
            agent_config=agent_cfg
        )

        # Verify all memories were captured
        all_memories = await self.engine.get_user_mem_by_page(
            user_id=self.user_id,
            scope_id=self.scope_id,
            page_size=50,
            page_idx=1
        )
        logger.info(f"Total memories captured: {len(all_memories)}")
        for mem in all_memories:
            logger.info(f"  - {mem.mem_id}: {mem.content}")

        for query, expected_context in query_checklist:
            results = await self.engine.search_user_mem(
                query=query,
                num=3,
                user_id=self.user_id,
                scope_id=self.scope_id,
            )
            logger.info(f"Search: '{query}' ({expected_context})")
            for result in results:
                logger.info(f"  - {result.mem_info.mem_id}: {result.mem_info.content}, Score[{result.score:.4f}]")

            # Normalize expected_context to list of (keyword, should_contain) tuples
            context_checks = []
            
            if isinstance(expected_context, str):
                # Single string: should contain
                context_checks.append((expected_context, True))
            elif (isinstance(expected_context, tuple) and len(expected_context) == 2 and
                  isinstance(expected_context[1], bool)):
                # Tuple with bool: (keyword, should_contain)
                context_checks.append(expected_context)
            elif isinstance(expected_context, list):
                # List: could be list of strings or list of tuples
                for item in expected_context:
                    if isinstance(item, str):
                        # String: should contain
                        context_checks.append((item, True))
                    elif isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], bool):
                        # Tuple with bool: (keyword, should_contain)
                        context_checks.append(item)
            
            # Check all context conditions
            for keyword, should_contain in context_checks:
                mem_found = False
                for result in results:
                    mem_found = mem_found or (keyword in result.mem_info.content)
                
                if should_contain:
                    self.assertTrue(mem_found, f"Can not find {query}-{keyword} in memory (should contain)")
                else:
                    self.assertFalse(mem_found, f"Found {query}-{keyword} in memory (should not contain)")

    async def _user_var_check(
        self,
        variable_defines: list[Param],
        messages: list[BaseMessage],
        variable_checklist: list[tuple[str, str]]
    ):
        agent_cfg = AgentMemoryConfig(
            mem_variables=variable_defines,
            enable_long_term_mem=False,
        )
        await self.engine.add_messages(
            user_id=self.user_id,
            scope_id=self.scope_id,
            messages=messages,
            agent_config=agent_cfg
        )

        variables = await self.engine.get_variables(
            user_id=self.user_id,
            scope_id=self.scope_id,
        )
        for name, expect_value in variable_checklist:
            self.assertIn(name, variables, f"variable {name} not found")
            actual_val = variables[name]
            self.assertIn(expect_value, actual_val,
                          f"variable {name} expected value: {expect_value}, actual value: {actual_val}")


    async def test_variable_01(self):
        messages = [
            BaseMessage(role="user", content="你好，我是Tom"),
            BaseMessage(role="assistant", content="你好Tom，很高兴认识你"),
            BaseMessage(role="user", content="我是一名数据分析师"),
            BaseMessage(role="assistant", content="数据分析是个很有前景的领域"),
        ]
        variable_defines = [
            Param.string("姓名", "用户姓名", required=False),
            Param.string("职业", "用户职业", required=False),
        ]
        variable_checklist = [
            ("姓名", "Tom"),
            ("职业", "数据分析师")
        ]
        await self._user_var_check(variable_defines, messages, variable_checklist)

    async def test_user_mem_base(self):
        messages = [
            BaseMessage(role="user", content="你好，我是Tom"),
            BaseMessage(role="assistant", content="你好Tom，很高兴认识你"),
            BaseMessage(role="user", content="我是一名数据分析师"),
            BaseMessage(role="assistant", content="数据分析是个很有前景的领域"),
            BaseMessage(role="user", content="业余时间我喜欢阅读和跑步"),
            BaseMessage(role="assistant", content="阅读和跑步都是很好的爱好"),
            BaseMessage(role="user", content="我的目标是成为数据科学家"),
            BaseMessage(role="assistant", content="你一定能实现"),
        ]
        query_checklist = [
            ("我是谁", "Tom"),
            ("我的工作", "数据分析师"),
            ("推荐运动", "跑步"),
        ]
        await self._user_mem_check(messages, query_checklist)

    async def test_user_mem_not_self(self):
        messages = [
            BaseMessage(role="user", content="你好，我是Tom"),
            BaseMessage(role="assistant", content="你好Tom，很高兴认识你"),
            BaseMessage(role="user", content="我是一名硬件工程师"),
            BaseMessage(role="assistant", content="硬件工程师是个很有前景的职业"),
            BaseMessage(role="user", content="我有个朋友叫宋朝"),
            BaseMessage(role="assistant", content="你的朋友叫宋朝这个名字挺有意思的"),
        ]
        query_checklist = [
            ("我是谁", "Tom"),
            ("我的工作", "硬件工程师"),
        ]
        await self._user_mem_check(messages, query_checklist)

        messages = [
            BaseMessage(role="user", content="他是一名软件工程师"),
            BaseMessage(role="assistant", content="软件工程师也是个很有前景的职业"),
            BaseMessage(role="user", content="他业余时间喜欢阅读和跑步"),
            BaseMessage(role="assistant", content="阅读和跑步都是很好的爱好"),
        ]
        query_checklist = [
            ("宋朝是谁", ["朋友", "软件工程师"]),
        ]
        await self._user_mem_check(messages, query_checklist)

    async def test_user_mem_update(self):
        messages = [
            BaseMessage(role="user", content="你好，我是Tom"),
            BaseMessage(role="assistant", content="你好Tom，很高兴认识你"),
            BaseMessage(role="user", content="我是一名硬件工程师"),
            BaseMessage(role="assistant", content="硬件工程师是个很有前景的职业"),
            BaseMessage(role="user", content="业余时间我喜欢阅读和跑步"),
            BaseMessage(role="assistant", content="阅读和跑步都是很好的爱好"),
        ]
        query_checklist = [
            ("我的工作", "硬件工程师"),
            ("我的爱好", ["阅读", "跑步"]),
        ]
        await self._user_mem_check(messages, query_checklist)
        messages = [
            BaseMessage(role="user", content="我转行成为了一名软件工程师"),
            BaseMessage(role="assistant", content="恭喜你成功转行"),
            BaseMessage(role="user", content="我现在不爱跑步了"),
            BaseMessage(role="assistant", content="跑步是很好的爱好，建议要坚持"),
        ]
        query_checklist = [
            ("我的工作", "软件工程师"),
            ("不喜欢", "跑步"),
        ]
        await self._user_mem_check(messages, query_checklist)

    async def test_user_mem_reference(self):
        messages = [
            BaseMessage(role="user", content="苹果的营养成分分析"),
            BaseMessage(role="assistant", content="苹果是一种常见的水果，营养成分丰富且易于消化"),
        ]
        query_checklist = [
            ("水果", ("苹果", False)),
        ]
        await self._user_mem_check(messages, query_checklist)
        messages = [
            BaseMessage(role="user", content="我比较喜欢吃它"),
            BaseMessage(role="assistant", content="那真是太棒了"),
        ]
        query_checklist = [
            ("水果", ["用户", "苹果"]),
        ]
        await self._user_mem_check(messages, query_checklist)

    async def test_user_mem_episodic(self):
        messages = [
            BaseMessage(role="user", content="你好，我是Tom"),
            BaseMessage(role="assistant", content="你好Tom，很高兴认识你"),
            BaseMessage(role="user", content="我昨天去了北京旅游"),
            BaseMessage(role="assistant", content="北京是个很棒的地方"),
            BaseMessage(role="user", content="我参观了故宫博物院"),
            BaseMessage(role="assistant", content="故宫是中国文化的瑰宝"),
            BaseMessage(role="user", content="今天我买了一本新书"),
            BaseMessage(role="assistant", content="阅读是个好习惯"),
        ]
        query_checklist = [
            ("北京旅游", ["用户", "北京", "故宫博物院"]),
            ("买了什么", ["用户", "新书"]),
        ]
        await self._user_mem_check(messages, query_checklist)

    async def test_user_mem_episodic_conflict_real(self):
        """
        Test real conflicting memories that need to be updated.
        Real conflicts: old memory should be deleted/replaced with new one.
        Examples: job change, location change, status change.
        """
        messages = [
            BaseMessage(role="user", content="你好，我是Tom"),
            BaseMessage(role="assistant", content="你好Tom，很高兴认识你"),
            BaseMessage(role="user", content="我上周和家人一起搬到了北京居住"),
            BaseMessage(role="assistant", content="北京是个很棒的城市"),
            BaseMessage(role="user", content="昨天我正式加入了A公司开始工作"),
            BaseMessage(role="assistant", content="A公司是家很好的公司"),
        ]
        query_checklist = [
            ("我住在哪里", "北京"),
            ("我在哪里工作", "A公司"),
            ("搬家", ["用户", "北京", "家人"]),
            ("入职", ["用户", "A公司"]),
        ]
        await self._user_mem_check(messages, query_checklist)

        messages = [
            BaseMessage(role="user", content="昨天我和同事一起搬到了上海定居"),
            BaseMessage(role="assistant", content="上海也是个很棒的城市"),
            BaseMessage(role="user", content="今天我成功跳槽到了B公司并完成了入职手续"),
            BaseMessage(role="assistant", content="恭喜你有了新的工作机会"),
        ]
        query_checklist = [
            ("我住在哪里", "上海"),
            ("我在哪里工作", "B公司"),
            ("最近搬家", ["用户", "上海", "同事"]),
            ("新工作", ["用户", "B公司", "入职手续"]),
        ]
        await self._user_mem_check(messages, query_checklist)

    async def test_user_mem_episodic_conflict_false(self):
        """
        Test false conflicting memories that should coexist.
        False conflicts: memories look similar but represent different events/times.
        Examples: meals at different times, activities on different days.
        """
        messages = [
            BaseMessage(role="user", content="你好，我是Tom"),
            BaseMessage(role="assistant", content="你好Tom，很高兴认识你"),
            BaseMessage(role="user", content="我今天中午吃了面条"),
            BaseMessage(role="assistant", content="面条是不错的午餐选择"),
            BaseMessage(role="user", content="晚上我吃了米饭"),
            BaseMessage(role="assistant", content="米饭是很常见的主食"),
        ]
        query_checklist = [
            ("中午吃了什么", "面条"),
            ("晚上吃了什么", "米饭"),
        ]
        await self._user_mem_check(messages, query_checklist)

        messages = [
            BaseMessage(role="user", content="今天上午我去看了电影"),
            BaseMessage(role="assistant", content="看电影是很好的娱乐活动"),
            BaseMessage(role="user", content="今天下午我去逛了公园"),
            BaseMessage(role="assistant", content="逛公园很放松身心"),
        ]
        query_checklist = [
            ("今天上午做了什么", "电影"),
            ("今天下午做了什么", "公园"),
        ]
        await self._user_mem_check(messages, query_checklist)

    async def test_user_mem_semantic(self):
        messages = [
            BaseMessage(role="user", content="你好，我是Tom"),
            BaseMessage(role="assistant", content="你好Tom，很高兴认识你"),
            BaseMessage(role="user", content="地球是太阳系中的第三颗行星"),
            BaseMessage(role="assistant", content="是的，地球是我们的家园"),
            BaseMessage(role="user", content="水的化学式是H2O"),
            BaseMessage(role="assistant", content="没错，这是水的化学表达式"),
            BaseMessage(role="user", content="Python是一种流行的编程语言"),
            BaseMessage(role="assistant", content="Python确实很受欢迎"),
        ]
        query_checklist = [
            ("地球位置", ["太阳系", "第三颗行星"]),
            ("水的化学式", "H2O"),
            ("Python是什么", "编程语言"),
        ]
        await self._user_mem_check(messages, query_checklist)

    async def test_user_mem_mixed(self):
        messages = [
            BaseMessage(role="user", content="你好，我是Tom"),
            BaseMessage(role="assistant", content="你好Tom，很高兴认识你"),
            BaseMessage(role="user", content="我是一名数据分析师"),
            BaseMessage(role="assistant", content="数据分析是个很有前景的领域"),
            BaseMessage(role="user", content="我上周参加了一个Python培训"),
            BaseMessage(role="assistant", content="Python很适合数据分析"),
            BaseMessage(role="user", content="Python是一种解释型编程语言"),
            BaseMessage(role="assistant", content="是的，Python语法简洁易学"),
            BaseMessage(role="user", content="我喜欢使用Python进行数据可视化"),
            BaseMessage(role="assistant", content="数据可视化很重要"),
        ]
        query_checklist = [
            ("我是谁", "Tom"),
            ("我的职业", "数据分析师"),
            ("参加了什么培训", ["用户", "Python"]),
            ("Python是什么类型的语言", "解释型编程语言"),
            ("Python用途", "数据可视化"),
        ]
        await self._user_mem_check(messages, query_checklist)


def run_tests():
    """Run all tests."""
    unittest.main(argv=[__file__], verbosity=2, exit=False)


if __name__ == "__main__":
    run_tests()
