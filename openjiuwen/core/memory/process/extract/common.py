# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from dataclasses import dataclass
from typing import Any
from openjiuwen.core.foundation.llm import BaseMessage
from openjiuwen.core.foundation.llm import Model


@dataclass
class ExtractMemoryParams:
    user_id: str
    scope_id: str
    messages: list[BaseMessage]
    history_messages: list[BaseMessage]
    base_chat_model: Model


@dataclass
class MemoryOperationParams:
    user_id: str
    scope_id: str
    message_mem_id: str
    timestamp: str
    base_chat_model: Model
    semantic_store: Any
