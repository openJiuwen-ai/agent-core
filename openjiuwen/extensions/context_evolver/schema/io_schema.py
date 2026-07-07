# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Memory system schemas for different approaches."""

from typing import Optional, List, Union, Dict, Any
import json
from datetime import datetime, timezone
import hashlib
from pydantic import BaseModel, Field, ConfigDict

from openjiuwen.core.common.logging import context_engine_logger as logger

from ..core.schema import VectorNode

from .io_fallback import (
    ace_section_and_content_from_metadata,
    cognition_fields_from_metadata,
    deserialization_target_algorithm,
    reasoning_bank_item_dicts_from_metadata,
    reasoning_bank_query_from_metadata,
    reme_when_and_content_from_metadata,
    use_cross_algorithm_fallback,
)


class BaseMemory(BaseModel):
    """Abstract base class for all memory types."""
    workspace_id: str = Field(default="default", description="Workspace/user identifier")

    def to_vector_node(self) -> VectorNode:
        """Convert memory to vector node for storage.

        Returns:
            VectorNode representation

        Raises:
            NotImplementedError: Subclasses must implement
        """
        raise NotImplementedError("Subclasses must implement to_vector_node")

    @classmethod
    def from_vector_node(cls, node: VectorNode) -> "BaseMemory":
        """Convert vector node back to memory.

        Args:
            node: VectorNode from storage

        Returns:
            Memory instance

        Raises:
            NotImplementedError: Subclasses must implement
        """
        raise NotImplementedError("Subclasses must implement from_vector_node")


# ============================================================================
# ACE Memory Schemas
# ============================================================================

class ACEMemory(BaseMemory):
    """ACE Memory schema."""

    id: str = Field(description="Memory identifier")
    section: str = Field(description="Memory section/category")
    content: str = Field(description="Memory content")
    helpful: int = Field(default=0, description="Helpful count")
    harmful: int = Field(default=0, description="Harmful count")
    neutral: int = Field(default=0, description="Neutral count")
    created_at: datetime = Field(description="Creation timestamp")
    updated_at: datetime = Field(description="Last update timestamp")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "mem_001",
                "section": "python",
                "content": "Use lru_cache for memoization",
                "helpful": 0,
                "harmful": 0,
                "neutral": 0,
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
            }
        }
    )


    def to_vector_node(self) -> VectorNode:
        """Convert to vector node for storage.

        Returns:
            VectorNode with ACE memory data in metadata
        """
        # Use ID as node ID
        node_id = f"ace_{self.workspace_id}_{self.id}"

        # Content is what gets embedded
        embedding_content = self.content

        # Store all fields in metadata
        metadata = {
            "type": "exp_memory",
            "id": self.id,
            "section": self.section,
            "content": self.content,
            "helpful": self.helpful,
            "harmful": self.harmful,
            "neutral": self.neutral,
            "created_at": self.created_at.isoformat() if isinstance(self.created_at, datetime) else self.created_at,
            "updated_at": self.updated_at.isoformat() if isinstance(self.updated_at, datetime) else self.updated_at,
            "workspace_id": self.workspace_id
        }

        return VectorNode(
            id=node_id,
            content=embedding_content,
            metadata=metadata,
        )

    @classmethod
    def from_vector_node(cls, node: VectorNode) -> "ACEMemory":
        """Convert from vector node.

        Args:
            node: VectorNode from storage

        Returns:
            ACEMemory instance
        """
        metadata = node.metadata

        target = deserialization_target_algorithm(cls)
        if use_cross_algorithm_fallback(node, target):
            section, content = ace_section_and_content_from_metadata(metadata, node)
        else:
            section = str(metadata.get("section") or "general")
            content = str(metadata.get("content") if metadata.get("content") is not None else "") or str(
                node.content or ""
            )

        now_iso = datetime.now(timezone.utc).isoformat()

        return cls(
            id=metadata.get("id", ""),
            section=section,
            content=content,
            helpful=metadata.get("helpful", 0),
            harmful=metadata.get("harmful", 0),
            neutral=metadata.get("neutral", 0),
            created_at=metadata.get("created_at") or now_iso,
            updated_at=metadata.get("updated_at") or metadata.get("created_at") or now_iso,
            workspace_id=metadata.get("workspace_id", "default"),
        )



