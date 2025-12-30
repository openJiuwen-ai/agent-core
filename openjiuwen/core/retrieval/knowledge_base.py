# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Knowledge Base Abstract Base Class

Provides a unified interface for knowledge bases as the top-level entry point.
"""
from abc import ABC, abstractmethod
from typing import Any, List, Optional, Dict

from openjiuwen.core.retrieval.common.config import KnowledgeBaseConfig, RetrievalConfig
from openjiuwen.core.retrieval.common.document import Document
from openjiuwen.core.retrieval.common.retrieval_result import RetrievalResult
from openjiuwen.core.retrieval.indexing.processor.parser.base import Parser
from openjiuwen.core.retrieval.indexing.processor.chunker.base import Chunker
from openjiuwen.core.retrieval.indexing.processor.extractor.base import Extractor
from openjiuwen.core.retrieval.vector_store.base import VectorStore
from openjiuwen.core.retrieval.embedding.base import Embedding
from openjiuwen.core.retrieval.indexing.indexer.base import Indexer
from openjiuwen.core.common.logging import logger


class KnowledgeBase(ABC):
    """Knowledge Base Abstract Base Class"""
    
    def __init__(
        self,
        config: KnowledgeBaseConfig,
        vector_store: Optional[VectorStore] = None,
        embed_model: Optional[Embedding] = None,
        parser: Optional[Parser] = None,
        chunker: Optional[Chunker] = None,
        extractor: Optional[Extractor] = None,
        index_manager: Optional[Indexer] = None,
        llm_client: Optional[Any] = None,
        **kwargs: Any,
    ):
        self.config = config
        self.vector_store = vector_store
        self.embed_model = embed_model
        self.parser = parser
        self.chunker = chunker
        self.extractor = extractor
        self.index_manager = index_manager
        self.llm_client = llm_client
    
    @abstractmethod
    async def parse_files(
        self,
        file_paths: List[str],
        **kwargs: Any,
    ) -> List[Document]:
        """
        Parse files from file paths into a list of Document objects
        
        Args:
            file_paths: List of file paths
            **kwargs: Additional parameters
            
        Returns:
            List of Document objects
        """
        pass
    
    @abstractmethod
    async def add_documents(
        self,
        documents: List[Document],
        **kwargs: Any,
    ) -> List[str]:
        """Add documents to the knowledge base"""
        pass
    
    @abstractmethod
    async def retrieve(
        self,
        query: str,
        config: Optional[RetrievalConfig] = None,
        **kwargs: Any,
    ) -> List[RetrievalResult]:
        """Retrieve relevant documents"""
        pass
    
    @abstractmethod
    async def delete_documents(
        self,
        doc_ids: List[str],
        **kwargs: Any,
    ) -> bool:
        """Delete documents"""
        pass
    
    @abstractmethod
    async def update_documents(
        self,
        documents: List[Document],
        **kwargs: Any,
    ) -> List[str]:
        """Update documents"""
        pass
    
    @abstractmethod
    async def get_statistics(self) -> Dict[str, Any]:
        """Get knowledge base statistics"""
        pass
    
    async def close(self) -> None:
        """Close the knowledge base and release resources"""
        import inspect

        async def _maybe_await(obj):
            if not obj:
                return
            if inspect.iscoroutinefunction(getattr(obj, "close", None)):
                await obj.close()
            elif hasattr(obj, "close"):
                try:
                    obj.close()
                except Exception:
                    logger.warning("Failed to close object", exc_info=True)

        await _maybe_await(self.vector_store)
        await _maybe_await(self.index_manager)
