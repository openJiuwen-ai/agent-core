# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Text Chunker Abstract Base Class

Inherits from Processor, provides text chunking interface.
"""
import uuid
from abc import abstractmethod
from typing import List, Optional, Any, Callable

from openjiuwen.core.retrieval.indexing.processor.base import Processor
from openjiuwen.core.retrieval.common.document import Document, TextChunk


class Chunker(Processor):
    """Text chunker abstract base class (inherits from Processor)"""
    
    def __init__(
        self,
        chunk_size: int = 512,
        chunk_overlap: int = 50,
        length_function: Optional[Callable[[str], int]] = None,
        **kwargs: Any,
    ):
        """
        Initialize text chunker
        
        Args:
            chunk_size: Chunk size
            chunk_overlap: Chunk overlap size
            length_function: Length calculation function (default uses character count)
        """
        if chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be less than chunk_size")
        
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.length_function = length_function or len

    def chunk_text(self, text: str) -> List[str]:
        """
        Chunk text
        
        Args:
            text: Text to be chunked
            
        Returns:
            List of chunked texts
        """
        return None
    
    def chunk_documents(self, documents: List[Document]) -> List[TextChunk]:
        """
        Chunk document list
        
        Args:
            documents: Document list
            
        Returns:
            Document chunk list
        """
        chunks = []
        for doc in documents:
            texts = self.chunk_text(doc.text)
            for i, text in enumerate(texts):
                chunk = TextChunk(
                    id_=str(uuid.uuid4()),
                    text=text,
                    doc_id=doc.id_,
                    metadata={
                        **doc.metadata,
                        "chunk_index": i,
                        "total_chunks": len(texts),
                    },
                )
                chunks.append(chunk)
        return chunks

    async def process(self, documents: List[Document], **kwargs: Any) -> List[TextChunk]:
        """
        Process documents (implements Processor's process method)
        
        Args:
            documents: Document list
            **kwargs: Additional parameters
            
        Returns:
            Document chunk list
        """
        return self.chunk_documents(documents)
