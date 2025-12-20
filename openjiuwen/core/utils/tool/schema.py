#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Dict, Any, List, Optional

from pydantic import BaseModel, Field


class Parameters(BaseModel):
    type: str = Field(default="object")
    properties: Dict[str, Any] = Field(default_factory=dict)
    required: List[str] = Field(default_factory=list)


class ToolInfo(BaseModel):
    type: str = Field(default="function")
    name: str = Field(default="")
    description: str = Field(default="")
    parameters: Optional[Parameters] = Field(default=None)


class ToolCall(BaseModel):
    id: Optional[str]
    type: str
    name: str
    arguments: str
    index: Optional[int] = None