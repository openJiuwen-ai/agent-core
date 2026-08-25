# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared SFT raw/sample payload helpers."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from pydantic import BaseModel

from openjiuwen.agent_evolving.agent_rl.online.gateway.common import utc_now_iso

SFT_RAW_PROTOCOL_VERSION = "sft-raw-v1"
SFT_SAMPLE_PROTOCOL_VERSION = "sft-sample-v1"

_CHAT_MESSAGE_FIELDS = (
    "role",
    "content",
    "name",
    "tool_calls",
    "tool_call_id",
    "reasoning_content",
    "reasoning",
    "refusal",
    "annotations",
    "audio",
    "function_call",
)


def json_safe(value: Any) -> Any:
    """Return a JSON-serializable representation without assuming object shape."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, BaseModel):
        return json_safe(value.model_dump())
    return str(value)


def normalize_tool_calls(value: Any) -> list[dict[str, Any]]:
    """Normalize flat/project tool calls to the OpenAI nested shape."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return []
    if not isinstance(value, list):
        return []

    normalized: list[dict[str, Any]] = []
    for item in value:
        payload = json_safe(item)
        if not isinstance(payload, dict):
            continue
        function = payload.get("function")
        if isinstance(function, dict):
            call_function = dict(function)
            arguments = call_function.get("arguments")
            if isinstance(arguments, str):
                try:
                    call_function["arguments"] = json.loads(arguments)
                except (TypeError, json.JSONDecodeError):
                    pass
            if call_function.get("arguments") in (None, ""):
                call_function["arguments"] = {}
            normalized.append(
                {
                    "id": payload.get("id") or "",
                    "type": payload.get("type") or "function",
                    "function": {
                        "name": str(call_function.get("name") or ""),
                        "arguments": call_function.get("arguments", {}),
                    },
                }
            )
            continue

        # AssistantMessage.model_dump() already emits the nested format, but
        # trajectory and legacy callers may still provide flat ToolCall data.
        arguments = payload.get("arguments") or ""
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except (TypeError, json.JSONDecodeError):
                pass
        if arguments in (None, ""):
            arguments = {}
        call: dict[str, Any] = {
            "id": payload.get("id") or "",
            "type": payload.get("type") or "function",
            "function": {
                "name": payload.get("name") or "",
                "arguments": arguments,
            },
        }
        normalized.append(call)
    return normalized


def normalize_tool_definition(value: Any) -> dict[str, Any] | None:
    """Normalize one tool definition into the OpenAI ChatML shape."""
    payload = json_safe(value)
    if not isinstance(payload, dict):
        return None

    tool_type = str(payload.get("type") or "function")
    function = payload.get("function") if isinstance(payload.get("function"), dict) else {}
    if not function:
        function = {key: payload[key] for key in ("name", "description", "parameters") if key in payload}
    if not function.get("name"):
        return None

    normalized: dict[str, Any] = {
        "type": tool_type,
        "function": {
            "name": str(function.get("name") or ""),
        },
    }
    if function.get("description") is not None:
        normalized["function"]["description"] = function["description"]
    if function.get("parameters") is not None:
        normalized["function"]["parameters"] = json_safe(function["parameters"])
    return normalized


def normalize_tool_definitions(value: Any) -> list[dict[str, Any]]:
    """Normalize a list of tool definitions for ChatML export."""
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in value:
        tool = normalize_tool_definition(item)
        if tool is not None:
            normalized.append(tool)
    return normalized


def normalize_message(message: Any) -> dict[str, Any]:
    """Normalize one chat message into OpenAI-style dict form."""
    if isinstance(message, dict):
        normalized = json_safe(message)
        if not isinstance(normalized, dict):
            return {"role": "unknown", "content": str(message)}
    else:
        dumped = json_safe(message)
        if isinstance(dumped, dict) and dumped.get("role") is not None:
            normalized = dumped
        else:
            role = getattr(message, "role", "unknown")
            normalized = {
                "role": str(role or "unknown"),
                "content": str(getattr(message, "content", message) or ""),
            }

    if normalized.get("tool_calls") is not None:
        tool_calls = normalize_tool_calls(normalized.get("tool_calls"))
        if tool_calls:
            normalized["tool_calls"] = tool_calls
        else:
            normalized.pop("tool_calls", None)
    if (
        normalized.get("role") == "assistant"
        and normalized.get("tool_calls")
        and normalized.get("content") in ("", None)
    ):
        normalized["content"] = None
    return normalized


def normalize_messages(messages: Any) -> list[dict[str, Any]]:
    """Normalize a message list; invalid inputs become an empty list."""
    if not isinstance(messages, list):
        return []
    return [normalize_message(message) for message in messages]


def normalize_assistant_message(value: Any) -> dict[str, Any]:
    """Normalize a supervisor or trajectory response into an assistant message."""
    if isinstance(value, dict):
        payload = json_safe(value)
        if isinstance(payload, dict):
            choices = payload.get("choices")
            if isinstance(choices, list) and choices:
                choice = choices[0]
                if isinstance(choice, dict):
                    message = choice.get("message")
                    if isinstance(message, dict):
                        return normalize_assistant_message(message)
                    delta = choice.get("delta")
                    if isinstance(delta, dict):
                        return normalize_assistant_message(delta)
                    if choice.get("text") is not None:
                        return {"role": "assistant", "content": str(choice.get("text") or "")}
            if payload.get("message") and isinstance(payload["message"], dict):
                return normalize_assistant_message(payload["message"])
            message = {key: payload[key] for key in _CHAT_MESSAGE_FIELDS if key in payload}
            message.setdefault("role", "assistant")
            if "content" not in message:
                message["content"] = None if message.get("tool_calls") else (payload.get("response_text") or "")
            return normalize_message(message)
    content = getattr(value, "content", value)
    message = {
        "role": str(getattr(value, "role", "assistant") or "assistant"),
        "content": json_safe(content),
    }
    tool_calls = getattr(value, "tool_calls", None)
    if tool_calls:
        message["tool_calls"] = normalize_tool_calls(tool_calls)
    return normalize_message(message)


