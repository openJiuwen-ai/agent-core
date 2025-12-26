# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
Index manager abstract base class test cases
"""
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.retrieval.indexing.indexer.base import Indexer
from openjiuwen.core.retrieval.common.config import IndexConfig
from openjiuwen.core.retrieval.common.document import TextChunk


class ConcreteIndexer(Indexer):
    """Concrete index manager implementation for testing abstract base class"""

    async def build_index(self, chunks, config, embed_model=None, **kwargs):
        return True

    async def update_index(self, chunks, doc_id, config, embed_model=None, **kwargs):
        return True

    async def delete_index(self, doc_id, index_name, **kwargs):
        return True

    async def index_exists(self, index_name):
        return True

    async def get_index_info(self, index_name):
        return {"count": 10}


class TestIndexer:
    """Index manager abstract base class tests"""

    @pytest.mark.asyncio
    async def test_build_index(self):
        """Test building index"""
        indexer = ConcreteIndexer()
        chunks = [
            TextChunk(id_="1", text="chunk 1", doc_id="doc_1"),
            TextChunk(id_="2", text="chunk 2", doc_id="doc_1"),
        ]
        config = IndexConfig(index_name="test_index", index_type="vector")
        result = await indexer.build_index(chunks, config)
        assert result is True

    @pytest.mark.asyncio
    async def test_update_index(self):
        """Test updating index"""
        indexer = ConcreteIndexer()
        chunks = [TextChunk(id_="1", text="updated chunk", doc_id="doc_1")]
        config = IndexConfig(index_name="test_index", index_type="vector")
        result = await indexer.update_index(chunks, "doc_1", config)
        assert result is True

    @pytest.mark.asyncio
    async def test_delete_index(self):
        """Test deleting index"""
        indexer = ConcreteIndexer()
        result = await indexer.delete_index("doc_1", "test_index")
        assert result is True

    @pytest.mark.asyncio
    async def test_index_exists(self):
        """Test checking if index exists"""
        indexer = ConcreteIndexer()
        result = await indexer.index_exists("test_index")
        assert result is True

    @pytest.mark.asyncio
    async def test_get_index_info(self):
        """Test getting index information"""
        indexer = ConcreteIndexer()
        info = await indexer.get_index_info("test_index")
        assert "count" in info
        assert info["count"] == 10