class ACESummarizeRequest(BaseModel):
    """ACE Summarize request schema."""

    matts: str = Field(
        default="none",
        description="MaTTS mode: none, parallel, or sequential"
    )
    query: str = Field(description="Query for summarization")
    trajectories: List[str] = Field(description="List of trajectory strings")
    ground_truth: Optional[str] = Field(default=None, description="Optional ground truth")
    feedback: Optional[List[str]] = Field(default=None, description="Optional environment feedback for trajectories")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "matts": "parallel",
                "query": "How to implement caching?",
                "trajectories": ["Traj1", "Traj2", "Traj3"],
                "ground_truth": "python code implementing caching",
                "feedback": ["feedback1", "feedback2", "feedback3"],
            }
        }
    )



class ACESummarizeResponse(BaseModel):
    """ACE Summarize response schema."""

    status: str = Field(description="Operation status")
    memory: List[ACEMemory] = Field(description="List of created or updated memories")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "success",
                "memory": [
                    {
                        "id": "mem_001",
                        "section": "python",
                        "content": "Use lru_cache for memoization",
                        "helpful": 0,
                        "harmful": 0,
                        "neutral": 0,
                        "created_at": "2024-01-01T00:00:00Z",
                        "updated_at": "2024-01-01T00:00:00Z",
                    }
                ]
            }
        }
    )




class ACERetrieveRequest(BaseModel):
    """ACE Retrieve request schema (retrieves all memories)."""

    user_id: Optional[str] = Field(default=None, description="Optional user identifier")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "user_id": "alice"
            }
        }
    )




class ACERetrievedMemory(BaseModel):
    """ACE Retrieved memory item."""

    id: str = Field(description="Memory identifier")
    section: str = Field(description="Memory section")
    content: str = Field(description="Memory content")
    helpful: int = Field(description="Helpful count")
    harmful: int = Field(description="Harmful count")
    neutral: int = Field(description="Neutral count")


class ACERetrieveResponse(BaseModel):
    """ACE Retrieve response schema."""

    status: str = Field(description="Operation status")
    memory_string: str = Field(description="Formatted memory string")
    retrieved_memory: List[ACERetrievedMemory] = Field(description="List of retrieved memories")
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "success",
                "memory_string": "Section: python\nContent: Use lru_cache...",
                "retrieved_memory": [
                    {
                        "id": "mem_001",
                        "section": "python",
                        "content": "Use lru_cache for memoization",
                        "helpful": 0,
                        "harmful": 0,
                        "neutral": 0,
                    }
                ],
            }
        }
    )


# ============================================================================
# ReasoningBank Schemas
# ============================================================================


class ReasoningBankMemoryItem(BaseModel):
    """ReasoningBank memory item."""

    title: str = Field(description="Memory title")
    description: str = Field(description="Memory description")
    content: str = Field(description="Memory content")


