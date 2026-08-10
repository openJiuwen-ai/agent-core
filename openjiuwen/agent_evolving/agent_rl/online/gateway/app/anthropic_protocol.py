# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Translate Anthropic Messages requests to the OpenAI-compatible gateway."""

from __future__ import annotations

import copy
import json
import re
import secrets
from collections.abc import AsyncIterator, Mapping
from typing import Any

from fastapi.responses import JSONResponse, StreamingResponse

_BILLING_HEADER_RE = re.compile(r"^\s*x-anthropic-billing-header:[^\n]*\n?", re.IGNORECASE | re.MULTILINE)
_STOP_REASON_MAP = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "content_filter": "refusal",
}
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


class AnthropicRequestError(ValueError):
    """Anthropic payload cannot be represented by the gateway protocol."""


def _system_text(system: Any) -> str:
    if system is None:
        return ""
    if isinstance(system, str):
        return _BILLING_HEADER_RE.sub("", system).strip()
    if not isinstance(system, list):
        raise AnthropicRequestError("system must be a string or text block list")
    parts: list[str] = []
    for block in system:
        if not isinstance(block, Mapping) or block.get("type") != "text":
            raise AnthropicRequestError("system blocks must be text objects")
        text = block.get("text", "")
        if not isinstance(text, str):
            raise AnthropicRequestError("system text must be a string")
        parts.append(text)
    return _BILLING_HEADER_RE.sub("", "\n".join(parts)).strip()


def _image_part(block: Mapping[str, Any]) -> dict[str, Any]:
    source = block.get("source")
    if not isinstance(source, Mapping):
        raise AnthropicRequestError("image.source must be an object")
    source_type = source.get("type")
    if source_type == "base64":
        media_type, data = source.get("media_type"), source.get("data")
        if not isinstance(media_type, str) or not isinstance(data, str):
            raise AnthropicRequestError("base64 image source requires media_type and data")
        url = f"data:{media_type};base64,{data}"
    elif source_type == "url" and isinstance(source.get("url"), str):
        url = source["url"]
    else:
        raise AnthropicRequestError(f"unsupported image source type: {source_type}")
    return {"type": "image_url", "image_url": {"url": url}}


def _tool_result_messages(block: Mapping[str, Any]) -> list[dict[str, Any]]:
    tool_call_id = block.get("tool_use_id")
    if not isinstance(tool_call_id, str) or not tool_call_id:
        raise AnthropicRequestError("tool_result.tool_use_id must be a non-empty string")
    content = block.get("content", "")
    if isinstance(content, str):
        return [{"role": "tool", "tool_call_id": tool_call_id, "content": content}]
    if not isinstance(content, list):
        raise AnthropicRequestError("tool_result.content must be a string or block list")

    messages: list[dict[str, Any]] = []
    text_parts: list[str] = []

    def flush_text() -> None:
        if text_parts:
            messages.append({"role": "tool", "tool_call_id": tool_call_id, "content": "\n".join(text_parts)})
            text_parts.clear()

    for part in content:
        if not isinstance(part, Mapping):
            raise AnthropicRequestError("tool_result content blocks must be objects")
        if part.get("type") == "text" and isinstance(part.get("text"), str):
            text_parts.append(part["text"])
        elif part.get("type") == "image":
            flush_text()
            if not messages:
                messages.append({"role": "tool", "tool_call_id": tool_call_id, "content": ""})
            messages.append({"role": "user", "content": [_image_part(part)]})
        else:
            raise AnthropicRequestError(f"unsupported tool_result block type: {part.get('type')}")
    flush_text()
    return messages or [{"role": "tool", "tool_call_id": tool_call_id, "content": ""}]


def _user_messages(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"role": "user", "content": content}]
    if not isinstance(content, list):
        raise AnthropicRequestError("user content must be a string or block list")
    messages: list[dict[str, Any]] = []
    parts: list[dict[str, Any]] = []

    def flush_user() -> None:
        if not parts:
            return
        value: Any
        if all(part.get("type") == "text" for part in parts):
            value = "\n".join(str(part["text"]) for part in parts)
        else:
            value = list(parts)
        messages.append({"role": "user", "content": value})
        parts.clear()

    for block in content:
        if not isinstance(block, Mapping):
            raise AnthropicRequestError("user content blocks must be objects")
        block_type = block.get("type")
        if block_type == "text" and isinstance(block.get("text"), str):
            parts.append({"type": "text", "text": block["text"]})
        elif block_type == "image":
            parts.append(_image_part(block))
        elif block_type == "tool_result":
            flush_user()
            messages.extend(_tool_result_messages(block))
        else:
            raise AnthropicRequestError(f"unsupported user block type: {block_type}")
    flush_user()
    return messages


