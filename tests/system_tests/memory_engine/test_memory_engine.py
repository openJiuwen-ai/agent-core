import unittest
import os
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import create_async_engine

from openjiuwen.core.foundation.llm import ModelConfig
from openjiuwen.core.foundation.llm import BaseModelInfo
from openjiuwen.core.memory.config.config import MemoryEngineConfig, MemoryScopeConfig
from openjiuwen.core.memory.long_term_memory import LongTermMemory
from openjiuwen.core.memory.store.impl.dbm_kv_store import DbmKVStore
from openjiuwen.core.memory.store.impl.milvus_semantic_store import MilvusSemanticStore
from openjiuwen.core.memory.embed_models.api import APIEmbedModel
from openjiuwen.core.memory.store.impl.default_db_store import DefaultDbStore
from openjiuwen.core.foundation.llm import BaseMessage
from openjiuwen.core.common.logging import logger


@unittest.skip("skip system test")
class TestMemoryEngine(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    async def _create_memory_engine() -> LongTermMemory:
        model_provider = ""
        api_key = ""
        api_base = ""
        model_name = ""

        db_user = ""
        db_passport = ""
        db_host = ""
        db_port = ""
        agent_db_name = ""

        milvus_host = ""
        milvus_port = ""
        collection_name = ''
        embedding_dims = 1024

        embedding_base_url = ""
        embedding_model_name = ""
        embedding_api_key = ""

        sys_config_dict = {
            "record_message": True,
            "ai_msg_gen_max_len": 64,
            "history_window_size_to_gen_mem": 5,
        }
        sys_config = MemoryEngineConfig(**sys_config_dict)

        os.environ["LLM_SSL_VERIFY"] = "false"
        os.environ["RESTFUL_SSL_VERIFY"] = "false"
        os.environ["SSRF_PROTECT_ENABLED"] = "false"
        os.environ["EMBED_SSL_VERIFY"] = "false"

        base_model_config = ModelConfig(
            model_provider=model_provider,
            model_info=BaseModelInfo(
                api_key=api_key,
                api_base=api_base,
                model=model_name,
            )
        )

        db_engine_instance = create_async_engine(
            f"mysql+aiomysql://{db_user}:{db_passport}@{db_host}:{db_port}/{agent_db_name}?charset=utf8mb4",
            pool_size=20,
            max_overflow=20
        )
        db_store = DefaultDbStore(db_engine_instance)

        dbm_test_dir = "test_dbm"
        os.makedirs(dbm_test_dir, exist_ok=True)
        dbm_kv_path = os.path.join(dbm_test_dir, "testdb")
        dbm_kv_store = DbmKVStore(dbm_kv_path)
        embed_model = APIEmbedModel(model_name=embedding_model_name,
                                    base_url=embedding_base_url,
                                    api_key=embedding_api_key,
                                    max_retries=3,
                                    timeout=60)
        sem_store = MilvusSemanticStore(milvus_host=milvus_host,
                                        milvus_port=milvus_port,
                                        token=None,
                                        embed_model=embed_model,
                                        collection_name=collection_name,
                                        embedding_dims=embedding_dims)

        memory_engine = await LongTermMemory.register_store(kv_store=dbm_kv_store,
                                                          semantic_store=sem_store,
                                                          db_store=db_store).create_mem_engine_instance(sys_config)

        memory_engine.init_base_llm(base_model_config)
        return memory_engine

    @staticmethod
    async def _check_user_profile(expect_profile: str, user_profile_set: set) -> bool:
        for user_profile in user_profile_set:
            if user_profile.find(expect_profile) != -1:
                return True
        return False

    @unittest.skip("skip system test")
    async def test_memory_engine(self):
        logger.set_level("INFO")
        mem_engine = await TestMemoryEngine._create_memory_engine()
        mem_config_dict = {
            "mem_variables": {
                "姓名": "用户姓名",
                "职业": "用户职业",
                "居住地": "用户居住地",
                "爱好": "用户爱好",
                "年龄": "用户年龄"
            },
            "enable_long_term_mem": True
        }
        user_id = "user_id12345abcd"
        group_id = "group_id12345abcd"
        mem_config = MemoryScopeConfig(**mem_config_dict)
        mem_engine.set_group_config(group_id, mem_config)
        recent_message = await mem_engine.get_message_by_id(user_id)
        self.assertIsNone(recent_message)
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
        input_messages = [[test_msg1, assistant_msg], [test_msg2], [test_msg3], [test_msg4, test_msg5]]
        message_id = "-1"
        # add
        for input_message in input_messages:
            timestamp = datetime.now(tz=timezone.utc)
            message_id = await mem_engine.add_conversation_messages(user_id, group_id, input_message, timestamp)

        # test get_message_by_id
        recent_message = await mem_engine.get_message_by_id(message_id)
        logger.info(f"recent_message: {recent_message}")
        self.assertEqual(recent_message[0].content, test_msg5.content)

        user_profile = await mem_engine.list_user_mem(user_id=user_id, group_id=group_id, num=10, page=1)
        test_variable = await mem_engine.list_user_variables(user_id=user_id, group_id=group_id)
        logger.info(f"get_user_profile: {user_profile}, message_id: {message_id}")
        logger.info(f"test_variable: {test_variable}")
        user_profile_mem_list = []
        user_profile_set = set()
        if user_profile:
            for mem in user_profile:
                logger.info(f"user profile: {mem['mem']}")
                user_profile_mem_list.append(mem['mem'])
                user_profile_set.add(mem['mem'])
        # test user profile no overlap
        self.assertEqual(len(user_profile_mem_list), len(user_profile_set))
        logger.info(f"get_test_variable: {test_variable}")
        self.assertEqual(len(user_profile), 5)
        self.assertEqual(len(test_variable), 5)

        # check user profile value
        expect_user_profiles = [
            "张明",
            "运动",
            "20",
            "软件工程师",
            "杭州"
        ]
        for expect_user_profile in expect_user_profiles:
            self.assertTrue(TestMemoryEngine._check_user_profile(expect_user_profile, user_profile_set))

        # test search user profile
        search_res = await mem_engine.search_user_mem(user_id=user_id, group_id=group_id, query="用户的职业", num=1)
        logger.info(f"search_res: {search_res}")
        self.assertEqual(len(search_res), 1)
        self.assertEqual(search_res[0]['mem'], "用户的职业是软件工程师")

        # test update user profile
        await mem_engine.update_mem_by_id(user_id=user_id,
                                          group_id=group_id,
                                          mem_id=search_res[0]['id'],
                                          memory="用户的职业是硬件工程师")

        user_profile = await mem_engine.list_user_mem(user_id=user_id, group_id=group_id, num=10, page=1)
        if user_profile:
            for mem in user_profile:
                logger.info(f"after update user profile: {mem['mem']}")
        search_res = await mem_engine.search_user_mem(user_id=user_id, group_id=group_id, query="用户的职业", num=5)
        logger.info(f"search_res: {search_res}")
        self.assertEqual(len(search_res), 5)
        self.assertEqual(search_res[0]['mem'], "用户的职业是硬件工程师")

        # test delete user profile
        await mem_engine.delete_mem_by_id(user_id, group_id, search_res[0]['id'])
        user_profile = await mem_engine.list_user_mem(user_id=user_id, group_id=group_id, num=10, page=1)
        self.assertEqual(len(user_profile), 4)

        # test variable
        self.assertEqual(test_variable['姓名'], "张明")
        self.assertEqual(test_variable['爱好'], "运动")
        self.assertNotEqual(test_variable['年龄'].find("20"), -1)
        self.assertEqual(test_variable['职业'], "软件工程师")
        self.assertEqual(test_variable['居住地'], "杭州")

        # test update variable
        await mem_engine.update_user_variable(user_id=user_id,
                                              group_id=group_id,
                                              name="姓名",
                                              value="李四")
        test_variable = await mem_engine.list_user_variables(user_id=user_id, group_id=group_id)
        self.assertEqual(len(test_variable), 5)
        self.assertEqual(test_variable['姓名'], "李四")

        # test delete variable
        await mem_engine.delete_user_variable(user_id=user_id,
                                              group_id=group_id,
                                              name="年龄")
        test_variable = await mem_engine.list_user_variables(user_id=user_id, group_id=group_id)
        self.assertEqual(len(test_variable), 4)
        self.assertNotIn("年龄", test_variable)

        # test delete all
        await mem_engine.delete_mem_by_user_id(user_id=user_id,
                                               group_id=group_id)
        user_profile = await mem_engine.list_user_mem(user_id=user_id, group_id=group_id, num=10, page=1)
        test_variable = await mem_engine.list_user_variables(user_id=user_id, group_id=group_id)
        search_res = await mem_engine.search_user_mem(user_id=user_id, group_id=group_id, query="用户的职业", num=1)
        self.assertTrue(not user_profile)
        self.assertTrue(not test_variable)
        self.assertTrue(not search_res)