class ReasoningBankMemory(BaseMemory):
    """ReasoningBank Memory schema."""

    query: str = Field(description="Query used as embedding index")
    memory: List[ReasoningBankMemoryItem] = Field(description="List of memory items")
    label: Optional[bool] = Field(default=None, description="Memory label")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "query": "How to implement caching?",
                "memory": [
                    {
                        "title": "Python Caching",
                        "description": "Memoization technique",
                        "content": "Use functools.lru_cache decorator",
                    }
                ],
                "label": True,
            }
        }
    )


    def to_vector_node(self) -> VectorNode:
        """Convert to vector node.

        Returns:
            VectorNode with memory data in metadata
        """
        # Generate unique ID based on title and content
        combined = f"{self.query}|{self.memory[0].title}" if self.memory else self.query
        content_hash = hashlib.md5(combined.encode()).hexdigest()
        node_id = f"reasoning_bank_{self.workspace_id}_{content_hash}"

        # Combine title, description and content for embedding
        embedding_content = self.query

        now_iso = datetime.now(timezone.utc).isoformat()
        memory_text = "\n".join(
            f"{item.title}: {item.content}" for item in self.memory if hasattr(item, "content")
        ) or self.query

        # Store all fields in metadata
        metadata = {
            "type": "exp_memory",
            "query": self.query,
            "memory": self.memory,
            "label": self.label,
            "workspace_id": self.workspace_id,
            "memory_text": memory_text,
            "created_at": now_iso,
            "updated_at": now_iso,
        }

        return VectorNode(
            id=node_id,
            content=embedding_content,
            metadata=metadata,
        )

    @classmethod
    def from_vector_node(cls, node: VectorNode) -> "ReasoningBankMemory":
        """Convert from vector node.

        Args:
            node: VectorNode from storage

        Returns:
            ReasoningBankMemory instance
        """
        metadata = node.metadata
        target = deserialization_target_algorithm(cls)

        if use_cross_algorithm_fallback(node, target):
            dicts = reasoning_bank_item_dicts_from_metadata(metadata, node)
            memory_items = []
            for d in dicts:
                try:
                    memory_items.append(ReasoningBankMemoryItem.model_validate(d))
                except Exception as exc:
                    logger.warning("Failed to validate ReasoningBankMemoryItem, skipping: %s", exc)
                    continue
            item_dicts = [
                {"title": m.title, "description": m.description, "content": m.content}
                for m in memory_items
            ]
            query = reasoning_bank_query_from_metadata(metadata, node, item_dicts)
        else:
            raw_memory = metadata.get("memory")
            if isinstance(raw_memory, str):
                try:
                    raw_memory = json.loads(raw_memory)
                except json.JSONDecodeError:
                    raw_memory = []
            memory_items: List[ReasoningBankMemoryItem] = []
            if isinstance(raw_memory, list):
                for item in raw_memory:
                    if hasattr(item, "model_dump"):
                        try:
                            item = item.model_dump()
                        except Exception as exc:
                            logger.warning("Failed to serialize ReasoningBankMemoryItem, skipping: %s", exc)
                            continue
                    if isinstance(item, dict):
                        try:
                            memory_items.append(ReasoningBankMemoryItem.model_validate(item))
                        except Exception as exc:
                            logger.warning("Failed to validate ReasoningBankMemoryItem, skipping: %s", exc)
                            continue
            query = str(metadata.get("query") or "").strip()

        if not str(query).strip() and memory_items:
            query = memory_items[0].title

        label: Optional[bool] = metadata.get("label")
        if label is None:
            is_correct_str = metadata.get("is_correct")
            if is_correct_str is not None and is_correct_str != "none":
                label = str(is_correct_str).lower() == "true"
            elif metadata.get("helpful") is not None or metadata.get("harmful") is not None:
                label = int(metadata.get("helpful", 0)) > int(metadata.get("harmful", 0))

        return cls(
            query=str(query),
            memory=memory_items,
            label=label,
            workspace_id=metadata.get("workspace_id", "default"),
        )

    def __repr__(self) -> str:
        """String representation."""
        return (
            f"ReasoningBankMemory(query='{self.query}', memory='{self.memory}', "
            f"label='{self.label}', workspace_id='{self.workspace_id}')"
        )


class ReasoningBankSummarizeRequest(BaseModel):
    """ReasoningBank Summarize request schema."""

    matts: str = Field(
        default="none",
        description="MaTTS mode: none, parallel, or sequential"
    )
    query: str = Field(description="Query for summarization")
    trajectories: List[str] = Field(description="List of trajectory strings")
    label: Optional[List[bool]] = Field(default=None, description="Optional labels for trajectories")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "matts": "parallel",
                "query": "How to handle errors?",
                "trajectories": ["Traj1", "Traj2", "Traj3"],
                "label": [True, False, True],
            }
        }
    )



