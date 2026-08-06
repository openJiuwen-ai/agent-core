# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Project Codex observability records and SDK tool events into Jiuwen.

The rollout trace is the primary source for model calls because its
``inference_call_id`` joins an exact request payload, response payload, model,
usage, and native execution window. Generic App Server OTel spans are retained
only as a compatibility fallback for Codex builds without rollout tracing.
"""

from __future__ import annotations

import asyncio
import json
import re
import shlex
import time
from typing import Any

_TRACER_NAME = "openjiuwen.agent_teams.observability.codex"
_NATIVE_EXPORT_QUIET_S = 0.15
_MAX_INDEXED_MESSAGES = 48
_ROLLOUT_TOOL_OVERLAP_TOLERANCE_NS = 250_000_000
_EXEC_TOOL_PATTERN = re.compile(r"\btools\.([A-Za-z0-9_]+)\s*\(")
_EXEC_TOOL_LITERAL_PATTERN = re.compile(
    r"""\btools\s*\[\s*(["'])([A-Za-z0-9_]+)\1\s*\]\s*\(""",
)
_EXEC_TOOL_VARIABLE_PATTERN = re.compile(
    r"""\b(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*"""
    r"""(["'])([A-Za-z0-9_]+)\2\s*;[\s\S]*?\btools\s*\[\s*\1\s*\]\s*\(""",
)
_EXEC_COMMAND_PATTERN = re.compile(
    r"""\bcmd\s*:\s*("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')""",
)


def _json_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def _structured_text(value: Any) -> str:
    """Keep SDK-rendered text intact and serialize only structured values."""
    return value if isinstance(value, str) else _json_text(value)


def _truncate(value: str, max_length: int) -> str:
    return value if len(value) <= max_length else f"{value[:max_length]}…"


def _value(value: Any, *names: str) -> Any:
    for name in names:
        if isinstance(value, dict) and name in value:
            return value[name]
        if hasattr(value, name):
            return getattr(value, name)
    return None


def _usage(value: Any) -> dict[str, int]:
    return {
        "input_tokens": int(_value(value, "inputTokens", "input_tokens") or 0),
        "cached_input_tokens": int(
            _value(value, "cachedInputTokens", "cached_input_tokens") or 0,
        ),
        "output_tokens": int(_value(value, "outputTokens", "output_tokens") or 0),
        "reasoning_output_tokens": int(
            _value(value, "reasoningOutputTokens", "reasoning_output_tokens") or 0,
        ),
        "total_tokens": int(_value(value, "totalTokens", "total_tokens") or 0),
    }


def _content_text(value: Any) -> str:
    """Render protocol content while preserving structured non-text items."""
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return _json_text(value)
    text_parts: list[str] = []
    structured_parts: list[Any] = []
    for item in value:
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            text_parts.append(item["text"])
        else:
            structured_parts.append(item)
    if structured_parts:
        text_parts.append(_json_text(structured_parts))
    return "\n".join(part for part in text_parts if part)


def _request_messages(payload: Any) -> list[dict[str, Any]]:
    """Extract the ordered model-visible messages from an inference request."""
    if not isinstance(payload, dict):
        return [{"role": "user", "content": _json_text(payload)}]
    messages: list[dict[str, Any]] = []
    instructions = payload.get("instructions")
    if instructions:
        messages.append(
            {
                "role": "developer",
                "content": _content_text(instructions),
            },
        )
    items = payload.get("input")
    if not isinstance(items, list):
        items = payload.get("messages")
    if not isinstance(items, list):
        messages.append({"role": "user", "content": _json_text(payload)})
        return messages
    for item in items:
        if not isinstance(item, dict):
            messages.append({"role": "user", "content": _json_text(item)})
            continue
        item_type = str(item.get("type") or "")
        role = str(item.get("role") or "")
        if item_type in {"function_call_output", "custom_tool_call_output", "mcp_tool_call_output"}:
            messages.append(
                {
                    "role": "tool",
                    "name": str(item.get("name") or item_type),
                    "call_id": str(item.get("call_id") or ""),
                    "content": _content_text(item.get("output")),
                },
            )
            continue
        if item_type in {"function_call", "custom_tool_call"}:
            messages.append(
                {
                    "role": "assistant",
                    "name": str(item.get("name") or ""),
                    "call_id": str(item.get("call_id") or ""),
                    "content": str(item.get("arguments") or item.get("input") or ""),
                },
            )
            continue
        messages.append(
            {
                "role": role or "user",
                "content": _content_text(item.get("content", item)),
            },
        )
    return messages


def _response_parts(payload: Any) -> tuple[str, str, list[dict[str, Any]]]:
    """Extract assistant text, exposed reasoning, and tool calls."""
    if not isinstance(payload, dict):
        return _json_text(payload), "", []
    items = payload.get("output_items")
    if not isinstance(items, list):
        items = payload.get("output")
    if not isinstance(items, list):
        return _json_text(payload), "", []
    output: list[str] = []
    reasoning: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            output.append(_json_text(item))
            continue
        item_type = str(item.get("type") or "")
        if item_type in {"message", "agent_message"}:
            output.append(_content_text(item.get("content")))
        elif item_type == "reasoning":
            reasoning.append(_content_text(item.get("summary")))
            reasoning.append(_content_text(item.get("content")))
        elif item_type in {
            "function_call",
            "custom_tool_call",
            "local_shell_call",
            "mcp_tool_call",
            "tool_search_call",
        }:
            tool_calls.append(item)
    return (
        "\n".join(part for part in output if part),
        "\n".join(part for part in reasoning if part and part != "null"),
        tool_calls,
    )


def _tool_correlation_keys(item: dict[str, Any]) -> set[str]:
    return {str(value) for value in (item.get("id"), item.get("call_id")) if value is not None and str(value)}


def _command_display_name(code: str) -> str:
    match = _EXEC_COMMAND_PATTERN.search(code)
    if match is None:
        return "shell.exec_command"
    literal = match.group(1)
    try:
        command = json.loads(literal) if literal.startswith('"') else literal[1:-1]
        executable = shlex.split(command)[0]
    except (IndexError, TypeError, ValueError):
        return "shell.exec_command"
    executable = re.sub(r"[^A-Za-z0-9_.-].*$", "", executable.rsplit("/", maxsplit=1)[-1])
    return f"shell.{executable}" if executable else "shell.exec_command"


def _exec_tool_identifier(code: str) -> str:
    """Resolve only statically named tools from Codex exec JavaScript."""
    direct_match = _EXEC_TOOL_PATTERN.search(code)
    if direct_match is not None:
        return direct_match.group(1)
    literal_match = _EXEC_TOOL_LITERAL_PATTERN.search(code)
    if literal_match is not None:
        return literal_match.group(2)
    variable_match = _EXEC_TOOL_VARIABLE_PATTERN.search(code)
    if variable_match is not None:
        return variable_match.group(3)
    return ""


def _decode_rollout_tool(item: dict[str, Any]) -> dict[str, Any]:
    """Turn a generic Codex ``exec`` call into a readable tool identity."""
    item_type = str(item.get("type") or "custom_tool_call")
    raw_name = str(item.get("name") or item_type)
    raw_input = item.get("input", item.get("arguments", ""))
    code = raw_input if isinstance(raw_input, str) else _json_text(raw_input)
    tool_name = raw_name
    display_name = raw_name
    if item_type == "custom_tool_call" and raw_name == "exec":
        identifier = _exec_tool_identifier(code)
        if identifier.startswith("mcp__") and "__" in identifier[5:]:
            server, tool = identifier[5:].rsplit("__", maxsplit=1)
            tool_name = f"{server}.{tool}"
            display_name = "codex.exec"
        elif identifier == "exec_command":
            tool_name = _command_display_name(code)
            display_name = "codex.exec"
        elif identifier:
            tool_name = identifier
            display_name = "codex.exec"
        elif "ALL_TOOLS" in code:
            tool_name = "codex.tool_discovery"
            display_name = tool_name
        else:
            tool_name = "codex.internal.exec"
            display_name = tool_name
    return {
        "tool_name": tool_name,
        "display_name": display_name,
        "tool_args": {"code": code},
        "item_type": item_type,
        "source": "rollout",
        "boundary_exact": False,
        "correlation_keys": _tool_correlation_keys(item),
    }


def _sdk_tool_display_name(
    *,
    item_type: str,
    tool_args: Any,
) -> str | None:
    """Keep an SDK dynamic exec wrapper distinct from its nested tool."""
    if item_type != "dynamicToolCall":
        return None
    code = tool_args.get("code") if isinstance(tool_args, dict) else tool_args
    if not isinstance(code, str) or not code:
        return None
    decoded = _decode_rollout_tool(
        {
            "type": "custom_tool_call",
            "name": "exec",
            "input": code,
        },
    )
    return str(decoded.get("display_name") or "") or None


def _rollout_tool_outputs(request_payload: Any) -> dict[str, Any]:
    if not isinstance(request_payload, dict):
        return {}
    items = request_payload.get("input")
    if not isinstance(items, list):
        items = request_payload.get("messages")
    if not isinstance(items, list):
        return {}
    outputs: dict[str, Any] = {}
    output_types = {
        "function_call_output",
        "custom_tool_call_output",
        "local_shell_call_output",
        "mcp_tool_call_output",
    }
    for item in items:
        if not isinstance(item, dict) or item.get("type") not in output_types:
            continue
        output = item.get("output", item.get("result"))
        for key in _tool_correlation_keys(item):
            outputs[key] = output
    return outputs


def _normalized_tool_leaf(name: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name or "").rsplit(".", maxsplit=1)[-1].lower()).strip("_")


class CodexSpanBridge:
    """Create exact rollout model spans and correlated SDK tool spans."""

    def __init__(
        self,
        *,
        member_name: str,
        member_agent_id: str,
        team_name: str,
        session_id: str,
    ) -> None:
        self._member_name = member_name
        self._member_agent_id = member_agent_id
        self._team_name = team_name
        self._session_id = session_id
        self._turn_index = 0
        self._turn_span: Any | None = None
        self._config: Any | None = None
        self._thread_id: str | None = None
        self._model: Any | None = None
        self._native_trace_enabled = False
        self._rollout_trace_enabled = False
        self._native_span_count = 0
        self._rollout_span_count = 0
        self._last_native_span_at = 0.0
        self._last_rollout_event_at = 0.0
        self._rollout_turn_ended_at = 0.0
        self._native_model_events: list[dict[str, Any]] = []
        self._inferences: dict[str, dict[str, Any]] = {}
        self._emitted_inference_ids: set[str] = set()
        self._rollout_turn_id: str | None = None
        self._tool_records: dict[str, dict[str, Any]] = {}
        self._rollout_tool_records: dict[str, dict[str, Any]] = {}
        self._output: list[str] = []
        self._reasoning: list[str] = []
        self._inputs: list[dict[str, Any]] = []
        self._response_ids: list[str] = []
        self._usage = _usage(None)
        self._llm_index = 0
        self._captured_runtime = self._observability_runtime()

    def enable_native_model_spans(self) -> None:
        """Mark native trace delivery as active for this member."""
        self._native_trace_enabled = True

    def enable_rollout_trace(self) -> None:
        """Mark stable-ID rollout delivery as the primary model-call source."""
        self._rollout_trace_enabled = True

    # Compatibility name used by older runtime tests/callers.
    enable_native_api_timing = enable_native_model_spans

    def native_traceparent(self) -> str | None:
        """Return the current Jiuwen team span as a W3C parent carrier."""
        runtime = self._observability_runtime() or self._captured_runtime
        if runtime is None:
            return None
        _, _, team_span = runtime
        context = team_span.get_span_context()
        if not context.is_valid:
            return None
        flags = int(context.trace_flags) & 0xFF
        return f"00-{context.trace_id:032x}-{context.span_id:016x}-{flags:02x}"

    def start_turn(
        self,
        *,
        prompt: str,
        thread_id: str | None,
        developer_instructions: str | None = None,
        model: Any | None = None,
    ) -> None:
        self.finish_turn(status="cancelled")
        runtime = self._observability_runtime() or self._captured_runtime
        if runtime is None:
            return
        self._captured_runtime = runtime
        tracer, config, team_span = runtime

        from opentelemetry import context as otel_context
        from opentelemetry.trace import SpanKind, set_span_in_context

        from openjiuwen.agent_teams.observability.redaction import redact_prompt
        from openjiuwen.agent_teams.observability.semconv import (
            AT_AGENT_ID,
            AT_AGENT_INPUT,
            AT_AGENT_NAME,
            AT_AGENT_ROLE,
            AT_MEMBER_ID,
            AT_MEMBER_NAME,
            AT_SESSION_ID,
            AT_TEAM_ID,
            AT_TEAM_NAME,
            LANGFUSE_OBSERVATION_INPUT,
            LANGFUSE_OBSERVATION_TYPE,
            LANGFUSE_SESSION_ID,
        )

        self._turn_index += 1
        span = tracer.start_span(
            name=f"agent.{self._member_name}.codex_turn.{self._turn_index}",
            context=set_span_in_context(team_span, otel_context.get_current()),
            kind=SpanKind.INTERNAL,
        )
        safe_prompt = redact_prompt(prompt, config)
        span.set_attribute(LANGFUSE_OBSERVATION_TYPE, "agent")
        span.set_attribute(LANGFUSE_OBSERVATION_INPUT, safe_prompt)
        span.set_attribute(AT_AGENT_INPUT, safe_prompt)
        span.set_attribute(AT_AGENT_ID, self._member_agent_id)
        span.set_attribute(AT_AGENT_NAME, self._member_name)
        span.set_attribute(AT_AGENT_ROLE, "teammate")
        span.set_attribute(AT_MEMBER_ID, self._member_name)
        span.set_attribute(AT_MEMBER_NAME, self._member_name)
        if self._team_name:
            span.set_attribute(AT_TEAM_ID, self._team_name)
            span.set_attribute(AT_TEAM_NAME, self._team_name)
        if self._session_id:
            span.set_attribute(AT_SESSION_ID, self._session_id)
            span.set_attribute(LANGFUSE_SESSION_ID, self._session_id)
        if thread_id:
            span.set_attribute("codex.thread.id", thread_id)

        messages: list[dict[str, Any]] = []
        if developer_instructions:
            messages.append(
                {
                    "role": "developer",
                    "content": redact_prompt(str(developer_instructions), config),
                },
            )
        messages.append({"role": "user", "content": safe_prompt})
        self._turn_span = span
        self._config = config
        self._thread_id = thread_id
        self._model = model
        self._native_span_count = 0
        self._rollout_span_count = 0
        self._last_native_span_at = 0.0
        self._last_rollout_event_at = 0.0
        self._rollout_turn_ended_at = 0.0
        self._native_model_events = []
        self._inferences = {}
        self._emitted_inference_ids = set()
        self._rollout_turn_id = None
        self._tool_records = {}
        self._rollout_tool_records = {}
        self._output = []
        self._reasoning = []
        self._inputs = messages
        self._response_ids = []
        self._usage = _usage(None)
        self._llm_index = 0

    def append_output(self, delta: str) -> None:
        if self._turn_span is not None and delta:
            self._output.append(delta)

    def append_reasoning(self, delta: str) -> None:
        if self._turn_span is not None and delta:
            self._reasoning.append(delta)

    def set_reasoning_fallback(self, text: str) -> None:
        if text and not self._reasoning:
            self.append_reasoning(text)

    @staticmethod
    def append_raw_response_item(_: Any) -> None:
        """Raw SDK items are not model-call boundaries."""

    def complete_model_response(
        self,
        *,
        response_id: Any = None,
        usage: Any = None,
        **_: Any,
    ) -> None:
        """Keep SDK completion data only for the final turn summary."""
        if self._turn_span is None:
            return
        if response_id is not None:
            self._response_ids.append(str(response_id))
        parsed = _usage(usage)
        if parsed["total_tokens"]:
            self._usage = parsed

    def record_model_usage(
        self,
        *,
        input_tokens: int,
        cached_input_tokens: int,
        output_tokens: int,
        reasoning_output_tokens: int,
        total_tokens: int,
        thread_total_tokens: int = 0,
    ) -> None:
        del thread_total_tokens
        self._usage = {
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_input_tokens,
            "output_tokens": output_tokens,
            "reasoning_output_tokens": reasoning_output_tokens,
            "total_tokens": total_tokens,
        }

    def record_rollout_event(self, event: dict[str, Any]) -> None:
        """Join native inference lifecycle records by ``inference_call_id``."""
        if not self._rollout_trace_enabled:
            return
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return
        event_type = str(payload.get("type") or "")
        thread_id = str(event.get("thread_id") or payload.get("thread_id") or "")
        if self._thread_id and thread_id and thread_id != self._thread_id:
            return
        event_turn_id = str(
            event.get("codex_turn_id") or payload.get("codex_turn_id") or "",
        )
        if event_type == "codex_turn_started":
            self._rollout_turn_id = event_turn_id or None
            self._last_rollout_event_at = time.monotonic()
            return
        if event_type == "codex_turn_ended":
            if self._rollout_turn_id and event_turn_id != self._rollout_turn_id:
                return
            if self._rollout_turn_id is None and event_turn_id:
                self._rollout_turn_id = event_turn_id
            now = time.monotonic()
            self._last_rollout_event_at = now
            self._rollout_turn_ended_at = now
            return
        if not event_type.startswith("inference_"):
            return
        if self._rollout_turn_id and event_turn_id != self._rollout_turn_id:
            return
        if self._rollout_turn_id is None and event_turn_id:
            self._rollout_turn_id = event_turn_id
        inference_call_id = str(payload.get("inference_call_id") or "")
        if not inference_call_id:
            return
        if event_type == "inference_started":
            self._complete_rollout_tools(event)
        record = self._inferences.setdefault(inference_call_id, {})
        if event_type == "inference_started":
            record["started"] = event
        elif event_type in {
            "inference_completed",
            "inference_failed",
            "inference_cancelled",
        }:
            record["terminal"] = event
        else:
            return
        self._last_rollout_event_at = time.monotonic()
        self._maybe_emit_rollout_inference(inference_call_id)

    def _maybe_emit_rollout_inference(self, inference_call_id: str) -> None:
        if inference_call_id in self._emitted_inference_ids:
            return
        record = self._inferences.get(inference_call_id)
        if not record or "started" not in record or "terminal" not in record:
            return
        terminal_payload = record["terminal"].get("payload")
        if not isinstance(terminal_payload, dict):
            return
        # Failed/cancelled attempts remain represented by turn diagnostics but
        # do not become successful Langfuse generations.
        if terminal_payload.get("type") != "inference_completed":
            return
        self._emit_rollout_inference(
            inference_call_id,
            record["started"],
            record["terminal"],
        )
        self._emitted_inference_ids.add(inference_call_id)

    def _emit_rollout_inference(
        self,
        inference_call_id: str,
        started: dict[str, Any],
        terminal: dict[str, Any],
    ) -> None:
        """Emit one exact generation from one rollout inference lifecycle."""
        turn_span = self._turn_span
        config = self._config
        if turn_span is None or config is None or not turn_span.is_recording():
            return
        started_payload = started.get("payload")
        terminal_payload = terminal.get("payload")
        if not isinstance(started_payload, dict) or not isinstance(terminal_payload, dict):
            return
        start_ns = int(started.get("wall_time_unix_ms") or 0) * 1_000_000
        end_ns = int(terminal.get("wall_time_unix_ms") or 0) * 1_000_000
        if start_ns <= 0 or end_ns < start_ns:
            return
        request_payload = (started.get("resolved_payloads") or {}).get("request_payload")
        response_payload = (terminal.get("resolved_payloads") or {}).get("response_payload")
        if request_payload is None or response_payload is None:
            return

        from opentelemetry import context as otel_context
        from opentelemetry.trace import (
            SpanKind,
            Status,
            StatusCode,
            set_span_in_context,
        )

        from openjiuwen.agent_teams.observability.redaction import (
            redact_completion,
            redact_prompt,
        )
        from openjiuwen.agent_teams.observability.semconv import (
            AT_MEMBER_NAME,
            AT_SESSION_ID,
            AT_TEAM_NAME,
            GEN_AI_COMPLETION,
            GEN_AI_OPERATION_NAME,
            GEN_AI_PROMPT,
            GEN_AI_PROVIDER_NAME,
            GEN_AI_REQUEST_MESSAGE_COUNT,
            GEN_AI_REQUEST_MODEL,
            GEN_AI_RESPONSE_MODEL,
            GEN_AI_SYSTEM,
            GEN_AI_TOOL_CALLS,
            GEN_AI_USAGE_CACHE_TOKENS,
            GEN_AI_USAGE_COMPLETION_TOKENS,
            GEN_AI_USAGE_PROMPT_TOKENS,
            GEN_AI_USAGE_REASONING_TOKENS,
            GEN_AI_USAGE_TOTAL_TOKENS,
            LANGFUSE_GEN_AI_COMPLETION,
            LANGFUSE_GEN_AI_PROMPT,
            LANGFUSE_OBSERVATION_INPUT,
            LANGFUSE_OBSERVATION_OUTPUT,
            LANGFUSE_OBSERVATION_TYPE,
            LANGFUSE_SESSION_ID,
        )
        from openjiuwen.agent_teams.observability.setup import get_tracer

        messages = _request_messages(request_payload)
        completion, reasoning, tool_calls = _response_parts(response_payload)
        usage = _usage(response_payload.get("token_usage"))
        model = str(started_payload.get("model") or self._model or "unknown")
        provider = str(started_payload.get("provider_name") or "openai")
        self._llm_index += 1
        span = get_tracer(_TRACER_NAME).start_span(
            name="llm.call",
            context=set_span_in_context(turn_span, otel_context.get_current()),
            kind=SpanKind.CLIENT,
            start_time=start_ns,
        )
        span.set_attribute(LANGFUSE_OBSERVATION_TYPE, "generation")
        span.set_attribute(GEN_AI_SYSTEM, "codex")
        span.set_attribute(GEN_AI_OPERATION_NAME, "chat")
        span.set_attribute(GEN_AI_PROVIDER_NAME, provider)
        span.set_attribute(GEN_AI_REQUEST_MODEL, model)
        span.set_attribute(GEN_AI_RESPONSE_MODEL, model)
        span.set_attribute(GEN_AI_REQUEST_MESSAGE_COUNT, len(messages))
        span.set_attribute("codex.observation.granularity", "rollout_inference")
        span.set_attribute("codex.model.call.observed", True)
        span.set_attribute("codex.model.call.paired", True)
        span.set_attribute("codex.model.call.boundary_exact", True)
        span.set_attribute("codex.model.call.index", self._llm_index)
        span.set_attribute("codex.inference.call_id", inference_call_id)
        span.set_attribute("codex.rollout.started_seq", int(started.get("seq") or 0))
        span.set_attribute("codex.rollout.ended_seq", int(terminal.get("seq") or 0))
        response_id = terminal_payload.get("response_id")
        if response_id:
            span.set_attribute("codex.response.id", str(response_id))
        upstream_request_id = terminal_payload.get("upstream_request_id")
        if upstream_request_id:
            span.set_attribute("codex.upstream.request_id", str(upstream_request_id))
        codex_turn_id = str(
            started.get("codex_turn_id") or started_payload.get("codex_turn_id") or "",
        )
        if codex_turn_id:
            span.set_attribute("codex.turn.id", codex_turn_id)
        if self._thread_id:
            span.set_attribute("codex.thread.id", self._thread_id)
        span.set_attribute(AT_MEMBER_NAME, self._member_name)
        if self._team_name:
            span.set_attribute(AT_TEAM_NAME, self._team_name)
        if self._session_id:
            span.set_attribute(AT_SESSION_ID, self._session_id)
            span.set_attribute(LANGFUSE_SESSION_ID, self._session_id)

        emit_standard = config.backend != "langfuse"
        attributes_per_message = 4 if emit_standard else 2
        writable_messages = max(
            (config.max_attributes - 40) // attributes_per_message,
            0,
        )
        indexed_message_count = min(_MAX_INDEXED_MESSAGES, writable_messages)
        indexed_messages = messages[-indexed_message_count:] if indexed_message_count else []
        for index, message in enumerate(indexed_messages):
            role = str(message.get("role") or "")
            content = redact_prompt(_content_text(message.get("content")), config)
            if emit_standard:
                span.set_attribute(f"{GEN_AI_PROMPT}.{index}.role", role)
                span.set_attribute(f"{GEN_AI_PROMPT}.{index}.content", content)
            span.set_attribute(f"{LANGFUSE_GEN_AI_PROMPT}.{index}.role", role)
            span.set_attribute(f"{LANGFUSE_GEN_AI_PROMPT}.{index}.content", content)
        input_json = _json_text(messages)
        span.set_attribute(
            LANGFUSE_OBSERVATION_INPUT,
            redact_prompt(input_json, config),
        )

        safe_completion = redact_completion(completion, config)
        if emit_standard:
            span.set_attribute(f"{GEN_AI_COMPLETION}.0.role", "assistant")
            span.set_attribute(f"{GEN_AI_COMPLETION}.0.content", safe_completion)
        span.set_attribute(f"{LANGFUSE_GEN_AI_COMPLETION}.0.role", "assistant")
        span.set_attribute(f"{LANGFUSE_GEN_AI_COMPLETION}.0.content", safe_completion)
        if tool_calls:
            span.set_attribute(
                GEN_AI_TOOL_CALLS,
                redact_completion(_json_text(tool_calls), config),
            )
        output_message: dict[str, Any] = {
            "role": "assistant",
            "content": completion,
        }
        if tool_calls:
            output_message["tool_calls"] = tool_calls
        output_object: dict[str, Any] = {
            "choices": [{"index": 0, "message": output_message}],
        }
        if any(usage.values()):
            output_object["usage"] = usage
        span.set_attribute(
            LANGFUSE_OBSERVATION_OUTPUT,
            redact_completion(_json_text(output_object), config),
        )
        if usage["input_tokens"]:
            span.set_attribute(GEN_AI_USAGE_PROMPT_TOKENS, usage["input_tokens"])
        if usage["cached_input_tokens"]:
            span.set_attribute(GEN_AI_USAGE_CACHE_TOKENS, usage["cached_input_tokens"])
        if usage["output_tokens"]:
            span.set_attribute(GEN_AI_USAGE_COMPLETION_TOKENS, usage["output_tokens"])
        if usage["reasoning_output_tokens"]:
            span.set_attribute(
                GEN_AI_USAGE_REASONING_TOKENS,
                usage["reasoning_output_tokens"],
            )
        if usage["total_tokens"]:
            span.set_attribute(GEN_AI_USAGE_TOTAL_TOKENS, usage["total_tokens"])

        self._record_rollout_tools(
            tool_calls,
            start_ns=end_ns,
            parent_inference_call_id=inference_call_id,
        )

        if reasoning:
            reasoning_span = get_tracer(_TRACER_NAME).start_span(
                name="llm.reasoning",
                context=set_span_in_context(span, otel_context.get_current()),
                start_time=end_ns,
            )
            safe_reasoning = redact_completion(reasoning, config)
            reasoning_span.set_attribute(LANGFUSE_OBSERVATION_INPUT, "llm reasoning")
            reasoning_span.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, safe_reasoning)
            reasoning_span.set_attribute(f"{GEN_AI_COMPLETION}.0.role", "reasoning")
            reasoning_span.set_attribute(f"{GEN_AI_COMPLETION}.0.is_reasoning", True)
            reasoning_span.set_attribute(f"{GEN_AI_COMPLETION}.0.content", safe_reasoning)
            if usage["reasoning_output_tokens"]:
                reasoning_span.set_attribute(
                    GEN_AI_USAGE_REASONING_TOKENS,
                    usage["reasoning_output_tokens"],
                )
            reasoning_span.set_status(Status(StatusCode.OK))
            reasoning_span.end(end_time=end_ns)

        span.set_status(Status(StatusCode.OK))
        span.end(end_time=end_ns)
        self._rollout_span_count += 1
        self._last_rollout_event_at = time.monotonic()

    def _record_rollout_tools(
        self,
        tool_calls: list[dict[str, Any]],
        *,
        start_ns: int,
        parent_inference_call_id: str,
    ) -> None:
        for index, tool_call in enumerate(tool_calls):
            decoded = _decode_rollout_tool(tool_call)
            keys = decoded["correlation_keys"]
            primary_key = next(iter(sorted(keys)), f"rollout-{self._llm_index}-{index}")
            record = self._rollout_tool_records.setdefault(
                primary_key,
                {
                    "call_id": primary_key,
                    "start_ns": start_ns,
                    "parent_inference_call_id": parent_inference_call_id,
                    "parent_exact": True,
                    **decoded,
                },
            )
            record["correlation_keys"].update(keys)

    def _complete_rollout_tools(self, started: dict[str, Any]) -> None:
        request_payload = (started.get("resolved_payloads") or {}).get("request_payload")
        outputs = _rollout_tool_outputs(request_payload)
        if not outputs:
            return
        end_ns = int(started.get("wall_time_unix_ms") or 0) * 1_000_000
        if end_ns <= 0:
            return
        for record in self._rollout_tool_records.values():
            if "end_ns" in record:
                continue
            keys = record.get("correlation_keys") or set()
            matched_key = next((key for key in keys if key in outputs), None)
            if matched_key is None:
                continue
            record["tool_result"] = outputs[matched_key]
            record["end_ns"] = max(int(record["start_ns"]), end_ns)

    @staticmethod
    def _rollout_tool_covered_by_sdk(
        rollout: dict[str, Any],
        sdk_call_id: str,
        sdk: dict[str, Any],
    ) -> bool:
        keys = rollout.get("correlation_keys") or set()
        if sdk_call_id in keys:
            return True
        rollout_start = int(rollout.get("start_ns") or 0)
        rollout_end = int(rollout.get("end_ns") or rollout_start)
        sdk_start = int(sdk.get("start_ns") or 0)
        sdk_end = int(sdk.get("end_ns") or sdk_start)
        tolerance = _ROLLOUT_TOOL_OVERLAP_TOLERANCE_NS
        overlaps = sdk_start <= rollout_end + tolerance and sdk_end >= rollout_start - tolerance
        if not overlaps:
            return False
        rollout_name = str(rollout.get("tool_name") or "")
        sdk_name = str(sdk.get("tool_name") or "")
        if rollout_name == "codex.tool_discovery":
            return False
        if rollout_name == "codex.internal.exec":
            return True
        rollout_leaf = _normalized_tool_leaf(rollout_name)
        sdk_leaf = _normalized_tool_leaf(sdk_name)
        if rollout_leaf == sdk_leaf:
            return True
        return rollout_name.startswith("shell.") and sdk_leaf == "shell"

    def _reconcile_rollout_tools(self) -> None:
        """Use SDK tools when present and synthesize only missing wrappers."""
        for rollout_key, rollout in self._rollout_tool_records.items():
            matched_sdk_id: str | None = None
            for sdk_call_id, sdk in self._tool_records.items():
                if self._rollout_tool_covered_by_sdk(rollout, sdk_call_id, sdk):
                    matched_sdk_id = sdk_call_id
                    break
            if matched_sdk_id is not None:
                sdk = self._tool_records[matched_sdk_id]
                sdk["parent_inference_call_id"] = rollout.get(
                    "parent_inference_call_id",
                )
                sdk["parent_exact"] = matched_sdk_id in (rollout.get("correlation_keys") or set())
                continue
            record_key = rollout_key
            if record_key in self._tool_records:
                record_key = f"rollout:{record_key}"
            self._tool_records[record_key] = {key: value for key, value in rollout.items() if key != "correlation_keys"}

    def record_native_model_span(self, event: dict[str, Any]) -> None:
        """Cache an OTel timing fallback until rollout availability is known."""
        if not self._native_trace_enabled or self._turn_span is None:
            return
        self._native_model_events.append(dict(event))
        self._last_native_span_at = time.monotonic()

    def _emit_native_model_span(self, event: dict[str, Any]) -> None:
        """Emit one content-less fallback for Codex builds without rollout."""
        turn_span = self._turn_span
        config = self._config
        if not self._native_trace_enabled:
            return
        if turn_span is None or config is None:
            return
        if not turn_span.is_recording():
            return
        start_ns = int(event.get("start_time_ns") or 0)
        end_ns = int(event.get("end_time_ns") or 0)
        if start_ns <= 0 or end_ns < start_ns:
            return
        attributes = event.get("attributes")
        if not isinstance(attributes, dict):
            attributes = {}

        from opentelemetry import context as otel_context
        from opentelemetry.trace import (
            SpanKind,
            Status,
            StatusCode,
            set_span_in_context,
        )

        from openjiuwen.agent_teams.observability.semconv import (
            AT_MEMBER_NAME,
            AT_SESSION_ID,
            AT_TEAM_NAME,
            GEN_AI_OPERATION_NAME,
            GEN_AI_PROVIDER_NAME,
            GEN_AI_REQUEST_MODEL,
            GEN_AI_SYSTEM,
            LANGFUSE_OBSERVATION_TYPE,
            LANGFUSE_SESSION_ID,
        )
        from openjiuwen.agent_teams.observability.setup import get_tracer

        self._llm_index += 1
        span = get_tracer(_TRACER_NAME).start_span(
            name="llm.call",
            context=set_span_in_context(turn_span, otel_context.get_current()),
            kind=SpanKind.CLIENT,
            start_time=start_ns,
        )
        span.set_attribute(LANGFUSE_OBSERVATION_TYPE, "generation")
        span.set_attribute(GEN_AI_SYSTEM, "codex")
        span.set_attribute(GEN_AI_OPERATION_NAME, "chat")
        span.set_attribute(GEN_AI_PROVIDER_NAME, "openai")
        span.set_attribute(
            GEN_AI_REQUEST_MODEL,
            str(attributes.get("model") or self._model or "unknown"),
        )
        span.set_attribute("codex.observation.granularity", "native_sampling_span")
        span.set_attribute("codex.llm.call.proxy", False)
        span.set_attribute("codex.model.call.observed", True)
        span.set_attribute("codex.model.call.paired", False)
        span.set_attribute("codex.model.call.index", self._llm_index)
        span.set_attribute(
            "codex.model.call.boundary",
            str(event.get("name") or "run_sampling_request"),
        )
        span.set_attribute("codex.model.call.boundary_exact", True)
        span.set_attribute("codex.model.call.start_observed", True)
        span.set_attribute("codex.native.trace_id", str(event.get("trace_id") or ""))
        span.set_attribute("codex.native.span_id", str(event.get("span_id") or ""))
        parent_span_id = str(event.get("parent_span_id") or "")
        if parent_span_id:
            span.set_attribute("codex.native.parent_span_id", parent_span_id)
        native_turn_id = attributes.get("turn_id")
        if native_turn_id:
            span.set_attribute("codex.turn.id", str(native_turn_id))
        span.set_attribute(AT_MEMBER_NAME, self._member_name)
        if self._team_name:
            span.set_attribute(AT_TEAM_NAME, self._team_name)
        if self._session_id:
            span.set_attribute(AT_SESSION_ID, self._session_id)
            span.set_attribute(LANGFUSE_SESSION_ID, self._session_id)
        if self._thread_id:
            span.set_attribute("codex.thread.id", self._thread_id)

        if int(event.get("status_code") or 0) == 2:
            description = self._redact_diagnostic(
                event.get("status_message") or "native model span failed",
            )
            span.set_status(Status(StatusCode.ERROR, description))
        else:
            span.set_status(Status(StatusCode.OK))
        span.end(end_time=end_ns)
        self._native_span_count += 1
        self._last_native_span_at = time.monotonic()

    # Compatibility names retained while callers migrate to trace terminology.
    record_native_event = record_native_model_span
    record_native_api_request = record_native_model_span

    def start_tool(
        self,
        *,
        call_id: str,
        tool_name: str,
        tool_args: Any,
        item_type: str,
        server_name: str | None = None,
    ) -> None:
        """Remember the SDK tool start until its model parent is known."""
        if self._turn_span is None or self._config is None:
            return
        record = {
            "call_id": call_id,
            "tool_name": tool_name,
            "tool_args": tool_args,
            "item_type": item_type,
            "server_name": server_name,
            "start_ns": time.time_ns(),
        }
        display_name = _sdk_tool_display_name(
            item_type=item_type,
            tool_args=tool_args,
        )
        if display_name is not None:
            record["display_name"] = display_name
        self._tool_records[call_id] = record

    def finish_tool(
        self,
        *,
        call_id: str,
        tool_name: str,
        tool_args: Any,
        tool_result: Any,
        item_type: str,
        server_name: str | None = None,
        error: Any | None = None,
    ) -> None:
        """Complete a remembered SDK tool call without guessing its parent."""
        if self._turn_span is None or self._config is None:
            return
        record = self._tool_records.setdefault(
            call_id,
            {
                "call_id": call_id,
                "tool_name": tool_name,
                "tool_args": tool_args,
                "item_type": item_type,
                "server_name": server_name,
                "start_ns": time.time_ns(),
            },
        )
        record.update(
            {
                "tool_name": tool_name,
                "tool_args": tool_args,
                "tool_result": tool_result,
                "item_type": item_type,
                "server_name": server_name,
                "error": error,
                "end_ns": time.time_ns(),
            },
        )
        display_name = _sdk_tool_display_name(
            item_type=item_type,
            tool_args=tool_args,
        )
        if display_name is not None:
            record["display_name"] = display_name

    def _emit_tool_spans(self) -> None:
        """Emit tools as turn children with explicit model-call causality."""
        turn_span = self._turn_span
        config = self._config
        if turn_span is None or config is None or not turn_span.is_recording():
            return

        from opentelemetry import context as otel_context
        from opentelemetry.trace import (
            SpanKind,
            Status,
            StatusCode,
            set_span_in_context,
        )

        from openjiuwen.agent_teams.observability.redaction import (
            redact_completion,
            redact_prompt,
        )
        from openjiuwen.agent_teams.observability.semconv import (
            AT_MEMBER_NAME,
            AT_SESSION_ID,
            AT_TEAM_NAME,
            GEN_AI_TOOL_ID,
            GEN_AI_TOOL_INPUT,
            GEN_AI_TOOL_NAME,
            GEN_AI_TOOL_OUTPUT,
            LANGFUSE_OBSERVATION_INPUT,
            LANGFUSE_OBSERVATION_OUTPUT,
            LANGFUSE_OBSERVATION_TYPE,
            LANGFUSE_SESSION_ID,
        )
        from openjiuwen.agent_teams.observability.setup import get_tracer

        tracer = get_tracer(_TRACER_NAME)
        now_ns = time.time_ns()
        for call_id, record in self._tool_records.items():
            tool_name = str(record.get("tool_name") or "")
            item_type = str(record.get("item_type") or "")
            explicit_display_name = str(record.get("display_name") or "")
            display_name = (
                explicit_display_name
                or tool_name.rsplit(".", maxsplit=1)[-1]
                or item_type
                or "unknown"
            )
            observation_tool_name = explicit_display_name or tool_name
            start_ns = int(record.get("start_ns") or now_ns)
            end_ns = max(start_ns, int(record.get("end_ns") or now_ns))
            span = tracer.start_span(
                name=f"tool.{display_name}",
                context=set_span_in_context(turn_span, otel_context.get_current()),
                kind=SpanKind.INTERNAL,
                start_time=start_ns,
            )
            safe_input = redact_prompt(_json_text(record.get("tool_args")), config)
            span.set_attribute(LANGFUSE_OBSERVATION_TYPE, "tool")
            span.set_attribute(LANGFUSE_OBSERVATION_INPUT, safe_input)
            span.set_attribute(GEN_AI_TOOL_NAME, observation_tool_name)
            if observation_tool_name != tool_name:
                span.set_attribute("codex.tool.logical_name", tool_name)
            span.set_attribute(GEN_AI_TOOL_INPUT, safe_input)
            span.set_attribute(GEN_AI_TOOL_ID, call_id)
            span.set_attribute("codex.item.type", item_type)
            parent_inference_call_id = str(
                record.get("parent_inference_call_id") or "",
            )
            if parent_inference_call_id:
                span.set_attribute(
                    "codex.tool.parent_inference_call_id",
                    parent_inference_call_id,
                )
            span.set_attribute(
                "codex.tool.parent_exact",
                bool(record.get("parent_exact", False)),
            )
            span.set_attribute(
                "codex.tool.source",
                str(record.get("source") or "sdk"),
            )
            span.set_attribute(
                "codex.tool.boundary_exact",
                bool(record.get("boundary_exact", True)),
            )
            server_name = record.get("server_name")
            if server_name:
                span.set_attribute("codex.mcp.server", str(server_name))
            span.set_attribute(AT_MEMBER_NAME, self._member_name)
            if self._team_name:
                span.set_attribute(AT_TEAM_NAME, self._team_name)
            if self._session_id:
                span.set_attribute(AT_SESSION_ID, self._session_id)
                span.set_attribute(LANGFUSE_SESSION_ID, self._session_id)

            error = record.get("error")
            completed = "end_ns" in record
            if completed:
                safe_output = redact_completion(
                    _structured_text(record.get("tool_result")),
                    config,
                )
                span.set_attribute(GEN_AI_TOOL_OUTPUT, safe_output)
                span.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, safe_output)
            if error is None and completed:
                span.set_status(Status(StatusCode.OK))
            else:
                description = self._redact_diagnostic(error) if error is not None else "incomplete tool call"
                span.set_attribute("codex.tool.error", description)
                span.set_status(Status(StatusCode.ERROR, description))
            span.end(end_time=end_ns)

    def _redact_diagnostic(self, value: Any) -> str:
        """Apply the strictest active policy to errors and diagnostics."""
        config = self._config
        text = _json_text(value)
        if config is None:
            return _truncate(text, 40960)
        from openjiuwen.agent_teams.observability.redaction import (
            redact_completion,
            redact_prompt,
        )

        if config.redact_prompts:
            return redact_prompt(text, config)
        return redact_completion(text, config)

    def record_error(self, error: Any, *, will_retry: bool = False) -> None:
        span = self._turn_span
        if span is not None and span.is_recording():
            span.add_event(
                "codex.error",
                {
                    "codex.error.detail": self._redact_diagnostic(error),
                    "codex.error.will_retry": will_retry,
                },
            )

    async def wait_for_native_observations(self, *, timeout_s: float = 1.0) -> None:
        """Allow rollout and fallback OTel exporters to flush after the stream."""
        if not (self._rollout_trace_enabled or self._native_trace_enabled) or self._turn_span is None:
            return
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        # Always allow one batch interval after the SDK stream finishes.  An
        # earlier model span may already have arrived while the final span is
        # still buffered by Codex.
        await asyncio.sleep(min(0.2, timeout_s))
        while loop.time() < deadline:
            now = time.monotonic()
            rollout_ready = not self._rollout_trace_enabled or (
                self._rollout_turn_ended_at > 0
                and self._last_rollout_event_at > 0
                and now - self._last_rollout_event_at >= _NATIVE_EXPORT_QUIET_S
            )
            native_ready = not self._native_trace_enabled or (
                self._last_native_span_at > 0 and now - self._last_native_span_at >= _NATIVE_EXPORT_QUIET_S
            )
            if rollout_ready and native_ready:
                return
            await asyncio.sleep(0.02)

    def _emit_sdk_summary(self) -> None:
        """Emit final SDK content separately from native model-call timing."""
        turn_span = self._turn_span
        config = self._config
        if turn_span is None or config is None or not turn_span.is_recording():
            return
        if not (self._output or self._reasoning or self._usage["total_tokens"]):
            return

        from opentelemetry import context as otel_context
        from opentelemetry.trace import Status, StatusCode, set_span_in_context

        from openjiuwen.agent_teams.observability.redaction import (
            redact_completion,
            redact_prompt,
        )
        from openjiuwen.agent_teams.observability.semconv import (
            GEN_AI_USAGE_COMPLETION_TOKENS,
            GEN_AI_USAGE_PROMPT_TOKENS,
            GEN_AI_USAGE_TOTAL_TOKENS,
            LANGFUSE_OBSERVATION_INPUT,
            LANGFUSE_OBSERVATION_OUTPUT,
            LANGFUSE_OBSERVATION_TYPE,
        )
        from openjiuwen.agent_teams.observability.setup import get_tracer

        tracer = get_tracer(_TRACER_NAME)
        summary = tracer.start_span(
            name="codex.sdk.summary",
            context=set_span_in_context(turn_span, otel_context.get_current()),
        )
        summary.set_attribute(LANGFUSE_OBSERVATION_TYPE, "span")
        summary.set_attribute(
            LANGFUSE_OBSERVATION_INPUT,
            redact_prompt(_json_text(self._inputs), config),
        )
        output = redact_completion("".join(self._output), config)
        summary.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, output)
        if self._response_ids:
            summary.set_attribute("codex.response.ids", self._response_ids)
        if self._usage["total_tokens"]:
            summary.set_attribute(
                GEN_AI_USAGE_PROMPT_TOKENS,
                self._usage["input_tokens"],
            )
            summary.set_attribute(
                GEN_AI_USAGE_COMPLETION_TOKENS,
                self._usage["output_tokens"],
            )
            summary.set_attribute(
                GEN_AI_USAGE_TOTAL_TOKENS,
                self._usage["total_tokens"],
            )
        reasoning = redact_completion("".join(self._reasoning), config)
        if reasoning:
            reasoning_span = tracer.start_span(
                name="llm.reasoning",
                context=set_span_in_context(summary, otel_context.get_current()),
            )
            reasoning_span.set_attribute(LANGFUSE_OBSERVATION_INPUT, "SDK reasoning summary")
            reasoning_span.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, reasoning)
            reasoning_span.set_status(Status(StatusCode.OK))
            reasoning_span.end()
        summary.set_status(Status(StatusCode.OK))
        summary.end()

    def finish_turn(self, *, status: str, error: Any | None = None) -> None:
        span = self._turn_span
        if span is None:
            return

        from opentelemetry.trace import Status, StatusCode

        from openjiuwen.agent_teams.observability.redaction import redact_completion
        from openjiuwen.agent_teams.observability.semconv import (
            AT_AGENT_OUTPUT,
            LANGFUSE_OBSERVATION_OUTPUT,
        )

        for inference_call_id in tuple(self._inferences):
            self._maybe_emit_rollout_inference(inference_call_id)
        if self._rollout_span_count == 0:
            for event in self._native_model_events:
                self._emit_native_model_span(event)
            self._emit_sdk_summary()
        self._reconcile_rollout_tools()
        self._emit_tool_spans()
        if self._config is not None and span.is_recording():
            output = redact_completion("".join(self._output), self._config)
            span.set_attribute(AT_AGENT_OUTPUT, output)
            span.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, output)
            span.set_attribute("codex.turn.status", status)
            span.set_attribute(
                "codex.rollout.model_span_count",
                self._rollout_span_count,
            )
            span.set_attribute("codex.native.model_span_count", self._native_span_count)
            if error is not None or status in {"failed", "cancelled"}:
                description = self._redact_diagnostic(error) if error is not None else status
                span.set_status(Status(StatusCode.ERROR, description))
            else:
                span.set_status(Status(StatusCode.OK))
            span.end()
        self._turn_span = None
        self._config = None
        self._native_model_events = []
        self._inferences = {}
        self._emitted_inference_ids = set()
        self._rollout_turn_id = None
        self._rollout_turn_ended_at = 0.0
        self._tool_records = {}
        self._rollout_tool_records = {}
        self._output = []
        self._reasoning = []
        self._inputs = []
        self._response_ids = []

    def close_member_session(self) -> None:
        self.finish_turn(status="cancelled")

    @staticmethod
    def _observability_runtime() -> tuple[Any, Any, Any] | None:
        try:
            from openjiuwen.agent_teams.observability.setup import (
                get_config,
                get_tracer,
                is_initialized,
            )
            from openjiuwen.agent_teams.observability.span_context import get_team_span
        except ImportError:
            return None
        if not is_initialized():
            return None
        config = get_config()
        team_span = get_team_span()
        if config is None or team_span is None or not team_span.is_recording():
            return None
        return get_tracer(_TRACER_NAME), config, team_span


__all__ = ["CodexSpanBridge"]
