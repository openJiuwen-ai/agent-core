# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared LLM call helpers for OfficeQA dialogue loops."""

from __future__ import annotations

import json
from typing import Any

from openjiuwen.agent_evolving.skill_train.llm_client import chat_target_messages

LOCAL_TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "Locate corpus files by basename or relative glob.",
            "parameters": {
                "type": "object",
                "properties": {"pattern": {"type": "string"}},
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": "Read a contiguous line window from an allow-listed path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start": {"type": "integer"},
                    "limit": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Find a literal substring inside an allow-listed file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string"},
                },
                "required": ["pattern", "path"],
            },
        },
    },
]


def metadata_of(message: object) -> dict:
    metadata = getattr(message, "metadata", None)
    return metadata if isinstance(metadata, dict) else {}


def tool_calls_openai_shape(message: object) -> list[dict]:
    """Flatten nested / object tool-calls into OpenAI chat dicts."""
    raw = getattr(message, "tool_calls", None) or []
    out: list[dict] = []
    for tc in raw:
        if isinstance(tc, dict):
            fn = tc.get("function")
            if isinstance(fn, dict):
                name = str(fn.get("name") or "")
                arguments = str(fn.get("arguments") or "{}")
            else:
                name = str(tc.get("name") or "")
                arguments = str(tc.get("arguments") or "{}")
            out.append(
                {
                    "id": tc.get("id"),
                    "type": tc.get("type") or "function",
                    "function": {"name": name, "arguments": arguments},
                }
            )
            continue

        fn_obj = getattr(tc, "function", None)
        name = getattr(tc, "name", None)
        arguments = getattr(tc, "arguments", None)
        if fn_obj is not None and not name:
            name = getattr(fn_obj, "name", "") or ""
            arguments = getattr(fn_obj, "arguments", None)
        if arguments is None:
            arguments = "{}"
        elif not isinstance(arguments, str):
            arguments = json.dumps(arguments, ensure_ascii=False)
        out.append(
            {
                "id": getattr(tc, "id", None),
                "type": getattr(tc, "type", None) or "function",
                "function": {"name": str(name or ""), "arguments": arguments},
            }
        )
    return out


def parse_tool_arguments(raw_args: Any) -> dict:
    if isinstance(raw_args, dict):
        return raw_args
    if not isinstance(raw_args, str):
        return {}
    try:
        loaded = json.loads(raw_args or "{}")
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def invoke_chat(
    messages: list[dict],
    *,
    max_completion_tokens: int,
    tools: list[dict] | None = None,
    tool_choice: str | None = None,
) -> tuple[Any, dict]:
    kwargs: dict[str, Any] = {
        "messages": messages,
        "max_completion_tokens": max_completion_tokens,
        "retries": 3,
        "stage": "rollout",
        "return_message": True,
    }
    if tools is not None:
        kwargs["tools"] = tools
    if tool_choice is not None:
        kwargs["tool_choice"] = tool_choice
    message, _ = chat_target_messages(**kwargs)
    return message, metadata_of(message)


def append_assistant_event(
    conversation: list[dict],
    *,
    content: str,
    turn: int | None = None,
    metadata: dict | None = None,
) -> None:
    event: dict[str, Any] = {"type": "message", "content": content}
    if turn is not None:
        event["turn"] = turn
    if metadata:
        event["response_metadata"] = metadata
    conversation.append(event)
