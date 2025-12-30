# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Vector Store Abstract Base Class

Provides a unified interface for vector stores.
"""
from abc import ABC, abstractmethod
from typing import Any, List, Optional

from openjiuwen.core.retrieval.common.retrieval_result import SearchResult


class VectorStore(ABC):
    """Vector store abstract base class"""
    
    @abstractmethod
    async def add(
        self,
        data: dict | List[dict],
        batch_size: int | None = 128,
        **kwargs: Any,
    ) -> None:
        """Add vectors"""
        pass
    
    @abstractmethod
    async def search(
        self,
        query_vector: List[float],
        top_k: int = 5,
        filters: Optional[dict] = None,
        **kwargs: Any,
    ) -> List[SearchResult]:
        """
        Vector search
        
        Args:
            query_vector: Query vector
            top_k: Number of results to return
            filters: Metadata filter conditions
            **kwargs: Additional parameters
            
        Returns:
            List of search results
        """
        pass
    
    @abstractmethod
    async def sparse_search(
        self,
        query_text: str,
        top_k: int = 5,
        filters: Optional[dict] = None,
        **kwargs: Any,
    ) -> List[SearchResult]:
        """
        Sparse search (BM25)
        
        Args:
            query_text: Query text
            top_k: Number of results to return
            filters: Metadata filter conditions
            **kwargs: Additional parameters
            
        Returns:
            List of search results
        """
        pass
    
    @abstractmethod
    async def hybrid_search(
        self,
        query_text: str,
        query_vector: Optional[List[float]] = None,
        top_k: int = 5,
        alpha: float = 0.5,
        filters: Optional[dict] = None,
        **kwargs: Any,
    ) -> List[SearchResult]:
        """
        Hybrid search (sparse retrieval + vector retrieval)
        
        Args:
            query_text: Query text
            query_vector: Query vector (optional, if provided will be used, otherwise needs to be embedded first)
            top_k: Number of results to return
            alpha: Hybrid weight (0=pure sparse retrieval, 1=pure vector retrieval, 0.5=balanced)
            filters: Metadata filter conditions
            **kwargs: Additional parameters
            
        Returns:
            List of search results
        """
        pass
    
    @abstractmethod
    async def delete(
        self,
        ids: Optional[List[str]] = None,
        filter_expr: Optional[str] = None,
        **kwargs: Any,
    ) -> bool:
        """Delete vectors"""
        pass
