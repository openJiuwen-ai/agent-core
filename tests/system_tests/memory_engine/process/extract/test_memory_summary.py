import os
import base64
import unittest
import asyncio
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import create_async_engine

from openjiuwen.core.memory.long_term_memory import LongTermMemory
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.schema.message import BaseMessage
from openjiuwen.core.foundation.store.kv.in_memory_kv_store import InMemoryKVStore
from openjiuwen.core.foundation.store import create_vector_store
from openjiuwen.core.foundation.llm.schema.config import ModelRequestConfig, ModelClientConfig
from openjiuwen.core.foundation.store.db.default_db_store import DefaultDbStore
from openjiuwen.core.memory.config.config import MemoryEngineConfig, AgentMemoryConfig, MemoryScopeConfig
from openjiuwen.core.retrieval.common.config import EmbeddingConfig
from openjiuwen.core.memory.manage.mem_model.memory_unit import MemoryType
from openjiuwen.core.application.llm_agent import LLMAgent
from openjiuwen.core.single_agent.legacy.config import ReActAgentConfig
from openjiuwen.core.foundation.llm import ModelConfig
from openjiuwen.core.foundation.llm.schema.mode_info import BaseModelInfo


@unittest.skip("skip system test")
class TestLongTermMemory(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):

        # reset singleton
        self.engine = LongTermMemory()

        try:
            crypto_key = base64.b64decode(os.getenv("SERVER_AES_MASTER_KEY_ENV", ""))
        except Exception:
            crypto_key = b""

        # ---------- KV Store ----------
        kv_store = InMemoryKVStore()

        # ---------- vector_store ----------
        self.vector_store = create_vector_store(
            "chroma",
            milvus_host=os.getenv("MILVUS_HOST"),
            milvus_port=os.getenv("MILVUS_PORT"),
            embedding_dims=int(os.getenv("EMBEDDING_MODEL_DIMENTION")),
            token=os.getenv("MILVUS_TOKEN", None)
        )
        # ---------- db_store ----------
        db_user = os.getenv("DB_USER")
        db_passport = os.getenv("DB_PASSWORD")
        db_host = os.getenv("DB_HOST")
        db_port = os.getenv("DB_PORT")
        agent_db_name = os.getenv("AGENT_DB_NAME")


        db_store = DefaultDbStore(create_async_engine(
            f"mysql+aiomysql://{db_user}:{db_passport}@{db_host}:{db_port}/{agent_db_name}?charset=utf8mb4",
            pool_size=20,
            max_overflow=20
        ))

        # ---------- Config ----------
        default_model_cfg = ModelRequestConfig(model=os.getenv("MODEL_NAME"))
        default_model_client_cfg = ModelClientConfig(
            client_id="1",
            client_provider=os.getenv("MODEL_PROVIDER"),
            api_key=os.getenv("API_KEY"),
            api_base=os.getenv("API_BASE"),
            verify_ssl=False
        )
        self.memory_engine_config = MemoryEngineConfig(
            default_model_cfg=default_model_cfg,
            default_model_client_cfg=default_model_client_cfg,
            crypto_key=crypto_key
        )

        await self.engine.register_store(
            kv_store=kv_store,
            vector_store=self.vector_store,
            db_store=db_store
        )
        self.engine.set_config(self.memory_engine_config)


    async def test_add_summary(self):
        # ===================== 1. enable summary =====================
        scope_id = "app0131_07"
        user_id = "user0131_07"
        scope_model_cfg = ModelRequestConfig(model=os.getenv("MODEL_NAME"), temperature=0.05)
        scope_model_client_cfg = ModelClientConfig(
                client_id="1",
                client_provider=os.getenv("MODEL_PROVIDER"),
                api_key=os.getenv("API_KEY"),
                api_base=os.getenv("API_BASE"),
                verify_ssl=False
            )
        embed_config = EmbeddingConfig(
            model_name=os.getenv("EMBED_MODEL_NAME", os.getenv("EMBED_MODEL_NAME")),
            api_key=os.getenv("EMBED_API_KEY"),
            base_url=os.getenv("EMBED_API_BASE"),
        )
        scope_cfg = MemoryScopeConfig(
            model_cfg=scope_model_cfg,
            model_client_cfg=scope_model_client_cfg,
            embedding_cfg=embed_config
        )
        agent_cfg = AgentMemoryConfig(
            mem_variables=[],
            enable_long_term_mem=True,
        )
        await self.engine.set_scope_config(scope_id, scope_cfg)

        # before add_messages
        user_summary = await self.engine.search_user_history_summary(user_id=user_id, scope_id=scope_id,
                                                                     query="张明", num=10)
        logger.info(f"All user summary before add_messages：{user_summary}")
        assert len(user_summary) == 0

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

        # Print all user summary stored in memory
        user_summary = await self.engine.get_user_mem_by_page(user_id=user_id, scope_id=scope_id,
                                                              page_size=10, page_idx=1, memory_type=MemoryType.SUMMARY)
        logger.info(f"All user summary after add_messages: {user_summary}")
        for summary in user_summary:
            assert "张明" in summary.content
            assert "20岁" in summary.content
            assert "软件工程师" in summary.content
            assert "杭州" in summary.content
            assert "运动" in summary.content
        logger.info(f"Number of user summary: {len(user_summary)}")
        for summary in user_summary:
            logger.info(f"Summary: {summary}")
        logger.info(f"get_user_summary raw: {user_summary}")

        # search_user_summary
        user_summary = await self.engine.search_user_history_summary(user_id=user_id,
                                                                     scope_id=scope_id, query="张明", num=10)
        logger.info(f"user summary: {user_summary}")
        for summary in user_summary:
            assert "张明" in summary.mem_info.content

        # delete summary
        await self.engine.delete_mem_by_id(user_id=user_id, scope_id=scope_id, mem_id=user_summary[0].mem_info.mem_id)
        user_summary = await self.engine.search_user_history_summary(user_id=user_id,
                                                                     scope_id=scope_id, query="张明", num=10)
        logger.info(f"user summary after delete: {user_summary}")
        assert len(user_summary) == 0

        # ===================== 2. disable summary =====================
        agent_cfg = AgentMemoryConfig(
            mem_variables=[],
            enable_long_term_mem=False,
        )
        await self.engine.add_messages(user_id=user_id, scope_id=scope_id,
                                       messages=input_messages, timestamp=timestamp, agent_config=agent_cfg)
        user_summary = await self.engine.search_user_history_summary(user_id=user_id, scope_id=scope_id, query="张明",
                                                                     num=10)
        logger.info(f"user summary after delete: {user_summary}")
        assert len(user_summary) == 0

    async def test_agent_add_summary(self):
        # ===================== 1. enable summary =====================
        scope_id = "app0131_08"
        user_id = "user0131_08"
        scope_model_cfg = ModelRequestConfig(model=os.getenv("MODEL_NAME"), temperature=0.05)
        scope_model_client_cfg = ModelClientConfig(
            client_id="1",
            client_provider=os.getenv("MODEL_PROVIDER"),
            api_key=os.getenv("API_KEY"),
            api_base=os.getenv("API_BASE"),
            verify_ssl=False
        )
        embed_config = EmbeddingConfig(
            model_name=os.getenv("EMBED_MODEL_NAME", os.getenv("EMBED_MODEL_NAME")),
            api_key=os.getenv("EMBED_API_KEY"),
            base_url=os.getenv("EMBED_API_BASE"),
        )
        scope_cfg = MemoryScopeConfig(
            model_cfg=scope_model_cfg,
            model_client_cfg=scope_model_client_cfg,
            embedding_cfg=embed_config
        )
        agent_cfg = AgentMemoryConfig(
            mem_variables=[],
            enable_long_term_mem=True,
        )
        await self.engine.set_scope_config(scope_id, scope_cfg)

        # before add_messages
        user_summary = await self.engine.search_user_history_summary(user_id=user_id,
                                                                     scope_id=scope_id, query="张明", num=10)
        logger.info(f"All user summary before add_messages：{user_summary}")
        assert len(user_summary) == 0

        model_cfg = ModelConfig(model_provider=os.getenv("MODEL_PROVIDER"), model_info=BaseModelInfo(
            api_key=os.getenv("API_KEY"),
            api_base=os.getenv("API_BASE"),
            model=os.getenv("MODEL_NAME"),
            temperature=0.95,
            top_p=0.1,
            timeout=60,
        ))
        llm_agent_cfg = ReActAgentConfig(
            id=scope_id,
            version="0.0.1",
            description="AI助手",
            plugins=[],
            workflows=[],
            model=model_cfg,
            prompt_template=[
                {"role": "system", "content": "你是一个智能助手，你的任务是根据用户的输入，完成用户的请求。"}],
            tools=[],
            memory_scope_id=scope_id,
            agent_memory_config=agent_cfg
        )
        llm_agent = LLMAgent(llm_agent_cfg)

        query = ["我叫张明", "var等于7"]
        for q in query:
            result = await llm_agent.invoke({"query": q, "user_id": user_id, "scope_id": scope_id})
            logger.info(f"llm输出:{result}")
        await asyncio.sleep(30)

        # search_user_summary
        user_summary = await self.engine.search_user_history_summary(user_id=user_id,
                                                                     scope_id=scope_id,
                                                                     query="张明",
                                                                     num=10)
        logger.info(f"user summary: {user_summary}")
        for summary in user_summary:
            assert "张明" in summary.mem_info.content or "变量" in summary.mem_info.content

        # delete summary
        await self.engine.delete_mem_by_user_id(user_id=user_id, scope_id=scope_id)
        user_summary = await self.engine.search_user_history_summary(user_id=user_id,
                                                                     scope_id=scope_id, query="张明", num=10)
        logger.info(f"user summary after delete: {user_summary}")
        assert len(user_summary) == 0


if __name__ == "__main__":
    unittest.main()