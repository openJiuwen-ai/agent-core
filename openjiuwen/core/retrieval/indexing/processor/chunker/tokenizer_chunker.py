# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Fixed Size Chunker Implementation

A simple text chunker based on character length.
"""
import uuid
from typing import List, Any

from openjiuwen.core.common.logging import logger
from openjiuwen.core.retrieval.indexing.processor.chunker.base import Chunker
from openjiuwen.core.retrieval.common.document import Document
from openjiuwen.core.retrieval.indexing.processor.chunker.text_splitter import IndexSentenceSplitter



class TokenizerChunker(Chunker):
    """Fixed size chunker based on character length"""

    def __init__(
        self,
        chunk_size: int,
        chunk_overlap: int,
        tokenizer: Any,
        **kwargs: Any,
    ):
        """
        Initialize fixed size chunker
        
        Args:
            chunk_size: Chunk size (number of characters)
            chunk_overlap: Chunk overlap size (number of characters)
            length_function: Length calculation function (defaults to character count)
        """
        super().__init__(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            **kwargs,
        )
        self.tokenizer = tokenizer

    def chunk_text(self, text: str) -> List[str]:
        """
        Chunk text
        
        Args:
            text: Text to be chunked
            
        Returns:
            List of chunked texts
        """
        if not text:
            return []

        splitter = IndexSentenceSplitter(
            tokenizer=self.tokenizer,
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
        )
        doc = Document(text=text, metadata={})
        text_nodes = splitter.split(doc)
        chunks = []
        for node in text_nodes:
            chunks.append(node.text)
        
        logger.info("Token chunking completed: generated %d chunks", len(chunks))
        return chunks
