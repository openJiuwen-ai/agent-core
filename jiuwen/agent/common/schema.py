#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""schema"""
from typing import Dict, Any

from pydantic import BaseModel, Field


class WorkflowSchema(BaseModel):
    id: str = Field(default="")
    name: str = Field(default="")
    description: str = Field(default="")
    version: str = Field(default="")
    inputs: Dict[str, Any] = Field(default_factory=dict)


class PluginSchema(BaseModel):
    id: str = Field(default="")
    version: str = Field(default="")
    name: str = Field(default="")
    description: str = Field(default="")
    inputs: Dict[str, Any] = Field(default_factory=dict)


class McpSchema(BaseModel):
    id: str = Field(default="")
    name: str = Field(default="")
    description: str = Field(default="")
    inputs: Dict[str, Any] = Field(default_factory=dict)
