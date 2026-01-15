# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
Knowledge base abstract base class test cases
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.retrieval import KnowledgeBase
from openjiuwen.core.retrieval import KnowledgeBaseConfig


class ConcreteKnowledgeBase(KnowledgeBase):
    """Concrete knowledge base implementation for testing abstract base class"""

    async def parse_files(self, file_paths, **kwargs):
        return []

    async def add_documents(self, documents, **kwargs):
        return [doc.id_ for doc in documents]

    async def retrieve(self, query, config=None, **kwargs):
        return []

    async def delete_documents(self, doc_ids, **kwargs):
        return True

    async def update_documents(self, documents, **kwargs):
        return [doc.id_ for doc in documents]

    async def get_statistics(self):
        return {"kb_id": self.config.kb_id}


class TestKnowledgeBase:
    """Knowledge base abstract base class tests"""

    @staticmethod
    def test_init():
        """Test initialization"""
        config = KnowledgeBaseConfig(kb_id="test_kb")
        kb = ConcreteKnowledgeBase(config=config)
        assert kb.config == config
        assert kb.vector_store is None
        assert kb.embed_model is None
        assert kb.parser is None
        assert kb.chunker is None
        assert kb.extractor is None
        assert kb.index_manager is None
        assert kb.llm_client is None

    @staticmethod
    def test_init_with_components():
        """Test initialization with components"""
        config = KnowledgeBaseConfig(kb_id="test_kb")
        mock_vector_store = MagicMock()
        mock_embed_model = MagicMock()
        mock_parser = MagicMock()
        mock_chunker = MagicMock()
        mock_extractor = MagicMock()
        mock_index_manager = MagicMock()
        mock_llm_client = MagicMock()
        mock_vector_store.database_name = "database_name"
        mock_index_manager.database_name = "database_name"

        kb = ConcreteKnowledgeBase(
            config=config,
            vector_store=mock_vector_store,
            embed_model=mock_embed_model,
            parser=mock_parser,
            chunker=mock_chunker,
            extractor=mock_extractor,
            index_manager=mock_index_manager,
            llm_client=mock_llm_client,
        )
        assert kb.vector_store == mock_vector_store
        assert kb.embed_model == mock_embed_model
        assert kb.parser == mock_parser
        assert kb.chunker == mock_chunker
        assert kb.extractor == mock_extractor
        assert kb.index_manager == mock_index_manager
        assert kb.llm_client == mock_llm_client

    @pytest.mark.asyncio
    async def test_close_with_async_close(self):
        """Test close (async close method)"""
        config = KnowledgeBaseConfig(kb_id="test_kb")
        mock_vector_store = AsyncMock()
        mock_vector_store.close = AsyncMock()
        mock_index_manager = AsyncMock()
        mock_index_manager.close = AsyncMock()
        mock_vector_store.database_name = "database_name"
        mock_index_manager.database_name = "database_name"

        kb = ConcreteKnowledgeBase(
            config=config,
            vector_store=mock_vector_store,
            index_manager=mock_index_manager,
        )
        await kb.close()
        mock_vector_store.close.assert_called_once()
        mock_index_manager.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_with_sync_close(self):
        """Test close (sync close method)"""
        config = KnowledgeBaseConfig(kb_id="test_kb")
        mock_vector_store = MagicMock()
        mock_vector_store.close = MagicMock()
        mock_index_manager = MagicMock()
        mock_index_manager.close = MagicMock()
        mock_vector_store.database_name = "database_name"
        mock_index_manager.database_name = "database_name"

        kb = ConcreteKnowledgeBase(
            config=config,
            vector_store=mock_vector_store,
            index_manager=mock_index_manager,
        )
        await kb.close()
        mock_vector_store.close.assert_called_once()
        mock_index_manager.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_with_none_components(self):
        """Test close (components are None)"""
        config = KnowledgeBaseConfig(kb_id="test_kb")
        kb = ConcreteKnowledgeBase(config=config)
        # Should not raise exception
        await kb.close()

    @pytest.mark.asyncio
    async def test_close_with_exception(self):
        """Test exception when closing"""
        config = KnowledgeBaseConfig(kb_id="test_kb")
        mock_vector_store = MagicMock()
        mock_vector_store.close = MagicMock(side_effect=Exception("Close error"))

        kb = ConcreteKnowledgeBase(
            config=config,
            vector_store=mock_vector_store,
        )
        # Should catch exception, not raise
        await kb.close()
