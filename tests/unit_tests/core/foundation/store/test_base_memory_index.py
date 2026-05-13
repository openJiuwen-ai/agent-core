# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from datetime import datetime, timezone
from unittest.mock import Mock
import pytest

from openjiuwen.core.foundation.store.base_memory_index import MemoryDoc
from openjiuwen.core.foundation.store.index.vector_memory_index import VectorMemoryIndex
from openjiuwen.core.memory.manage.mem_model.memory_unit import FragmentMemoryUnit, MemoryType
from openjiuwen.core.foundation.store.base_vector_store import BaseVectorStore, VectorSearchResult
from openjiuwen.core.foundation.store.base_embedding import Embedding as BaseEmbedding
from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.memory.manage.index.fragment_memory_manager import FragmentMemoryManager
from openjiuwen.core.memory.manage.index.write_manager import WriteManager
from openjiuwen.core.memory.manage.search.search_manager import SearchManager, SearchParams
from openjiuwen.core.foundation.store.base_kv_store import BaseKVStore
from openjiuwen.core.foundation.store.base_db_store import BaseDbStore


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


class TestVectorMemoryIndex:
    """Test the VectorMemoryIndex implementation"""

    @staticmethod
    @pytest.mark.asyncio
    async def test_add_memories():
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

        vector_mem_index = VectorMemoryIndex(mock_vector_store, mock_embedding)
        await vector_mem_index.add_memories("user_1", "scope_1", mem_docs)

        # Since we group by type, embed_documents should be called twice
        assert mock_embedding.embed_documents.call_count == 2

        # Verify vector_store.add_docs was called twice (once per type)
        assert mock_vector_store.add_docs.call_count == 2

    @staticmethod
    @pytest.mark.asyncio
    async def test_delete_by_user():
        mock_vector_store = Mock(spec=BaseVectorStore)
        mock_embedding = Mock(spec=BaseEmbedding)

        mock_vector_store.list_collection_names.return_value = [
            "memory_user_1_scope_1_fragment",
            "memory_user_1_scope_2_summary",
            "memory_user_2_scope_1_fragment",
        ]

        vector_mem_index = VectorMemoryIndex(mock_vector_store, mock_embedding)
        await vector_mem_index.delete_by_user("user_1")

        mock_vector_store.list_collection_names.assert_called_once()
        assert mock_vector_store.delete_docs_by_filters.call_count == 2

        call_args_list = mock_vector_store.delete_docs_by_filters.call_args_list
        assert call_args_list[0][1]["collection_name"] == "memory_user_1_scope_1_fragment"
        assert call_args_list[0][1]["filters"] == {"user_id": "user_1"}
        assert call_args_list[1][1]["collection_name"] == "memory_user_1_scope_2_summary"
        assert call_args_list[1][1]["filters"] == {"user_id": "user_1"}


