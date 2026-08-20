# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Project canonical projection from trajectory spans to chat messages."""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping, Sequence
from copy import deepcopy
from typing import Any, Final, Literal, TypeAlias

from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.spans import (
    iter_spans,
    read_llm_exchange,
    read_tool_call,
    span_sort_key,
)
from openjiuwen.agent_evolving.trajectory.team import span_category


MessageField: TypeAlias = Literal["content", "name", "tool_calls", "tool_call_id"]
DEFAULT_EVOLUTION_MESSAGE_FIELDS: Final[frozenset[MessageField]] = frozenset(
    {"content", "name", "tool_calls", "tool_call_id"}
)
_MESSAGE_FIELDS = frozenset(DEFAULT_EVOLUTION_MESSAGE_FIELDS)


def _field(value: object, name: str) -> object | None:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _tool_call_field(tool_call: object, name: str) -> object | None:
    value = _field(tool_call, name)
    if value is not None and value != "":
        return value
    function = _field(tool_call, "function")
    nested_value = _field(function, name) if function is not None else None
    return nested_value if nested_value is not None else value


def tool_call_id(tool_call: object) -> object | None:
    """Return a tool-call ID from project-flat or OpenAI nested input."""

    return _tool_call_field(tool_call, "id")


def tool_call_name(tool_call: object) -> object | None:
    """Return a tool-call name from project-flat or OpenAI nested input."""

    return _tool_call_field(tool_call, "name")


def tool_call_arguments(tool_call: object) -> object | None:
    """Return tool-call arguments from project-flat or OpenAI nested input."""

    return _tool_call_field(tool_call, "arguments")


def _json_text(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _normalize_tool_call(tool_call: object) -> dict[str, Any]:
    result: dict[str, Any] = {}
    call_id = tool_call_id(tool_call)
    if call_id is not None:
        result["id"] = deepcopy(call_id)

    call_type = _field(tool_call, "type")
    result["type"] = deepcopy(call_type) if call_type is not None else "function"

    function: dict[str, Any] = {}
    name = tool_call_name(tool_call)
    arguments = tool_call_arguments(tool_call)
    if name is not None:
        function["name"] = deepcopy(name)
    if arguments is not None:
        function["arguments"] = _json_text(arguments)
    result["function"] = function
    return result


def _normalize_message(message: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(message))
    tool_calls = result.get("tool_calls")
    if isinstance(tool_calls, Sequence) and not isinstance(tool_calls, (str, bytes)):
        result["tool_calls"] = [_normalize_tool_call(tool_call) for tool_call in tool_calls]
    return result


def _longest_overlap(messages: Sequence[dict[str, Any]], prompt: Sequence[dict[str, Any]]) -> int:
    upper_bound = min(len(messages), len(prompt))
    for length in range(upper_bound, 0, -1):
        if list(messages[-length:]) == list(prompt[:length]):
            return length
    return 0


def _merge_prompt(messages: list[dict[str, Any]], prompt: list[dict[str, Any]]) -> None:
    leading_context = 0
    while (
        leading_context < len(prompt)
        and prompt[leading_context].get("role") in {"system", "developer"}
        and leading_context < len(messages)
        and messages[leading_context] == prompt[leading_context]
    ):
        leading_context += 1
    prompt = prompt[leading_context:]
    overlap = _longest_overlap(messages, prompt)
    messages.extend(deepcopy(prompt[overlap:]))


def _tool_message(
    tool_call: Mapping[str, Any],
    tool_call_names: Mapping[str, object],
) -> dict[str, Any]:
    result: dict[str, Any] = {"role": "tool"}
    call_id = tool_call.get("id")
    name = tool_call.get("name")
    if name is None and call_id is not None:
        name = tool_call_names.get(str(call_id))
    if name is not None:
        result["name"] = deepcopy(name)
    if call_id is not None:
        result["tool_call_id"] = deepcopy(call_id)

    output = tool_call.get("output")
    if output is not None:
        result["content"] = _json_text(output)
    else:
        error = tool_call.get("error")
        if isinstance(error, Mapping) and error.get("message") is not None:
            result["content"] = str(error["message"])
    return result


def _select_fields(message: Mapping[str, Any], fields: frozenset[str]) -> dict[str, Any]:
    return {key: deepcopy(value) for key, value in message.items() if key == "role" or key in fields}


def trajectory_to_messages(
    trajectory: Trajectory,
    *,
    fields: Collection[MessageField] = DEFAULT_EVOLUTION_MESSAGE_FIELDS,
) -> list[dict[str, Any]]:
    """Project canonical LLM/tool spans into detached OpenAI-compatible messages.

    ``fields`` selects semantic output fields; physical span attribute mapping
    remains owned by trajectory accessors. Message values are preserved rather
    than validated, while tool-call containers are normalized structurally.
    """

    selected_fields = frozenset(fields)
    unknown_fields = selected_fields - _MESSAGE_FIELDS
    if unknown_fields:
        names = ", ".join(sorted(unknown_fields))
        raise ValueError(f"unknown trajectory message fields: {names}")

    messages: list[dict[str, Any]] = []
    tool_call_names: dict[str, object] = {}
    for span in sorted(iter_spans(trajectory), key=span_sort_key):
        category = span_category(span)
        if category == "llm":
            raw_prompt, raw_completions = read_llm_exchange(span)
            prompt = [_normalize_message(message) for message in raw_prompt]
            completions = [_normalize_message(message) for message in raw_completions]
            _merge_prompt(messages, prompt)
            messages.extend(completions)
            for message in (*prompt, *completions):
                for tool_call in message.get("tool_calls", ()):
                    call_id = _field(tool_call, "id")
                    name = tool_call_name(tool_call)
                    if call_id is not None and name is not None:
                        tool_call_names[str(call_id)] = deepcopy(name)
            continue
        if category == "tool":
            messages.append(_tool_message(read_tool_call(span), tool_call_names))

    return [_select_fields(message, selected_fields) for message in messages]


__all__ = [
    "DEFAULT_EVOLUTION_MESSAGE_FIELDS",
    "MessageField",
    "tool_call_arguments",
    "tool_call_id",
    "tool_call_name",
    "trajectory_to_messages",
]
