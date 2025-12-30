# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Document Data Models

Contains Document and TextChunk data models.
"""
import uuid
from typing import Dict, Any

from pydantic import BaseModel, Field


class Document(BaseModel):
    """Document data model"""
    id_: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Document ID")
    text: str = Field(..., description="Document text content")
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Document metadata"
    )


class TextChunk(BaseModel):
    """Text chunk data model"""
    id_: str = Field(..., description="Chunk ID")
    text: str = Field(..., description="Chunk text content")
    doc_id: str = Field(..., description="Parent document ID")
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Chunk metadata"
    )
    embedding: list[float] | None = Field(
        default=None, description="Chunk embedding vector"
    )
    
    @classmethod
    def from_document(cls, doc: Document, chunk_text: str, id_: str = "") -> "TextChunk":
        """Create TextChunk from Document"""
        return cls(
            id_=id_ if id_ else str(uuid.uuid4()),
            text=chunk_text,
            doc_id=doc.id_,
            metadata=doc.metadata,
        )
