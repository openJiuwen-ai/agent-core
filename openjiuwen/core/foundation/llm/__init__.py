# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

# Core classes
from openjiuwen.core.foundation.llm.model import Model as Model
from openjiuwen.core.foundation.llm.model import init_model as init_model
from openjiuwen.core.foundation.llm.model_clients.ascend_affinity_model_client import (
    AscendAffinityModelClient as AscendAffinityModelClient,
)
from openjiuwen.core.foundation.llm.model_clients.base_model_client import BaseModelClient as BaseModelClient

# Built-in implementations
from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient as OpenAIModelClient
from openjiuwen.core.foundation.llm.output_parsers.json_output_parser import JsonOutputParser as JsonOutputParser
from openjiuwen.core.foundation.llm.output_parsers.markdown_output_parser import (
    MarkdownOutputParser as MarkdownOutputParser,
)
from openjiuwen.core.foundation.llm.output_parsers.output_parser import BaseOutputParser as BaseOutputParser
from openjiuwen.core.foundation.llm.schema.config import (
    ModelClientConfig as ModelClientConfig,
)

# Configuration
from openjiuwen.core.foundation.llm.schema.config import (
    ModelRequestConfig as ModelRequestConfig,
)
from openjiuwen.core.foundation.llm.schema.config import (
    ProviderType as ProviderType,
)

# Messages
from openjiuwen.core.foundation.llm.schema.message import (
    OPENJIUWEN_MESSAGE_ORIGIN_EXTERNAL_USER as OPENJIUWEN_MESSAGE_ORIGIN_EXTERNAL_USER,
)
from openjiuwen.core.foundation.llm.schema.message import (
    OPENJIUWEN_MESSAGE_ORIGIN_HARNESS_INTERNAL as OPENJIUWEN_MESSAGE_ORIGIN_HARNESS_INTERNAL,
)
from openjiuwen.core.foundation.llm.schema.message import (
    OPENJIUWEN_MESSAGE_ORIGIN_METADATA as OPENJIUWEN_MESSAGE_ORIGIN_METADATA,
)
from openjiuwen.core.foundation.llm.schema.message import (
    OPENJIUWEN_MESSAGE_PROVENANCE_METADATA as OPENJIUWEN_MESSAGE_PROVENANCE_METADATA,
)
from openjiuwen.core.foundation.llm.schema.message import (
    OPENJIUWEN_MESSAGE_SOURCE_KIND_METADATA as OPENJIUWEN_MESSAGE_SOURCE_KIND_METADATA,
)
from openjiuwen.core.foundation.llm.schema.message import (
    AssistantMessage as AssistantMessage,
)
from openjiuwen.core.foundation.llm.schema.message import (
    BaseMessage as BaseMessage,
)
from openjiuwen.core.foundation.llm.schema.message import (
    SystemMessage as SystemMessage,
)
from openjiuwen.core.foundation.llm.schema.message import (
    ToolMessage as ToolMessage,
)
from openjiuwen.core.foundation.llm.schema.message import (
    UsageMetadata as UsageMetadata,
)
from openjiuwen.core.foundation.llm.schema.message import (
    UserMessage as UserMessage,
)
from openjiuwen.core.foundation.llm.schema.message_chunk import (
    AssistantMessageChunk as AssistantMessageChunk,
)
from openjiuwen.core.foundation.llm.schema.mode_info import BaseModelInfo as BaseModelInfo
from openjiuwen.core.foundation.llm.schema.mode_info import ModelConfig as ModelConfig

# Tools
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall as ToolCall

# ============ Public API exports ============

# Core classes
_CORE_CLASSES = [
    "Model",
    "init_model",
    "BaseModelClient",
    "BaseOutputParser",
]

# Configuration classes
_CONFIG_CLASSES = ["ModelRequestConfig", "ModelClientConfig", "ProviderType", "BaseModelInfo", "ModelConfig"]

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
    "OpenAIModelClient",
    "AscendAffinityModelClient",
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
    + _MESSAGE_CLASSES
    + _MESSAGE_METADATA_CONSTANTS
    + _MESSAGE_CHUNK_CLASSES
    + _TOOL_CLASSES
    + _PREBUILT_MODEL_CLIENTS
    + _PREBUILT_OUTPUT_PARSERS
)
