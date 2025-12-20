#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Union, Dict, List, Optional, Any
from pydantic import BaseModel


class Parameters(BaseModel):
    type: str = "object"
    properties: Dict[str, Any] = {}
    required: List[str]


class Function(BaseModel):
    name: str = ''
    description: str = ''
    parameters: Optional[Parameters] = None


class ToolInfo(BaseModel):
    type: str = "function"
    function: Function


class FunctionInfo(BaseModel):
    name: str
    arguments: str


class ToolCall(BaseModel):
    args: Dict[str, Any] = {}
    id: Optional[str]
    index: int = 0
    type: str
    function: FunctionInfo


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


class HumanMessage(BaseMessage):
    role: str = "user"


class SystemMessage(BaseMessage):
    role: str = "system"


class ToolMessage(BaseMessage):
    role: str = "tool"
    tool_call_id: str
