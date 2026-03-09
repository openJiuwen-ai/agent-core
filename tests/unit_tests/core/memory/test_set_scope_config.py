import logging
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from openjiuwen.core.memory.long_term_memory import LongTermMemory
from openjiuwen.core.memory.config.config import MemoryScopeConfig, ModelRequestConfig, ModelClientConfig
from openjiuwen.core.foundation.store import InMemoryKVStore
from openjiuwen.core.foundation.store.base_vector_store import BaseVectorStore
from openjiuwen.core.foundation.store.base_db_store import BaseDbStore
from openjiuwen.core.retrieval.common.config import EmbeddingConfig

logger = logging.getLogger(__name__)


class MockVectorStore(BaseVectorStore):
    async def create_collection(self, *args, **kwargs):
        pass
    
    async def drop_collection(self, *args, **kwargs):
        pass
    
    async def add_docs(self, *args, **kwargs):
        pass
    
    async def search(self, *args, **kwargs):
        return []
    
    async def delete(self, *args, **kwargs):
        pass
    
    async def get_schema(self, *args, **kwargs):
        pass
    
    async def add_doc(self, *args, **kwargs):
        pass
    
    async def collection_exists(self, *args, **kwargs):
        return False
    
    async def delete_collection(self, *args, **kwargs):
        pass
    
    async def delete_docs_by_filters(self, *args, **kwargs):
        pass
    
    async def delete_docs_by_ids(self, *args, **kwargs):
        pass


class MockAsyncEngine:
    def begin(self):
        # 返回一个模拟的连接对象，实现异步上下文管理器
        return self
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass
    
    async def run_sync(self, func):
        # 模拟同步运行函数，不执行任何实际操作
        pass


class MockDbStore(BaseDbStore):
    def get_async_engine(self) -> AsyncEngine:
        return MockAsyncEngine()
    
    async def execute(self, *args, **kwargs):
        pass
    
    async def close(self):
        pass


@pytest.mark.asyncio
async def test_set_scope_config_without_set_config():
    """
    测试不调用set_config函数，直接调用set_scope_config程序是否会挂
    """
    logger.info("=== 开始测试: 不调用set_config直接调用set_scope_config ===")
    
    try:
        # 创建LongTermMemory实例（这会调用__init__方法）
        memory = LongTermMemory()
        
        # 先注册存储
        mock_kv = InMemoryKVStore()
        mock_vector = MockVectorStore()
        mock_db = MockDbStore()
        
        logger.info("注册模拟存储...")
        await memory.register_store(
            kv_store=mock_kv,
            vector_store=mock_vector,
            db_store=mock_db
        )
        
        # 打印注册后的状态
        logger.info(f"注册后 - kv_store: {memory.kv_store is not None}")
        logger.info(f"注册后 - vector_store: {memory.vector_store is not None}")
        logger.info(f"注册后 - db_store: {memory.db_store is not None}")

        # 直接调用set_scope_config
        scope_id = "test_scope_123"

        # 创建一个简单的MemoryScopeConfig实例
        scope_config = MemoryScopeConfig(
            model_cfg=ModelRequestConfig(model="test_model"),
            model_client_cfg=ModelClientConfig(
                client_provider="DashScope",
                api_key="test_api_key",
                api_base="https://dashscope.aliyuncs.com/api/v1"
            ),
            embedding_cfg=EmbeddingConfig(
                model_name="test_embedding_model",
                base_url="https://dashscope.aliyuncs.com/api/v1",
                api_key="test_api_key",
            )
        )

        logger.info(f"准备调用set_scope_config，scope_id: {scope_id}")
        result = await memory.set_scope_config(scope_id, scope_config)
        logger.info(f"set_scope_config调用结果: {result}")

        # 验证配置是否成功设置
        logger.info("\n验证配置是否成功设置...")
        retrieved_config = await memory.get_scope_config(scope_id)
        if retrieved_config:
            logger.info("配置设置成功！")
            logger.info(f"model_name: {retrieved_config.model_cfg.model_name}")
            logger.info(f"client_provider: {retrieved_config.model_client_cfg.client_provider}")
        else:
            logger.info("配置设置失败！")
        
        logger.info("\n=== 测试完成: 程序未崩溃 ===")
        return True
        
    except Exception as e:
        logger.error(f"\n=== 测试结果: 程序发生异常 ===")
        logger.error(f"异常类型: {type(e).__name__}")
        logger.error(f"异常信息: {str(e)}")
        return False

