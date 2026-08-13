# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Union, List, Optional, Any, Dict
from pydantic import BaseModel, Field, SerializerFunctionWrapHandler, model_serializer, model_validator

from openjiuwen.core.foundation.llm.schema.content_part import (
    ContentPart,
    ImagePart,
    TextPart,
    normalize_content_part,
)
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall


class UsageMetadata(BaseModel):
    code: int = 0
    err_msg: str = ""
    prompt: str = ""
    task_id: str = ""
    model_name: str = ""
    total_latency: float = 0.0
    first_token_time: str = ""
    request_start_time: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_tokens: int = 0
    reasoning_tokens: int = 0
    input_cost: float = 0.0
    output_cost: float = 0.0
    total_cost: float = 0.0


class BaseMessage(BaseModel):
    role: str
    content: Union[str, List[Union[str, dict, ContentPart]]] = ""
    """Content part can encode both text and images while avoiding the usage
    of opaque dicts. The other options are kept to not break the API compatibility
    with user code since agent-core is a library and not an application.
    """
    name: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @property
    def parts(self) -> List[ContentPart]:
        """Content as typed parts, normalizing raw ``str``/``dict`` items on read."""
        if isinstance(self.content, str):
            return [TextPart(text=self.content)]
        normalized = (normalize_content_part(item) for item in self.content)
        return [part for part in normalized if isinstance(part, (TextPart, ImagePart))]

    @property
    def text(self) -> str:
        """The textual content only joined with newlines, with non-text parts skipped."""
        if isinstance(self.content, str):
            return self.content
        return "\n".join(part.text for part in self.parts if isinstance(part, TextPart))


def _to_openai_tool_call(call: ToolCall) -> dict[str, Any]:
    """Render a flat ``ToolCall`` back into OpenAI's nested wire shape."""
    result: dict[str, Any] = {
        "id": call.id,
        "type": call.type,
        "function": {
            "name": call.name,
            "arguments": call.arguments,
        },
    }
    if call.response_item_id is not None:
        result["response_item_id"] = call.response_item_id
    return result


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

    @model_validator(mode="before")
    @classmethod
    def convert_openai_tool_calls_format(cls, data: Any) -> Any:
        """Convert OpenAI API format tool_calls to flat ToolCall format.

        OpenAI API format has nested 'function' object:
        {"id": "xxx", "type": "function", "function": {"name": "...", "arguments": "..."}}

        ToolCall model expects flat format:
        {"id": "xxx", "type": "function", "name": "...", "arguments": "..."}
        """
        if isinstance(data, dict) and "tool_calls" in data and data["tool_calls"]:
            converted_tool_calls = []
            for tc in data["tool_calls"]:
                if isinstance(tc, dict) and "function" in tc and isinstance(tc["function"], dict):
                    # OpenAI format - convert to flat format
                    converted_tc = {
                        "id": tc.get("id"),
                        "type": tc.get("type", "function"),
                        "name": tc["function"].get("name", ""),
                        "arguments": tc["function"].get("arguments", ""),
                        "index": tc.get("index"),
                        "response_item_id": tc.get("response_item_id"),
                    }
                    converted_tool_calls.append(converted_tc)
                else:
                    # Already flat format or ToolCall instance
                    converted_tool_calls.append(tc)
            data["tool_calls"] = converted_tool_calls
        return data

    @model_serializer(mode="wrap")
    def serialize_compact_openai_shape(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """Emit the compact OpenAI-shaped dict, via pydantic's own serializer.
        This replaces a hand-written ``model_dump`` override that bypassed
        pydantic entirely.
        """
        result = {key: value for key, value in handler(self).items() if value is not None}

        if not self.metadata:
            result.pop("metadata", None)

        if self.tool_calls:
            result["tool_calls"] = [_to_openai_tool_call(call) for call in self.tool_calls]
        else:
            result.pop("tool_calls", None)

        return result


class UserMessage(BaseMessage):
    role: str = "user"


class SystemMessage(BaseMessage):
    role: str = "system"


class ToolMessage(BaseMessage):
    role: str = "tool"
    tool_call_id: str
