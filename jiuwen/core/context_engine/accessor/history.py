#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from pydantic import BaseModel, Field
from typing import List, Dict, Optional, Union

from jiuwen.core.context_engine.base import ContextOwner
from jiuwen.core.utils.llm.messages import BaseMessage

DEFAULT_HISTORY_LENGTH = 100


class ConversationMessage(BaseModel):
    order_id: int
    message: BaseMessage
    owner: List[ContextOwner] = Field(default=[])
    tags: Dict[str, str] = Field(default={})

    def is_owner(self, target_owner: ContextOwner) -> bool:
        if not target_owner:
            return True
        for owner in self.owner:
            if owner in target_owner:
                return True
        return False


class ConversationHistory:
    def __init__(self):
        self.__history = []
        self.__expired_history = []
        self.__compressed_history = []
        self.__conversation_order_id = 0
        self.__history_capacity: int = DEFAULT_HISTORY_LENGTH

    def __len__(self):
        return len(self.__history)

    def add_message(self, message: BaseMessage,
                    owner: Optional[List[ContextOwner]] = None,
                    tags: Optional[Dict[str, str]] = None):
        self.__history.append(ConversationMessage(
            order_id=self.__conversation_order_id,
            message=message,
            owner=owner or [],
            tags=tags or {}
        ))
        self.__conversation_order_id += 1

    def get_messages(self,
                     num: int,
                     owner: Optional[ContextOwner] = None,
                     tags: Optional[Dict[str, str]] = None) -> List[BaseMessage]:
        num = num if num > 0 else DEFAULT_HISTORY_LENGTH
        filtered_history = []
        for message in self.__history:
            if not message.is_owner(owner):
                continue
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
        return filtered_history[-1 * num:]

    def batch_add_messages(self,
                           messages: List[BaseMessage],
                           owner: Optional[List[ContextOwner]] = None,
                           tags: Optional[Dict[str, str]] = None
                           ):
        """batch add conversation messages"""
        for msg in messages:
            if isinstance(msg, ConversationMessage):
                self.__history.append(msg)
            elif isinstance(msg, BaseMessage):
                self.__history.append(ConversationMessage(
                    order_id=self.__conversation_order_id,
                    message=msg,
                    owner=owner or [],
                    tags=tags or {}
                ))

    def get_latest_message(self, role: str = None) -> Union[BaseMessage, None]:
        if len(self.__history) == 0:
            return None

        if role is None:
            return self.__history[-1].message

        for msg in reversed(self.__history):
            if msg.message.role == role:
                return msg.message

        return None