class ReasoningBankSummarizeResponse(BaseModel):
    """ReasoningBank Summarize response schema."""

    status: str = Field(description="Operation status")
    memory: List[ReasoningBankMemory] = Field(description="Created or updated memory")
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "success",
                "memory": [
                    {
                    "query": "How to handle errors?",
                    "memory": [
                        {
                            "title": "Error Handling",
                            "description": "Best practices",
                            "content": "Use specific exception types",
                        }
                    ],
                    "label": True,
                    }
                ]
            }
        }
    )


class ReasoningBankRetrieveRequest(BaseModel):
    """ReasoningBank Retrieve request schema."""

    query: str = Field(description="Query to retrieve memories")
    topk: int = Field(default=5, description="Number of top memories to retrieve")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "query": "How to implement caching?",
                "topk": 3,
            }
        }
    )



class ReasoningBankRetrievedMemory(BaseModel):
    """ReasoningBank retrieved memory item."""

    title: str = Field(description="Memory title")
    description: str = Field(description="Memory description")
    content: str = Field(description="Memory content")


class ReasoningBankRetrieveResponse(BaseModel):
    """ReasoningBank Retrieve response schema."""

    status: str = Field(description="Operation status")
    memory_string: str = Field(description="Formatted memory string")
    retrieved_memory: List[ReasoningBankRetrievedMemory] = Field(
        description="List of retrieved memories"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "success",
                "memory_string": "Title: Python Caching\nDescription: Memoization...",
                "retrieved_memory": [
                    {
                        "title": "Python Caching",
                        "description": "Memoization technique",
                        "content": "Use functools.lru_cache decorator",
                    }
                ],
            }
        }
    )


# ============================================================================
# ReMe Schemas
# ============================================================================

class ReMeMemoryMetadata(BaseModel):
    """ReMe Memory metadata."""

    tags: List[str] = Field(default_factory=list, description="Memory tags")
    step_type: Optional[str] = Field(default=None, description="Step type")
    tools_used: List[str] = Field(default_factory=list, description="Tools used")
    confidence: Optional[float] = Field(default=None, description="Confidence score")
    freq: int = Field(default=0, description="Frequency of use")
    utility: Optional[float] = Field(default=None, description="Utility score")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "tags": ["python", "caching"],
                "step_type": "implementation",
                "tools_used": ["functools"],
                "confidence": 0.95,
                "freq": 5,
                "utility": 4,
            }
        }
    )


