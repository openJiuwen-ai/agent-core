#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from pydantic import Field

from openjiuwen.agent.config.base import AgentConfig, LLMCallConfig


class ChatAgentConfig(AgentConfig):
    model: LLMCallConfig = Field(default=LLMCallConfig())
