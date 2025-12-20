#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Config of Agent"""
from typing import List, Optional, Dict

from pydantic import BaseModel, Field

from openjiuwen.agent.common.schema import WorkflowSchema, PluginSchema
from openjiuwen.agent.common.enum import ControllerType
from openjiuwen.core.component.common.configs.model_config import ModelConfig


class AgentConfig(BaseModel):
    id: str = Field(default="")
    version: str = Field(default="")
    description: str = Field(default="")
    controller_type: ControllerType = Field(default=ControllerType.Undefined)
    workflows: List[WorkflowSchema] = Field(default_factory=list)
    model: Optional[ModelConfig] = Field(default=None)
    tools: List[str] = Field(default_factory=list)


class LLMCallConfig(BaseModel):
    model: Optional[ModelConfig] = Field(default=None)
    system_prompt: List[Dict] = Field(default_factory=list)
    user_prompt: List[Dict] = Field(default_factory=list)
    freeze_system_prompt: bool = Field(default=False)
    freeze_user_prompt: bool = Field(default=True)
