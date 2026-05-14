# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from unittest.mock import Mock, patch
import pytest

from openjiuwen.core.memory.long_term_memory import LongTermMemory
from openjiuwen.core.foundation.store.index.vector_memory_index import VectorMemoryIndex
from openjiuwen.core.foundation.store.base_vector_store import BaseVectorStore
from openjiuwen.core.foundation.store.base_embedding import Embedding as BaseEmbedding
from openjiuwen.core.common.utils.singleton import Singleton


class TestLongTermMemoryRegisterPlugin:
    """Test the register_plugin functionality in LongTermMemory"""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset the LongTermMemory singleton before each test"""
        Singleton._instances.pop(LongTermMemory, None)

    @pytest.mark.asyncio
    async def test_register_plugin(self):
        """Test that LongTermMemory can register a custom BaseMemoryIndex plugin"""
        long_term_memory = LongTermMemory()

        mock_vector_store = Mock(spec=BaseVectorStore)
        mock_embedding = Mock(spec=BaseEmbedding)
        mock_embedding.embed_documents.return_value = [[0.1, 0.2, 0.3]]

        await long_term_memory.register_plugin(
            name="test_vector_index",
            cls=VectorMemoryIndex,
            params={"vector_store": mock_vector_store, "embedding_model": mock_embedding}
        )

        # First registered plugin becomes the default memory_index
        assert long_term_memory.memory_index is not None
        assert isinstance(long_term_memory.memory_index, VectorMemoryIndex)

    @pytest.mark.asyncio
    async def test_register_multiple_plugins(self):
        """Test that only the first registered plugin becomes default"""
        long_term_memory = LongTermMemory()

        mock_vector_store1 = Mock(spec=BaseVectorStore)
        mock_vector_store2 = Mock(spec=BaseVectorStore)
        mock_embedding = Mock(spec=BaseEmbedding)
        mock_embedding.embed_documents.return_value = [[0.1, 0.2, 0.3]]

        # Register first plugin
        await long_term_memory.register_plugin(
            name="test_vector_index1",
            cls=VectorMemoryIndex,
            params={"vector_store": mock_vector_store1, "embedding_model": mock_embedding}
        )
        first_index = long_term_memory.memory_index

        # Register second plugin
        await long_term_memory.register_plugin(
            name="test_vector_index2",
            cls=VectorMemoryIndex,
            params={"vector_store": mock_vector_store2, "embedding_model": mock_embedding}
        )

        # Default remains the first registered plugin
        assert long_term_memory.memory_index is first_index
        assert isinstance(long_term_memory.memory_index, VectorMemoryIndex)

    @pytest.mark.asyncio
    @patch('openjiuwen.core.memory.long_term_memory.create_tables', return_value=None)
    async def test_register_plugin_after_store_registration(self, mock_create_tables):
        """Test that manual plugin registration doesn't overwrite auto-registered default"""
        long_term_memory = LongTermMemory()

        mock_kv_store = Mock()
        mock_vector_store = Mock(spec=BaseVectorStore)
        from openjiuwen.core.foundation.store.base_db_store import BaseDbStore
        mock_db_store = Mock(spec=BaseDbStore)
        mock_embedding = Mock(spec=BaseEmbedding)
        mock_embedding.embed_documents.return_value = [[0.1, 0.2, 0.3]]
        await long_term_memory.register_store(
            kv_store=mock_kv_store,
            vector_store=mock_vector_store,
            db_store=mock_db_store,
            embedding_model=mock_embedding
        )

        # Auto-registered by register_store
        assert long_term_memory.memory_index is not None
        auto_index = long_term_memory.memory_index

        # Register a custom plugin manually — should not overwrite default
        custom_vector_store = Mock(spec=BaseVectorStore)
        await long_term_memory.register_plugin(
            name="custom_vector_index",
            cls=VectorMemoryIndex,
            params={"vector_store": custom_vector_store, "embedding_model": mock_embedding}
        )

        assert long_term_memory.memory_index is auto_index


if __name__ == "__main__":
    pytest.main([__file__])
