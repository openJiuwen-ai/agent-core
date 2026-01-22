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
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.exception.codes import StatusCode


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
            chunk_size: Chunk size, must be greater than 0
            chunk_overlap: Chunk overlap size, must be greater than or equal to 0 and less than chunk_size
            length_function: Length calculation function
            **kwargs: Other parameters

        Raises:
            ValueError: If chunk_size <= 0, chunk_overlap < 0, or chunk_overlap >= chunk_size

        Note:
            - chunk_size and chunk_overlap are validated during initialization
            - If chunk_overlap >= chunk_size, a ValueError will be raised
        """
        if chunk_size <= 0:
            raise build_error(
                StatusCode.RETRIEVAL_INDEXING_CHUNK_SIZE_INVALID,
                error_msg=f"chunk_size must be greater than 0, current value: {chunk_size}"
            )
        if chunk_overlap < 0:
            raise build_error(
                StatusCode.RETRIEVAL_INDEXING_CHUNK_OVERLAP_INVALID,
                error_msg=f"chunk_overlap must be greater than or equal to 0, current value: {chunk_overlap}"
            )
        if chunk_overlap >= chunk_size:
            raise build_error(
                StatusCode.RETRIEVAL_INDEXING_CHUNK_OVERLAP_INVALID,
                error_msg="chunk_overlap must be less than chunk_size"
            )

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
