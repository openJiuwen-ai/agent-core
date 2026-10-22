# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

from typing import Union, List, Optional, Any, Dict
from pydantic import BaseModel, Field, field_validator, model_validator

from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.foundation.llm.utf8_sanitize import (
    sanitize_message_content,
    sanitize_utf8_text,
)


OPENJIUWEN_MESSAGE_PROVENANCE_METADATA = "_openjiuwen_message_provenance"
OPENJIUWEN_MESSAGE_ORIGIN_METADATA = "_openjiuwen_message_origin"
OPENJIUWEN_MESSAGE_SOURCE_KIND_METADATA = "_openjiuwen_message_source_kind"
OPENJIUWEN_MESSAGE_ORIGIN_EXTERNAL_USER = "external_user"
OPENJIUWEN_MESSAGE_ORIGIN_HARNESS_INTERNAL = "harness_internal"


class UsageMetadata(BaseModel):
    code: int = 0
    err_msg: str = ""
    prompt: str = ""
    task_id: str = ""
    model_name: str = ""
    total_latency: float = 0.
    first_token_time: str = ""
    request_start_time: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_tokens: int = 0
    cache_read_tokens: Optional[int] = None
    cache_miss_tokens: Optional[int] = None
    cache_write_tokens: Optional[int] = None
    cache_status: Optional[str] = None
    cache_source: Optional[str] = None
    cache_authoritative: bool = False
    cache_creation_input_tokens: Optional[int] = None
    reasoning_tokens: int = 0
    input_cost: float = 0.
    output_cost: float = 0.
    total_cost: float = 0.


class BaseMessage(BaseModel):
    role: str
    content: Union[str, List[Union[str, dict]]] = ""
    name: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AssistantMessage(BaseMessage):
    role: str = "assistant"
    tool_calls: Optional[List[ToolCall]] = None
    usage_metadata: Optional[UsageMetadata] = None
    finish_reason: str = "null"
    parser_content: Optional[Any] = None
    reasoning_content: Optional[str] = None
    # Optional token-level fields populated when the provider returns them
    # (e.g. vLLM with return_token_ids=True / logprobs=True). Used by RL
    # trajectory collection to skip re-tokenization.
    prompt_token_ids: Optional[List[int]] = None
    completion_token_ids: Optional[List[int]] = None
    logprobs: Optional[Any] = None
    response_id: Optional[str] = None
    response_model: Optional[str] = None
    provider_metadata: Dict[str, Any] = Field(default_factory=dict)
    provider_content: Optional[Any] = None

    @field_validator("content", mode="before")
    @classmethod
    def _sanitize_inbound_content(cls, value: Any) -> Any:
        """Inbound LLM reply content: replace lone surrogates with U+FFFD."""
        return sanitize_message_content(value)

    @field_validator("reasoning_content", mode="before")
    @classmethod
    def _sanitize_inbound_reasoning(cls, value: Any) -> Any:
        if isinstance(value, str):
            return sanitize_utf8_text(value)[0]
        return value

    @model_validator(mode='before')
    @classmethod
    def convert_openai_tool_calls_format(cls, data: Any) -> Any:
        """Convert OpenAI API format tool_calls to flat ToolCall format.

        OpenAI API format has nested 'function' object:
        {"id": "xxx", "type": "function", "function": {"name": "...", "arguments": "..."}}

        ToolCall model expects flat format:
        {"id": "xxx", "type": "function", "name": "...", "arguments": "..."}
        """
        if isinstance(data, dict) and 'tool_calls' in data and data['tool_calls']:
            converted_tool_calls = []
            for tc in data['tool_calls']:
                if isinstance(tc, dict) and 'function' in tc and isinstance(tc['function'], dict):
                    # OpenAI format - convert to flat format
                    converted_tc = {
                        'id': tc.get('id'),
                        'type': tc.get('type', 'function'),
                        'name': tc['function'].get('name', ''),
                        'arguments': tc['function'].get('arguments', ''),
                        'index': tc.get('index'),
                        'response_item_id': tc.get('response_item_id'),
                    }
                    converted_tool_calls.append(converted_tc)
                else:
                    # Already flat format or ToolCall instance
                    converted_tool_calls.append(tc)
            data['tool_calls'] = converted_tool_calls
        return data

    def model_dump(self, **kwargs) -> dict[str, Any]:
        # Use getattr for optional fields: checkpoint-restored instances may
        # predate newer schema keys (pydantic v2 fields are not class attrs,
        # so a missing __dict__ key raises AttributeError and breaks callers
        # such as context-usage.v1 analyze/token counting).
        result = {
            "role": self.role,
            "content": self.content,
        }
        name = getattr(self, "name", None)
        if name is not None:
            result["name"] = name
        metadata = getattr(self, "metadata", None) or {}
        if metadata:
            result["metadata"] = metadata
        tool_calls = getattr(self, "tool_calls", None)
        if tool_calls:
            dumped_tool_calls = []
            for call in tool_calls:
                dumped_tool_calls.append({
                    "id": call.id,
                    "type": call.type,
                    "function": {
                        "name": call.name,
                        "arguments": call.arguments
                    }
                })
                response_item_id = getattr(call, "response_item_id", None)
                if response_item_id is not None:
                    dumped_tool_calls[-1]["response_item_id"] = response_item_id
            result["tool_calls"] = dumped_tool_calls
        usage_metadata = getattr(self, "usage_metadata", None)
        if usage_metadata is not None:
            result["usage_metadata"] = usage_metadata.model_dump(**kwargs)
        finish_reason = getattr(self, "finish_reason", None)
        if finish_reason is not None:
            result["finish_reason"] = finish_reason
        parser_content = getattr(self, "parser_content", None)
        if parser_content is not None:
            result["parser_content"] = parser_content
        reasoning_content = getattr(self, "reasoning_content", None)
        if reasoning_content is not None:
            result["reasoning_content"] = reasoning_content
        prompt_token_ids = getattr(self, "prompt_token_ids", None)
        if prompt_token_ids is not None:
            result["prompt_token_ids"] = prompt_token_ids
        completion_token_ids = getattr(self, "completion_token_ids", None)
        if completion_token_ids is not None:
            result["completion_token_ids"] = completion_token_ids
        logprobs = getattr(self, "logprobs", None)
        if logprobs is not None:
            result["logprobs"] = logprobs
        response_id = getattr(self, "response_id", None)
        if response_id is not None:
            result["response_id"] = response_id
        response_model = getattr(self, "response_model", None)
        if response_model is not None:
            result["response_model"] = response_model
        provider_metadata = getattr(self, "provider_metadata", None) or {}
        if provider_metadata:
            result["provider_metadata"] = provider_metadata
        provider_content = getattr(self, "provider_content", None)
        if provider_content is not None:
            result["provider_content"] = provider_content
        return result


class UserMessage(BaseMessage):
    role: str = "user"

class SystemMessage(BaseMessage):
    role: str = "system"


class ToolMessage(BaseMessage):
    role: str = "tool"
    tool_call_id: str
