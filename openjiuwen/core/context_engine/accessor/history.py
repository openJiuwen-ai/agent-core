#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import List, Dict, Optional, Union

from pydantic import BaseModel, Field

from openjiuwen.core.context_engine.config import ContextEngineConfig
from openjiuwen.core.utils.llm.messages import BaseMessage

DEFAULT_HISTORY_LENGTH = 100


class ConversationMessage(BaseModel):
    order_id: int
    message: BaseMessage
    tags: Dict[str, str] = Field(default={})


class ConversationHistory:
    def __init__(self, config: ContextEngineConfig):
        self._history = []
        self._conversation_order_id = 0
        self._history_queue_length: int = config.conversation_history_length
        self._history_queue_rebuild_length = self._history_queue_length * 2

    def __len__(self):
        return len(self._history)

    def add_message(self, message: BaseMessage,
                    tags: Optional[Dict[str, str]] = None):
        self._history.append(ConversationMessage(
            order_id=self._conversation_order_id,
            message=message,
            tags=tags or {}
        ))
        if len(self._history) >= self._history_queue_rebuild_length:
            self._rebuild_history()
        self._conversation_order_id += 1

    def get_messages(self,
                     num: int,
                     tags: Optional[Dict[str, str]] = None) -> List[BaseMessage]:
        num = num if num >= 0 else DEFAULT_HISTORY_LENGTH
        filtered_history = []
        for i in range(len(self._history) - 1, -1, -1):
            if len(filtered_history) >= num:
                break
            message = self._history[i]
            matched = True
            if tags:
                for key, value in tags.items():
                    msg_tag = message.tags.get(key)
                    if msg_tag != value:
                        matched = False
                        break
            if not matched:
                continue
            filtered_history.append(message.message)
        filtered_history.reverse()
        return filtered_history

    def batch_add_messages(self,
                           messages: List[BaseMessage],
                           tags: Optional[Dict[str, str]] = None
                           ):
        """batch add conversation messages"""
        for msg in messages:
            self.add_message(msg, tags)

    def get_latest_message(self, role: str = None) -> Union[BaseMessage, None]:
        if len(self._history) == 0:
            return None

        if role is None:
            return self._history[-1].message

        for msg in reversed(self._history):
            if msg.message.role == role:
                return msg.message

        return None

    def _rebuild_history(self):
        msg_to_reserve = max(self._history_queue_rebuild_length - self._history_queue_length, 1)
        self._history = self._history[msg_to_reserve:]