class ReMeMemory(BaseMemory):
    """ReMe Memory schema."""

    when_to_use: str = Field(description="Conditions for using this memory")
    content: str = Field(description="Memory content")
    score: float = Field(default=0.0, description="Memory score (0-1)")
    created_at: datetime = Field(description="Creation timestamp")
    updated_at: datetime = Field(description="Last update timestamp")
    metadata: ReMeMemoryMetadata = Field(
        default_factory=ReMeMemoryMetadata,
        description="Additional metadata"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "when_to_use": "When implementing caching in Python",
                "content": "Use functools.lru_cache decorator",
                "score": 0.8,
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "metadata": {
                    "tags": ["python", "caching"],
                    "step_type": "implementation",
                    "tools_used": ["functools"],
                    "confidence": 0.95,
                    "freq": 4,
                    "utility": 5,
                },
            }
        }
    )


    def to_vector_node(self) -> VectorNode:
        """Convert to vector node for storage.

        Returns:
            VectorNode with ReMe memory data in metadata
        """
        # Create unique node ID
        node_id = f"reme_{self.workspace_id}_{hashlib.md5(self.when_to_use.encode()).hexdigest()[:12]}"
        embedding_content = self.when_to_use

        # Store all fields in metadata
        metadata = {
            "type": "exp_memory",
            "when_to_use": self.when_to_use,
            "content": self.content,
            "score": self.score,
            "created_at": self.created_at.isoformat() if isinstance(self.created_at, datetime) else self.created_at,
            "updated_at": self.updated_at.isoformat() if isinstance(self.updated_at, datetime) else self.updated_at,
            "workspace_id": self.workspace_id,
            "metadata": {
                "tags": self.metadata.tags,
                "step_type": self.metadata.step_type,
                "tools_used": self.metadata.tools_used,
                "confidence": self.metadata.confidence,
                "freq": self.metadata.freq,
                "utility": self.metadata.utility,
            }
        }

        return VectorNode(
            id=node_id,
            content=embedding_content,
            metadata=metadata,
        )

    @classmethod
    def from_vector_node(cls, node: VectorNode) -> "ReMeMemory":
        """Convert from vector node.

        Args:
            node: VectorNode from storage

        Returns:
            ReMeMemory instance
        """
        metadata = node.metadata
        target = deserialization_target_algorithm(cls)

        # Reconstruct ReMeMemoryMetadata
        memory_metadata = ReMeMemoryMetadata(
            tags=metadata.get("metadata", {}).get("tags", []),
            step_type=metadata.get("metadata", {}).get("step_type", ""),
            tools_used=metadata.get("metadata", {}).get("tools_used", []),
            confidence=metadata.get("metadata", {}).get("confidence", 0.0),
            freq=metadata.get("metadata", {}).get("freq", 0),
            utility=metadata.get("metadata", {}).get("utility", 0),
        )

        if use_cross_algorithm_fallback(node, target):
            wt, body = reme_when_and_content_from_metadata(metadata, node)
        else:
            wt = str(metadata.get("when_to_use") or "").strip()
            body = str(metadata.get("content") or "").strip()
            if not wt:
                wt = str(node.content or "").strip()
            if not body:
                body = str(metadata.get("content", "") or "").strip()

        return cls(
            workspace_id=metadata.get("workspace_id", ""),
            when_to_use=wt or metadata.get("when_to_use") or node.content or "",
            content=body if body else str(metadata.get("content", "")),
            score=metadata.get("score", 0.0),
            created_at=metadata.get("created_at", datetime.now(timezone.utc).isoformat()),
            updated_at=metadata.get("updated_at", datetime.now(timezone.utc).isoformat()),
            metadata=memory_metadata,
        )


class ReMeSummarizeRequest(BaseModel):
    """ReMe Summarize request schema."""

    matts: str = Field(
        default="none",
        description="MaTTS mode: none, parallel, or sequential"
    )
    trajectories: List[str] = Field(description="List of trajectory strings")
    score: Optional[List[float]] = Field(
        default=None,
        description="Optional scores for trajectories (0-1)"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "matts": "parallel",
                "trajectories": ["Traj1", "Traj2", "Traj3"],
                "score": [0.85, 0.92, 1],
            }
        }
    )



class ReMeSummarizeResponse(BaseModel):
    """ReMe Summarize response schema."""

    status: str = Field(description="Operation status")
    memory: List[ReMeMemory] = Field(description="Created or updated memory")
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "success",
                "memory": [
                    {
                    "when_to_use": "When implementing caching in Python",
                    "content": "Use functools.lru_cache decorator",
                    "score": 0.92,
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-01T00:00:00Z",
                    "metadata": {
                        "tags": ["python", "caching"],
                        "step_type": "implementation",
                        "tools_used": ["functools"],
                        "confidence": 0.95,
                        "freq": 0,
                        "utility": 0,
                        },
                    }
                ]
            }
        }
    )



class ReMeRetrieveRequest(BaseModel):
    """ReMe Retrieve request schema."""

    query: str = Field(description="Query to retrieve memories")
    topk_retrieval: int = Field(default=10, description="Number of memories to retrieve initially")
    topk_rerank: int = Field(default=5, description="Number of memories after reranking")
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "query": "How to implement caching?",
                "topk_retrieval": 10,
                "topk_rerank": 5,
            }
        }
    )



class ReMeRetrievedMemory(BaseModel):
    """ReMe retrieved memory item."""

    when_to_use: str = Field(description="Conditions for using this memory")
    content: str = Field(description="Memory content")


