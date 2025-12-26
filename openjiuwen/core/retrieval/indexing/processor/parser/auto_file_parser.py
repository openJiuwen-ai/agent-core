# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.

"""
自动文件解析器

采用插件式架构，支持通过装饰器注册新的文件格式解析器。
"""

import os
from typing import Dict, List, Any, Type, Callable

from openjiuwen.core.common.logging import logger
from openjiuwen.core.retrieval.indexing.processor.parser.base import Parser
from openjiuwen.core.retrieval.common.document import Document


# 全局解析器注册表
_PARSER_REGISTRY: Dict[str, Callable[[], Parser]] = {}


def register_parser(file_extensions: List[str]):
    """
    装饰器：注册文件格式解析器
    
    Args:
        file_extensions: 支持的文件扩展名列表，如 [".pdf", ".PDF"]
    
    Returns:
        装饰器函数
    """
    def decorator(parser_class: Type[Parser]) -> Type[Parser]:
        def _create_parser_instance(cls=parser_class):
            return cls()
        
        for ext in file_extensions:
            normalized_ext = ext.lower()
            _PARSER_REGISTRY[normalized_ext] = _create_parser_instance
            logger.info(f"Registered parser {parser_class.__name__} for {normalized_ext}")
        return parser_class
    return decorator


class AutoFileParser(Parser):
    """自动文件解析器
    
    采用插件式架构，根据文件格式自动选择合适的解析器进行解析。
    支持通过 @register_parser 装饰器注册新的解析器。
    """
    
    def __init__(self, **kwargs: Any):
        # 导入所有解析器，触发注册
        self._ensure_parsers_loaded()
        super().__init__(**kwargs)
    
    def _ensure_parsers_loaded(self):
        """确保所有解析器都已加载并注册"""
        # 动态导入所有解析器模块，触发装饰器执行
        try:
            from openjiuwen.core.retrieval.indexing.processor.parser.pdf_parser import PDFParser
            from openjiuwen.core.retrieval.indexing.processor.parser.txt_md_parser import TxtMdParser
            from openjiuwen.core.retrieval.indexing.processor.parser.json_parser import JSONParser
            from openjiuwen.core.retrieval.indexing.processor.parser.word_parser import WordParser
        except ImportError as e:
            logger.warning(f"Failed to import some parser modules: {e}")
    
    async def parse(self, doc: str, doc_id: str = "", **kwargs: Any) -> List[Document]:
        """
        根据文件格式自动选择合适的解析器解析文件
        
        Args:
            doc: 文件路径
            doc_id: 文档ID
            **kwargs: 额外参数
            
        Returns:
            文档列表
            
        Raises:
            FileNotFoundError: 文件不存在
            ValueError: 不支持的文件格式
        """
        if not os.path.exists(doc):
            raise FileNotFoundError(f"File {doc} does not exist")
        
        # 获取文件扩展名
        file_ext = os.path.splitext(doc)[-1].lower()
        
        # 检查是否支持该格式
        if file_ext not in _PARSER_REGISTRY:
            raise ValueError(
                f"Unsupported format: {file_ext}, "
                f"only {list(_PARSER_REGISTRY.keys())} are supported"
            )
        
        # 获取对应的解析器实例
        parser = _PARSER_REGISTRY[file_ext]()
        logger.info(f"Using {parser.__class__.__name__} to parse {doc}")
        
        # 使用对应的解析器进行解析，获取文档对象列表
        documents = await parser.parse(doc, doc_id, **kwargs)

        if not documents:
            return []

        # 从 kwargs 获取文件信息，或使用默认值
        file_name = kwargs.get("file_name", os.path.basename(doc))
        
        # 增强文档的元数据
        for document in documents:
            document.metadata.update({
                "title": file_name,
                "file_path": doc,
                "file_ext": file_ext,
            })

        return documents

    def supports(self, doc: str) -> bool:
        """
        检查是否支持该文档
        
        Args:
            doc: 文件路径
            
        Returns:
            是否支持
        """
        if not os.path.exists(doc):
            return False
        
        file_ext = os.path.splitext(doc)[-1].lower()
        return file_ext in _PARSER_REGISTRY
    
    @classmethod
    def register_new_parser(cls, file_extension: str, parser_factory: Callable[[], Parser]):
        """
        动态注册新的解析器（运行时）
        
        Args:
            file_extension: 文件扩展名，如 ".pdf"
            parser_factory: 解析器工厂函数，调用后返回解析器实例
        """
        normalized_ext = file_extension.lower()
        _PARSER_REGISTRY[normalized_ext] = parser_factory
        logger.info(f"Dynamically registered parser for {normalized_ext}")
    
    @classmethod
    def get_supported_formats(cls) -> List[str]:
        """
        获取所有支持的文件格式
        
        Returns:
            支持的文件格式列表
        """
        return list(_PARSER_REGISTRY.keys())
