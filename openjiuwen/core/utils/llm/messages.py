#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Union, Dict, List, Optional, Any
from pydantic import BaseModel

from openjiuwen.core.utils.tool.schema import ToolCall


class BaseMessage(BaseModel):
    role: str
    content: Union[str, List[Union[str, Dict]]] = ""
    name: Optional[str] = None


class UsageMetadata(BaseModel):
    code: int = 0
    errmsg: str = ""
    prompt: str = ""
    task_id: str = ""
    model_name: str = ""
    finish_reason: str = ""
    total_latency: float = 0.
    model_stats: dict = {}
    first_token_time: str = ""
    request_start_time: str = ""


class AIMessage(BaseMessage):
    role: str = "assistant"
    tool_calls: Optional[List[ToolCall]] = None
    usage_metadata: Optional[UsageMetadata] = None
    raw_content: Optional[str] = None
    reason_content: Optional[str] = None

    def model_dump(self, **kwargs) -> dict[str, Any]:
        result = {
            "role": self.role,
            "content": self.content,
        }
        if self.name:
            result["name"] = self.name
        if self.tool_calls:
            tool_calls = []
            for call in self.tool_calls:
                tool_calls.append({
                    "id": call.id,
                    "type": call.type,
                    "function": {
                        "name": call.name,
                        "arguments": call.arguments
                    }
                })
            result["tool_calls"] = tool_calls
        if self.usage_metadata:
            result["usage_metadata"] = self.usage_metadata.model_dump(**kwargs)
        if self.raw_content:
            result["raw_content"] = self.raw_content
        if self.reason_content:
            result["reason_content"] = self.reason_content
        return result


class HumanMessage(BaseMessage):
    role: str = "user"


class SystemMessage(BaseMessage):
    role: str = "system"


class ToolMessage(BaseMessage):
    role: str = "tool"
    tool_call_id: str
