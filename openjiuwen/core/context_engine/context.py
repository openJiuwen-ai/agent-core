# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Optional, Dict, Union, List

from openjiuwen.core.context_engine.base import Context, ContextOwner
from openjiuwen.core.context_engine.accessor.accessor import ContextAccessor
from openjiuwen.core.utils.llm.messages import BaseMessage


class ContextImpl(Context):
    def __init__(self,
                 owner: ContextOwner,
                 accessor: ContextAccessor):
        self._owner = owner
        self._accessor: ContextAccessor = accessor

    def batch_add_messages(self,
                     messages: List[BaseMessage],
                     tags: Optional[Dict[str, str]] = None):
        history = self._accessor.history(self._owner)
        history.batch_add_messages(messages=messages, tags=tags)

    def add_message(self,
                    message: BaseMessage,
                    tags: Optional[Dict[str, str]] = None):
        history = self._accessor.history(self._owner)
        history.add_message(message, tags=tags)

    def get_messages(self,
                    num: int = -1,
                    tags: Optional[Dict[str, str]] = None) -> List[BaseMessage]:
        history = self._accessor.history(self._owner)
        messages = history.get_messages(num, tags=tags)
        return messages

    def get_latest_message(self, role: str = None) -> Optional[BaseMessage]:
        history = self._accessor.history(self._owner)
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
