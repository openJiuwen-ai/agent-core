# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple, Union
from datetime import datetime, timezone
from pydantic import BaseModel, Field

from openjiuwen.core.foundation.llm.schema.message import BaseMessage


class MessageMetadata(BaseModel):
    message_id: str
    user_id: str
    scope_id: str
    session_id: str
    timestamp: datetime
    message_type: str


class BaseMessageStore(ABC):
    """
    Abstract base class for message storage, defining a unified message storage interface
    """

    @abstractmethod
    async def add_message(self, message_add: Dict[str, Any]) -> str:
        """
        Add a single message

        Args:
            message_add: Dict containing message data with keys:
                - message: BaseMessage object
                - user_id: str
                - scope_id: str
                - session_id: str
                - timestamp: Optional[datetime]

        Returns:
            str: The generated message ID
        """
        pass

    @abstractmethod
    async def add_messages(self, message_adds: List[Dict[str, Any]]) -> List[str]:
        """
        Batch add messages

        Args:
            message_adds: List of dicts containing message data

        Returns:
            List[str]: List of generated message IDs
        """
        pass

    @abstractmethod
    async def get_message_by_id(self, message_id: str) -> Tuple[BaseMessage, MessageMetadata]:
        """
        Get message by message ID

        Args:
            message_id: Message ID

        Returns:
            Tuple[BaseMessage, MessageMetadata]: (message object, message metadata) tuple

        Raises:
            BaseError: When message does not exist
        """
        pass

    @abstractmethod
    async def get_messages(
        self,
        message_filter: Dict[str, Any],
        limit: int = 10,
        order_by: str = "timestamp",
        order_direction: str = "desc",
    ) -> List[Tuple[BaseMessage, MessageMetadata]]:
        """
        Get messages by filter with pagination

        Args:
            message_filter: Dict with filter conditions, supported keys:
                - user_id: Optional[str]
                - scope_id: Optional[str]
                - session_id: Optional[str]
                - message_type: Optional[str]
                - start_time: Optional[datetime]
                - end_time: Optional[datetime]
            limit: Maximum number of results
            order_by: Field to sort by
            order_direction: Sort direction ("asc" or "desc")

        Returns:
            List[Tuple[BaseMessage, MessageMetadata]]: List of (message object, message metadata) tuples
        """
        pass

    @abstractmethod
    async def update_message(self, message_id: str, content: Union[str, List[Union[str, dict]]]) -> bool:
        """
        Update message content

        Args:
            message_id: Message ID
            content: New message content

        Returns:
            bool: Whether the update was successful
        """
        pass

    @abstractmethod
    async def delete_message_by_id(self, message_id: str) -> bool:
        """
        Delete a single message by message ID

        Args:
            message_id: Message ID

        Returns:
            bool: Whether the deletion was successful
        """
        pass

    @abstractmethod
    async def delete_messages(self, message_filter: Dict[str, Any]) -> int:
        """
        Delete messages matching the filter

        Args:
            message_filter: Dict with filter conditions

        Returns:
            int: Number of messages deleted
        """
        pass

    @abstractmethod
    async def count_messages(self, message_filter: Dict[str, Any]) -> int:
        """
        Count messages matching the filter

        Args:
            message_filter: Dict with filter conditions

        Returns:
            int: Number of messages
        """
        pass

    @abstractmethod
    async def get_schema_version(self) -> int | None:
        """
        Get the current schema version of the message store.

        Returns:
            int | None: Current version number or None if not set
        """
        pass

    @abstractmethod
    async def set_schema_version(self, version: int) -> None:
        """
        Set the schema version of the message store.

        Args:
            version: New version number to store
        """
        pass
