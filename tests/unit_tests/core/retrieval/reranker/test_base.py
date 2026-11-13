# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Reranker model abstract base class test cases
"""

import pytest

from openjiuwen.core.foundation.store.base_reranker import RerankerConfig
from openjiuwen.core.retrieval.common.document import Document
from openjiuwen.core.retrieval.reranker.base import Reranker


class ConcreteReranker(Reranker):
    """Concrete reranker model implementation for testing abstract base class"""

    async def rerank(
        self, query: str, doc: list[str | Document], instruct: bool | str = True, **kwargs
    ) -> dict[str, float]:
        """Test implementation of rerank"""
        doc_ids = [d if isinstance(d, str) else d.id_ for d in doc]
        return {doc_id: 0.5 for doc_id in doc_ids}

    def rerank_sync(
        self, query: str, doc: list[str | Document], instruct: bool | str = True, **kwargs
    ) -> dict[str, float]:
        """Test implementation of rerank_sync"""
        doc_ids = [d if isinstance(d, str) else d.id_ for d in doc]
        return {doc_id: 0.5 for doc_id in doc_ids}


class TestReranker:
    """Reranker model abstract base class tests"""

    @staticmethod
    @pytest.mark.asyncio
    async def test_rerank_with_strings():
        """Test rerank with string documents"""
        model = ConcreteReranker()
        query = "test query"
        docs = ["doc1", "doc2", "doc3"]
        result = await model.rerank(query, docs)
        assert len(result) == 3
        assert all(doc_id in result for doc_id in docs)
        assert all(score == 0.5 for score in result.values())

    @staticmethod
    @pytest.mark.asyncio
    async def test_rerank_with_documents():
        """Test rerank with Document objects"""
        model = ConcreteReranker()
        query = "test query"
        docs = [
            Document(text="First document", id_="doc1"),
            Document(text="Second document", id_="doc2"),
        ]
        result = await model.rerank(query, docs)
        assert len(result) == 2
        assert "doc1" in result
        assert "doc2" in result
        assert all(score == 0.5 for score in result.values())

    @staticmethod
    @pytest.mark.asyncio
    async def test_rerank_with_mixed_input():
        """Test rerank with mixed string and Document inputs"""
        model = ConcreteReranker()
        query = "test query"
        docs = ["doc1", Document(text="Second document", id_="doc2")]
        result = await model.rerank(query, docs)
        assert len(result) == 2
        assert "doc1" in result
        assert "doc2" in result

    @staticmethod
    @pytest.mark.asyncio
    async def test_rerank_with_instruct_true():
        """Test rerank with instruct=True"""
        model = ConcreteReranker()
        query = "test query"
        docs = ["doc1"]
        result = await model.rerank(query, docs, instruct=True)
        assert "doc1" in result

    @staticmethod
    @pytest.mark.asyncio
    async def test_rerank_with_instruct_false():
        """Test rerank with instruct=False"""
        model = ConcreteReranker()
        query = "test query"
        docs = ["doc1"]
        result = await model.rerank(query, docs, instruct=False)
        assert "doc1" in result

    @staticmethod
    @pytest.mark.asyncio
    async def test_rerank_with_custom_instruct():
        """Test rerank with custom instruction string"""
        model = ConcreteReranker()
        query = "test query"
        docs = ["doc1"]
        result = await model.rerank(query, docs, instruct="Custom instruction")
        assert "doc1" in result

    @staticmethod
    def test_rerank_sync_with_strings():
        """Test rerank_sync with string documents"""
        model = ConcreteReranker()
        query = "test query"
        docs = ["doc1", "doc2", "doc3"]
        result = model.rerank_sync(query, docs)
        assert len(result) == 3
        assert all(doc_id in result for doc_id in docs)
        assert all(score == 0.5 for score in result.values())

    @staticmethod
    def test_rerank_sync_with_documents():
        """Test rerank_sync with Document objects"""
        model = ConcreteReranker()
        query = "test query"
        docs = [
            Document(text="First document", id_="doc1"),
            Document(text="Second document", id_="doc2"),
        ]
        result = model.rerank_sync(query, docs)
        assert len(result) == 2
        assert "doc1" in result
        assert "doc2" in result

    @staticmethod
    def test_rerank_sync_with_instruct_true():
        """Test rerank_sync with instruct=True"""
        model = ConcreteReranker()
        query = "test query"
        docs = ["doc1"]
        result = model.rerank_sync(query, docs, instruct=True)
        assert "doc1" in result

    @staticmethod
    def test_rerank_sync_with_instruct_false():
        """Test rerank_sync with instruct=False"""
        model = ConcreteReranker()
        query = "test query"
        docs = ["doc1"]
        result = model.rerank_sync(query, docs, instruct=False)
        assert "doc1" in result

    @staticmethod
    def test_rerank_sync_with_custom_instruct():
        """Test rerank_sync with custom instruction string"""
        model = ConcreteReranker()
        query = "test query"
        docs = ["doc1"]
        result = model.rerank_sync(query, docs, instruct="Custom instruction")
        assert "doc1" in result


class TestRerankerConfigPopulation:
    """issue #2434: alias 不能让按字段名传值被静默丢弃"""

    @staticmethod
    def test_field_name_is_accepted():
        config = RerankerConfig(model_name="bge-reranker", api_base="http://localhost:8000")
        assert config.model_name == "bge-reranker"

    @staticmethod
    def test_alias_is_still_accepted():
        config = RerankerConfig(model="bge-reranker", api_base="http://localhost:8000")
        assert config.model_name == "bge-reranker"

    @staticmethod
    def test_model_name_reaches_the_request_payload():
        """配置值要真的流到请求体里，不能只是个摆设"""
        from openjiuwen.core.retrieval.reranker.standard_reranker import StandardReranker

        config = RerankerConfig(model_name="bge-reranker", api_base="http://localhost:8000")
        reranker = StandardReranker(config)
        params = reranker._request_params(query="q", instruct=False)
        assert params["model"] == "bge-reranker"
