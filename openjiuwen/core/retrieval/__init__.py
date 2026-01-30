# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from openjiuwen.core.common.logging import logger

# Knowledge base implementations
# Common data models and configs
from openjiuwen.core.retrieval.common.callbacks import BaseCallback, TqdmCallback
from openjiuwen.core.retrieval.common.config import (
    EmbeddingConfig,
    IndexConfig,
    KnowledgeBaseConfig,
    RetrievalConfig,
    VectorStoreConfig,
)
from openjiuwen.core.retrieval.common.document import Document, MultimodalDocument, TextChunk
from openjiuwen.core.retrieval.common.retrieval_result import RetrievalResult, SearchResult
from openjiuwen.core.retrieval.common.triple import Triple
from openjiuwen.core.retrieval.common.triple_beam import TripleBeam
from openjiuwen.core.retrieval.embedding.api_embedding import APIEmbedding

# Embedding related
from openjiuwen.core.retrieval.embedding.base import Embedding
from openjiuwen.core.retrieval.embedding.ollama_embedding import OllamaEmbedding
from openjiuwen.core.retrieval.embedding.openai_embedding import OpenAIEmbedding
from openjiuwen.core.retrieval.embedding.utils import parse_base64_embedding
from openjiuwen.core.retrieval.embedding.vllm_embedding import VLLMEmbedding
from openjiuwen.core.retrieval.graph_knowledge_base import GraphKnowledgeBase

# Indexer related
from openjiuwen.core.retrieval.indexing.indexer.base import Indexer
from openjiuwen.core.retrieval.indexing.indexer.milvus_indexer import MilvusIndexer

# Processor base classes
from openjiuwen.core.retrieval.indexing.processor.base import Processor
from openjiuwen.core.retrieval.indexing.processor.chunker.base import Chunker
from openjiuwen.core.retrieval.indexing.processor.chunker.char_chunker import CharChunker

# Chunker implementations
from openjiuwen.core.retrieval.indexing.processor.chunker.chunking import TextChunker
from openjiuwen.core.retrieval.indexing.processor.chunker.text_preprocessor import (
    PreprocessingPipeline,
    SpecialCharacterNormalizer,
    TextPreprocessor,
    URLEmailRemover,
    WhitespaceNormalizer,
)
from openjiuwen.core.retrieval.indexing.processor.chunker.text_splitter import (
    CharSplitter,
    IndexSentenceSplitter,
    TextSplitter,
)
from openjiuwen.core.retrieval.indexing.processor.chunker.tokenizer_chunker import TokenizerChunker
from openjiuwen.core.retrieval.indexing.processor.extractor.base import Extractor

# Extractor implementations
from openjiuwen.core.retrieval.indexing.processor.extractor.triple_extractor import TripleExtractor

# Parser implementations
from openjiuwen.core.retrieval.indexing.processor.parser.auto_file_parser import AutoFileParser
from openjiuwen.core.retrieval.indexing.processor.parser.base import Parser
from openjiuwen.core.retrieval.indexing.processor.parser.json_parser import JSONParser
from openjiuwen.core.retrieval.indexing.processor.parser.pdf_parser import PDFParser
from openjiuwen.core.retrieval.indexing.processor.parser.txt_md_parser import TxtMdParser
from openjiuwen.core.retrieval.indexing.processor.parser.word_parser import WordParser
from openjiuwen.core.retrieval.indexing.processor.splitter.base import Splitter

# Splitter implementations
from openjiuwen.core.retrieval.indexing.processor.splitter.splitter import SentenceSplitter

# Vector field related
from openjiuwen.core.retrieval.indexing.vector_fields.milvus_fields import (
    MilvusAUTO,
    MilvusFLAT,
    MilvusHNSW,
    MilvusIVF,
    MilvusSCANN,
)
from openjiuwen.core.retrieval.indexing.vector_fields.pg_fields import PGVectorField
from openjiuwen.core.retrieval.knowledge_base import KnowledgeBase
from openjiuwen.core.retrieval.retriever.agentic_retriever import AgenticRetriever

# Retriever related
from openjiuwen.core.retrieval.retriever.base import Retriever
from openjiuwen.core.retrieval.retriever.graph_retriever import GraphRetriever
from openjiuwen.core.retrieval.retriever.hybrid_retriever import HybridRetriever
from openjiuwen.core.retrieval.retriever.sparse_retriever import SparseRetriever
from openjiuwen.core.retrieval.retriever.vector_retriever import VectorRetriever
from openjiuwen.core.retrieval.simple_knowledge_base import (
    SimpleKnowledgeBase,
    retrieve_multi_kb,
    retrieve_multi_kb_with_source,
)