class TestWriteManagerIntegration:
    """Test WriteManager integration with VectorMemoryIndex through FragmentMemoryManager"""

    @staticmethod
    @pytest.mark.asyncio
    async def test_write_manager_add_memories():
        mock_vector_store = Mock(spec=BaseVectorStore)
        mock_embedding = Mock(spec=BaseEmbedding)
        mock_embedding.embed_documents.return_value = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
        mock_model = Mock(spec=Model)
        mock_semantic_store = Mock()

        vector_mem_index = VectorMemoryIndex(mock_vector_store, mock_embedding)
        fragment_manager = FragmentMemoryManager(memory_index=vector_mem_index)
        write_manager = WriteManager({"fragment": fragment_manager}, memory_index=vector_mem_index)

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

        await write_manager.add_memories(
            user_id="user_1",
            scope_id="scope_1",
            memories={
                MemoryType.USER_PROFILE.value: [mem_units[0]],
                MemoryType.SEMANTIC_MEMORY.value: [mem_units[1]],
            },
            llm=mock_model,
            semantic_store=mock_semantic_store,
        )

        assert mock_embedding.embed_documents.call_count == 2
        assert mock_vector_store.add_docs.call_count == 2

    @staticmethod
    @pytest.mark.asyncio
    async def test_write_manager_search():
        mock_vector_store = Mock(spec=BaseVectorStore)
        mock_embedding = Mock(spec=BaseEmbedding)
        mock_embedding.embed_query.return_value = [0.1, 0.2, 0.3]

        mock_result = Mock(spec=VectorSearchResult)
        mock_result.fields = {
            "id": "test_id_1",
            "text": "Test memory 1",
            "type": "user_profile",
            "timestamp": _TEST_DT,
            "fields": {"source_id": "source_1", "metadata": {"key": "value"}},
        }
        mock_result.score = 0.95

        def _search_side_effect(collection_name, **kwargs):
            if "user_profile" in collection_name:
                return [mock_result]
            return []

        mock_vector_store.search.side_effect = _search_side_effect

        vector_mem_index = VectorMemoryIndex(mock_vector_store, mock_embedding)
        fragment_manager = FragmentMemoryManager(memory_index=vector_mem_index)

        from openjiuwen.core.memory.manage.search.search_manager import SearchManager, SearchParams
        search_manager = SearchManager({"fragment": fragment_manager}, b"")

        results = await search_manager.search(
            params=SearchParams(
                user_id="user_1",
                scope_id="scope_1",
                query="test query",
                top_k=2,
            ),
        )

        assert mock_embedding.embed_query.call_count == 3
        assert mock_vector_store.search.call_count == 3
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
    def _setup_ltm(mock_vector_store, mock_embedding, mock_kv_store, mock_db_store):
        from openjiuwen.core.memory.long_term_memory import LongTermMemory
        from openjiuwen.core.memory.config.config import MemoryEngineConfig
        from openjiuwen.core.memory.manage.index.summary_manager import SummaryManager
        from openjiuwen.core.memory.manage.index.variable_manager import VariableManager
        from openjiuwen.core.memory.manage.mem_model.data_id_manager import DataIdManager

        TestLongTermMemoryIndexIntegration._reset_singleton()
        ltm = LongTermMemory()

        ltm.kv_store = mock_kv_store
        ltm.vector_store = mock_vector_store
        ltm.db_store = mock_db_store
        ltm._base_embed = mock_embedding
        ltm.memory_index = VectorMemoryIndex(mock_vector_store, mock_embedding)

        config = MemoryEngineConfig()
        data_id_generator = DataIdManager()

        ltm._sys_mem_config = config
        ltm.fragment_memory_manager = FragmentMemoryManager(
            memory_index=ltm.memory_index,
            data_id_generator=data_id_generator,
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
        ltm.search_manager = SearchManager(managers, config.crypto_key)

        return ltm

    @staticmethod
    @pytest.mark.asyncio
    async def test_ltm_write_through_memory_index():
        """WriteManager.add_memories -> FragmentMemoryManager -> memory_index.add_memories"""
        mock_vector_store = Mock(spec=BaseVectorStore)
        mock_embedding = Mock(spec=BaseEmbedding)
        mock_embedding.embed_documents.return_value = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
        mock_kv_store = Mock(spec=BaseKVStore)
        mock_db_store = Mock(spec=BaseDbStore)
        mock_model = Mock(spec=Model)

        ltm = TestLongTermMemoryIndexIntegration._setup_ltm(
            mock_vector_store, mock_embedding, mock_kv_store, mock_db_store,
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
        mock_vector_store = Mock(spec=BaseVectorStore)
        mock_embedding = Mock(spec=BaseEmbedding)
        mock_embedding.embed_query.return_value = [0.1, 0.2, 0.3]
        mock_kv_store = Mock(spec=BaseKVStore)
        mock_db_store = Mock(spec=BaseDbStore)

        mock_result = Mock(spec=VectorSearchResult)
        mock_result.fields = {
            "id": "ltm_search_id_1",
            "text": "LTM search result",
            "type": "user_profile",
            "timestamp": _TEST_DT,
            "fields": {"source_id": "src_1", "metadata": {}},
        }
        mock_result.score = 0.92

        def _search_side_effect(collection_name, **kwargs):
            if "user_profile" in collection_name:
                return [mock_result]
            return []

        mock_vector_store.search.side_effect = _search_side_effect

        ltm = TestLongTermMemoryIndexIntegration._setup_ltm(
            mock_vector_store, mock_embedding, mock_kv_store, mock_db_store,
        )

        params = SearchParams(
            user_id="user_ltm",
            scope_id="scope_ltm",
            query="test query",
            top_k=5,
            search_type=MemoryType.USER_PROFILE.value,
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
        mock_vector_store = Mock(spec=BaseVectorStore)
        mock_embedding = Mock(spec=BaseEmbedding)
        mock_kv_store = Mock(spec=BaseKVStore)
        mock_db_store = Mock(spec=BaseDbStore)

        mock_vector_store.list_collection_names.return_value = [
            "memory_user_ltm_scope_ltm_user_profile",
        ]

        mock_result = Mock(spec=VectorSearchResult)
        mock_result.fields = {
            "id": "ltm_del_id_1",
            "text": "Memory to delete",
            "type": MemoryType.USER_PROFILE.value,
            "timestamp": _TEST_DT,
            "fields": {"source_id": "src_1"},
        }
        mock_result.score = 1.0
        mock_vector_store.search.return_value = [mock_result]

        ltm = TestLongTermMemoryIndexIntegration._setup_ltm(
            mock_vector_store, mock_embedding, mock_kv_store, mock_db_store,
        )

        await ltm.write_manager.delete_mem_by_id(
            user_id="user_ltm",
            scope_id="scope_ltm",
            mem_id="ltm_del_id_1",
        )

        mock_vector_store.list_collection_names.assert_called()
        assert mock_vector_store.delete_docs_by_ids.call_count >= 1

        TestLongTermMemoryIndexIntegration._reset_singleton()

    @staticmethod
    @pytest.mark.asyncio
    async def test_ltm_update_through_memory_index():
        """WriteManager.update_mem_by_id -> memory_index.get_by_id + delete + add"""
        mock_vector_store = Mock(spec=BaseVectorStore)
        mock_embedding = Mock(spec=BaseEmbedding)
        mock_embedding.embed_documents.return_value = [[0.1, 0.2, 0.3]]
        mock_kv_store = Mock(spec=BaseKVStore)
        mock_db_store = Mock(spec=BaseDbStore)

        mock_vector_store.list_collection_names.return_value = [
            "memory_user_ltm_scope_ltm_user_profile",
        ]

        mock_result = Mock(spec=VectorSearchResult)
        mock_result.fields = {
            "id": "ltm_update_id_1",
            "text": "Old memory content",
            "type": MemoryType.USER_PROFILE.value,
            "timestamp": _TEST_DT,
            "fields": {"source_id": "src_1"},
        }
        mock_result.score = 1.0
        mock_vector_store.search.return_value = [mock_result]

        ltm = TestLongTermMemoryIndexIntegration._setup_ltm(
            mock_vector_store, mock_embedding, mock_kv_store, mock_db_store,
        )

        await ltm.write_manager.update_mem_by_id(
            user_id="user_ltm",
            scope_id="scope_ltm",
            mem_id="ltm_update_id_1",
            memory="Updated memory content",
        )

        mock_vector_store.list_collection_names.assert_called()
        assert mock_vector_store.delete_docs_by_ids.call_count >= 1
        assert mock_vector_store.add_docs.call_count >= 1

        TestLongTermMemoryIndexIntegration._reset_singleton()

    @staticmethod
    @pytest.mark.asyncio
    async def test_ltm_delete_by_user_through_memory_index():
        """WriteManager.delete_mem_by_user_id -> FragmentMemoryManager -> memory_index.delete_by_user_and_scope"""
        mock_vector_store = Mock(spec=BaseVectorStore)
        mock_embedding = Mock(spec=BaseEmbedding)
        mock_kv_store = Mock(spec=BaseKVStore)
        mock_db_store = Mock(spec=BaseDbStore)

        mock_vector_store.list_collection_names.return_value = [
            "memory_user_ltm_scope_ltm_user_profile",
        ]

        ltm = TestLongTermMemoryIndexIntegration._setup_ltm(
            mock_vector_store, mock_embedding, mock_kv_store, mock_db_store,
        )

        await ltm.write_manager.delete_mem_by_user_id(
            user_id="user_ltm",
            scope_id="scope_ltm",
        )

        assert mock_vector_store.delete_docs_by_filters.call_count >= 1

        TestLongTermMemoryIndexIntegration._reset_singleton()


if __name__ == "__main__":
    pytest.main([__file__])
