# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Index Manager Abstract Base Class

Provides a unified interface for index management.
"""
from abc import ABC, abstractmethod
from typing import Any, List, Optional, Dict

from openjiuwen.core.retrieval.common.config import IndexConfig
from openjiuwen.core.retrieval.common.document import TextChunk
from openjiuwen.core.retrieval.embedding.base import Embedding


class Indexer(ABC):
    """Index manager abstract base class"""
    
    @abstractmethod
    async def build_index(
        self,
        chunks: List[TextChunk],
        config: IndexConfig,
        embed_model: Optional[Embedding] = None,
        **kwargs: Any,
    ) -> bool:
        """Build index"""
        pass
    
    @abstractmethod
    async def update_index(
        self,
        chunks: List[TextChunk],
        doc_id: str,
        config: IndexConfig,
        embed_model: Optional[Embedding] = None,
        **kwargs: Any,
    ) -> bool:
        """Update index"""
        pass
    
    @abstractmethod
    async def delete_index(
        self,
        doc_id: str,
        index_name: str,
        **kwargs: Any,
    ) -> bool:
        """Delete index"""
        pass
    
    @abstractmethod
    async def index_exists(
        self,
        index_name: str,
    ) -> bool:
        """Check if index exists"""
        pass
    
    @abstractmethod
    async def get_index_info(
        self,
        index_name: str,
    ) -> Dict[str, Any]:
        """Get index information"""
        pass