def _omit_optional_defaults(value: Any, schema: Any) -> Any:
    if isinstance(value, Mapping) and isinstance(schema, Mapping):
        properties = schema.get("properties")
        properties = properties if isinstance(properties, Mapping) else {}
        required_value = schema.get("required")
        required = set(required_value) if isinstance(required_value, list) else set()
        result: dict[Any, Any] = {}
        for key, item in value.items():
            field_schema = properties.get(key)
            has_default = isinstance(field_schema, Mapping) and "default" in field_schema
            default = field_schema.get("default") if has_default else None
            same_bool_type = isinstance(item, bool) == isinstance(default, bool)
            is_optional_default = key not in required and has_default
            if is_optional_default and same_bool_type and item == default:
                continue
            result[key] = _omit_optional_defaults(item, field_schema)
        return result
    if isinstance(value, list) and isinstance(schema, Mapping):
        return [_omit_optional_defaults(item, schema.get("items")) for item in value]
    return value


def _tool_schemas(tools: Any) -> dict[str, Any]:
    if not isinstance(tools, list):
        return {}
    return {
        tool["name"]: tool.get("input_schema", {})
        for tool in tools
        if isinstance(tool, Mapping) and isinstance(tool.get("name"), str)
    }


def _assistant_message(content: Any, schemas: Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(content, str):
        return {"role": "assistant", "content": content}
    if not isinstance(content, list):
        raise AnthropicRequestError("assistant content must be a string or block list")
    texts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, Mapping):
            raise AnthropicRequestError("assistant content blocks must be objects")
        block_type = block.get("type")
        if block_type == "text" and isinstance(block.get("text"), str):
            texts.append(block["text"])
        elif block_type == "tool_use":
            tool_id, name, arguments = block.get("id"), block.get("name"), block.get("input", {})
            if not isinstance(tool_id, str) or not tool_id or not isinstance(name, str):
                raise AnthropicRequestError("tool_use requires string id and name")
            if not isinstance(arguments, Mapping):
                raise AnthropicRequestError("tool_use.input must be an object")
            arguments = _omit_optional_defaults(arguments, schemas.get(name))
            tool_calls.append(
                {
                    "id": tool_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(arguments, ensure_ascii=False, separators=(",", ":")),
                    },
                }
            )
        elif block_type == "thinking":
            continue
        else:
            raise AnthropicRequestError(f"unsupported assistant block type: {block_type}")
    message: dict[str, Any] = {"role": "assistant", "content": "\n".join(texts)}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return message


def _fold_system_messages(messages: Any) -> Any:
    if not isinstance(messages, list):
        return messages
    result: list[Any] = []
    pending: list[str] = []
    for source in messages:
        message = copy.deepcopy(source)
        if isinstance(message, Mapping) and message.get("role") == "system":
            text = _system_text(message.get("content"))
            if text:
                reminder = f"<system-reminder>\n{text}\n</system-reminder>"
                previous_user = next(
                    (item for item in reversed(result) if isinstance(item, dict) and item.get("role") == "user"),
                    None,
                )
                if previous_user is None:
                    pending.append(reminder)
                elif isinstance(previous_user.get("content"), list):
                    previous_user["content"].append({"type": "text", "text": reminder})
                else:
                    content = previous_user.get("content", "")
                    previous_user["content"] = f"{content}\n{reminder}" if content else reminder
            continue
        if pending and isinstance(message, dict) and message.get("role") == "user":
            reminder = "\n".join(pending)
            content = message.get("content", "")
            if isinstance(content, list):
                content.insert(0, {"type": "text", "text": reminder})
            else:
                message["content"] = f"{reminder}\n{content}" if content else reminder
            pending.clear()
        result.append(message)
    if pending:
        result.append({"role": "user", "content": "\n".join(pending)})
    return result


