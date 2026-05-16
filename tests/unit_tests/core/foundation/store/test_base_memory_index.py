# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from datetime import datetime, timezone
from unittest.mock import Mock, patch, AsyncMock
import pytest

from openjiuwen.core.foundation.store.base_memory_index import MemoryDoc
from openjiuwen.core.foundation.store.index.simple_memory_index import SimpleMemoryIndex
from openjiuwen.core.memory.manage.mem_model.memory_unit import FragmentMemoryUnit, MemoryType
from openjiuwen.core.foundation.store.base_vector_store import BaseVectorStore, VectorSearchResult
from openjiuwen.core.foundation.store.base_embedding import Embedding as BaseEmbedding
from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.memory.manage.index.fragment_memory_manager import FragmentMemoryManager
from openjiuwen.core.memory.manage.index.write_manager import WriteManager
from openjiuwen.core.memory.manage.search.search_manager import SearchManager, SearchParams
from openjiuwen.core.foundation.store.base_kv_store import BaseKVStore
from openjiuwen.core.foundation.store.base_db_store import BaseDbStore
from openjiuwen.core.memory.manage.update.mem_update_checker import MemoryStatus, MemoryActionItem


_TEST_DT = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


class TestMemoryDoc:
    """Test the MemoryDoc data model"""

    @staticmethod
    def test_memory_doc_creation():
        mem_doc = MemoryDoc(
            id="test_id_123",
            text="Test memory content",
            type="fragment",
            timestamp=_TEST_DT,
        )
        assert mem_doc.id == "test_id_123"
        assert mem_doc.text == "Test memory content"
        assert mem_doc.type == "fragment"
        assert mem_doc.timestamp == _TEST_DT
        assert mem_doc.fields == {}

    @staticmethod
    def test_memory_doc_with_fields():
        fields = {
            "session_id": "session_123",
            "metadata": {"key": "value"}
        }
        mem_doc = MemoryDoc(
            id="test_id_123",
            text="Test memory content",
            type="fragment",
            timestamp=_TEST_DT,
            fields=fields,
        )
        assert mem_doc.fields == fields

    @staticmethod
    def test_memory_doc_dict_conversion():
        mem_doc = MemoryDoc(
            id="test_id_123",
            text="Test memory content",
            type="fragment",
            timestamp=_TEST_DT,
            fields={"session_id": "session_123"},
        )
        mem_dict = mem_doc.model_dump()
        assert isinstance(mem_dict, dict)
        assert mem_dict["id"] == "test_id_123"
        assert mem_dict["text"] == "Test memory content"
        assert mem_dict["type"] == "fragment"
        assert mem_dict["fields"] == {"session_id": "session_123"}

        # Round-trip
        new_mem_doc = MemoryDoc(**mem_dict)
        assert new_mem_doc == mem_doc


class TestSimpleMemoryIndex:
    """Test the SimpleMemoryIndex implementation"""

    @staticmethod
    @pytest.mark.asyncio
    async def test_add_memories():
        mock_kv_store = AsyncMock(spec=BaseKVStore)
        mock_kv_store.get.return_value = None  # No existing ID tracking
        mock_vector_store = Mock(spec=BaseVectorStore)
        mock_embedding = Mock(spec=BaseEmbedding)
        mock_embedding.embed_documents.return_value = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]

        mem_docs = [
            MemoryDoc(
                id="test_id_1",
                text="Test memory 1",
                type="fragment",
                timestamp=_TEST_DT,
                fields={"session_id": "session_1"},
            ),
            MemoryDoc(
                id="test_id_2",
                text="Test memory 2",
                type="summary",
                timestamp=_TEST_DT,
                fields={"session_id": "session_2"},
            ),
        ]

        index = SimpleMemoryIndex(mock_kv_store, mock_vector_store, mock_embedding)
        await index.add_memories("user_1", "scope_1", mem_docs)

        # Since we group by type, embed_documents should be called twice
        assert mock_embedding.embed_documents.call_count == 2

        # Verify vector_store.add_docs was called twice (once per type)
        assert mock_vector_store.add_docs.call_count == 2

        # Verify kv_store.set was called for data and ID tracking
        assert mock_kv_store.set.call_count >= 2

    @staticmethod
    @pytest.mark.asyncio
    async def test_delete_by_user():
        mock_kv_store = AsyncMock(spec=BaseKVStore)
        mock_vector_store = Mock(spec=BaseVectorStore)

        mock_vector_store.list_collection_names.return_value = [
            "uid_user_1_gid_scope_1_mtype_fragment",
            "uid_user_1_gid_scope_2_mtype_summary",
            "uid_user_2_gid_scope_1_mtype_fragment",
        ]

        index = SimpleMemoryIndex(mock_kv_store, mock_vector_store)
        await index.delete_by_user("user_1")

        mock_vector_store.list_collection_names.assert_called_once()
        # SimpleMemoryIndex uses delete_collection for each matching collection
        assert mock_vector_store.delete_collection.call_count == 2