class ReMeRetrieveResponse(BaseModel):
    """ReMe Retrieve response schema."""

    status: str = Field(description="Operation status")
    memory_string: str = Field(description="Formatted memory string")
    retrieved_memory: List[ReMeRetrievedMemory] = Field(
        description="List of retrieved memories"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "success",
                "memory_string": "When to use: When implementing caching...",
                "retrieved_memory": [
                    {
                        "when_to_use": "When implementing caching in Python",
                        "content": "Use functools.lru_cache decorator",
                    }
                ],
            }
        }
    )

# ============================================================================
# Cognition Schemas
# ============================================================================


class CognitionMemory(BaseMemory):
    """Cognition Memory schema."""

    id: str = Field(description="Memory identifier")
    query: str = Field(description="Original user query")
    description: str = Field(description="Summary of what this memory is about")
    experience: List[str] = Field(default_factory=list, description="List of actionable insights")
    is_correct: Optional[bool] = Field(default=None, description="Whether the trajectory was successful")
    attributes: Dict[str, Any] = Field(default_factory=dict, description="Dynamic schema classification tags")
    created_at: datetime = Field(description="Creation timestamp")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "cog_001",
                "query": "How to connect to database?",
                "description": "Steps to establish PostgreSQL connection",
                "experience": ["Use psycopg2", "Always close cursor"],
                "is_correct": True,
                "attributes": {"domain": "database", "intent": "connection", "other": "PostgreSQL"},
                "created_at": "2024-01-01T00:00:00Z"
            }
        }
    )

    def to_vector_node(self) -> VectorNode:
        """Convert to vector node for storage."""
        node_id = f"cognition_{self.workspace_id}_{self.id}"
        
        # Content used for embedding
        embedding_content = f"Query: {self.query}\nDescription: {self.description}"
        
        memory_text = "\n".join(self.experience) if self.experience else self.description
        metadata = {
            "type": "exp_memory",
            "id": self.id,
            "workspace_id": self.workspace_id,
            "query": self.query,
            "description": self.description,
            "is_correct": str(self.is_correct) if self.is_correct is not None else "none",
            "experience_json": json.dumps(self.experience, ensure_ascii=False),
            "attributes_json": json.dumps(self.attributes, ensure_ascii=False),
            "memory_text": memory_text,
            "created_at": self.created_at.isoformat() if isinstance(self.created_at, datetime) else self.created_at
        }
        
        return VectorNode(
            id=node_id,
            content=embedding_content,
            metadata=metadata,
        )

    @classmethod
    def from_vector_node(cls, node: VectorNode) -> "CognitionMemory":
        """Convert from vector node."""
        metadata = node.metadata

        is_correct_str = metadata.get("is_correct", "none")
        is_correct = None if is_correct_str == "none" else (is_correct_str.lower() == "true")

        target = deserialization_target_algorithm(cls)
        if use_cross_algorithm_fallback(node, target):
            query, description, experience, attributes = cognition_fields_from_metadata(
                metadata, node
            )
        else:
            try:
                experience = json.loads(metadata.get("experience_json", "[]"))
                if not isinstance(experience, list):
                    experience = []
                experience = [str(i) for i in experience if i is not None and str(i).strip()]
            except (json.JSONDecodeError, TypeError):
                experience = []
            try:
                attributes = json.loads(metadata.get("attributes_json", "{}"))
                if not isinstance(attributes, dict):
                    attributes = {}
            except (json.JSONDecodeError, TypeError):
                attributes = {}
            query = str(metadata.get("query") or "").strip()
            description = str(metadata.get("description") or "").strip()
            if not experience:
                fallback = metadata.get("memory_text")
                if fallback:
                    experience = [str(fallback)]
            if not description:
                description = query
            if not query:
                query = description

        return cls(
            workspace_id=metadata.get("workspace_id", "default"),
            id=metadata.get("id", ""),
            query=query,
            description=description,
            experience=experience,
            is_correct=is_correct,
            attributes=attributes,
            created_at=metadata.get("created_at") or datetime.now(timezone.utc).isoformat()
        )


