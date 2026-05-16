# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
Base memory index module for managing and searching memory documents.

This module provides abstract interfaces for memory storage and retrieval,
including the MemoryDoc data model and BaseMemoryIndex abstract base class.
"""
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class MemoryDoc(BaseModel):
    """
    A memory document representing a single piece of stored memory.

    This data model encapsulates the essential information for a memory entry,
    including its unique identifier, textual content, and type classification.
    """

    id: str = Field(default="", description="Unique identifier for the memory document")
    text: str = Field(default="", description="Text content of the memory")
    type: str = Field(default="", description="Type/category of the memory")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc).astimezone(),
        description="Timestamp of the memory entry",
    )
    fields: dict[str, Any] = Field(default_factory=dict, description="Additional extension fields.")


class BaseMemoryIndex(ABC):
    """
    Abstract base class for memory index implementations.

    This class defines the interface for memory storage and retrieval operations.
    Concrete implementations should provide specific backing stores (e.g., vector
    stores, databases) for persisting and searching memory documents.

    Memory documents are scoped by user_id and scope_id, allowing for
    multi-tenant and multi-scenario memory management.
    """

    @abstractmethod
    async def add_memories(self, user_id: str, scope_id: str, memories: list[MemoryDoc]):
        """
        Add new memory documents.

        Args:
            user_id: The user identifier to scope memories under
            scope_id: The scope identifier for grouping related memories
            memories: List of memory documents to add
        """
        pass

    @abstractmethod
    async def update_memories(self, user_id: str, scope_id: str, memories: list[MemoryDoc]):
        """
        Update memory documents.

        Args:
            user_id: The user identifier to scope memories under
            scope_id: The scope identifier for grouping related memories
            memories: List of memory documents to update
        """
        pass

    @abstractmethod
    async def delete_memories(self, user_id: str, scope_id: str, ids: list[str]):
        """
        Delete specific memory documents by their IDs.

        Args:
            user_id: The user identifier to scope memories under
            scope_id: The scope identifier for grouping related memories
            ids: List of memory document IDs to delete
        """
        pass

    @abstractmethod
    async def delete_by_user(self, user_id: str):
        """
        Delete all memory documents for a specific user across all scopes.

        Args:
            user_id: The user identifier whose memories should be deleted
        """
        pass

    @abstractmethod
    async def delete_by_scope(self, scope_id: str):
        """
        Delete all memory documents for a specific scope across all users.

        Args:
            scope_id: The scope identifier whose memories should be deleted
        """
        pass

    @abstractmethod
    async def delete_by_user_and_scope(self, user_id: str, scope_id: str):
        """
        Delete all memory documents for a specific user and scope combination.

        Args:
            user_id: The user identifier to scope memories under
            scope_id: The scope identifier for grouping related memories
        """
        pass

    @abstractmethod
    async def search(
        self,
        user_id: str,
        scope_id: str,
        query: str,
        mem_types: list[str] | None = None,
        top_k: int = 10
    ) -> list[tuple[MemoryDoc, float]]:
        """
        Search for memory documents matching a query.

        Performs semantic search over the stored memories and returns the
        most relevant results along with their relevance scores.

        Args:
            user_id: The user identifier to scope memories under
            scope_id: The scope identifier for grouping related memories
            query: The search query text
            mem_types: Filter of memory types to search. Can be:
                - A list with one or more specific memory types (e.g., ["user_profile"]).
                - An empty list [] to search all memory types.
            top_k: Maximum number of results to return (default: 10)

        Returns:
            A list of tuples, each containing a MemoryDoc and its relevance score.
            Scores are typically in the range [0, 1], where higher values indicate
            greater relevance.
        """
        pass

    @abstractmethod
    async def get_by_id(self, user_id: str, scope_id: str, mem_id: str) -> MemoryDoc | None:
        """
        Retrieve a specific memory document by mem_id.

        This method fetches the memory document that matches the mem_id.
        If no document with the specified ID exists, it returns None.

        Args:
            user_id: The user identifier to scope memories under.
            scope_id: The scope identifier for grouping related memories.
            mem_id: The unique identifier of the memory document to retrieve.

        Returns:
            MemoryDoc | None: The retrieved memory document if found, otherwise None.
        """
        pass

    async def list_memories(self, user_id: str, scope_id: str, offset: int,
                            limit: int, mem_types: list[str]) -> list[MemoryDoc]:
        """
        Retrieve a paginated list of memory documents for a specific user and scope.

        This method fetches memory documents scoped under the given user_id and scope_id,
        with support for pagination via offset and limit parameters.

        Args:
            user_id: The user identifier to scope memories under.
            scope_id: The scope identifier for grouping related memories.
            offset: The starting index of the documents to retrieve.
            limit: The maximum number of documents to return.
            mem_types: Filter of memory types to search. Can be:
                - A list with one or more specific memory types (e.g., ["user_profile"]).
                - An empty list [] to search all memory types.
                - If multiple mem_types are provided, output them in the order of mem_type.

        Returns:
            list[MemoryDoc]: A list of memory documents matching the criteria.
        """
        pass

    def get_schema_version(self) -> int:
        """
        Get the current schema version.

        Returns:
            int: Current schema version, returns 0 if not set.
        """
        pass

    def update_schema_version(self, version: int) -> None:
        """
        Update the schema version.

        Args:
            version: New schema version number
        """
        pass

    async def create_backup(self) -> str:
        """
        Create a backup of the current data.

        Returns:
            str: Backup identifier.
        """
        pass

    async def restore_backup(self, backup_id: str) -> None:
        """
        Restore data from a backup.

        Args:
            backup_id: Backup identifier
        """
        pass

    @abstractmethod
    async def cleanup_backup(self, backup_id: str) -> None:
        """
        Clean up a backup.

        Args:
            backup_id: Backup identifier
        """
        pass

    @abstractmethod
    async def list_user_scopes(self) -> list[tuple[str, str]]:
        """
        List all user-scope combinations in the index.

        Returns:
            list[tuple[str, str]]: List of (user_id, scope_id) tuples
        """
        pass
