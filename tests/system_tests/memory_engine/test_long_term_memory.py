import os
import base64
import time
import unittest
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import create_async_engine

from openjiuwen.core.memory.long_term_memory import LongTermMemory
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.schema.message import BaseMessage
from openjiuwen.core.memory.store.impl.memory_chroma_vector_store import MemoryChromaVectorStore
from openjiuwen.core.foundation.store.in_memory_kv_store import InMemoryKVStore
# from openjiuwen.core.memory.store.impl.memory_milvus_vector_store import MemoryMilvusVectorStore
from openjiuwen.core.foundation.llm.schema.config import ModelRequestConfig, ModelClientConfig
from openjiuwen.core.foundation.store.default_db_store import DefaultDbStore
from openjiuwen.core.memory.config.config import MemoryEngineConfig, AgentMemoryConfig, MemoryScopeConfig
from openjiuwen.core.common.schema.param import Param
from openjiuwen.core.retrieval.common.config import EmbeddingConfig


@unittest.skip("skip system test")
class TestLongTermMemory(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):

        # reset singleton
        self.engine = LongTermMemory()

        try:
            crypto_key = base64.b64decode(os.getenv("SERVER_AES_MASTER_KEY_ENV", ""))
        except Exception:
            crypto_key = b""

        embed_config = EmbeddingConfig(
            model_name=os.getenv("EMBED_MODEL_NAME", "xxxx"),
            api_key=os.getenv("EMBED_API_KEY", "xxxx"),
            base_url=os.getenv("EMBED_API_BASE", "xxxxx"),
        )

        # ---------- KV Store ----------
        kv_store = InMemoryKVStore

        # ---------- vector_store ----------
        # vector_store = MemoryMilvusVectorStore(
        #     milvus_host=os.getenv("MILVUS_HOST", "xxxx"),
        #     milvus_port=os.getenv("MILVUS_PORT", "xxxx"),
        #     embedding_dims=int(os.getenv("EMBEDDING_MODEL_DIMENTION", 1024)),
        #     token=os.getenv("MILVUS_TOKEN", None)
        # )
        vector_store = MemoryChromaVectorStore(persist_directory="./resource_dir")

        # ---------- db_store ----------
        db_user = os.getenv("DB_USER", "xxxx")
        db_passport = os.getenv("DB_PASSWORD", "xxxx")
        db_host = os.getenv("DB_HOST", "xxxx")
        db_port = os.getenv("DB_PORT", "xxxx")
        agent_db_name = os.getenv("AGENT_DB_NAME", "xxxx")

        db_store = DefaultDbStore(create_async_engine(
            f"mysql+aiomysql://{db_user}:{db_passport}@{db_host}:{db_port}/{agent_db_name}?charset=utf8mb4",
            pool_size=20,
            max_overflow=20
        ))

        # ---------- Config ----------
        default_model_cfg = ModelRequestConfig(model="qwen-plus-latest")
        default_model_client_cfg = ModelClientConfig(
            client_id="1",
            client_provider="OpenAI",
            api_key="xxxx",
            api_base="xxxx",
            verify_ssl=False
        )

        self.memory_engine_config = MemoryEngineConfig(
            default_model_cfg=default_model_cfg,
            default_model_client_cfg=default_model_client_cfg,
            crypto_key=crypto_key
        )

        await self.engine.register_store(
            kv_store=kv_store,
            vector_store=vector_store,
            db_store=db_store
        )
        self.engine.set_config(self.memory_engine_config)

    @staticmethod
    def _check_user_profile(expect_profile: str, user_profile_set: set) -> bool:
        for user_profile in user_profile_set:
            if user_profile.find(expect_profile) != -1:
                return True
        return False

    async def test_engine_initialized(self):
        self.assertIsNotNone(self.engine.kv_store)
        self.assertIsNotNone(self.engine.semantic_store)
        self.assertIsNotNone(self.engine.db_store)

    async def test_set_scope_config(self):
        scope_id = "test_scope"
        scope_model_cfg = ModelRequestConfig(model="qwen-plus-latest", temperature=0.05)
        scope_model_client_cfg = ModelClientConfig(
            client_id="1",
            client_provider="OpenAI",
            api_key="xxxx",
            api_base="xxxx",
            verify_ssl=False
        )
        embed_config = EmbeddingConfig(
            model_name=os.getenv("EMBED_MODEL_NAME", "text-embedding-v3"),
            api_key=os.getenv("EMBED_API_KEY", "xxxx"),
            base_url=os.getenv("EMBED_API_BASE", "xxxx"),
        )
        scope_cfg = MemoryScopeConfig(
            model_cfg=scope_model_cfg,
            model_client_cfg=scope_model_client_cfg,
            embedding_cfg=embed_config
        )
        await self.engine.set_scope_config(scope_id, scope_cfg)
        result = await self.engine.set_scope_config(scope_id, scope_cfg)
        self.assertTrue(result)

    async def test_delete_scope(self):
        scope_id = "app0108_1"
        user_id = "user0108_1"
        user_profile = await self.engine.get_user_mem_by_page(user_id=user_id, scope_id=scope_id,
                                                              page_size=10, page_idx=1)
        logger.info(f"All user profiles after add_messages: {user_profile}")
        logger.info(f"Number of user profiles: {len(user_profile)}")

        user_variables_before = await self.engine.get_variables(user_id=user_id, scope_id=scope_id)
        logger.info(f"user_variables_before: {user_variables_before}")

        # Add some test data first
        await self.engine.set_scope_config(scope_id, MemoryScopeConfig(
            model_cfg=ModelRequestConfig(model="qwen-plus-latest", temperature=0.05),
            model_client_cfg=ModelClientConfig(
                client_id="1",
                client_provider="OpenAI",
                api_key="xxxx",
                api_base="xxxx",
                verify_ssl=False
            ),
            embedding_cfg=EmbeddingConfig(
                model_name=os.getenv("EMBED_MODEL_NAME", "text-embedding-v3"),
                api_key=os.getenv("EMBED_API_KEY", "xxxx"),
                base_url=os.getenv("EMBED_API_BASE", "xxxx"),
            )
        ))

        # Add some user variables
        await self.engine.update_variables(
            variables={"姓名": "张明", "职业": "软件工程师", "居住地": "杭州"},
            user_id=user_id,
            scope_id=scope_id
        )

        # Check that user memory exists before deletion
        user_profile_before = await self.engine.get_user_mem_by_page(user_id=user_id, scope_id=scope_id,
                                                                     page_size=10, page_idx=1)
        user_variables_before = await self.engine.get_variables(user_id=user_id, scope_id=scope_id)

        logger.info(f"All user profiles before delete_scope: {user_profile_before}")
        logger.info(f"Number of user profiles before: {len(user_profile_before)}")
        logger.info(f"User variables before delete_scope: {user_variables_before}")

        # Test delete_scope functionality
        delete_result = await self.engine.delete_scope_config(scope_id)
        delete_result_mem = await self.engine.delete_mem_by_scope(scope_id)

        self.assertTrue(delete_result)
        self.assertTrue(delete_result_mem)

        # Verify scope is deleted from kv_store
        retrieved_config = await self.engine.get_scope_config(scope_id)
        self.assertIsNone(retrieved_config)

        # Check that user memory is deleted after deletion
        user_profile_after = await self.engine.get_user_mem_by_page(user_id=user_id, scope_id=scope_id,
                                                                    page_size=10, page_idx=1)
        user_variables_after = await self.engine.get_variables(user_id=user_id, scope_id=scope_id)

        logger.info(f"All user profiles after delete_scope: {user_profile_after}")
        logger.info(f"Number of user profiles after: {len(user_profile_after)}")
        logger.info(f"User variables after delete_scope: {user_variables_after}")
        user_profile = await self.engine.get_user_mem_by_page(user_id=user_id, scope_id=scope_id,
                                                              page_size=10, page_idx=1)
        logger.info(f"All user profiles after add_messages: {user_profile}")
        logger.info(f"Number of user profiles: {len(user_profile)}")

        user_variables = await self.engine.get_variables(user_id=user_id, scope_id=scope_id)
        logger.info(f"All user variables after delete_scope: {user_variables}")


    async def test_add_messages(self):
        scope_id = "app0108_1"
        user_id = "user0108_1"
        # scope_model_cfg = ModelRequestConfig(model="deepseek-chat", temperature=0.05)
        # scope_model_client_cfg = ModelClientConfig(
        #     client_id="1",
        #     client_type="OpenAI",
        #     api_key="sk-86ef526f551b4770bcb83ad2d99e9c45",
        #     api_base="https://api.deepseek.com/v1",
        #     verify_ssl=False
        # )
        scope_model_cfg = ModelRequestConfig(model="qwen-max", temperature=0.05)
        scope_model_client_cfg = ModelClientConfig(
            client_id="1",
            client_provider="OpenAI",
            api_key="xxxx",
            api_base="xxxx",
            verify_ssl=False
        )
        embed_config = EmbeddingConfig(
            model_name=os.getenv("EMBED_MODEL_NAME", "text-embedding-v3"),
            api_key=os.getenv("EMBED_API_KEY", "xxxx"),
            base_url=os.getenv("EMBED_API_BASE", "xxxx"),
        )
        scope_cfg = MemoryScopeConfig(
            model_cfg=scope_model_cfg,
            model_client_cfg=scope_model_client_cfg,
            embedding_cfg=embed_config
        )
        agent_cfg = AgentMemoryConfig(
            mem_variables=[
                Param.string("姓名", "用户姓名", required=False),
                Param.string("职业", "用户职业", required=False),
                Param.string("居住地", "用户居住地", required=False),
                Param.string("爱好", "用户爱好", required=False),
                Param.string("年龄", "用户年龄", required=False)
            ],
            enable_long_term_mem=True,
        )
        await self.engine.set_scope_config(scope_id, scope_cfg)
        test_msg1 = BaseMessage(role="user",
                                content="你好，我叫张明")
        assistant_msg = BaseMessage(role="assistant",
                                    content="很高兴认识你")
        test_msg2 = BaseMessage(role="user",
                                content="我喜欢运动")
        test_msg3 = BaseMessage(role="user",
                                content="我今年20岁")
        test_msg4 = BaseMessage(role="user",
                                content="我的工作是软件工程师")
        test_msg5 = BaseMessage(role="user",
                                content="我来自杭州")

        # Add all messages at once so they are all treated as current messages for extraction
        timestamp = datetime.now(tz=timezone.utc)
        input_messages = [test_msg1, assistant_msg, test_msg2, test_msg3, test_msg4, test_msg5]
        await self.engine.add_messages(user_id=user_id, scope_id=scope_id,
                                   messages=input_messages, timestamp=timestamp, agent_config=agent_cfg)

        # Print all user profiles stored in memory
        user_profile = await self.engine.get_user_mem_by_page(user_id=user_id, scope_id=scope_id,
                                                              page_size=10, page_idx=1)
        logger.info(f"All user profiles after add_messages: {user_profile}")
        logger.info(f"Number of user profiles: {len(user_profile)}")
        for profile in user_profile:
            logger.info(f"Profile: {profile}")
        logger.info(f"get_user_profile raw: {user_profile}")
        test_variable = await self.engine.get_variables(user_id=user_id, scope_id=scope_id)
        logger.info(f"test_variable: {test_variable}")
        user_profile_mem_list = []
        user_profile_set = set()
        if user_profile:
            for mem in user_profile:
                logger.info(f"user profile: {mem.content}")
                user_profile_mem_list.append(mem.content)
                user_profile_set.add(mem.content)
        # Add more logs for debugging
        logger.info(f"user_profile_mem_list: {user_profile_mem_list}")
        logger.info(f"user_profile_set: {user_profile_set}")
        logger.info(f"user_profile_set length: {len(user_profile_set)}")

        # Check each expected profile individually
        expected_profiles = [
            "张明",
            "运动",
            "20",
            "软件工程师",
            "杭州"
        ]

        for expected_profile in expected_profiles:
            found = any(expected_profile in profile_content for profile_content in user_profile_mem_list)
            logger.info(f"Check if '{expected_profile}' exists in profiles: {found}")
        # Check the total number of user profiles using the already verified user_profile_set
        self.assertEqual(len(user_profile_set), 5, f"Expected total user profiles: 5, actual: {len(user_profile_set)}")
        logger.info(f"get_test_variable: {test_variable}")
        logger.info(f"test_variable length: {len(test_variable)}")
        # Check what's in test_variable
        for key, value in test_variable.items():
            logger.info(f"{key}: {value}")
        self.assertEqual(len(user_profile_set), 5)
        self.assertEqual(len(test_variable), 5)
        # check user profile value
        expect_user_profiles = [
            "张明",
            "运动",
            "20",
            "杭州"
        ]
        logger.info(f"Checking expect_user_profiles before update: {expect_user_profiles}")
        for expect_user_profile in expect_user_profiles:
            found = TestLongTermMemory._check_user_profile(expect_user_profile, user_profile_set)
            logger.info(f"Check if '{expect_user_profile}' is in user_profile_set: {found}")
            self.assertTrue(found)

        search_res = await self.engine.search_user_mem(user_id=user_id, scope_id=scope_id,
                                                       query="用户的职业", num=5)
        logger.info(f"Search results for '用户的职业': {search_res}")

        self.assertGreater(len(search_res), 0, "No occupation profiles found in search results")
        occupation_mem = search_res[0].mem_info
        logger.info(
            f"Found occupation profile from search: {occupation_mem.content} with mem_id: {occupation_mem.mem_id}")

        # test update user profile using the directly found mem_id
        await self.engine.update_mem_by_id(user_id=user_id,
                                           scope_id=scope_id,
                                           mem_id=occupation_mem.mem_id,
                                           memory="用户的职业是硬件工程师")
        # Get all user profiles after update (page_idx=0)
        user_profile = await self.engine.get_user_mem_by_page(user_id=user_id,
                                                              scope_id=scope_id,
                                                              page_size=10,
                                                              page_idx=0)
        if user_profile:
            for mem in user_profile:
                logger.info(f"after update user profile: {mem.content}")
        search_res = await self.engine.search_user_mem(user_id=user_id, scope_id=scope_id,
                                                       query="用户的职业", num=5)
        time.sleep(0.5)
        logger.info(f"search_res: {search_res}")
        # Check if we found the updated occupation profile
        self.assertGreater(len(search_res), 0, "No search results found for '用户的职业'")
        self.assertEqual(search_res[0].mem_info.content, "用户的职业是硬件工程师")

        # test variable
        self.assertEqual(test_variable['姓名'], "张明")
        self.assertEqual(test_variable['爱好'], "运动")
        self.assertNotEqual(test_variable['年龄'].find("20"), -1)
        self.assertEqual(test_variable['职业'], "软件工程师")
        self.assertEqual(test_variable['居住地'], "杭州")

        # test update variable
        await self.engine.update_variables(user_id=user_id,
                                               scope_id=scope_id,
                                               variables={"姓名": "李四"})
        test_variable = await self.engine.get_variables(user_id=user_id, scope_id=scope_id)
        self.assertEqual(len(test_variable), 5)
        self.assertEqual(test_variable['姓名'], "李四")

        # test delete variable
        await self.engine.delete_variables(user_id=user_id,
                                              scope_id=scope_id,
                                              names=["年龄"])
        test_variable = await self.engine.get_variables(user_id=user_id, scope_id=scope_id)
        self.assertEqual(len(test_variable), 4)
        self.assertEqual(test_variable['姓名'], "李四")
        self.assertNotIn("年龄", test_variable)

        # test delete all
        await self.engine.delete_mem_by_user_id(user_id=user_id,
                                               scope_id=scope_id)
        user_profile = await self.engine.get_user_mem_by_page(user_id=user_id, scope_id=scope_id,
                                                              page_size=10, page_idx=1)
        test_variable = await self.engine.get_variables(user_id=user_id, scope_id=scope_id)
        search_res = await self.engine.search_user_mem(user_id=user_id, scope_id=scope_id, query="用户的职业", num=1)
        self.assertTrue(not user_profile)
        self.assertTrue(not test_variable)
        self.assertTrue(not search_res)


if __name__ == "__main__":
    unittest.main()