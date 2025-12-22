# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import List, Optional, Union

from openjiuwen.core.foundation.llm import BaseMessage


class ContextMessageBuffer:
    def __init__(self, history_messages: List[BaseMessage]):
        self._context_messages: List[BaseMessage] = history_messages[:]
        self._history_messages_size = len(history_messages)

    def size(self) -> int:
        return len(self._context_messages)

    def add_back(self, messages: Union[BaseMessage, List[BaseMessage]]):
        if isinstance(messages, BaseMessage):
            self._context_messages.append(messages)
            return
        for msg in messages:
            self._context_messages.append(msg)

    def get_back(self, size: Optional[int] = None, with_history: bool = True) -> List[BaseMessage]:
        context_messages = self._context_messages[:]
        if size is None:
            return context_messages \
                if with_history \
                else context_messages[self._history_messages_size:]
        total_size = len(context_messages)
        context_size = total_size - self._history_messages_size
        size = min(size, context_size) if not with_history else min(size, total_size)
        return context_messages[total_size - size:]

    def pop_back(self, size: Optional[int] = None, with_history: bool = True) -> List[BaseMessage]:
        popped_messages = self.get_back(size, with_history)
        popped_size = len(popped_messages)
        self._context_messages = self._context_messages[:self.size() - popped_size]
        return popped_messages

    def set_messages(self, messages: List[BaseMessage], with_history: bool = True):
        if with_history:
            self._context_messages = messages
            self._history_messages_size = 0
            return
        history_messages = self._context_messages[:self._history_messages_size]
        self._context_messages = history_messages + messages
