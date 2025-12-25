import os
import shutil
import uuid
from pathlib import Path
from datetime import datetime, timezone
import asyncio
import pytest
from sqlalchemy.ext.asyncio.engine import create_async_engine
from openjiuwen.core.memory.embed_models import APIEmbedModel
from openjiuwen.core.memory.store.impl.dbm_kv_store import DbmKVStore
from openjiuwen.core.memory.store.impl.default_db_store import DefaultDbStore
from openjiuwen.core.memory.store.impl.milvus_semantic_store import MilvusSemanticStore
from openjiuwen.core.utils.llm.messages import HumanMessage
from openjiuwen.core.memory.engine.memory_engine import MemoryEngine
from openjiuwen.core.memory.config.config import SysMemConfig, MemoryConfig
from openjiuwen.core.component.common.configs.model_config import ModelConfig
from openjiuwen.core.utils.llm.base import BaseModelInfo
from openjiuwen.core.common.logging import logger

API_BASE = os.getenv("API_BASE", "")
API_KEY = os.getenv("API_KEY", "")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")
os.environ.setdefault("LLM_SSL_VERIFY", "false")


@pytest.fixture(name="memory_engine_instance", scope="class")
def get_memory_engine():
    current_dir = os.path.dirname("./")
    resource_dir = os.path.join(current_dir, 'resources')
    os.makedirs(resource_dir, exist_ok=True)
    kv_db_path = os.path.join(resource_dir, 'dbmstore')
    config = SysMemConfig(
        crypto_key=os.getenv("SERVER_AES_MASTER_KEY_ENV", b'')
    )
    embed_model = APIEmbedModel(
        base_url=os.getenv("EMBED_API_BASE"),
        model_name=os.getenv("EMBED_MODEL_NAME"),
        api_key=os.getenv("EMBED_API_KEY"),
        timeout=int(os.getenv("EMBED_TIMEOUT", 60)),
        max_retries=int(os.getenv("EMBED_MAX_RETRIES", 3)),
    )
    semantic_store = MilvusSemanticStore(
        milvus_host=os.getenv("MILVUS_HOST"),
        milvus_port=os.getenv("MILVUS_PORT"),
        collection_name=os.getenv("MILVUS_COLLECTION_NAME"),
        embedding_dims=os.getenv("EMBEDDING_MODEL_DIMENTION", 1024),
        embed_model=embed_model,
        token=os.getenv("MILVUS_TOKEN", None)
    )
    utc_now = datetime.now(timezone.utc)
    time_str = utc_now.strftime("%Y%m%d%H%M%S")
    uuid_str = uuid.uuid4().hex[:6]
    path = Path(f"{resource_dir}/test_sql_db_{time_str}_{uuid_str}.db").resolve()
    db_store = DefaultDbStore(create_async_engine(f"sqlite+aiosqlite:///{path}"))
    MemoryEngine.register_store(
        kv_store=DbmKVStore(kv_db_path),
        db_store=db_store,
        semantic_store=semantic_store
    )
    engine = asyncio.run(MemoryEngine.create_mem_engine_instance(config))
    yield engine

    abs_path = os.path.abspath(resource_dir)
    if os.path.isdir(abs_path):
        shutil.rmtree(abs_path)


class TestMemoryEngine:
    @pytest.mark.skip(reason="need real llm & embedding & milvus")
    @pytest.mark.asyncio
    async def test_basic(self, memory_engine_instance):
        user_id = "test_basic1"
        group_id = "test_basic1"
        memory_engine_instance.set_group_llm_config(group_id=group_id, llm_config=ModelConfig(
            model_provider=MODEL_PROVIDER,
            model_info=BaseModelInfo(
                api_key=API_KEY,
                api_base=API_BASE,
                model=MODEL_NAME
            )
        ))
        memory_engine_instance.set_group_config(group_id=group_id, config=MemoryConfig(
            mem_variables={"name": "用户的姓名", "age": "用户的年龄", "career": "用户的职业"},
            enable_long_term_mem=True
        ))
        # test add memory
        await memory_engine_instance.add_conversation_messages(user_id=user_id, group_id=group_id, messages=[
            HumanMessage(content="你好，我叫张明，今年20岁，目前刚到杭州来杭州做软件开发工作，比较喜欢吃甜口的")
        ], timestamp=datetime.now(timezone.utc))
        variable_memory = await memory_engine_instance.list_user_variables(user_id=user_id, group_id=group_id)
        assert len(variable_memory) == 3
        logger.info(f"all variable_memory: \n{variable_memory}")
        long_term_memory = await memory_engine_instance.list_user_mem(user_id=user_id, group_id=group_id, num=999,
                                                                      page=1)
        long_term_memory_size = len(long_term_memory)
        assert long_term_memory_size > 1
        logger.info(f"all long_term_memory: \n{long_term_memory}")

        # test update variable_memory
        assert (await memory_engine_instance.update_user_variable(user_id=user_id, group_id=group_id, name="name",
                                                                  value="王武"))
        variable_memory = await memory_engine_instance.list_user_variables(user_id=user_id, group_id=group_id)
        assert variable_memory.get("name") == "王武"
        logger.info(f"updated variable_memory: \n{variable_memory}")
        # test delete variable_memory
        assert (await memory_engine_instance.delete_user_variable(user_id=user_id, group_id=group_id, name="name"))
        variable_memory = await memory_engine_instance.list_user_variables(user_id=user_id, group_id=group_id)
        assert variable_memory.get("name", "none") == "none"
        logger.info(f"deleted variable_memory: \n{variable_memory}")
        # test update long_term_memory
        update_id = long_term_memory[0]["id"]
        assert (await memory_engine_instance.update_mem_by_id(user_id=user_id, group_id=group_id, mem_id=update_id,
                                                              memory="用户喜欢打羽毛球"))
        long_term_memory = await memory_engine_instance.list_user_mem(user_id=user_id, group_id=group_id, num=999,
                                                                      page=1)
        assert long_term_memory_size == len(long_term_memory)
        for mem in long_term_memory:
            if mem["id"] == update_id:
                assert mem["mem"] == "用户喜欢打羽毛球"
        logger.info(f"updated long_term_memory: \n{long_term_memory}")
        # test search long_term_memory
        search_result = await memory_engine_instance.search_user_mem(user_id=user_id, group_id=group_id,
                                                               query="用户喜欢什么运动？", num=1)
        assert len(search_result) == 1
        assert search_result[0]["id"] == update_id
        assert search_result[0]["mem"] == "用户喜欢打羽毛球"
        logger.info(f"search long_term_memory result: \n{search_result}")
        # test delete long_term_memory
        assert (await memory_engine_instance.delete_mem_by_id(user_id=user_id, group_id=group_id, mem_id=update_id))
        long_term_memory = await memory_engine_instance.list_user_mem(user_id=user_id, group_id=group_id, num=999,
                                                                      page=1)
        assert len(long_term_memory) == long_term_memory_size - 1
        for mem in long_term_memory:
            assert mem["id"] != update_id
        logger.info(f"deleted long_term_memory: \n{long_term_memory}")
