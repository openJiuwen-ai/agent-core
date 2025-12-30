# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Document Parser Abstract Base Class

Inherits from Processor, provides document parsing interface.
"""
import os
from abc import abstractmethod
from typing import List, AsyncIterator, Any, Optional

from openjiuwen.core.retrieval.indexing.processor.base import Processor
from openjiuwen.core.retrieval.common.document import Document


class Parser(Processor):
    """Document parser abstract base class (inherits from Processor)"""
    
    async def parse(self, doc: str, doc_id: str = "", **kwargs: Any) -> List[Document]:
        """
        Parse document
        
        Args:
            doc: Document source (file path, URL, etc.)
            doc_id: Document ID
            **kwargs: Additional parameters
            
        Returns:
            Document list
        """
        content = await self._parse(doc)
        if content:
            return [Document(
                id_=doc_id,
                text=content,
                metadata={}
            )]
        return []
    
    async def _parse(self, file_path: str) -> Optional[str]:
        pass

    async def lazy_parse(self, doc: str, doc_id: str = "", **kwargs: Any) -> AsyncIterator[Document]:
        """Default lazy loading implementation based on parse."""
        docs = await self.parse(doc, doc_id=doc_id, **kwargs)
        for d in docs:
            yield d

    async def process(self, *args: Any, **kwargs: Any) -> Any:
        """Compatible with Processor abstract method, defaults to calling parse."""
        return await self.parse(*args, **kwargs)
    
    def supports(self, doc: str) -> bool:
        """
        Check if the document source is supported
        
        Args:
            doc: Document source
            
        Returns:
            Whether it is supported
        """
        return False
