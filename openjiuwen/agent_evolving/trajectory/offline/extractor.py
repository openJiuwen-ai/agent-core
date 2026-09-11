# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Offline Session-tracer adapter producing canonical OTLP spans."""

from __future__ import annotations

import hashlib
from copy import deepcopy
from datetime import datetime
from collections.abc import Mapping
from typing import Any

from openjiuwen.core.common.logging import logger

from openjiuwen.extensions.observability import semconv

from openjiuwen.agent_evolving.trajectory.offline.builder import TrajectoryBuilder
from openjiuwen.agent_evolving.trajectory.schema import (
    RL_COMPLETION_TOKEN_IDS,
    RL_LOGPROBS,
    RL_PROMPT_TOKEN_IDS,
)
from openjiuwen.agent_evolving.trajectory.serialization import to_json_compatible
from openjiuwen.agent_evolving.trajectory.spans import (
    attributes_from_map,
    normalize_span,
    write_llm_exchange,
)
from openjiuwen.agent_evolving.trajectory.model import Trajectory


def _get(value: Any, name: str, default: Any = None) -> Any:
    result = getattr(value, name, default)
    return default if result is None else result


def _unwrap(value: Any, key: str) -> Any:
    if isinstance(value, Mapping) and set(value) == {key}:
        return value[key]
    return value


def _dt_to_nanos(value: Any) -> int | None:
    """Normalize Session tracer timestamps expressed as datetime, millis, or nanos.

    Numeric inputs below ``10**14`` are treated as Unix milliseconds; larger
    values are treated as Unix nanoseconds. Microsecond timestamps are outside
    this adapter's input contract.
    """

    if value is None:
        return None
    if isinstance(value, datetime):
        return int(value.timestamp() * 1_000_000_000)
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if abs(number) >= 10**14 else number * 1_000_000


def _native_id(span: Any, *names: str) -> str | None:
    for name in names:
        value = _get(span, name)
        if value is None:
            continue
        if isinstance(value, bytes):
            return value.hex()
        if isinstance(value, int):
            width = 32 if "trace" in name.lower() else 16
            return f"{value:0{width}x}"
        text = str(value).strip()
        if text:
            return text
    return None


def _derived_id(trace_id: str, span: Any, index: int) -> tuple[str, str]:
    native_trace = _native_id(span, "trace_id", "traceId", "traceid") or trace_id
    if len(native_trace) != 32:
        native_trace = hashlib.sha256(native_trace.encode()).hexdigest()[:32]
    native_span = _native_id(span, "span_id", "spanId", "spanid")
    if native_span is None:
        native_span = hashlib.sha256(f"{native_trace}:{_get(span, 'invoke_id', '')}:{index}".encode()).hexdigest()[:16]
    elif len(native_span) != 16:
        native_span = hashlib.sha256(native_span.encode()).hexdigest()[:16]
    return native_trace, native_span


def _extract_inputs(span: Any) -> Any:
    return _unwrap(to_json_compatible(_get(span, "inputs")), "inputs")


def _extract_outputs(span: Any) -> Any:
    return _unwrap(to_json_compatible(_get(span, "outputs")), "outputs")


def _llm_params(span: Any) -> Mapping[str, Any]:
    records = _get(span, "on_invoke_data", []) or []
    if isinstance(records, Mapping):
        records = [records]
    for record in records:
        if isinstance(record, Mapping) and isinstance(record.get("llm_params"), Mapping):
            return record["llm_params"]
    return {}


def _message(value: Any, default_role: str | None = None) -> dict[str, Any] | None:
    value = to_json_compatible(value)
    if isinstance(value, Mapping):
        if "message" in value and isinstance(value["message"], Mapping):
            return _message(value["message"], default_role)
        role = value.get("role") or default_role
        content = value.get("content")
        if role is not None or content is not None:
            item = {"role": str(role or ""), "content": content if content is not None else ""}
            for key in ("name", "tool_calls"):
                if key in value:
                    item[key] = deepcopy(value[key])
            return item
        choices = value.get("choices")
        if isinstance(choices, list) and choices:
            return _message(choices[0], default_role)
        if "response" in value:
            return _message(value["response"], default_role)
    if value is not None:
        return {"role": default_role or "assistant", "content": value}
    return None


