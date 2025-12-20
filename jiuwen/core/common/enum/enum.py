#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from enum import Enum


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    FUNCTION = "function"


class WorkflowLLMResponseType(Enum):
    JSON = "json"
    MARKDOWN = "markdown"
    TEXT = "text"