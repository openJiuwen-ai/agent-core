import os
import base64
import time
import unittest
from datetime import datetime, timezone

from openjiuwen.core.memory.long_term_memory import LongTermMemory
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm1.schema.message import BaseMessage
from openjiuwen.core.memory.store.impl.dbm_kv_store import DbmKVStore
from openjiuwen.core.memory.store.impl.milvus_semantic_store import MilvusSemanticStore
from openjiuwen.core.memory.embed_models.api import APIEmbedModel
from openjiuwen.core.foundation.llm1.schema.config import ModelConfig, ModelClientConfig
from openjiuwen.core.memory.store.impl.default_db_store import DefaultDbStore
from openjiuwen.core.memory.config.config import MemoryEngineConfig, MemoryScopeConfig
from sqlalchemy.ext.asyncio import create_async_engine
from openjiuwen.core.common.schema.param import Param


@unittest.skip("skip system test")
class TestLongTermMemory(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        # reset singleton
        self.engine = LongTermMemory()

        try:
            crypto_key = base64.b64decode(os.getenv("SERVER_AES_MASTER_KEY_ENV", ""))
        except Exception:
            crypto_key = b""

        # ---------- Embed ----------
        embed_model = APIEmbedModel(
            base_url=os.getenv("EMBED_API_BASE", "xxxx"),
            model_name=os.getenv("EMBED_MODEL_NAME", "xxxx"),
            api_key=os.getenv("EMBED_API_KEY", "xxxx"),
            timeout=int(os.getenv("EMBED_TIMEOUT", 60)),
            max_retries=int(os.getenv("EMBED_MAX_RETRIES", 3)),
        )

        # ---------- KV Store ----------
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        resource_dir = os.path.join(project_root, "resources")
        os.makedirs(resource_dir, exist_ok=True)
        kv_path = os.path.join(resource_dir, "dbmstore")
        kv_store = DbmKVStore(kv_path)

        # ---------- semantic_store ----------
        semantic_store = MilvusSemanticStore(
            milvus_host=os.getenv("MILVUS_HOST", "xxxx"),
            milvus_port=os.getenv("MILVUS_PORT", "xxxx"),
            collection_name=os.getenv("MILVUS_COLLECTION_NAME", "xxxx"),
            embedding_dims=int(os.getenv("EMBEDDING_MODEL_DIMENTION", 1024)),
            embed_model=embed_model,
            token=os.getenv("MILVUS_TOKEN", None)
        )

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
        default_model_cfg = ModelConfig(model="xxxx")
        default_model_client_cfg = ModelClientConfig(
            client_id="xxxx",
            client_type="OpenAI",
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
            semantic_store=semantic_store,
            db_store=db_store
        )
        self.engine.set_config(self.memory_engine_config)

    @staticmethod
    async def _check_user_profile(expect_profile: str, user_profile_set: set) -> bool:
        for user_profile in user_profile_set:
            if user_profile.find(expect_profile) != -1:
                return True
        return False

    async def test_engine_initialized(self):
        self.assertIsNotNone(self.engine._sys_mem_config)
        self.assertIsNotNone(self.engine.kv_store)
        self.assertIsNotNone(self.engine.semantic_store)
        self.assertIsNotNone(self.engine.db_store)

    async def test_set_scope_config(self):
        scope_id = "test_scope"
        scope_model_cfg = ModelConfig(model="xxxx", temperature=0.05)
        scope_model_client_cfg = ModelClientConfig(
            client_id="xxxx",
            client_type="OpenAI",
            api_key="xxxx",
            api_base="xxxx",
            verify_ssl=False
        )
        scope_cfg = MemoryScopeConfig(
            mem_variables=[
                Param.string("姓名", "用户姓名", required=False),
                Param.string("职业", "用户职业", required=False),
                Param.string("居住地", "用户居住地", required=False),
                Param.string("爱好", "用户爱好", required=False),
            ],
            enable_long_term_mem=True,
            model_cfg=scope_model_cfg,
            model_client_cfg=scope_model_client_cfg)
        result = self.engine.set_scope_config(scope_id, scope_cfg)
        self.assertTrue(result)
        self.assertIn(scope_id, self.engine._scope_config)
        self.assertEqual(self.engine._scope_config[scope_id], scope_cfg)

    async def test_add_messages(self):
        scope_id = "app0107_1"
        scope_model_cfg = ModelConfig(model="xxxx", temperature=0.05)
        scope_model_client_cfg = ModelClientConfig(
            client_id="xxxx",
            client_type="OpenAI",
            api_key="xxxx",
            api_base="xxxx",
            verify_ssl=False
        )
        scope_cfg = MemoryScopeConfig(
            mem_variables=[
                Param.string("姓名", "用户姓名", required=False),
                Param.string("职业", "用户职业", required=False),
                Param.string("居住地", "用户居住地", required=False),
                Param.string("爱好", "用户爱好", required=False),
                Param.string("年龄", "用户年龄", required=False)
            ],
            enable_long_term_mem=True,
            model_cfg=scope_model_cfg,
            model_client_cfg=scope_model_client_cfg)
        self.engine.set_scope_config(scope_id, scope_cfg)
        user_id = "user0107_1"
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
        for input_message in input_messages:
            timestamp = datetime.now(tz=timezone.utc)
            await self.engine.add_messages(user_id=user_id, scope_id=scope_id,
                                       messages=input_message, timestamp=timestamp)

        user_profile = await self.engine.get_user_mem_by_page(user_id=user_id, scope_id=scope_id,
                                                              page_size=10, page_idx=1)
        test_variable = await self.engine.get_user_variable(user_id=user_id, scope_id=scope_id)
        logger.info(f"get_user_profile: {user_profile}")
        logger.info(f"test_variable: {test_variable}")
        user_profile_mem_list = []
        user_profile_set = set()
        if user_profile:
            for mem in user_profile:
                logger.info(f"user profile: {mem.content}")
                user_profile_mem_list.append(mem.content)
                user_profile_set.add(mem.content)
        # test user_mem_total_num
        total_num = await self.engine.user_mem_total_num(user_id=user_id, scope_id=scope_id)
        self.assertEqual(total_num, 5)
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
            self.assertTrue(TestLongTermMemory._check_user_profile(expect_user_profile, user_profile_set))

        #test search user profile
        search_res = await self.engine.search_user_mem(user_id=user_id, scope_id=scope_id, query="用户的职业", num=1)
        logger.info(f"search_res: {search_res}")
        self.assertEqual(len(search_res), 1)
        self.assertEqual(search_res[0].mem_info.content, "用户的职业是软件工程师")

        # test update user profile
        await self.engine.update_mem_by_id(user_id=user_id,
                                          scope_id=scope_id,
                                          mem_id=search_res[0].mem_info.mem_id,
                                          memory="用户的职业是硬件工程师")
        user_profile = await self.engine.get_user_mem_by_page(user_id=user_id,
                                                              scope_id=scope_id,
                                                              page_size=10,
                                                              page_idx=1)
        if user_profile:
            for mem in user_profile:
                logger.info(f"after update user profile: {mem.content}")
        search_res = await self.engine.search_user_mem(user_id=user_id, scope_id=scope_id,
                                                       query="用户的职业", num=5)
        time.sleep(0.5)
        logger.info(f"search_res: {search_res}")
        self.assertEqual(len(search_res), 5)
        self.assertEqual(search_res[0].mem_info.content, "用户的职业是硬件工程师")

        # test variable
        self.assertEqual(test_variable['姓名'], "张明")
        self.assertEqual(test_variable['爱好'], "运动")
        self.assertNotEqual(test_variable['年龄'].find("20"), -1)
        self.assertEqual(test_variable['职业'], "软件工程师")
        self.assertEqual(test_variable['居住地'], "杭州")

        # test update variable
        await self.engine.update_user_variable(user_id=user_id,
                                              scope_id=scope_id,
                                               variables={"姓名": "李四"})
        test_variable = await self.engine.get_user_variable(user_id=user_id, scope_id=scope_id)
        self.assertEqual(len(test_variable), 5)
        self.assertEqual(test_variable['姓名'], "李四")

        # test delete variable
        await self.engine.delete_user_variable(user_id=user_id,
                                              scope_id=scope_id,
                                              names=["年龄"])
        test_variable = await self.engine.get_user_variable(user_id=user_id, scope_id=scope_id)
        self.assertEqual(len(test_variable), 4)
        self.assertEqual(test_variable['姓名'], "李四")
        self.assertNotIn("年龄", test_variable)

        # test delete all
        await self.engine.delete_mem_by_user_id(user_id=user_id,
                                               scope_id=scope_id)
        user_profile = await self.engine.get_user_mem_by_page(user_id=user_id, scope_id=scope_id,
                                                              page_size=10, page_idx=1)
        test_variable = await self.engine.get_user_variable(user_id=user_id, scope_id=scope_id)
        search_res = await self.engine.search_user_mem(user_id=user_id, scope_id=scope_id, query="用户的职业", num=1)
        self.assertTrue(not user_profile)
        self.assertTrue(not test_variable)
        self.assertTrue(not search_res)


if __name__ == "__main__":
    unittest.main()
