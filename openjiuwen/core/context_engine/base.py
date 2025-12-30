# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from abc import ABC, abstractmethod
from typing import Union, Dict, Optional, List

from pydantic import BaseModel, Field

from openjiuwen.core.utils.llm.messages import BaseMessage


class ContextOwner(BaseModel):
    agent_id: str = Field(default="")
    workflow_id: str = Field(default="")
    session_id: str = Field(default="")
    app_id: str = Field(default="")
    user_id: str = Field(default="")

    def __hash__(self) -> int:
        return hash((self.agent_id, self.workflow_id, self.session_id, self.app_id, self.user_id))

    def __eq__(self, other: "ContextOwner") -> bool:
        if isinstance(other, ContextOwner):
            return self.agent_id == other.agent_id and self.workflow_id == other.workflow_id \
                and self.session_id == other.session_id and self.app_id == other.app_id
        return False

    def __contains__(self, other: "ContextOwner"):
        if isinstance(other, ContextOwner):
            return (not self.session_id or self.session_id == other.session_id) \
                    and (not self.agent_id or self.agent_id == other.agent_id) \
                    and (not self.workflow_id or self.workflow_id == other.workflow_id)
        return False


class Context(ABC):
    @abstractmethod
    def batch_add_messages(self,
                           messages: List[BaseMessage],
                           tags: Optional[Dict[str, str]] = None):
        pass

    @abstractmethod
    def add_message(self,
                    message: BaseMessage,
                    tags: Optional[Dict[str, str]] = None):
        pass

    @abstractmethod
    def get_messages(self,
                    num: int = -1,
                    tags: Optional[Dict[str, str]] = None) -> List[BaseMessage]:
        pass

    @abstractmethod
    def get_latest_message(self,
                           role: str = None) -> Optional[BaseMessage]:
        pass

