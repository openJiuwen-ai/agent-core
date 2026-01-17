# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import tempfile
import pytest

from openjiuwen.core.memory.store.impl.memory_chroma_vector_store import MemoryChromaVectorStore
from openjiuwen.core.retrieval.common.retrieval_result import SearchResult


@pytest.fixture
def table_name():
    return "test_table"


@pytest.fixture
def persist_directory():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        yield tmpdir


@pytest.fixture
def chroma_store(persist_directory):
    return MemoryChromaVectorStore(persist_directory=persist_directory)


class TestMemoryChromaVectorStore:
    """MemoryChromaVectorStore测试类"""

    @staticmethod
    def test_init_success(persist_directory):
        """测试初始化成功"""
        # 创建MemoryChromaVectorStore实例
        store = MemoryChromaVectorStore(persist_directory=persist_directory)

        # 验证初始化
        assert store.client is not None
        assert store.collection_cache == {}

    @pytest.mark.asyncio
    async def test_get_collection(self, chroma_store, persist_directory, table_name):
        """测试获取/创建集合"""
        # 第一次调用get_collection应该创建集合
        collection = await chroma_store.get_collection(table_name)
        assert collection is not None
        assert table_name in chroma_store.collection_cache
        assert chroma_store.collection_cache[table_name] is collection
        # 验证集合在客户端中存在
        assert await chroma_store.table_exists(table_name)

        # 第二次调用get_collection应该从缓存中获取
        collection2 = await chroma_store.get_collection(table_name)
        assert collection2 is collection

    @pytest.mark.asyncio
    async def test_remove_collection_from_cache(self, chroma_store, persist_directory, table_name):
        """测试从缓存中移除集合"""
        # 添加集合到缓存
        await chroma_store.get_collection(table_name)
        assert table_name in chroma_store.collection_cache

        # 从缓存中移除集合
        chroma_store.remove_collection_from_cache(table_name)
        assert table_name not in chroma_store.collection_cache

    @staticmethod
    def test_check_table_name(chroma_store):
        """测试检查表名"""
        # 测试空表名应该抛出异常
        with pytest.raises(ValueError, match="Chroma collection name is required for test_operation"):
            chroma_store.check_table_name(None, "test_operation")

        with pytest.raises(ValueError, match="Chroma collection name is required for test_operation"):
            chroma_store.check_table_name("", "test_operation")

        with pytest.raises(ValueError, match="Chroma collection name is required for test_operation"):
            chroma_store.check_table_name("   ", "test_operation")

    @pytest.mark.asyncio
    async def test_is_collection_exists(self, chroma_store, persist_directory, table_name):
        """测试集合是否存在"""
        # 测试集合不存在

        assert await chroma_store.table_exists(table_name) is False

        # 创建集合
        await chroma_store.get_collection(table_name)

        # 测试集合存在
        assert await chroma_store.table_exists(table_name) is True

    @pytest.mark.asyncio
    async def test_add_single_vector(self, chroma_store, persist_directory, table_name):
        """测试添加单个向量"""
        # 准备测试数据
        vector_data = {
            "id": "vec1",
            "embedding": [0.1, 0.2, 0.3, 0.4],
            "scope_id": "scope1"
        }

        # 调用add方法
        await chroma_store.add(data=vector_data, table_name=table_name)

        # 验证向量已添加
        collection = await chroma_store.get_collection(table_name)
        result = collection.get(ids=["vec1"], include=["embeddings", "metadatas"])
        rounded = [[round(x, 4) for x in vec] for vec in result["embeddings"].tolist()]
        assert result["ids"] == ["vec1"]
        assert rounded == [[0.1, 0.2, 0.3, 0.4]]
        assert result["metadatas"] == [{"scope_id": "scope1"}]

    @pytest.mark.asyncio
    async def test_add_multiple_vectors(self, chroma_store, persist_directory, table_name):
        """测试添加多个向量"""
        # 准备测试数据
        vector_data = [
            {
                "id": "vec1",
                "embedding": [0.1, 0.2, 0.3, 0.4],
                "scope_id": "scope1"
            },
            {
                "id": "vec2",
                "embedding": [0.5, 0.6, 0.7, 0.8],
                "scope_id": "scope2"
            }
        ]

        # 调用add方法
        await chroma_store.add(data=vector_data, table_name=table_name)

        # 验证向量已添加
        collection = await chroma_store.get_collection(table_name)
        result = collection.get(ids=["vec1", "vec2"])
        assert len(result["ids"]) == 2
        assert set(result["ids"]) == {"vec1", "vec2"}

    @pytest.mark.asyncio
    async def test_add_with_batching(self, chroma_store, persist_directory, table_name):
        """测试批量添加向量"""
        # 准备测试数据（超过默认batch_size=128）
        vector_data = [
            {
                "id": f"vec{i}",
                "embedding": [0.1, 0.2, 0.3, 0.4],
                "scope_id": f"scope{i}"
            }
            for i in range(200)
        ]

        # 调用add方法，设置较小的batch_size
        await chroma_store.add(data=vector_data, batch_size=100, table_name=table_name)

        # 验证向量已添加
        collection = await chroma_store.get_collection(table_name)
        result = collection.get()
        assert len(result["ids"]) == 200

    @pytest.mark.asyncio
    async def test_search(self, chroma_store, persist_directory, table_name):
        """测试搜索功能"""
        # 准备测试数据
        vector_data = [
            {
                "id": "vec1",
                "embedding": [0.1, 0.2, 0.3, 0.4],
                "scope_id": "scope1"
            },
            {
                "id": "vec2",
                "embedding": [0.5, 0.6, 0.7, 0.8],
                "scope_id": "scope2"
            }
        ]

        # 添加向量
        await chroma_store.add(data=vector_data, table_name=table_name)

        # 调用search方法
        query_vector = [0.1, 0.2, 0.3, 0.4]
        results = await chroma_store.search(query_vector, top_k=2, table_name=table_name, scope_id="scope1")

        # 验证结果
        assert isinstance(results, list)
        assert len(results) == 1  # 只有scope1的向量
        assert all(isinstance(result, SearchResult) for result in results)

        # 验证搜索结果
        assert results[0].id == "vec1"
        assert results[0].text == ""
        assert results[0].metadata == {"scope_id": "scope1"}

    @pytest.mark.asyncio
    async def test_search_empty_results(self, chroma_store, persist_directory, table_name):
        """测试搜索返回空结果"""
        # 调用search方法，搜索不存在的向量
        query_vector = [0.1, 0.2, 0.3, 0.4]
        results = await chroma_store.search(query_vector, top_k=2, table_name=table_name)

        # 验证结果为空列表
        assert results == []

    @pytest.mark.asyncio
    async def test_delete(self, chroma_store, persist_directory, table_name):
        """测试删除功能"""
        # 准备测试数据
        vector_data = {
            "id": "vec1",
            "embedding": [0.1, 0.2, 0.3, 0.4],
            "scope_id": "scope1"
        }

        # 添加向量
        await chroma_store.add(data=vector_data, table_name=table_name)

        # 验证向量已添加
        collection = await chroma_store.get_collection(table_name)
        result = collection.get(ids=["vec1"])
        assert result["ids"] == ["vec1"]

        # 调用delete方法
        ids_to_delete = ["vec1"]
        result = await chroma_store.delete(ids=ids_to_delete, table_name=table_name)

        # 验证删除成功
        assert result is True
        result = collection.get(ids=["vec1"])
        assert result["ids"] == []

    @pytest.mark.asyncio
    async def test_delete_nonexistent_collection(self, chroma_store, persist_directory, table_name):
        """测试删除不存在的集合"""
        # 调用delete方法
        result = await chroma_store.delete(ids=["vec1"], table_name=table_name)

        # 验证结果为True（应该跳过删除）
        assert result is True

    @pytest.mark.asyncio
    async def test_delete_empty_ids(self, chroma_store, persist_directory, table_name):
        """测试删除空ID列表"""
        # 调用delete方法，传入空ID列表
        result = await chroma_store.delete(ids=[], table_name=table_name)

        # 验证结果为True（应该跳过删除）
        assert result is True

    @pytest.mark.asyncio
    async def test_delete_table(self, chroma_store, persist_directory, table_name):
        """测试删除表功能"""
        # 创建表并添加数据
        vector_data = {
            "id": "vec1",
            "embedding": [0.1, 0.2, 0.3, 0.4],
            "scope_id": "scope1"
        }
        await chroma_store.add(data=vector_data, table_name=table_name)

        # 验证表存在
        assert chroma_store.table_exists(table_name)

        # 调用delete_table方法
        result = await chroma_store.delete_table(table_name)

        # 验证删除成功
        assert result is True
        assert not await chroma_store.table_exists(table_name)
        assert table_name not in chroma_store.collection_cache

    @pytest.mark.asyncio
    async def test_delete_nonexistent_table(self, chroma_store, persist_directory, table_name):
        """测试删除不存在的表"""
        # 调用delete_table方法
        result = await chroma_store.delete_table(table_name)

        # 验证结果为True（应该跳过删除）
        assert result is True
