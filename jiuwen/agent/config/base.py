#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Config of Agent"""
from typing import List

from pydantic import BaseModel, Field

from jiuwen.agent.common.schema import WorkflowSchema, PluginSchema
from jiuwen.agent.common.enum import ControllerType


class AgentConfig(BaseModel):
    id: str = Field(default="")
    version: str = Field(default="")
    description: str = Field(default="")
    controller_type: ControllerType = Field(default=ControllerType.Undefined)
    workflows: List[WorkflowSchema] = Field(default_factory=list)
