#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import List, Dict, Optional, Any

from pydantic import BaseModel, Field

from openjiuwen.agent.common.enum import ControllerType
from openjiuwen.agent.common.schema import PluginSchema
from openjiuwen.agent.config.base import AgentConfig
from openjiuwen.core.component.common.configs.model_config import ModelConfig
from openjiuwen.core.memory.config.config import MemoryConfig


class ConstrainConfig(BaseModel):
    reserved_max_chat_rounds: int = Field(default=10, gt=0)
    max_iteration: int = Field(default=5, gt=0)


class IntentDetectionConfig(BaseModel):
    intent_detection_template: List[Dict] = Field(default_factory=list)
    default_class: str = Field(default="分类1")
    enable_input: bool = Field(default=True)
    enable_history: bool = Field(default=False)
    chat_history_max_turn: int = Field(default=5)
    category_list: List[str] = Field(default_factory=list)
    user_prompt: str = Field(default="")
    example_content: List[str] = Field(default_factory=list)


class ReActAgentConfig(AgentConfig):
    controller_type: ControllerType = Field(default=ControllerType.ReActController)
    prompt_template_name: str = Field(default="react_system_prompt")
    prompt_template: List[Dict] = Field(default_factory=list)
    constrain: ConstrainConfig = Field(default=ConstrainConfig())
    plugins: List[PluginSchema] = Field(default_factory=list)
    memory_config: MemoryConfig = Field(default=MemoryConfig())
