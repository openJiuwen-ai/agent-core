# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Exception Definitions

Contains all RAG-related exception classes.
"""


class RAGException(Exception):
    """RAG module base exception"""
    pass


class KnowledgeBaseError(RAGException):
    """Knowledge base error"""
    pass


class KnowledgeBaseIndexError(RAGException):
    """Index error"""
    pass


class KnowledgeBaseRetrievalError(RAGException):
    """Retrieval error"""
    pass


class DocumentProcessingError(RAGException):
    """Document processing error"""
    pass


class VectorStoreError(RAGException):
    """Vector store error"""
    pass
