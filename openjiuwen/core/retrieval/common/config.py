# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Configuration Classes

All configuration classes are unified in this file.
"""
from typing import Optional, Literal, Dict, Any

from pydantic import BaseModel, Field, model_validator


class KnowledgeBaseConfig(BaseModel):
    """Knowledge base configuration"""
    kb_id: str = Field(..., description="Knowledge base identifier")
    index_type: Literal["hybrid", "bm25", "vector"] = Field(
        default="hybrid", description="Index type"
    )
    use_graph: bool = Field(default=False, description="Whether to use graph index")
    chunk_size: int = Field(default=512, description="Chunk size")
    chunk_overlap: int = Field(default=50, description="Chunk overlap")


class RetrievalConfig(BaseModel):
    """Retrieval configuration"""
    top_k: int = Field(default=5, description="Number of results to return")
    score_threshold: Optional[float] = Field(
        default=None, description="Score threshold"
    )
    use_graph: Optional[bool] = Field(
        default=None, description="Whether to use graph retrieval (None uses default config)"
    )
    agentic: bool = Field(default=False, description="Whether to use Agentic retrieval")
    graph_expansion: bool = Field(
        default=False, description="Whether to enable graph expansion"
    )
    filters: Optional[Dict[str, Any]] = Field(
        default=None, description="Metadata filter conditions"
    )


class IndexConfig(BaseModel):
    """Index configuration"""
    index_name: str = Field(..., description="Index name")
    index_type: Literal["hybrid", "bm25", "vector"] = Field(
        default="hybrid", description="Index type"
    )


class VectorStoreConfig(BaseModel):
    """Vector store configuration"""
    collection_name: str = Field(..., description="Collection name")
    distance_metric: Literal["cosine", "euclidean", "dot"] = Field(
        default="cosine", description="Distance metric"
    )


class EmbeddingConfig(BaseModel):
    """Embedding model configuration"""
    model_name: str = Field(..., description="Model name")
    api_key: Optional[str] = Field(None, description="API Key")
    base_url: Optional[str] = Field(None, description="API Base URL")
