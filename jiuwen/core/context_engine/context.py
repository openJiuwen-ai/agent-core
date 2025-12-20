#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Optional, Dict, Union, List

from jiuwen.core.context_engine.accessor.history import ConversationMessage
from jiuwen.core.context_engine.base import Context, ContextOwner
from jiuwen.core.context_engine.accessor.accessor import ContextAccessor
from jiuwen.core.utils.llm.messages import BaseMessage


class ContextImpl(Context):
    def __init__(self,
                 owner: ContextOwner,
                 accessor: ContextAccessor):
        self._owner = owner
        self._accessor: ContextAccessor = accessor

    def batch_add_messages(self,
                     messages:List[BaseMessage],
                     tags: Optional[Dict[str, str]] = None):
        history = self._accessor.history()
        history.batch_add_messages(messages=messages, owner=[self._owner], tags=tags)

    def add_message(self,
                    message: BaseMessage,
                    tags: Optional[Dict[str, str]] = None):
        history = self._accessor.history()
        history.add_message(message, owner=[self._owner], tags=tags)

    def get_messages(self,
                    num: int = -1,
                    tags: Optional[Dict[str, str]] = None) -> List[BaseMessage]:
        history = self._accessor.history()
        messages = history.get_messages(num, owner=self._owner, tags=tags)
        return messages

    def get_latest_message(self, role: str = None) -> Union[BaseMessage, None]:
        history = self._accessor.history()
        return history.get_latest_message(role=role)


class AgentContext(ContextImpl):
    def __init__(self,
                 owner: ContextOwner,
                 accessor: ContextAccessor):
        super().__init__(owner, accessor)


class WorkflowContext(ContextImpl):
    def __init__(self,
                 owner: ContextOwner,
                 accessor: ContextAccessor):
        super().__init__(owner, accessor)
