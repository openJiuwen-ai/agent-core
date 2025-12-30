# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Retrieval Result Data Models

Contains SearchResult and RetrievalResult data models.
"""
from typing import Dict, Any, Optional

from pydantic import BaseModel, Field


class SearchResult(BaseModel):
    """Search result data model"""
    id: str = Field(..., description="Result ID")
    text: str = Field(..., description="Text content")
    score: float = Field(..., description="Relevance score")
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Metadata"
    )


class RetrievalResult(BaseModel):
    """Retrieval result data model"""
    text: str = Field(..., description="Text content")
    score: float = Field(..., description="Relevance score")
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Metadata"
    )
    doc_id: Optional[str] = Field(None, description="Document ID")
    chunk_id: Optional[str] = Field(None, description="Chunk ID")
