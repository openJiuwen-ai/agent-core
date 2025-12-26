# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
Retrieval result data model test cases
"""
import pytest
from pydantic import ValidationError

from openjiuwen.core.retrieval.common.retrieval_result import (
    SearchResult,
    RetrievalResult,
)


class TestSearchResult:
    """Search result data model tests"""

    @staticmethod
    def test_create_search_result():
        """Test creating search result"""
        result = SearchResult(
            id="result_1",
            text="Test result",
            score=0.95,
        )
        assert result.id == "result_1"
        assert result.text == "Test result"
        assert result.score == 0.95
        assert result.metadata == {}

    @staticmethod
    def test_create_search_result_with_metadata():
        """Test creating search result with metadata"""
        metadata = {"doc_id": "doc_1", "source": "test"}
        result = SearchResult(
            id="result_1",
            text="Test result",
            score=0.95,
            metadata=metadata,
        )
        assert result.metadata == metadata

    @staticmethod
    def test_missing_required_fields():
        """Test missing required fields"""
        with pytest.raises(ValidationError):
            SearchResult()
        
        with pytest.raises(ValidationError):
            SearchResult(id="result_1")
        
        with pytest.raises(ValidationError):
            SearchResult(id="result_1", text="Test result")


class TestRetrievalResult:
    """Retrieval result data model tests"""

    @staticmethod
    def test_create_retrieval_result():
        """Test creating retrieval result"""
        result = RetrievalResult(
            text="Test result",
            score=0.95,
        )
        assert result.text == "Test result"
        assert result.score == 0.95
        assert result.metadata == {}
        assert result.doc_id is None
        assert result.chunk_id is None

    @staticmethod
    def test_create_retrieval_result_with_all_fields():
        """Test creating retrieval result with all fields"""
        metadata = {"source": "test"}
        result = RetrievalResult(
            text="Test result",
            score=0.95,
            metadata=metadata,
            doc_id="doc_1",
            chunk_id="chunk_1",
        )
        assert result.text == "Test result"
        assert result.score == 0.95
        assert result.metadata == metadata
        assert result.doc_id == "doc_1"
        assert result.chunk_id == "chunk_1"

    @staticmethod
    def test_missing_required_fields():
        """Test missing required fields"""
        with pytest.raises(ValidationError):
            RetrievalResult()
        
        with pytest.raises(ValidationError):
            RetrievalResult(text="Test result")

