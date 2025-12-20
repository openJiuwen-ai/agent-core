#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from pydantic import BaseModel, Field


class ContextEngineConfig(BaseModel):
    conversation_history_length: int = Field(default=20, ge=0)
