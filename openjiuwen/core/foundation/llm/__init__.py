# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from openjiuwen.core.foundation.llm.base import BaseModelClient, BaseModelInfo
from openjiuwen.core.foundation.llm.model_utils.default_model import RequestChatModel, OpenAIChatModel
from openjiuwen.core.foundation.llm.model_utils.model_factory import ModelFactory
from openjiuwen.core.foundation.llm.output_parser.base import BaseOutputParser
from openjiuwen.core.foundation.llm.output_parser.json_output_parser import JsonOutputParser
from openjiuwen.core.foundation.llm.output_parser.markdown_output_parser import MarkdownOutputParser
from openjiuwen.core.foundation.llm.messages import BaseMessage, AIMessage, ToolMessage, UsageMetadata, \
    SystemMessage, HumanMessage
from openjiuwen.core.foundation.llm.schema.messages_chunk import BaseMessageChunk, AIMessageChunk
from openjiuwen.core.foundation.llm.schema.model_config import ModelConfig

_MODEL_SCHEMA_CLASSES = [
    "ModelConfig",
    "BaseModelInfo"
]

_MODEL_CLIENT_CLASSES = [
    "BaseModelClient",
    "RequestChatModel", 
    "OpenAIChatModel",
]

_OUTPUT_PARSER_CLASSES = [
    "BaseOutputParser",
    "JsonOutputParser",
    "MarkdownOutputParser"
]

_MESSAGE_CLASSES = [
    "BaseMessage",
    "AIMessage",
    "ToolMessage",
    "UsageMetadata",
    "SystemMessage",
    "HumanMessage"
]

_MESSAGE_CHUNK_CLASSES = [
    "BaseMessageChunk",
    "AIMessageChunk"
]

_MODEL_FACTORY_CLASSES = [
    "ModelFactory"
]

__all__ = (
    _MODEL_SCHEMA_CLASSES +
    _MODEL_CLIENT_CLASSES +
    _OUTPUT_PARSER_CLASSES +
    _MESSAGE_CLASSES +
    _MESSAGE_CHUNK_CLASSES +
    _MODEL_FACTORY_CLASSES
)