def _usage(response: Any, params: Mapping[str, Any]) -> Mapping[str, Any]:
    response_map = response if isinstance(response, Mapping) else {}
    usage = response_map.get("usage")
    if not isinstance(usage, Mapping):
        usage = params.get("usage")
    return usage if isinstance(usage, Mapping) else {}


def _status_and_events(error: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if error is None:
        return {"code": "STATUS_CODE_OK"}, []
    message = str(error)
    event_attrs = {
        "exception.type": type(error).__name__,
        "exception.message": message,
    }
    return (
        {"code": "STATUS_CODE_ERROR", "message": message},
        [{"name": "exception", "attributes": attributes_from_map(event_attrs)}],
    )


class TrajectoryExtractor:
    """Extract Session tracer records directly into canonical OTLP."""

    def __init__(self, resource_manager: Any = None) -> None:
        self._resource_manager = resource_manager

    def extract(self, session: Any, case_id: str | None = None) -> Trajectory:
        tracer = self._get_tracer(session)
        spans = self._get_agent_spans(tracer)
        effective_case_id = str(case_id or "unknown")
        builder = TrajectoryBuilder(
            session_id=effective_case_id,
            source="offline",
            case_id=effective_case_id,
        )
        trace_id = hashlib.sha256(effective_case_id.encode()).hexdigest()[:32]
        for index, span in enumerate(spans):
            try:
                builder.record_span(self._build_span(span, trace_id, index))
            except Exception:
                logger.exception("[TrajectoryExtractor] failed to normalize offline span %s", index)
        return builder.build()

    def _build_span(self, span: Any, trace_id: str, index: int) -> dict[str, Any]:
        kind = self._classify_kind(span)
        native_trace, native_span = _derived_id(trace_id, span, index)
        name = str(_get(span, "name", "") or "")
        if kind == "llm":
            span_name = "llm.call"
        elif kind == "tool":
            tool_name = name.removeprefix("tool.") or str(_get(span, "tool_name", "unknown"))
            span_name = f"tool.{tool_name}"
        else:
            span_name = name or kind

        attrs: dict[str, Any] = {}
        if kind == "llm":
            params = _llm_params(span)
            attrs[semconv.GEN_AI_OPERATION_NAME] = "chat"
            model = params.get("model") or _get(span, "model")
            if model:
                attrs[semconv.GEN_AI_REQUEST_MODEL] = str(model)
            messages = params.get("messages") or []
            if isinstance(messages, Mapping):
                messages = [messages]
            prompts = [
                message for message in (_message(value, "user") for value in messages)
                if message is not None
            ]
            response = _extract_outputs(span)
            response_message = _message(response)
            completions = [] if response_message is None else [response_message]
            attrs.update(write_llm_exchange(prompts, completions))
            if response_message is not None and response_message.get("tool_calls") is not None:
                attrs[semconv.GEN_AI_TOOL_CALLS] = response_message["tool_calls"]
            tools = params.get("tools")
            if tools:
                attrs[semconv.GEN_AI_TOOL_DEFINITIONS] = to_json_compatible(tools)
            usage = _usage(response, params)
            for source_keys, target in (
                (("prompt_tokens", "input_tokens"), semconv.GEN_AI_USAGE_PROMPT_TOKENS),
                (("completion_tokens", "output_tokens"), semconv.GEN_AI_USAGE_COMPLETION_TOKENS),
                (("total_tokens",), semconv.GEN_AI_USAGE_TOTAL_TOKENS),
            ):
                value = next((usage[key] for key in source_keys if key in usage), None)
                if value is not None:
                    attrs[target] = value
            response_map = response if isinstance(response, Mapping) else {}
            for source_key, target in (
                ("prompt_token_ids", RL_PROMPT_TOKEN_IDS),
                ("completion_token_ids", RL_COMPLETION_TOKEN_IDS),
                ("logprobs", RL_LOGPROBS),
            ):
                if source_key in response_map:
                    attrs[target] = to_json_compatible(response_map[source_key])
        elif kind == "tool":
            tool_name = name.removeprefix("tool.") or str(_get(span, "tool_name", "unknown"))
            attrs[semconv.GEN_AI_OPERATION_NAME] = "execute_tool"
            attrs[semconv.GEN_AI_TOOL_NAME] = tool_name
            attrs[semconv.GEN_AI_TOOL_INPUT] = _extract_inputs(span)
            attrs[semconv.GEN_AI_TOOL_OUTPUT] = _extract_outputs(span)
            tool_id = _get(span, "tool_call_id") or _get(span, "call_id")
            if tool_id:
                attrs[semconv.GEN_AI_TOOL_ID] = str(tool_id)
            tool_info = self._tool_info(tool_name)
            if tool_info is not None:
                attrs[semconv.GEN_AI_TOOL_DEFINITIONS] = tool_info
        else:
            attrs[semconv.AT_AGENT_INPUT] = _extract_inputs(span)
            attrs[semconv.AT_AGENT_OUTPUT] = _extract_outputs(span)
            agent_id = _get(span, "agent_id")
            if agent_id:
                attrs[semconv.AT_AGENT_ID] = str(agent_id)

        status, events = _status_and_events(_get(span, "error"))
        parent_span = _native_id(span, "parent_span_id", "parentSpanId")
        result: dict[str, Any] = {
            "traceId": native_trace,
            "spanId": native_span,
            "name": span_name,
            "attributes": attributes_from_map(attrs),
            "status": status,
            "events": events,
        }
        if parent_span:
            result["parentSpanId"] = parent_span
        start_nanos = _dt_to_nanos(_get(span, "start_time"))
        end_nanos = _dt_to_nanos(_get(span, "end_time"))
        if start_nanos is not None:
            result["startTimeUnixNano"] = str(start_nanos)
        if end_nanos is not None:
            result["endTimeUnixNano"] = str(end_nanos)
        return normalize_span(result)

    def _tool_info(self, tool_name: str) -> Any:
        if self._resource_manager is None or not tool_name:
            return None
        try:
            info = self._resource_manager.get_tool_infos(tool_name)
            if info is None:
                return None
            params = getattr(info, "parameters", None)
            schema = params.model_json_schema() if hasattr(params, "model_json_schema") else params
            return {
                "name": tool_name,
                "description": getattr(info, "description", None) or "",
                "parameters": to_json_compatible(schema),
            }
        except Exception:
            logger.exception("[TrajectoryExtractor] failed to get tool info for %s", tool_name)
            return None

    @staticmethod
    def _get_tracer(session: Any) -> Any:
        tracer = getattr(session, "tracer", None)
        return tracer() if callable(tracer) else tracer

    @staticmethod
    def _get_agent_spans(tracer: Any) -> list[Any]:
        if tracer is None:
            return []
        manager = getattr(tracer, "tracer_agent_span_manager", None)
        get_spans = getattr(manager, "get_all_spans", None)
        if not callable(get_spans):
            return []
        result = get_spans()
        return result if isinstance(result, list) else []

    @staticmethod
    def _classify_kind(span: Any) -> str:
        invoke_type = str(_get(span, "invoke_type", "") or "").lower()
        if invoke_type in {"plugin", "tool"}:
            return "tool"
        if invoke_type == "llm":
            return "llm"
        return invoke_type or "agent"


__all__ = ["TrajectoryExtractor"]