# Utilities
from openjiuwen.core.retrieval.utils.config_manager import ConfigManager
from openjiuwen.core.retrieval.utils.exceptions import (
    DocumentProcessingError,
    KnowledgeBaseError,
    KnowledgeBaseIndexError,
    KnowledgeBaseRetrievalError,
    RAGException,
    VectorStoreError,
)
from openjiuwen.core.retrieval.utils.fusion import rrf_fusion

# Vector store related
from openjiuwen.core.retrieval.vector_store.base import VectorStore
from openjiuwen.core.retrieval.vector_store.milvus_store import MilvusVectorStore
from openjiuwen.core.retrieval.vector_store.pg_store import PGVectorStore

_KNOWLEDGE_BASE_CLASSES = [
    "KnowledgeBase",
    "SimpleKnowledgeBase",
    "GraphKnowledgeBase",
]

_KNOWLEDGE_BASE_FUNCTIONS = [
    "retrieve_multi_kb",
    "retrieve_multi_kb_with_source",
]

_COMMON_CLASSES = [
    "KnowledgeBaseConfig",
    "RetrievalConfig",
    "IndexConfig",
    "VectorStoreConfig",
    "EmbeddingConfig",
    "Document",
    "MultimodalDocument",
    "TextChunk",
    "RetrievalResult",
    "SearchResult",
    "Triple",
    "TripleBeam",
    "BaseCallback",
    "TqdmCallback",
]

_EMBEDDING_CLASSES = [
    "Embedding",
    "APIEmbedding",
    "OllamaEmbedding",
    "OpenAIEmbedding",
    "VLLMEmbedding",
]

_VECTOR_STORE_CLASSES = [
    "VectorStore",
    "MilvusVectorStore",
    "PGVectorStore",
]

_INDEXER_CLASSES = [
    "Indexer",
    "MilvusIndexer",
]

_PROCESSOR_CLASSES = [
    "Processor",
    "Parser",
    "Chunker",
    "Extractor",
    "Splitter",
    "SentenceSplitter",
    "TextSplitter",
    "CharSplitter",
    "IndexSentenceSplitter",
    "TextPreprocessor",
    "WhitespaceNormalizer",
    "URLEmailRemover",
    "SpecialCharacterNormalizer",
    "PreprocessingPipeline",
    "TextChunker",
    "CharChunker",
    "TokenizerChunker",
    "AutoFileParser",
    "JSONParser",
    "PDFParser",
    "TxtMdParser",
    "WordParser",
    "TripleExtractor",
]

_RETRIEVER_CLASSES = [
    "Retriever",
    "VectorRetriever",
    "SparseRetriever",
    "HybridRetriever",
    "GraphRetriever",
    "AgenticRetriever",
]

_UTILS = [
    "ConfigManager",
    "RAGException",
    "KnowledgeBaseError",
    "KnowledgeBaseIndexError",
    "KnowledgeBaseRetrievalError",
    "DocumentProcessingError",
    "VectorStoreError",
    "rrf_fusion",
    "parse_base64_embedding",
]

_VECTOR_FIELD_CLASSES = [
    "MilvusAUTO",
    "MilvusFLAT",
    "MilvusHNSW",
    "MilvusIVF",
    "MilvusSCANN",
    "PGVectorField",
]

try:
    from openjiuwen.core.retrieval.indexing.indexer.chroma_indexer import ChromaIndexer
    from openjiuwen.core.retrieval.indexing.vector_fields.chroma_fields import ChromaVectorField
    from openjiuwen.core.retrieval.vector_store.chroma_store import ChromaVectorStore

    _INDEXER_CLASSES.append("ChromaIndexer")
    _VECTOR_STORE_CLASSES.append("ChromaVectorStore")
    _VECTOR_FIELD_CLASSES.append("ChromaVectorField")
except Exception as e:
    logger.warning("Chroma database is disabled, reason: %r", e)

__all__ = (
    _KNOWLEDGE_BASE_CLASSES
    + _KNOWLEDGE_BASE_FUNCTIONS
    + _COMMON_CLASSES
    + _EMBEDDING_CLASSES
    + _VECTOR_STORE_CLASSES
    + _INDEXER_CLASSES
    + _PROCESSOR_CLASSES
    + _RETRIEVER_CLASSES
    + _UTILS
    + _VECTOR_FIELD_CLASSES
)
