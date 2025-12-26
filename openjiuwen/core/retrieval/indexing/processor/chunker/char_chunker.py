# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.

import uuid
from typing import List, Any

from openjiuwen.core.common.logging import logger
from openjiuwen.core.retrieval.indexing.processor.chunker.base import Chunker
from openjiuwen.core.retrieval.common.document import Document
from openjiuwen.core.retrieval.indexing.processor.chunker.text_splitter import CharSplitter


class CharChunker(Chunker):
    """固定大小分块器，基于字符长度进行分块"""

    def __init__(
        self,
        chunk_size: int = 512,
        chunk_overlap: int = 50,
        **kwargs: Any,
    ):
        """
        初始化固定大小分块器
        
        Args:
            chunk_size: 分块大小（字符数）
            chunk_overlap: 分块重叠大小（字符数）
        """
        super().__init__(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            **kwargs,
        )

    def chunk_text(self, text: str) -> List[str]:
        """
        分块文本
        
        Args:
            text: 待分块的文本
            
        Returns:
            分块后的文本列表
        """
        if not text:
            return []

        splitter = CharSplitter(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
        )
        
        doc = Document(text=text, metadata={})
        text_nodes = splitter.split(doc)
        
        chunks = []
        for node in text_nodes:
            chunks.append(node.text)
        
        logger.info("Character chunking completed: generated %d chunks", len(chunks))
        return chunks
