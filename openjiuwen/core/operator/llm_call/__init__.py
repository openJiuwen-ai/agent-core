# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
LLM invocation operators: LLMCallOperator with prompt tunables.

LLMCall: Backward compatible alias for LLMCallOperator.
"""

from openjiuwen.core.operator.llm_call.base import LLMCall, LLMCallOperator

__all__ = [
    "LLMCallOperator",
    "LLMCall",  # backward compatible alias
]
