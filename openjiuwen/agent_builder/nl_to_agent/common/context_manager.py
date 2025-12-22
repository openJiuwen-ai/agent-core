# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List


@dataclass
class DialogueMessage:
    content: str
    role: str
    timestamp: datetime


class DialogueHistoryCache:
    def __init__(self, max_history_size: int = 50) -> None:
        self._history = []
        self.max_history_size = max_history_size

    def get_history(self) -> List[DialogueMessage]:
        return self._history

    def get_messages(self, num: int) -> List[Dict[str, Any]]:
        num = num if num > 0 else self.max_history_size
        messages = self._history[-num:] if len(self._history) > num else self._history
        formatted_messages = []
        for msg in messages:
            msg_dict = {'content': msg.content, 'role': msg.role}
            formatted_messages.append(msg_dict)
        return formatted_messages

    def add_message(self, message: DialogueMessage) -> None:
        self._history.append(message)
        if len(self._history) > self.max_history_size:
            self._history.pop(0)

    def clear(self):
        self._history.clear()


class ContextManager:
    def __init__(self) -> None:
        self._dialogue_history = DialogueHistoryCache()

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
