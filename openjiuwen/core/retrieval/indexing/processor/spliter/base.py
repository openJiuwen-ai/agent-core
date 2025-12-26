# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
文本分割器抽象基类

提供文本分割的统一接口，子类需要实现具体的分割逻辑。
"""
from abc import ABC, abstractmethod
from typing import List, Tuple, Callable, Optional, Any

from openjiuwen.core.retrieval.common.document import Document, TextChunk
from openjiuwen.core.common.logging import logger


class Splitter(ABC):
    """文本分割器抽象基类"""
    
    def __init__(
        self,
        tokenizer: Optional[Callable] = None,
        chunk_size: int = 512,
        chunk_overlap: int = 50,
        **kwargs: Any,
    ):
        """
        初始化文本分割器
        
        Args:
            tokenizer: 分词器，需要具有 encode 和 decode 方法
            chunk_size: 分块大小（token 数或字符数）
            chunk_overlap: 分块重叠大小
            **kwargs: 其他参数
        """
        if chunk_size <= 0:
            raise ValueError(f"chunk_size 必须大于 0，当前值: {chunk_size}")
        if chunk_overlap < 0:
            raise ValueError(f"chunk_overlap 必须大于等于 0，当前值: {chunk_overlap}")
        if chunk_overlap >= chunk_size:
            raise ValueError(f"chunk_overlap ({chunk_overlap}) 必须小于 chunk_size ({chunk_size})")
        
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        
        if tokenizer is not None:
            self._validate_tokenizer(tokenizer)
            self.tokenizer = tokenizer
            if hasattr(tokenizer, "encode") and hasattr(tokenizer, "decode"):
                self.tokenizer_enc = tokenizer.encode
                self.tokenizer_dec = tokenizer.decode
            else:
                self.tokenizer_enc = tokenizer
                self.tokenizer_dec = None
        else:
            self.tokenizer = None
            self.tokenizer_enc = None
            self.tokenizer_dec = None
    
    def _validate_tokenizer(self, tokenizer: Callable) -> None:
        """
        验证分词器是否有效
        
        Args:
            tokenizer: 分词器对象
            
        Raises:
            ValueError: 如果分词器无效
        """
        if tokenizer is None:
            return
        
        # 检查是否有 encode 方法或可以直接调用
        if not (hasattr(tokenizer, "encode") or callable(tokenizer)):
            raise ValueError("Tokenizer 必须具有 encode 方法或可调用")
    
    @abstractmethod
    def __call__(self, doc: str) -> List[Tuple[str, int, int]]:
        """
        分割文档，返回 (文本, 起始位置, 结束位置) 的元组列表
        
        Args:
            doc: 待分割的文档文本
            
        Returns:
            分割后的块列表，每个元素为 (文本, 起始字符位置, 结束字符位置)
        """
        pass
    
    def get_nodes_from_documents(
        self, docs: List[Document]
    ) -> List[TextChunk]:
        """
        从文档列表中获取分割后的节点
        
        Args:
            docs: 文档列表
            
        Returns:
            分割后的文本块列表
        """
        returned_nodes = []
        for doc in docs:
            if not doc or not hasattr(doc, 'text') or not doc.text:
                logger.warning(f"跳过空文档: {doc}")
                continue
                
            chunk_tuples = self.__call__(doc.text)
            
            for chunk_text, start_idx, end_idx in chunk_tuples:
                _node = TextChunk.from_document(doc, chunk_text)
                returned_nodes.append(_node)
        
        logger.info(f"从 {len(docs)} 个文档中生成 {len(returned_nodes)} 个文本块")
        return returned_nodes
    
    def split_text(self, text: str) -> List[str]:
        """
        分割文本，仅返回文本列表（不包含位置信息）
        
        Args:
            text: 待分割的文本
            
        Returns:
            分割后的文本列表
        """
        chunks = self.__call__(text)
        return [chunk[0] for chunk in chunks]
    
    def _get_token_count(self, text: str) -> int:
        """
        获取文本的 token 数量
        
        Args:
            text: 文本内容
            
        Returns:
            token 数量，如果没有 tokenizer 则返回字符数
        """
        if self.tokenizer_enc is not None:
            tokens = self.tokenizer_enc(text)
            return len(tokens) if isinstance(tokens, (list, tuple)) else len(str(tokens))
        return len(text)