class TestWriteManagerIntegration:
    """Test WriteManager integration with SimpleMemoryIndex through FragmentMemoryManager"""

    @staticmethod
    @pytest.mark.asyncio
    async def test_write_manager_add_memories():
        mock_kv_store = AsyncMock(spec=BaseKVStore)
        mock_kv_store.get.return_value = None  # No existing ID tracking
        mock_vector_store = Mock(spec=BaseVectorStore)
        mock_embedding = Mock(spec=BaseEmbedding)
        mock_embedding.embed_documents.return_value = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
        mock_model = Mock(spec=Model)

        mem_index = SimpleMemoryIndex(mock_kv_store, mock_vector_store, mock_embedding)
        fragment_manager = FragmentMemoryManager(memory_index=mem_index)
        write_manager = WriteManager({"fragment": fragment_manager}, memory_index=mem_index)

        mem_units = [
            FragmentMemoryUnit(
                mem_id="test_id_1",
                content="Test memory 1",
                mem_type=MemoryType.USER_PROFILE,
                timestamp=datetime.now(tz=timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M:%S'),
                message_mem_id="source_1",
            ),
            FragmentMemoryUnit(
                mem_id="test_id_2",
                content="Test memory 2",
                mem_type=MemoryType.SEMANTIC_MEMORY,
                timestamp=datetime.now(tz=timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M:%S'),
                message_mem_id="source_2",
            ),
        ]

        mock_action_items = [
            MemoryActionItem(id="test_id_1", content="Test memory 1", status=MemoryStatus.ADD),
            MemoryActionItem(id="test_id_2", content="Test memory 2", status=MemoryStatus.ADD),
        ]
        with patch(
            "openjiuwen.core.memory.manage.index.fragment_memory_manager.MemUpdateChecker"
        ) as MockChecker:
            MockChecker.return_value.check = AsyncMock(return_value=mock_action_items)
            await write_manager.add_memories(
                user_id="user_1",
                scope_id="scope_1",
                memories={
                    MemoryType.USER_PROFILE.value: [mem_units[0]],
                    MemoryType.SEMANTIC_MEMORY.value: [mem_units[1]],
                },
                llm=mock_model,
            )

        assert mock_embedding.embed_documents.call_count == 2
        assert mock_vector_store.add_docs.call_count == 2

    @staticmethod
    @pytest.mark.asyncio
    async def test_write_manager_search():
        mock_kv_store = AsyncMock(spec=BaseKVStore)
        mock_vector_store = Mock(spec=BaseVectorStore)
        mock_embedding = Mock(spec=BaseEmbedding)
        mock_embedding.embed_query.return_value = [0.1, 0.2, 0.3]

        # Vector search returns hit IDs
        mock_vec_result = Mock(spec=VectorSearchResult)
        mock_vec_result.fields = {"id": "test_id_1"}
        mock_vec_result.score = 0.95

        def _search_side_effect(collection_name, **kwargs):
            if "user_profile" in collection_name:
                return [mock_vec_result]
            return []

        mock_vector_store.search.side_effect = _search_side_effect

        # KV store returns the full document data
        import json
        mock_kv_store.mget.return_value = [
            json.dumps({
                "id": "test_id_1",
                "mem": "Test memory 1",
                "mem_type": "user_profile",
                "timestamp": _TEST_DT.strftime("%Y-%m-%d %H-%M-%S"),
                "source_id": "source_1",
                "metadata": {"key": "value"},
            }).encode()
        ]

        mem_index = SimpleMemoryIndex(mock_kv_store, mock_vector_store, mock_embedding)
        fragment_manager = FragmentMemoryManager(memory_index=mem_index)

        search_manager = SearchManager({"fragment": fragment_manager}, b"", memory_index=mem_index)

        results = await search_manager.search(
            params=SearchParams(
                user_id="user_1",
                scope_id="scope_1",
                query="test query",
                top_k=2,
            ),
        )

        assert mock_embedding.embed_query.call_count == 1
        assert results is not None
        assert len(results) == 1
        assert results[0]["id"] == "test_id_1"
        assert results[0]["mem"] == "Test memory 1"
        assert results[0]["mem_type"] == "user_profile"
        assert results[0]["score"] == 0.95


class TestLongTermMemoryIndexIntegration:
    """Test that LongTermMemory correctly delegates to memory_index through the manager layer."""

    @staticmethod
    def _reset_singleton():
        from openjiuwen.core.common.utils.singleton import Singleton
        from openjiuwen.core.memory.long_term_memory import LongTermMemory
        with __import__('threading').Lock():
            Singleton._instances.pop(LongTermMemory, None)

    @staticmethod
    def _setup_ltm(mock_kv_store, mock_vector_store, mock_embedding, mock_db_store):
        from openjiuwen.core.memory.long_term_memory import LongTermMemory
        from openjiuwen.core.memory.config.config import MemoryEngineConfig
        from openjiuwen.core.memory.manage.index.summary_manager import SummaryManager
        from openjiuwen.core.memory.manage.index.variable_manager import VariableManager

        TestLongTermMemoryIndexIntegration._reset_singleton()
        ltm = LongTermMemory()

        ltm.kv_store = mock_kv_store
        ltm.vector_store = mock_vector_store
        ltm.db_store = mock_db_store
        ltm._base_embed = mock_embedding
        ltm.memory_index = SimpleMemoryIndex(mock_kv_store, mock_vector_store, mock_embedding)

        config = MemoryEngineConfig()

        ltm._sys_mem_config = config
        ltm.fragment_memory_manager = FragmentMemoryManager(
            memory_index=ltm.memory_index,
            crypto_key=config.crypto_key,
        )
        ltm.summary_manager = SummaryManager(
            memory_index=ltm.memory_index,
            crypto_key=config.crypto_key,
        )
        ltm.variable_manager = VariableManager(mock_kv_store, config.crypto_key)

        managers = {
            MemoryType.USER_PROFILE.value: ltm.fragment_memory_manager,
            MemoryType.EPISODIC_MEMORY.value: ltm.fragment_memory_manager,
            MemoryType.SEMANTIC_MEMORY.value: ltm.fragment_memory_manager,
            MemoryType.VARIABLE.value: ltm.variable_manager,
            MemoryType.SUMMARY.value: ltm.summary_manager,
        }
        ltm.fragment_type = [
            MemoryType.USER_PROFILE.value,
            MemoryType.EPISODIC_MEMORY.value,
            MemoryType.SEMANTIC_MEMORY.value,
        ]
        ltm.write_manager = WriteManager(managers, ltm.memory_index)
        ltm.search_manager = SearchManager(managers, config.crypto_key, ltm.memory_index)

        return ltm

    @staticmethod
    @pytest.mark.asyncio
    async def test_ltm_write_through_memory_index():
        """WriteManager.add_memories -> FragmentMemoryManager -> memory_index.add_memories"""
        mock_kv_store = AsyncMock(spec=BaseKVStore)
        mock_kv_store.get.return_value = None  # No existing ID tracking
        mock_vector_store = Mock(spec=BaseVectorStore)
        mock_embedding = Mock(spec=BaseEmbedding)
        mock_embedding.embed_documents.return_value = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
        mock_db_store = Mock(spec=BaseDbStore)
        mock_model = Mock(spec=Model)

        ltm = TestLongTermMemoryIndexIntegration._setup_ltm(
            mock_kv_store, mock_vector_store, mock_embedding, mock_db_store,
        )

        mem_units = [
            FragmentMemoryUnit(
                mem_id="ltm_test_id_1",
                content="LTM test memory 1",
                mem_type=MemoryType.USER_PROFILE,
                timestamp=datetime.now(tz=timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M:%S'),
                message_mem_id="source_1",
            ),
            FragmentMemoryUnit(
                mem_id="ltm_test_id_2",
                content="LTM test memory 2",
                mem_type=MemoryType.SEMANTIC_MEMORY,
                timestamp=datetime.now(tz=timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M:%S'),
                message_mem_id="source_2",
            ),
        ]

        mock_action_items = [
            MemoryActionItem(id="ltm_test_id_1", content="LTM test memory 1", status=MemoryStatus.ADD),
            MemoryActionItem(id="ltm_test_id_2", content="LTM test memory 2", status=MemoryStatus.ADD),
        ]
        with patch(
            "openjiuwen.core.memory.manage.index.fragment_memory_manager.MemUpdateChecker"
        ) as MockChecker:
            MockChecker.return_value.check = AsyncMock(return_value=mock_action_items)
            await ltm.write_manager.add_memories(
                user_id="user_ltm",
                scope_id="scope_ltm",
                memories={
                    MemoryType.USER_PROFILE.value: [mem_units[0]],
                    MemoryType.SEMANTIC_MEMORY.value: [mem_units[1]],
                },
                llm=mock_model,
            )

        assert mock_embedding.embed_documents.call_count >= 2
        assert mock_vector_store.add_docs.call_count >= 2

        TestLongTermMemoryIndexIntegration._reset_singleton()

    @staticmethod
    @pytest.mark.asyncio
    async def test_ltm_search_through_memory_index():
        """SearchManager.search -> FragmentMemoryManager.search -> memory_index.search"""
        import json

        mock_kv_store = AsyncMock(spec=BaseKVStore)
        mock_vector_store = Mock(spec=BaseVectorStore)
        mock_embedding = Mock(spec=BaseEmbedding)
        mock_embedding.embed_query.return_value = [0.1, 0.2, 0.3]
        mock_db_store = Mock(spec=BaseDbStore)

        # Vector search returns hit IDs
        mock_vec_result = Mock(spec=VectorSearchResult)
        mock_vec_result.fields = {"id": "ltm_search_id_1"}
        mock_vec_result.score = 0.92

        def _search_side_effect(collection_name, **kwargs):
            if "user_profile" in collection_name:
                return [mock_vec_result]
            return []

        mock_vector_store.search.side_effect = _search_side_effect

        # KV store returns the full document data
        mock_kv_store.mget.return_value = [
            json.dumps({
                "id": "ltm_search_id_1",
                "mem": "LTM search result",
                "mem_type": "user_profile",
                "timestamp": _TEST_DT.strftime("%Y-%m-%d %H-%M-%S"),
                "source_id": "src_1",
            }).encode()
        ]

        ltm = TestLongTermMemoryIndexIntegration._setup_ltm(
            mock_kv_store, mock_vector_store, mock_embedding, mock_db_store,
        )

        params = SearchParams(
            user_id="user_ltm",
            scope_id="scope_ltm",
            query="test query",
            top_k=5,
            search_type=[MemoryType.USER_PROFILE.value],
        )
        results = await ltm.search_manager.search(params)

        assert mock_embedding.embed_query.called
        assert mock_vector_store.search.called
        assert results is not None
        assert len(results) >= 1
        assert results[0]["id"] == "ltm_search_id_1"
        assert results[0]["mem"] == "LTM search result"
        assert results[0]["score"] == 0.92

        TestLongTermMemoryIndexIntegration._reset_singleton()

    @staticmethod
    @pytest.mark.asyncio
    async def test_ltm_delete_through_memory_index():
        """WriteManager.delete_mem_by_id -> memory_index.get_by_id + delete_memories"""
        import json

        mock_kv_store = AsyncMock(spec=BaseKVStore)
        mock_vector_store = Mock(spec=BaseVectorStore)
        mock_embedding = Mock(spec=BaseEmbedding)
        mock_db_store = Mock(spec=BaseDbStore)

        # get_by_id reads from KV store
        kv_data = json.dumps({
            "id": "ltm_del_id_1",
            "mem": "Memory to delete",
            "mem_type": MemoryType.USER_PROFILE.value,
            "timestamp": _TEST_DT.strftime("%Y-%m-%d %H-%M-%S"),
            "source_id": "src_1",
        }).encode()
        mock_kv_store.get.return_value = kv_data

        # SimpleMemoryIndex.delete_memories uses list_collection_names to find collections
        mock_vector_store.list_collection_names.return_value = [
            "uid_user_ltm_gid_scope_ltm_mtype_user_profile",
        ]

        ltm = TestLongTermMemoryIndexIntegration._setup_ltm(
            mock_kv_store, mock_vector_store, mock_embedding, mock_db_store,
        )

        await ltm.write_manager.delete_mem_by_id(
            user_id="user_ltm",
            scope_id="scope_ltm",
            mem_id="ltm_del_id_1",
        )

        # KV store should have delete calls (for data + ID tracking)
        assert mock_kv_store.delete.call_count >= 1
        # Vector store should have delete_docs_by_ids call
        assert mock_vector_store.delete_docs_by_ids.call_count >= 1

        TestLongTermMemoryIndexIntegration._reset_singleton()

    @staticmethod
    @pytest.mark.asyncio
    async def test_ltm_update_through_memory_index():
        """WriteManager.update_mem_by_id -> memory_index.get_by_id + delete + add"""
        import json

        mock_kv_store = AsyncMock(spec=BaseKVStore)
        mock_vector_store = Mock(spec=BaseVectorStore)
        mock_embedding = Mock(spec=BaseEmbedding)
        mock_embedding.embed_documents.return_value = [[0.1, 0.2, 0.3]]
        mock_db_store = Mock(spec=BaseDbStore)

        # get_by_id reads from KV store
        kv_data = json.dumps({
            "id": "ltm_update_id_1",
            "mem": "Old memory content",
            "mem_type": MemoryType.USER_PROFILE.value,
            "timestamp": _TEST_DT.strftime("%Y-%m-%d %H-%M-%S"),
            "source_id": "src_1",
        }).encode()
        # First calls return data (for get_by_id), later calls return None (for ID tracking during add)
        mock_kv_store.get.side_effect = [kv_data, kv_data, None, None, None, None]

        # SimpleMemoryIndex.delete_memories uses list_collection_names
        mock_vector_store.list_collection_names.return_value = [
            "uid_user_ltm_gid_scope_ltm_mtype_user_profile",
        ]

        ltm = TestLongTermMemoryIndexIntegration._setup_ltm(
            mock_kv_store, mock_vector_store, mock_embedding, mock_db_store,
        )

        await ltm.write_manager.update_mem_by_id(
            user_id="user_ltm",
            scope_id="scope_ltm",
            mem_id="ltm_update_id_1",
            memory="Updated memory content",
        )

        # Should delete old data and add new data
        assert mock_kv_store.delete.call_count >= 1
        assert mock_vector_store.delete_docs_by_ids.call_count >= 1
        assert mock_vector_store.add_docs.call_count >= 1

        TestLongTermMemoryIndexIntegration._reset_singleton()

    @staticmethod
    @pytest.mark.asyncio
    async def test_ltm_delete_by_user_through_memory_index():
        """WriteManager.delete_mem_by_user_id -> FragmentMemoryManager -> memory_index.delete_by_user_and_scope"""
        mock_kv_store = AsyncMock(spec=BaseKVStore)
        mock_vector_store = Mock(spec=BaseVectorStore)
        mock_embedding = Mock(spec=BaseEmbedding)
        mock_db_store = Mock(spec=BaseDbStore)

        mock_vector_store.list_collection_names.return_value = [
            "uid_user_ltm_gid_scope_ltm_mtype_user_profile",
        ]

        ltm = TestLongTermMemoryIndexIntegration._setup_ltm(
            mock_kv_store, mock_vector_store, mock_embedding, mock_db_store,
        )

        await ltm.write_manager.delete_mem_by_user_id(
            user_id="user_ltm",
            scope_id="scope_ltm",
        )

        # SimpleMemoryIndex uses delete_collection for user+scope deletion
        assert mock_vector_store.delete_collection.call_count >= 1
        assert mock_kv_store.delete_by_prefix.call_count >= 1

        TestLongTermMemoryIndexIntegration._reset_singleton()


if __name__ == "__main__":
    pytest.main([__file__])
