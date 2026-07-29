# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Data mapping helpers for the OpenAI account Responses backend."""

from __future__ import annotations

import json
import uuid
from typing import Any, Iterable, Optional, Union

import httpx
from pydantic import BaseModel

from openjiuwen.core.common.utils.header_utils import sanitize_headers
from openjiuwen.core.foundation.llm.schema.message import (
    AssistantMessage,
    BaseMessage,
    ToolMessage,
    UsageMetadata,
)
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.foundation.tool import ToolInfo


class OpenAIAccountResponsesError(Exception):
    """Raised when Responses API data cannot be prepared or parsed."""

    def __init__(self, message: str, *, status_code: Optional[int] = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code

    def __str__(self) -> str:
        return self.message


def build_request_body(
    *,
    model: str,
    messages: Union[str, list[BaseMessage], list[dict]],
    tools: Union[list[ToolInfo], list[dict], None] = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    max_tokens: Optional[int] = None,
    stop: Optional[str] = None,
    reasoning: Optional[dict[str, Any]] = None,
    include_reasoning_encrypted_content: bool = True,
    tool_choice: Union[str, dict, None] = "auto",
    parallel_tool_calls: bool = True,
    store: bool = False,
    extra_body: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Create a Responses API request body from OpenJiuwen message objects."""
    if not model:
        raise OpenAIAccountResponsesError("OpenAI account Responses request requires a model.")

    instructions, input_items = convert_messages(messages)
    body: dict[str, Any] = {
        "model": model,
        "instructions": instructions,
        "input": input_items,
        "store": store,
    }

    request_options = {
        "temperature": temperature,
        "top_p": top_p,
        "max_output_tokens": max_tokens,
        "stop": stop,
        "reasoning": reasoning,
    }
    for key, value in request_options.items():
        if value is not None:
            body[key] = value

    response_tools = convert_tools(tools)
    if response_tools:
        body["tools"] = response_tools
        body["parallel_tool_calls"] = parallel_tool_calls
        if tool_choice is not None:
            body["tool_choice"] = tool_choice

    if include_reasoning_encrypted_content:
        body["include"] = ["reasoning.encrypted_content"]
    if extra_body:
        body.update(extra_body)
    return body


def build_headers(
    *,
    access_token: str,
    session_id: Optional[str] = None,
    extra_headers: Optional[dict[str, str]] = None,
) -> dict[str, str]:
    """Create HTTP headers for an account-backed Responses request."""
    if not access_token:
        raise OpenAIAccountResponsesError("OpenAI account Responses request requires an access token.")

    headers = sanitize_headers(extra_headers)
    headers["Authorization"] = f"Bearer {access_token}"
    headers["Accept"] = "application/json"
    headers["Content-Type"] = "application/json"
    if session_id:
        headers["session_id"] = session_id
        headers["x-client-request-id"] = session_id
    return headers


def convert_messages(messages: Union[str, list[BaseMessage], list[dict]]) -> tuple[str, list[dict[str, Any]]]:
    """Map OpenJiuwen messages to Responses ``instructions`` and ``input``."""
    instructions: list[str] = []
    inputs: list[dict[str, Any]] = []
    fallback_tool_call_index = 0

    for message in _message_dicts(messages):
        role = str(message.get("role") or "user")
        content_text = _content_as_text(message.get("content"))

        if role in {"system", "developer"}:
            if content_text:
                instructions.append(content_text)
            continue

        if role == "tool":
            tool_result = _tool_result_item(message, content_text)
            if tool_result is not None:
                inputs.append(tool_result)
            continue

        if role == "assistant":
            if content_text:
                inputs.append(
                    {
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": content_text}],
                    }
                )
            for tool_call in message.get("tool_calls") or []:
                inputs.append(_response_input_tool_call(tool_call, fallback_index=fallback_tool_call_index))
                fallback_tool_call_index += 1
            continue

        inputs.append(
            {
                "role": "user",
                "content": [{"type": "input_text", "text": content_text}],
            }
        )

    return "\n\n".join(instructions), inputs


def convert_tools(tools: Union[list[ToolInfo], list[dict], None]) -> list[dict[str, Any]]:
    """Map tool declarations to the Responses ``tools`` shape."""
    if not tools:
        return []

    converted: list[dict[str, Any]] = []
    for tool in tools:
        converted.append(_tool_definition(tool))
    return converted


def parse_response(payload: dict[str, Any], *, model_name: str = "") -> AssistantMessage:
    """Turn a complete Responses API payload into an ``AssistantMessage``."""
    content_parts, reasoning_parts, tool_calls = _read_output_items(payload.get("output"))
    if not content_parts and payload.get("output_text"):
        content_parts.append(str(payload.get("output_text") or ""))

    return AssistantMessage(
        content="".join(content_parts),
        tool_calls=tool_calls or None,
        usage_metadata=_usage_from_payload(payload.get("usage"), model_name=model_name),
        finish_reason=_finish_reason(payload, has_tool_calls=bool(tool_calls)),
        reasoning_content="\n".join(reasoning_parts) if reasoning_parts else None,
    )


def parse_stream_event(
    event: Optional[dict[str, Any]],
    *,
    model_name: str = "",
) -> Optional[AssistantMessageChunk]:
    """Turn one Responses SSE event into an ``AssistantMessageChunk``."""
    if not event:
        return None

    event_type = str(event.get("type") or event.get("event") or "")
    if event_type in {"response.output_text.delta", "response.refusal.delta"}:
        return _text_delta_chunk(event)
    if event_type in {"response.reasoning_text.delta", "response.reasoning_summary_text.delta"}:
        return _reasoning_delta_chunk(event)
    if event_type == "response.output_item.done":
        return _done_output_item_chunk(event)
    if event_type in {"response.completed", "response.incomplete"}:
        return _terminal_stream_chunk(event, event_type=event_type, model_name=model_name)
    if event_type in {"response.failed", "error"}:
        raise OpenAIAccountResponsesError(_stream_error_message(event))
    return None


def iter_sse_events(lines: Iterable[str]) -> Iterable[dict[str, Any]]:
    """Yield decoded event dictionaries from raw SSE lines."""
    block: list[str] = []
    for line in lines:
        if line:
            block.append(line)
            continue

        event = parse_sse_block(block)
        block = []
        if event is not None:
            yield event

    if block:
        event = parse_sse_block(block)
        if event is not None:
            yield event


def parse_sse_block(lines: list[str]) -> Optional[dict[str, Any]]:
    """Decode a single SSE block."""
    event_type = ""
    data_parts: list[str] = []

    for raw_line in lines:
        line = raw_line.rstrip("\r")
        if not line or line.startswith(":"):
            continue

        field, separator, value = line.partition(":")
        if not separator:
            continue
        if value.startswith(" "):
            value = value[1:]

        if field == "event":
            event_type = value.strip()
        elif field == "data":
            data_parts.append(value.strip())

    if not data_parts:
        return {"type": event_type} if event_type else None

    raw_data = "\n".join(data_parts)
    if raw_data == "[DONE]":
        return None

    try:
        parsed = json.loads(raw_data)
    except json.JSONDecodeError as exc:
        raise OpenAIAccountResponsesError(f"Invalid OpenAI account SSE JSON: {raw_data}") from exc

    if not isinstance(parsed, dict):
        return None
    parsed.setdefault("type", event_type or parsed.get("event"))
    return parsed


def raise_for_http_error(response: httpx.Response) -> None:
    """Raise an OpenAI account error for unsuccessful HTTP responses."""
    if response.status_code < 400:
        return

    message = _http_error_message(response)
    if not message:
        message = f"OpenAI account Responses request failed with status {response.status_code}."
    raise OpenAIAccountResponsesError(message, status_code=response.status_code)


def message_from_stream_chunk(chunk: Optional[AssistantMessageChunk]) -> AssistantMessage:
    """Convert an accumulated stream chunk into a complete assistant message."""
    if chunk is None:
        return AssistantMessage(content="", finish_reason="stop")

    finish_reason = "tool_calls" if chunk.tool_calls else chunk.finish_reason
    if finish_reason == "null":
        finish_reason = "stop"

    return AssistantMessage(
        content=chunk.content,
        tool_calls=chunk.tool_calls,
        usage_metadata=chunk.usage_metadata,
        finish_reason=finish_reason,
        parser_content=chunk.parser_content,
        reasoning_content=chunk.reasoning_content,
        prompt_token_ids=chunk.prompt_token_ids,
        completion_token_ids=chunk.completion_token_ids,
        logprobs=chunk.logprobs,
    )


def _message_dicts(messages: Union[str, list[BaseMessage], list[dict]]) -> list[dict[str, Any]]:
    if isinstance(messages, str):
        return [{"role": "user", "content": messages}]
    if not isinstance(messages, list):
        raise OpenAIAccountResponsesError(f"Unsupported messages type: {type(messages).__name__}")

    converted: list[dict[str, Any]] = []
    for item in messages:
        if isinstance(item, dict):
            converted.append(dict(item))
        elif isinstance(item, ToolMessage):
            converted.append({"role": item.role, "content": item.content, "tool_call_id": item.tool_call_id})
        elif isinstance(item, BaseMessage):
            converted.append(item.model_dump())
        else:
            raise OpenAIAccountResponsesError(f"Unsupported message type: {type(item).__name__}")

    if not converted:
        raise OpenAIAccountResponsesError("OpenAI account Responses request requires at least one message.")
    return converted


def _content_as_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    if not isinstance(content, list):
        return str(content)

    text_parts: list[str] = []
    for part in content:
        text = _content_part_text(part)
        if text:
            text_parts.append(text)
    return "".join(text_parts)


def _content_part_text(part: Any) -> str:
    if isinstance(part, str):
        return part
    if not isinstance(part, dict):
        return ""

    for key in ("text", "content"):
        value = part.get(key)
        if value is not None:
            return str(value)
    return ""


def _tool_result_item(message: dict[str, Any], output: str) -> Optional[dict[str, Any]]:
    tool_call_id = str(message.get("tool_call_id") or message.get("call_id") or "").strip()
    if not tool_call_id:
        return None
    return {
        "type": "function_call_output",
        "call_id": tool_call_id,
        "output": output,
    }


def _response_input_tool_call(tool_call: Any, *, fallback_index: int) -> dict[str, Any]:
    if isinstance(tool_call, ToolCall):
        call_id = tool_call.id or f"call_{uuid.uuid4().hex}"
        return {
            "type": "function_call",
            "id": tool_call.response_item_id or call_id,
            "call_id": call_id,
            "name": tool_call.name,
            "arguments": _json_argument_string(tool_call.arguments),
        }

    if isinstance(tool_call, dict):
        function = _dict_or_empty(tool_call.get("function"))
        arguments = tool_call.get("arguments") or function.get("arguments") or "{}"
        name = tool_call.get("name") or function.get("name") or ""
        call_id = tool_call.get("call_id") or tool_call.get("id") or f"call_{fallback_index}"
        item_id = tool_call.get("response_item_id") or tool_call.get("id") or call_id
        return {
            "type": "function_call",
            "id": str(item_id),
            "call_id": str(call_id),
            "name": str(name),
            "arguments": _json_argument_string(arguments),
        }

    raise OpenAIAccountResponsesError(f"Unsupported tool call type: {type(tool_call).__name__}")


def _tool_definition(tool: Union[ToolInfo, dict]) -> dict[str, Any]:
    if isinstance(tool, ToolInfo):
        return {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": _schema_dict(tool.parameters),
        }

    if isinstance(tool, dict):
        function = _dict_or_empty(tool.get("function"))
        if function:
            return {
                "type": "function",
                "name": str(function.get("name") or ""),
                "description": str(function.get("description") or ""),
                "parameters": _schema_dict(function.get("parameters") or {}),
            }
        if tool.get("type") == "function" and tool.get("name"):
            return {
                "type": "function",
                "name": str(tool.get("name") or ""),
                "description": str(tool.get("description") or ""),
                "parameters": _schema_dict(tool.get("parameters") or {}),
            }

    raise OpenAIAccountResponsesError(f"Unsupported tool schema: {tool!r}")


def _schema_dict(schema: Any) -> dict[str, Any]:
    if isinstance(schema, dict):
        return schema
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        return schema.model_json_schema()
    if isinstance(schema, BaseModel):
        return schema.model_json_schema()
    return {"type": "object", "properties": {}}


def _read_output_items(output: Any) -> tuple[list[str], list[str], list[ToolCall]]:
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: list[ToolCall] = []

    if not isinstance(output, list):
        return content_parts, reasoning_parts, tool_calls

    for item in output:
        if not isinstance(item, dict):
            continue

        item_type = str(item.get("type") or "")
        if item_type == "message":
            content_parts.extend(_message_content_text(item))
        elif item_type == "reasoning":
            reasoning_parts.extend(_reasoning_text(item))
        elif item_type == "function_call":
            tool_calls.append(_tool_call_from_response_item(item, index=len(tool_calls)))

    return content_parts, reasoning_parts, tool_calls


def _message_content_text(item: dict[str, Any]) -> list[str]:
    content = item.get("content")
    if not isinstance(content, list):
        return []

    texts: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") in {"output_text", "text"} and part.get("text") is not None:
            texts.append(str(part.get("text")))
    return texts


def _reasoning_text(item: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    summary = item.get("summary")
    if isinstance(summary, list):
        for part in summary:
            if isinstance(part, str):
                texts.append(part)
            elif isinstance(part, dict) and part.get("text") is not None:
                texts.append(str(part.get("text")))

    text = item.get("text")
    if text is not None:
        texts.append(str(text))
    return texts


def _tool_call_from_response_item(item: dict[str, Any], *, index: int) -> ToolCall:
    response_item_id = str(item.get("id") or "").strip() or None
    call_id = str(item.get("call_id") or response_item_id or "")
    return ToolCall(
        id=call_id,
        type="function",
        name=str(item.get("name") or ""),
        arguments=_json_argument_string(item.get("arguments") or "{}"),
        index=index,
        response_item_id=response_item_id,
    )


def _text_delta_chunk(event: dict[str, Any]) -> Optional[AssistantMessageChunk]:
    delta = str(event.get("delta") or "")
    if not delta:
        return None
    return AssistantMessageChunk(content=delta, finish_reason="null")


def _reasoning_delta_chunk(event: dict[str, Any]) -> Optional[AssistantMessageChunk]:
    delta = str(event.get("delta") or "")
    if not delta:
        return None
    return AssistantMessageChunk(content="", reasoning_content=delta, finish_reason="null")


def _done_output_item_chunk(event: dict[str, Any]) -> Optional[AssistantMessageChunk]:
    item = event.get("item")
    if not isinstance(item, dict) or item.get("type") != "function_call":
        return None

    return AssistantMessageChunk(
        content="",
        tool_calls=[_tool_call_from_response_item(item, index=0)],
        finish_reason="tool_calls",
    )


def _terminal_stream_chunk(
    event: dict[str, Any],
    *,
    event_type: str,
    model_name: str,
) -> AssistantMessageChunk:
    response_payload = event.get("response")
    usage = response_payload.get("usage") if isinstance(response_payload, dict) else event.get("usage")
    finish_reason = "length" if event_type == "response.incomplete" else "stop"
    return AssistantMessageChunk(
        content="",
        usage_metadata=_usage_from_payload(usage, model_name=model_name),
        finish_reason=finish_reason,
    )


def _stream_error_message(event: dict[str, Any]) -> str:
    if event.get("message"):
        return str(event["message"])
    error = event.get("error")
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])
    return "OpenAI account Responses stream failed."


def _usage_from_payload(usage: Any, *, model_name: str) -> Optional[UsageMetadata]:
    if not isinstance(usage, dict):
        return None

    input_tokens = _int_or_zero(_first_present(usage, "input_tokens", "prompt_tokens"))
    output_tokens = _int_or_zero(_first_present(usage, "output_tokens", "completion_tokens"))
    total_tokens = _optional_int(usage.get("total_tokens"))
    if total_tokens is None:
        total_tokens = input_tokens + output_tokens

    cache_tokens = 0
    token_details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details")
    if isinstance(token_details, dict):
        cache_tokens = _int_or_zero(token_details.get("cached_tokens"))

    return UsageMetadata(
        model_name=model_name,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cache_tokens=cache_tokens,
    )


def _finish_reason(payload: dict[str, Any], *, has_tool_calls: bool) -> str:
    if has_tool_calls:
        return "tool_calls"
    if str(payload.get("status") or "") == "incomplete":
        return "length"
    return "stop"


def _http_error_message(response: httpx.Response) -> Optional[str]:
    try:
        payload = response.json()
    except ValueError:
        return None

    if not isinstance(payload, dict):
        return None

    error = payload.get("error")
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])
    if isinstance(error, str):
        return error
    if isinstance(payload.get("detail"), str):
        return str(payload["detail"])
    return None


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_present(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if payload.get(key) is not None:
            return payload.get(key)
    return None


def _json_argument_string(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _int_or_zero(value: Any) -> int:
    parsed = _optional_int(value)
    return 0 if parsed is None else parsed


def _optional_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
