# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

# Core classes
from openjiuwen.core.foundation.llm.model import Model, init_model
from openjiuwen.core.foundation.llm.model_clients.base_model_client import BaseModelClient
from openjiuwen.core.foundation.llm.output_parsers.output_parser import BaseOutputParser
from openjiuwen.core.foundation.llm.reasoning import (
    get_provider_reasoning_rules,
    get_reasoning_capability,
    get_reasoning_capability_catalog,
)
from openjiuwen.core.foundation.llm.reasoning_profiles import ReasoningCapability

# Configuration
from openjiuwen.core.foundation.llm.schema.config import (
    LLMApiMode,
    LLMAuthMode,
    LLMExtensionsConfig,
    KVCacheExtensionConfig,
    ReasoningConfig,
    ModelRequestConfig,
    ModelClientConfig,
    ProviderType,
)
from openjiuwen.core.foundation.llm.schema.mode_info import BaseModelInfo, ModelConfig
# Messages
from openjiuwen.core.foundation.llm.schema.message import (
    OPENJIUWEN_MESSAGE_ORIGIN_EXTERNAL_USER,
    OPENJIUWEN_MESSAGE_ORIGIN_HARNESS_INTERNAL,
    OPENJIUWEN_MESSAGE_ORIGIN_METADATA,
    OPENJIUWEN_MESSAGE_PROVENANCE_METADATA,
    OPENJIUWEN_MESSAGE_SOURCE_KIND_METADATA,
    BaseMessage,
    AssistantMessage,
    UserMessage,
    SystemMessage,
    ToolMessage,
    UsageMetadata
)
from openjiuwen.core.foundation.llm.schema.message_chunk import (
    AssistantMessageChunk,
)

# Tools
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall

# Built-in implementations
from openjiuwen.core.foundation.llm.model_clients.anthropic_model_client import AnthropicModelClient
from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient
from openjiuwen.core.foundation.llm.output_parsers.json_output_parser import JsonOutputParser
from openjiuwen.core.foundation.llm.output_parsers.markdown_output_parser import MarkdownOutputParser


# ============ Public API exports ============

# Core classes
_CORE_CLASSES = [
    "Model",
    "init_model",
    "BaseModelClient",
    "BaseOutputParser",
]

# Configuration classes
_CONFIG_CLASSES = [
    "ModelRequestConfig",
    "ModelClientConfig",
    "ProviderType",
    "LLMApiMode",
    "LLMAuthMode",
    "LLMExtensionsConfig",
    "KVCacheExtensionConfig",
    "ReasoningConfig",
    "BaseModelInfo",
    "ModelConfig"
]

_REASONING_APIS = [
    "ReasoningCapability",
    "get_provider_reasoning_rules",
    "get_reasoning_capability",
    "get_reasoning_capability_catalog",
]

# Message classes
_MESSAGE_CLASSES = [
    "BaseMessage",
    "AssistantMessage",
    "UserMessage",
    "SystemMessage",
    "ToolMessage",
    "UsageMetadata",
]

_MESSAGE_METADATA_CONSTANTS = [
    "OPENJIUWEN_MESSAGE_ORIGIN_EXTERNAL_USER",
    "OPENJIUWEN_MESSAGE_ORIGIN_HARNESS_INTERNAL",
    "OPENJIUWEN_MESSAGE_ORIGIN_METADATA",
    "OPENJIUWEN_MESSAGE_PROVENANCE_METADATA",
    "OPENJIUWEN_MESSAGE_SOURCE_KIND_METADATA",
]

# Streaming message classes
_MESSAGE_CHUNK_CLASSES = [
    "AssistantMessageChunk",
]

# Tool-related classes
_TOOL_CLASSES = [
    "ToolCall",
]

# Built-in ModelClient implementations
_PREBUILT_MODEL_CLIENTS = [
    "AnthropicModelClient",
    "OpenAIModelClient",
]

# Built-in OutputParser implementations
_PREBUILT_OUTPUT_PARSERS = [
    "JsonOutputParser",
    "MarkdownOutputParser",
]

# Combine all public APIs
__all__ = (
    _CORE_CLASSES
    + _CONFIG_CLASSES
    + _REASONING_APIS
    + _MESSAGE_CLASSES
    + _MESSAGE_METADATA_CONSTANTS
    + _MESSAGE_CHUNK_CLASSES
    + _TOOL_CLASSES
    + _PREBUILT_MODEL_CLIENTS
    + _PREBUILT_OUTPUT_PARSERS
)
