# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
Exception definition test cases
"""
import pytest

from openjiuwen.core.retrieval.utils.exceptions import (
    RAGException,
    KnowledgeBaseError,
    KnowledgeBaseIndexError,
    KnowledgeBaseRetrievalError,
    DocumentProcessingError,
    VectorStoreError,
)


class TestRAGException:
    """RAG exception tests"""

    @staticmethod
    def test_rag_exception():
        """测试基础 RAG 异常"""
        with pytest.raises(RAGException):
            raise RAGException("Test error")

    @staticmethod
    def test_rag_exception_message():
        """测试异常消息"""
        try:
            raise RAGException("Test error message")
        except RAGException as e:
            assert str(e) == "Test error message"


class TestKnowledgeBaseError:
    """Knowledge base error tests"""

    @staticmethod
    def test_knowledge_base_error():
        """测试知识库错误"""
        with pytest.raises(KnowledgeBaseError):
            raise KnowledgeBaseError("KB error")

    @staticmethod
    def test_knowledge_base_error_inheritance():
        """测试知识库错误继承关系"""
        assert issubclass(KnowledgeBaseError, RAGException)


class TestKnowledgeBaseIndexError:
    """Index error tests"""

    @staticmethod
    def test_knowledge_base_index_error():
        """测试索引错误"""
        with pytest.raises(KnowledgeBaseIndexError):
            raise KnowledgeBaseIndexError("Index error")

    @staticmethod
    def test_knowledge_base_index_error_inheritance():
        """测试索引错误继承关系"""
        assert issubclass(KnowledgeBaseIndexError, RAGException)


class TestKnowledgeBaseRetrievalError:
    """Retrieval error tests"""

    @staticmethod
    def test_knowledge_base_retrieval_error():
        """测试检索错误"""
        with pytest.raises(KnowledgeBaseRetrievalError):
            raise KnowledgeBaseRetrievalError("Retrieval error")

    @staticmethod
    def test_knowledge_base_retrieval_error_inheritance():
        """测试检索错误继承关系"""
        assert issubclass(KnowledgeBaseRetrievalError, RAGException)


class TestDocumentProcessingError:
    """Document processing error tests"""

    @staticmethod
    def test_document_processing_error():
        """测试文档处理错误"""
        with pytest.raises(DocumentProcessingError):
            raise DocumentProcessingError("Processing error")

    @staticmethod
    def test_document_processing_error_inheritance():
        """测试文档处理错误继承关系"""
        assert issubclass(DocumentProcessingError, RAGException)


class TestVectorStoreError:
    """Vector store error tests"""

    @staticmethod
    def test_vector_store_error():
        """测试向量存储错误"""
        with pytest.raises(VectorStoreError):
            raise VectorStoreError("Vector store error")

    @staticmethod
    def test_vector_store_error_inheritance():
        """测试向量存储错误继承关系"""
        assert issubclass(VectorStoreError, RAGException)

