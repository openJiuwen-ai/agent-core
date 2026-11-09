# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Forward guardrail backends from the detection library."""

from agent_shield.detection import (
    APIModelBackend,
    APIModelBackendConfig,
    GuardrailBackend,
    LLMPromptInjectionBackend,
    LLMPromptInjectionBackendConfig,
    LocalModelBackend,
    LocalModelBackendConfig,
    RuleBasedBackendConfig,
    RuleBasedPromptInjectionBackend,
)

__all__ = [
    "GuardrailBackend",
    "RuleBasedPromptInjectionBackend",
    "RuleBasedBackendConfig",
    "LLMPromptInjectionBackend",
    "LLMPromptInjectionBackendConfig",
    "APIModelBackend",
    "APIModelBackendConfig",
    "LocalModelBackend",
    "LocalModelBackendConfig",
]
