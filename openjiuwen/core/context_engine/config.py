#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from pydantic import BaseModel, Field


DEFAULT_CONVERSATION_HISTORY_LENGTH: int = 100


class ContextEngineConfig(BaseModel):
    conversation_history_length: int = Field(default=DEFAULT_CONVERSATION_HISTORY_LENGTH, ge=0)
