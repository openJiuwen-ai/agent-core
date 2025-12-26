# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
Embedding model abstract base class test cases
"""
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.retrieval.embedding.base import Embedding


class ConcreteEmbedding(Embedding):
    """Concrete embedding model implementation for testing abstract base class"""

    async def embed_query(self, text, **kwargs):
        return [0.1] * 384

    async def embed_documents(self, texts, batch_size=None, **kwargs):
        return [[0.1] * 384] * len(texts)

    @property
    def dimension(self):
        return 384


class TestEmbedding:
    """Embedding model abstract base class tests"""

    @pytest.mark.asyncio
    async def test_embed_query(self):
        """Test embedding query text"""
        model = ConcreteEmbedding()
        embedding = await model.embed_query("test query")
        assert len(embedding) == 384
        assert all(isinstance(x, float) for x in embedding)

    @pytest.mark.asyncio
    async def test_embed_documents(self):
        """Test embedding document texts"""
        model = ConcreteEmbedding()
        texts = ["text 1", "text 2", "text 3"]
        embeddings = await model.embed_documents(texts)
        assert len(embeddings) == 3
        assert all(len(emb) == 384 for emb in embeddings)

    @staticmethod
    def test_dimension():
        """Test dimension property"""
        model = ConcreteEmbedding()
        assert model.dimension == 384

