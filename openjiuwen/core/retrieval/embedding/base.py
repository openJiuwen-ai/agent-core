# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Embedding Model Abstract Base Class

Provides a unified interface for embedding models.
"""
from abc import ABC, abstractmethod
from typing import Any, List, Optional


class Embedding(ABC):
    """Embedding model abstract base class"""
    
    @abstractmethod
    async def embed_query(self, text: str, **kwargs: Any) -> List[float]:
        """Embed query text"""
        pass
    
    @abstractmethod
    async def embed_documents(
        self,
        texts: List[str],
        batch_size: Optional[int] = None,
        **kwargs: Any,
    ) -> List[List[float]]:
        """Embed document texts"""
        pass
    
    @property
    @abstractmethod
    def dimension(self) -> int:
        """Return embedding dimension"""
        pass