class CognitionSummarizeRequest(BaseModel):
    matts: str = Field(default="none")
    query: str = Field(description="Query for summarization")
    trajectories: List[str] = Field(description="List of trajectory strings")
    is_correct: Optional[bool] = Field(default=None)


class CognitionSummarizeResponse(BaseModel):
    status: str = Field(description="Operation status")
    memory: List[CognitionMemory] = Field(description="Created memory")


class CognitionRetrieveRequest(BaseModel):
    query: str = Field(description="Query to retrieve memories")
    topk: int = Field(default=5)


class CognitionRetrievedMemory(BaseModel):
    id: str = Field(description="Memory identifier")
    query: str = Field(description="Original query")
    description: str = Field(description="Memory description")
    experience: List[str] = Field(description="Actionable insights")


class CognitionRetrieveResponse(BaseModel):
    status: str = Field(description="Operation status")
    memory_string: str = Field(description="Formatted memory string")
    retrieved_memory: List[CognitionRetrievedMemory] = Field(description="List of retrieved memories")


# ============================================================================
# Ours (Custom) Schemas
# ============================================================================
# Note: Same structure as ReMe with different trajectory generation scaling method


class OursMemory(ReMeMemory):
    """Custom memory schema (same structure as ReMe)."""
    pass


class OursSummarizeRequest(ReMeSummarizeRequest):
    """Custom Summarize request schema (same structure as ReMe)."""
    pass


class OursSummarizeResponse(ReMeSummarizeResponse):
    """Custom Summarize response schema (same structure as ReMe)."""
    pass


class OursRetrieveRequest(ReMeRetrieveRequest):
    """Custom Retrieve request schema (same structure as ReMe)."""
    pass


class OursRetrievedMemory(ReMeRetrievedMemory):
    """Custom retrieved memory item (same structure as ReMe)."""
    pass


class OursRetrieveResponse(ReMeRetrieveResponse):
    """Custom Retrieve response schema (same structure as ReMe)."""
    pass


# ============================================================================
# Generic Schemas (Multi-Algorithm Support)
# ============================================================================

class SummarizeResponse(BaseModel):
    """Generic summarize response that works with any algorithm.

    The memory field can contain a list of ACEMemory, ReasoningBankMemory, or ReMeMemory
    depending on which algorithm is used.
    """

    status: str = Field(description="Operation status")
    memory: Union[List[ACEMemory], List[ReasoningBankMemory], List[ReMeMemory], List[CognitionMemory]] = Field(
        description="List of created or updated memories (algorithm-specific)"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "success",
                "memory": [
                    {
                        "query": "How to implement caching?",
                        "memory": [
                            {
                                "title": "Python Caching",
                                "description": "Memoization technique",
                                "content": "Use functools.lru_cache decorator",
                            }
                        ],
                        "label": True,
                    }
                ]
            }
        }
    )



class RetrieveResponse(BaseModel):
    """Generic retrieve response that works with any algorithm.

    The retrieved_memory field can contain ACERetrievedMemory, ReasoningBankRetrievedMemory,
    or ReMeRetrievedMemory depending on which algorithm is used.
    """

    status: str = Field(description="Operation status")
    memory_string: str = Field(description="Formatted memory string")
    retrieved_memory: Union[
        List[ACERetrievedMemory],
        List[ReasoningBankRetrievedMemory],
        List[ReMeRetrievedMemory],
        List[CognitionRetrievedMemory]
    ] = Field(description="List of retrieved memories (algorithm-specific)")
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "success",
                "memory_string": "Title: Python Caching\\nDescription: Memoization...",
                "retrieved_memory": [
                    {
                        "title": "Python Caching",
                        "description": "Memoization technique",
                        "content": "Use functools.lru_cache decorator",
                    }
                ],
            }
        }
    )