def assistant_text(message: dict[str, Any]) -> str:
    """Extract trainable assistant text from a normalized assistant message."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    return "" if content is None else str(content)


def assistant_has_trainable_output(message: dict[str, Any]) -> bool:
    """Return whether an assistant message has text or a structured tool call target."""
    return bool(assistant_text(message).strip() or message.get("tool_calls"))


def fingerprint_messages(messages: list[dict[str, Any]], assistant_message: dict[str, Any]) -> str:
    """Build a stable id suffix from training input/output content."""
    raw = json.dumps(
        {"messages": messages, "assistant_message": assistant_message},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def build_sft_sample(
    *,
    user_id: str,
    session_id: str,
    messages: list[dict[str, Any]],
    assistant_message: dict[str, Any],
    sample_id: str | None = None,
    source_raw_id: str = "",
    scenario: str = "",
    model_id: str = "",
    tools: Any = None,
    metadata: dict[str, Any] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Build one normalized ``sft-sample-v1`` payload."""
    normalized_messages = normalize_messages(messages)
    normalized_assistant = normalize_assistant_message(assistant_message)
    fallback_id = f"sft-{source_raw_id or session_id}-{fingerprint_messages(normalized_messages, normalized_assistant)}"
    sample = {
        "protocol_version": SFT_SAMPLE_PROTOCOL_VERSION,
        "sample_id": sample_id or fallback_id or str(uuid.uuid4()),
        "created_at": created_at or utc_now_iso(),
        "user_id": str(user_id or "").strip(),
        "session_id": str(session_id or "").strip(),
        "source_raw_id": str(source_raw_id or ""),
        "scenario": str(scenario or ""),
        "model_id": str(model_id or ""),
        "messages": normalized_messages,
        "assistant_message": normalized_assistant,
        "response_text": assistant_text(normalized_assistant),
        "metadata": json_safe(metadata or {}),
    }
    # Match llm-data-proxy ChatML: tools is a top-level field only when the
    # request actually exposed tool definitions.
    normalized_tools = normalize_tool_definitions(tools)
    if normalized_tools:
        sample["tools"] = normalized_tools
    return sample


def raw_user_id(raw: dict[str, Any], *, default_user_id: str = "") -> str:
    """Resolve the stable training user id from a raw SFT trajectory."""

    return str(raw.get("user_id") or raw.get("tenant_id") or default_user_id or "").strip()


def build_sft_samples_from_raw_steps(
    raw: dict[str, Any],
    *,
    scenario: str,
    default_user_id: str = "",
    target_model_id: str = "",
    metadata: dict[str, Any] | None = None,
    sample_id_suffix: str = "identity",
) -> list[dict[str, Any]]:
    """Convert one ``sft-raw-v1`` session into trainable ``sft-sample-v1`` items."""

    out: list[dict[str, Any]] = []
    raw_id = str(raw.get("raw_id") or raw.get("trajectory_id") or "")
    base_metadata = metadata or {}
    for step in raw.get("steps") or []:
        if not isinstance(step, dict) or step.get("type") != "llm":
            continue
        messages = normalize_messages(step.get("messages") or [])
        assistant = normalize_assistant_message(step.get("response") or {"content": step.get("response_text") or ""})
        if not messages or not assistant_has_trainable_output(assistant):
            continue
        out.append(
            build_sft_sample(
                sample_id=f"{raw_id}:{step.get('step_index', len(out))}:{sample_id_suffix}",
                user_id=raw_user_id(raw, default_user_id=default_user_id),
                session_id=str(raw.get("session_id") or ""),
                source_raw_id=raw_id,
                scenario=scenario,
                model_id=target_model_id or str(raw.get("model_id") or step.get("model_id") or ""),
                messages=messages,
                assistant_message=assistant,
                tools=step.get("tools"),
                metadata={
                    **base_metadata,
                    "raw_id": raw_id,
                    "step_index": step.get("step_index"),
                    "source_model_id": step.get("model_id") or raw.get("model_id"),
                },
            )
        )
    return out


def build_direct_supervisor_sft_samples(
    raw: dict[str, Any],
    *,
    scenario: str,
    default_user_id: str = "",
    target_model_id: str = "",
    flush_reason: str = "",
    metadata: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build direct-training samples from a supervisor-collected raw session.

    Scenario 2-1 v2 skips scheduler replay: the Docker task container already
    talks to the supervisor model, so SFTOnlineRail can upload trainable samples
    directly. This helper keeps that conversion identical to scheduler-side
    raw-to-sample conversion while tagging the source for observability.
    """

    dataset_case = raw.get("dataset_case") if isinstance(raw.get("dataset_case"), dict) else {}
    return build_sft_samples_from_raw_steps(
        raw,
        scenario=scenario,
        default_user_id=default_user_id,
        target_model_id=target_model_id,
        metadata={
            **(metadata or {}),
            "direct_supervisor_upload": True,
            "flush_reason": flush_reason,
            "dataset_case": dataset_case,
        },
        sample_id_suffix="direct",
    )
