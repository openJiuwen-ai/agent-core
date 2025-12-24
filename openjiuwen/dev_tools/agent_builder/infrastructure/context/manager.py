#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

from openjiuwen.dev_tools.agent_builder.common.constants import DEFAULT_MAX_HISTORY_SIZE
from openjiuwen.dev_tools.agent_builder.infrastructure.context.models import DialogueMessage


class DialogueHistoryCache:
    """In-memory cache to store dialogue history messages.

    Attributes:
        max_history_size (int): Maximum number of messages to store in history.
    """

    def __init__(self, max_history_size: int = DEFAULT_MAX_HISTORY_SIZE) -> None:
        """Initialize DialogueHistoryCache.

        Args:
            max_history_size (int): Maximum number of messages to store in history, defaults to 50.
        """
        self._history = []
        self.max_history_size = max_history_size

    def get_messages(self, num: int) -> List[Dict[str, Any]]:
        """Get the latest `num` messages from history.
        
        Args:
            num (int): Number of latest messages to retrieve. If num <= 0, retrieves all messages.

        Returns:
            List[Dict[str, Any]]: List of the latest `num` messages as dictionaries.
        """
        if num <= 0:
            num = self.max_history_size
        messages = self._history[-num:] if len(self._history) > num else self._history
        return [msg.to_dict() for msg in messages]

    def add_message(self, message: DialogueMessage) -> None:
        """Add a DialogueMessage to the dialogue history and automatically remove the oldest messages."""
        self._history.append(message)
        if len(self._history) > self.max_history_size:
            self._history.pop(0)

    def count(self) -> int:
        """Get the number of messages currently stored in history."""
        return len(self._history)

    def clear(self):
        """the entire dialogue."""
        self._history.clear()


class ContextManager:
    """Class for context management

    Manage conversation history, providing functions for adding, querying, and clearing messages.
    
    Example:
        ```python
        manager = ContextManager()
        manager.add_user_message("Hello")
        manager.add_assistant_message("Hello, what can help you?")
        history = manager.get_history()
        ```
    """

    def __init__(self, max_history_size: int = DEFAULT_MAX_HISTORY_SIZE) -> None:
        self._dialogue_history = DialogueHistoryCache(max_history_size)

    @property
    def max_history_size(self) -> int:
        """Get the maximum history size."""
        return self._dialogue_history.max_history_size

    def count_messages(self) -> int:
        """Get the number of messages currently stored in history."""
        return self._dialogue_history.count()

    def get_latest_k_messages(self, k: int) -> List[Dict[str, Any]]:
        return self._dialogue_history.get_messages(k)
    
    def get_history(self) -> List[Dict[str, Any]]:
        return self._dialogue_history.get_messages(-1)
    
    def add_message(self,
                    content: str,
                    role: str,
                    timestamp: Optional[datetime] = None) -> None:
        message = DialogueMessage(
            content=content,
            role=role,
            timestamp=timestamp or datetime.now(timezone.utc),
        )
        self._dialogue_history.add_message(message)
    
    def add_assistant_message(self, content: str) -> None:
        self.add_message(content=content, role='assistant')

    def add_user_message(self, content: str) -> None:
        self.add_message(content=content, role='user')

    def clear(self) -> None:
        self._dialogue_history.clear()