def _messages(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    source = _fold_system_messages(payload.get("messages"))
    if not isinstance(source, list) or not source:
        raise AnthropicRequestError("messages must be non-empty")
    result: list[dict[str, Any]] = []
    schemas = _tool_schemas(payload.get("tools"))
    system = _system_text(payload.get("system"))
    if system:
        result.append({"role": "system", "content": system})
    for message in source:
        if not isinstance(message, Mapping):
            raise AnthropicRequestError("messages entries must be objects")
        role = message.get("role")
        if role == "user":
            result.extend(_user_messages(message.get("content", "")))
        elif role == "assistant":
            result.append(_assistant_message(message.get("content", ""), schemas))
        else:
            raise AnthropicRequestError(f"unsupported message role: {role}")
    return result


def _tools(payload: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    source = payload.get("tools")
    if source is None:
        return None
    if not isinstance(source, list):
        raise AnthropicRequestError("tools must be a list")
    tools: list[dict[str, Any]] = []
    for tool in source:
        if not isinstance(tool, Mapping) or not isinstance(tool.get("name"), str):
            raise AnthropicRequestError("tool entries require a string name")
        function = {
            "name": tool["name"],
            "description": tool.get("description"),
            "parameters": copy.deepcopy(tool.get("input_schema", {})),
        }
        tools.append({"type": "function", "function": function})
    choice = payload.get("tool_choice", {"type": "auto"})
    choice_type = choice if isinstance(choice, str) else choice.get("type") if isinstance(choice, Mapping) else None
    if choice_type == "none":
        return None
    if choice_type != "auto":
        raise AnthropicRequestError(f"unsupported tool_choice: {choice_type}")
    return tools


def convert_anthropic_request(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Translate an Anthropic Messages request to the upstream OpenAI shape."""
    body: dict[str, Any] = {"messages": _messages(payload)}
    model = payload.get("model")
    if isinstance(model, str) and model.strip():
        body["model"] = model
    tools = _tools(payload)
    if tools:
        body["tools"] = tools
    for key in ("max_tokens", "temperature", "top_p", "top_k"):
        if key in payload:
            body[key] = payload[key]
    if "stop_sequences" in payload:
        body["stop"] = payload["stop_sequences"]
    if payload.get("stream") is True:
        body["stream"] = True
    return body


def _response_blocks(message: Mapping[str, Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    content = message.get("content")
    if isinstance(content, str) and content:
        blocks.append({"type": "text", "text": content})
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        for tool_call in tool_calls:
            function = tool_call.get("function") if isinstance(tool_call, Mapping) else None
            if not isinstance(function, Mapping):
                continue
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    arguments = {}
            blocks.append(
                {
                    "type": "tool_use",
                    "id": tool_call.get("id") or f"toolu_{secrets.token_hex(8)}",
                    "name": function.get("name") or "tool",
                    "input": arguments if isinstance(arguments, dict) else {},
                }
            )
    return blocks or [{"type": "text", "text": ""}]


def _response_data(response: Mapping[str, Any], model: str) -> dict[str, Any]:
    choices = response.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], Mapping) else {}
    message = choice.get("message") if isinstance(choice.get("message"), Mapping) else {}
    usage = response.get("usage") if isinstance(response.get("usage"), Mapping) else {}
    finish_reason = str(choice.get("finish_reason") or "stop")
    return {
        "id": f"msg_{secrets.token_hex(12)}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": _response_blocks(message),
        "stop_reason": _STOP_REASON_MAP.get(finish_reason, "end_turn"),
        "stop_sequence": None,
        "usage": {
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
        },
    }


def _stream_response(response: Mapping[str, Any], model: str) -> StreamingResponse:
    message = _response_data(response, model)

    def event(name: str, data: dict[str, Any]) -> bytes:
        return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()

    async def generate() -> AsyncIterator[bytes]:
        start = dict(message)
        start["content"] = []
        start["stop_reason"] = None
        start["usage"] = {"input_tokens": message["usage"]["input_tokens"], "output_tokens": 0}
        yield event("message_start", {"type": "message_start", "message": start})
        for index, block in enumerate(message["content"]):
            empty: dict[str, Any]
            delta: dict[str, Any]
            if block["type"] == "text":
                empty = {"type": "text", "text": ""}
                delta = {"type": "text_delta", "text": block["text"]}
            else:
                empty = {"type": "tool_use", "id": block["id"], "name": block["name"], "input": {}}
                delta = {"type": "input_json_delta", "partial_json": json.dumps(block["input"], ensure_ascii=False)}
            yield event("content_block_start", {"type": "content_block_start", "index": index, "content_block": empty})
            yield event("content_block_delta", {"type": "content_block_delta", "index": index, "delta": delta})
            yield event("content_block_stop", {"type": "content_block_stop", "index": index})
        yield event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": message["stop_reason"], "stop_sequence": None},
                "usage": message["usage"],
            },
        )
        yield event("message_stop", {"type": "message_stop"})

    return StreamingResponse(generate(), media_type="text/event-stream", headers=_SSE_HEADERS)


def anthropic_error_response(status_code: int, message: str) -> JSONResponse:
    """Return an error using Anthropic's response envelope."""
    error_type = "invalid_request_error" if 400 <= status_code < 500 else "api_error"
    return JSONResponse(
        status_code=status_code,
        content={"type": "error", "error": {"type": error_type, "message": message}},
    )


def anthropic_response(
    response: Mapping[str, Any],
    *,
    model: str,
    stream: bool,
) -> JSONResponse | StreamingResponse:
    """Translate an upstream OpenAI response to Anthropic JSON or SSE."""
    if stream:
        return _stream_response(response, model)
    return JSONResponse(content=_response_data(response, model))
