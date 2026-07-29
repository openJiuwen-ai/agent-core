# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pair Codex API-request logs with SDK responses and emit Jiuwen spans."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

_TRACER_NAME = "openjiuwen.agent_teams.observability.codex"


def _json_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


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


def _is_model_request(event_name: str, attributes: dict[str, Any]) -> bool:
    if event_name == "codex.websocket_request":
        return True
    endpoint = str(attributes.get("endpoint") or "").strip().lower()
    if not endpoint or endpoint == "unknown":
        return True
    endpoint = endpoint.split("?", maxsplit=1)[0].rstrip("/")
    return endpoint == "responses" or endpoint.endswith("/responses")


class CodexSpanBridge:
    """Create historical model spans and live SDK tool spans for one member."""

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
        self._native_api_timing = False
        self._requests: list[dict[str, Any]] = []
        self._responses: list[dict[str, Any]] = []
        self._tool_spans: dict[str, Any] = {}
        self._output: list[str] = []
        self._reasoning: list[str] = []
        self._turn_output: list[str] = []
        self._inputs: list[dict[str, Any]] = []
        self._next_inputs: list[dict[str, Any]] = []
        self._response_start_ns = 0
        self._usage = _usage(None)
        self._llm_index = 0
        self._captured_runtime = self._observability_runtime()

    def enable_native_api_timing(self) -> None:
        self._native_api_timing = True

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
        self._requests = []
        self._responses = []
        self._tool_spans = {}
        self._output = []
        self._reasoning = []
        self._turn_output = []
        self._inputs = messages
        self._next_inputs = []
        self._response_start_ns = time.time_ns()
        self._usage = _usage(None)
        self._llm_index = 0

    def _begin_response(self) -> None:
        if self._inputs:
            return
        self._inputs = self._next_inputs or [
            {"role": "user", "content": "Codex continuation"},
        ]
        self._next_inputs = []
        self._response_start_ns = time.time_ns()

    def append_output(self, delta: str) -> None:
        if self._turn_span is not None and delta:
            self._begin_response()
            self._output.append(delta)
            self._turn_output.append(delta)

    def append_reasoning(self, delta: str) -> None:
        if self._turn_span is not None and delta:
            self._begin_response()
            self._reasoning.append(delta)

    def set_reasoning_fallback(self, text: str) -> None:
        if text and not self._reasoning:
            self.append_reasoning(text)

    def append_raw_response_item(self, _: Any) -> None:
        """Do not export raw response objects; deltas and usage are sufficient."""

    def complete_model_response(
        self,
        *,
        response_id: Any = None,
        usage: Any = None,
        **_: Any,
    ) -> None:
        if self._turn_span is None:
            return
        self._usage = _usage(usage)
        self._queue_response(response_id=response_id, boundary="rawResponse/completed")

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
        self._begin_response()
        self._usage = {
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_input_tokens,
            "output_tokens": output_tokens,
            "reasoning_output_tokens": reasoning_output_tokens,
            "total_tokens": total_tokens,
        }

    def _response_populated(self) -> bool:
        return bool(self._output or self._reasoning or self._usage["total_tokens"])

    def _queue_response(
        self,
        *,
        response_id: Any,
        boundary: str,
        only_when_populated: bool = False,
    ) -> None:
        if self._turn_span is None:
            return
        if only_when_populated and not self._response_populated():
            return
        self._begin_response()
        self._responses.append(
            {
                "response_id": response_id,
                "boundary": boundary,
                "observed_at_ns": time.time_ns(),
                "fallback_start_ns": self._response_start_ns,
                "inputs": [dict(item) for item in self._inputs],
                "output": list(self._output),
                "reasoning": list(self._reasoning),
                "usage": dict(self._usage),
            },
        )
        self._inputs = []
        self._response_start_ns = 0
        self._output = []
        self._reasoning = []
        self._usage = _usage(None)

    def record_native_event(self, event: dict[str, Any]) -> None:
        if not self._native_api_timing or self._turn_span is None:
            return
        attributes = event.get("attributes")
        if not isinstance(attributes, dict):
            return
        if not _is_model_request(str(event.get("name") or ""), attributes):
            return
        conversation_id = attributes.get("conversation.id")
        if conversation_id and self._thread_id and str(conversation_id) != self._thread_id:
            return
        success = attributes.get("success")
        if isinstance(success, str):
            success = success.lower() == "true"
        if success is False:
            return
        end_ns = int(event.get("timestamp_ns") or time.time_ns())
        try:
            duration_ms = float(attributes.get("duration_ms") or 0)
        except (TypeError, ValueError):
            duration_ms = 0
        request = dict(event)
        request["attributes"] = dict(attributes)
        request["end_ns"] = end_ns
        request["start_ns"] = max(0, end_ns - int(duration_ms * 1_000_000))
        request["duration_ms"] = duration_ms
        self._requests.append(request)

    record_native_api_request = record_native_event

    def start_tool(
        self,
        *,
        call_id: str,
        tool_name: str,
        tool_args: Any,
        item_type: str,
        server_name: str | None = None,
    ) -> None:
        self._queue_response(
            response_id=None,
            boundary="item/started",
            only_when_populated=True,
        )
        turn_span = self._turn_span
        config = self._config
        if turn_span is None or config is None or not turn_span.is_recording():
            return

        from opentelemetry import context as otel_context
        from opentelemetry.trace import SpanKind, set_span_in_context

        from openjiuwen.agent_teams.observability.redaction import redact_prompt
        from openjiuwen.agent_teams.observability.semconv import (
            AT_MEMBER_NAME,
            AT_SESSION_ID,
            AT_TEAM_NAME,
            GEN_AI_TOOL_ID,
            GEN_AI_TOOL_INPUT,
            GEN_AI_TOOL_NAME,
            LANGFUSE_OBSERVATION_INPUT,
            LANGFUSE_OBSERVATION_TYPE,
            LANGFUSE_SESSION_ID,
        )
        from openjiuwen.agent_teams.observability.setup import get_tracer

        display_name = tool_name.rsplit(".", maxsplit=1)[-1] or item_type or "unknown"
        span = get_tracer(_TRACER_NAME).start_span(
            name=f"tool.{display_name}",
            context=set_span_in_context(turn_span, otel_context.get_current()),
            kind=SpanKind.INTERNAL,
        )
        safe_input = redact_prompt(_json_text(tool_args), config)
        span.set_attribute(LANGFUSE_OBSERVATION_TYPE, "tool")
        span.set_attribute(LANGFUSE_OBSERVATION_INPUT, safe_input)
        span.set_attribute(GEN_AI_TOOL_NAME, tool_name)
        span.set_attribute(GEN_AI_TOOL_INPUT, safe_input)
        span.set_attribute(GEN_AI_TOOL_ID, call_id)
        span.set_attribute("codex.item.type", item_type)
        if server_name:
            span.set_attribute("codex.mcp.server", server_name)
        span.set_attribute(AT_MEMBER_NAME, self._member_name)
        if self._team_name:
            span.set_attribute(AT_TEAM_NAME, self._team_name)
        if self._session_id:
            span.set_attribute(AT_SESSION_ID, self._session_id)
            span.set_attribute(LANGFUSE_SESSION_ID, self._session_id)
        self._tool_spans[call_id] = span

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
        del tool_args, item_type, server_name
        span = self._tool_spans.pop(call_id, None)
        if span is not None and span.is_recording():
            from opentelemetry.trace import Status, StatusCode

            from openjiuwen.agent_teams.observability.redaction import redact_completion
            from openjiuwen.agent_teams.observability.semconv import (
                GEN_AI_TOOL_OUTPUT,
                LANGFUSE_OBSERVATION_OUTPUT,
            )

            safe_output = redact_completion(_json_text(tool_result), self._config)
            span.set_attribute(GEN_AI_TOOL_OUTPUT, safe_output)
            span.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, safe_output)
            if error is None:
                span.set_status(Status(StatusCode.OK))
            else:
                description = _json_text(error)
                span.set_attribute("codex.tool.error", description)
                span.set_status(Status(StatusCode.ERROR, description))
            span.end()
        self._next_inputs.append(
            {
                "role": "tool",
                "name": tool_name,
                "content": tool_result if error is None else error,
            },
        )
        self._response_start_ns = time.time_ns()

    def record_error(self, error: Any, *, will_retry: bool = False) -> None:
        span = self._turn_span
        if span is not None and span.is_recording():
            span.add_event(
                "codex.error",
                {
                    "codex.error.detail": _json_text(error),
                    "codex.error.will_retry": will_retry,
                },
            )

    async def wait_for_native_observations(self, *, timeout_s: float = 1.0) -> None:
        if not self._native_api_timing or self._turn_span is None:
            return
        if self._response_populated():
            self._queue_response(response_id=None, boundary="turn/completed")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while len(self._requests) < len(self._responses) and loop.time() < deadline:
            await asyncio.sleep(0.01)
        self._pair()

    def _pair(self) -> None:
        """Pair requests and responses by nearest local completion timestamp."""
        while self._requests and self._responses:
            request_index, response_index = min(
                (
                    (request_index, response_index)
                    for request_index in range(len(self._requests))
                    for response_index in range(len(self._responses))
                ),
                key=lambda pair: abs(
                    int(self._requests[pair[0]].get("end_ns") or 0)
                    - int(self._responses[pair[1]].get("observed_at_ns") or 0),
                ),
            )
            self._emit_llm(
                self._requests.pop(request_index),
                self._responses.pop(response_index),
            )

    def _emit_llm(
        self,
        request: dict[str, Any],
        response: dict[str, Any],
    ) -> None:
        turn_span = self._turn_span
        config = self._config
        if turn_span is None or config is None or not turn_span.is_recording():
            return

        from opentelemetry import context as otel_context
        from opentelemetry.trace import SpanKind, Status, StatusCode, set_span_in_context

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
            GEN_AI_SYSTEM,
            GEN_AI_USAGE_COMPLETION_TOKENS,
            GEN_AI_USAGE_PROMPT_TOKENS,
            GEN_AI_USAGE_TOTAL_TOKENS,
            LANGFUSE_GEN_AI_COMPLETION,
            LANGFUSE_GEN_AI_PROMPT,
            LANGFUSE_OBSERVATION_INPUT,
            LANGFUSE_OBSERVATION_OUTPUT,
            LANGFUSE_OBSERVATION_TYPE,
            LANGFUSE_SESSION_ID,
        )
        from openjiuwen.agent_teams.observability.setup import get_tracer

        attributes = request["attributes"]
        event_name = str(request.get("name") or "codex.api_request")
        observed = event_name != "sdk.response"
        start_ns = int(request["start_ns"])
        end_ns = max(start_ns, int(request["end_ns"]))
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
        span.set_attribute(
            "codex.observation.granularity",
            "api_request" if observed else "response",
        )
        span.set_attribute("codex.llm.call.proxy", False)
        span.set_attribute("codex.model.call.observed", observed)
        span.set_attribute("codex.model.call.paired", observed)
        span.set_attribute("codex.model.call.index", self._llm_index)
        span.set_attribute("codex.model.call.boundary", event_name)
        span.set_attribute("codex.model.call.boundary_exact", observed)
        span.set_attribute("codex.model.call.start_observed", observed)
        if observed:
            span.set_attribute(
                f"{event_name}.duration_ms",
                request["duration_ms"],
            )
        span.set_attribute(AT_MEMBER_NAME, self._member_name)
        if self._team_name:
            span.set_attribute(AT_TEAM_NAME, self._team_name)
        if self._session_id:
            span.set_attribute(AT_SESSION_ID, self._session_id)
            span.set_attribute(LANGFUSE_SESSION_ID, self._session_id)
        if self._thread_id:
            span.set_attribute("codex.thread.id", self._thread_id)

        safe_messages = [
            {
                key: redact_prompt(_json_text(value), config) if key == "content" else str(value)
                for key, value in message.items()
            }
            for message in response["inputs"]
        ]
        limit = max(config.attribute_value_max_length * 10, 81920)
        span.set_attribute(GEN_AI_REQUEST_MESSAGE_COUNT, len(safe_messages))
        span.set_attribute(
            LANGFUSE_OBSERVATION_INPUT,
            _truncate(_json_text(safe_messages), limit),
        )
        for index, message in enumerate(safe_messages):
            role = message.get("role", "")
            content = message.get("content", "")
            span.set_attribute(f"{GEN_AI_PROMPT}.{index}.role", role)
            span.set_attribute(f"{GEN_AI_PROMPT}.{index}.content", content)
            span.set_attribute(f"{LANGFUSE_GEN_AI_PROMPT}.{index}.role", role)
            span.set_attribute(f"{LANGFUSE_GEN_AI_PROMPT}.{index}.content", content)

        output = redact_completion("".join(response["output"]), config)
        reasoning = redact_completion("".join(response["reasoning"]), config)
        span.set_attribute(f"{GEN_AI_COMPLETION}.0.role", "assistant")
        span.set_attribute(f"{GEN_AI_COMPLETION}.0.content", output)
        span.set_attribute(f"{LANGFUSE_GEN_AI_COMPLETION}.0.role", "assistant")
        span.set_attribute(f"{LANGFUSE_GEN_AI_COMPLETION}.0.content", output)
        usage = response["usage"]
        if usage["total_tokens"]:
            span.set_attribute(GEN_AI_USAGE_PROMPT_TOKENS, usage["input_tokens"])
            span.set_attribute(GEN_AI_USAGE_COMPLETION_TOKENS, usage["output_tokens"])
            span.set_attribute(GEN_AI_USAGE_TOTAL_TOKENS, usage["total_tokens"])
        span.set_attribute(
            LANGFUSE_OBSERVATION_OUTPUT,
            _truncate(
                _json_text(
                    {
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": output},
                            },
                        ],
                        "usage": usage,
                    },
                ),
                limit,
            ),
        )
        if response["response_id"] is not None:
            span.set_attribute("codex.response.id", str(response["response_id"]))
        if reasoning:
            reasoning_span = get_tracer(_TRACER_NAME).start_span(
                name="llm.reasoning",
                context=set_span_in_context(span, otel_context.get_current()),
                start_time=max(start_ns, end_ns - 1),
            )
            reasoning_span.set_attribute(LANGFUSE_OBSERVATION_INPUT, "llm reasoning")
            reasoning_span.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, reasoning)
            reasoning_span.set_status(Status(StatusCode.OK))
            reasoning_span.end(end_time=end_ns)
        span.set_status(Status(StatusCode.OK))
        span.end(end_time=end_ns)

    def finish_turn(self, *, status: str, error: Any | None = None) -> None:
        span = self._turn_span
        if span is None:
            return
        llm_index_before_finish = self._llm_index

        from opentelemetry.trace import Status, StatusCode

        from openjiuwen.agent_teams.observability.redaction import redact_completion
        from openjiuwen.agent_teams.observability.semconv import (
            AT_AGENT_OUTPUT,
            LANGFUSE_OBSERVATION_OUTPUT,
        )

        if self._response_populated():
            self._queue_response(response_id=None, boundary="turn/completed")
        if self._native_api_timing:
            self._pair()
            # Never drop a real, successful API request merely because the SDK
            # did not expose a matching response boundary (for example when
            # the App Server transport closes after completing the request).
            while self._requests:
                request = self._requests.pop(0)
                self._emit_llm(
                    request,
                    self._fallback_response(
                        observed_at_ns=int(request["end_ns"]),
                    ),
                )
        # SDK response notifications are still valuable when native OTel
        # delivery is unavailable or late.  Emit them as explicitly inferred
        # spans rather than leaving the whole Codex turn without llm.call.
        while self._responses:
            response = self._responses.pop(0)
            end_ns = int(response["observed_at_ns"])
            start_ns = int(response["fallback_start_ns"] or end_ns)
            self._emit_llm(
                {
                    "name": "sdk.response",
                    "attributes": {},
                    "start_ns": min(start_ns, end_ns),
                    "end_ns": end_ns,
                    "duration_ms": max(0, end_ns - start_ns) / 1_000_000,
                },
                response,
            )
        if self._llm_index == llm_index_before_finish:
            # Last-resort visibility for an App Server that closes before
            # exporting either its API-request log or an SDK response boundary.
            # A real codex.api_request always wins; this span is explicitly
            # marked ``response`` rather than pretending to be observed.
            end_ns = time.time_ns()
            start_ns = int(self._response_start_ns or end_ns)
            response = self._fallback_response(observed_at_ns=end_ns)
            response["output"] = list(self._output)
            response["reasoning"] = list(self._reasoning)
            response["usage"] = dict(self._usage)
            self._emit_llm(
                {
                    "name": "sdk.response",
                    "attributes": {},
                    "start_ns": min(start_ns, end_ns),
                    "end_ns": end_ns,
                    "duration_ms": max(0, end_ns - start_ns) / 1_000_000,
                },
                response,
            )
        for tool_span in self._tool_spans.values():
            if tool_span.is_recording():
                tool_span.set_status(Status(StatusCode.ERROR, "incomplete tool call"))
                tool_span.end()
        if self._config is not None and span.is_recording():
            output = redact_completion("".join(self._turn_output), self._config)
            span.set_attribute(AT_AGENT_OUTPUT, output)
            span.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, output)
            span.set_attribute("codex.turn.status", status)
            if error is not None or status in {"failed", "cancelled"}:
                description = _json_text(error) if error is not None else status
                span.set_status(Status(StatusCode.ERROR, description))
            else:
                span.set_status(Status(StatusCode.OK))
            span.end()
        self._turn_span = None
        self._config = None
        self._requests = []
        self._responses = []
        self._tool_spans = {}
        self._output = []
        self._reasoning = []
        self._turn_output = []
        self._inputs = []
        self._next_inputs = []

    def _fallback_response(self, *, observed_at_ns: int) -> dict[str, Any]:
        """Build an empty SDK-side response for an otherwise real API call."""
        inputs = self._inputs or self._next_inputs or [
            {"role": "user", "content": "Codex continuation"},
        ]
        return {
            "response_id": None,
            "boundary": "codex.api_request",
            "observed_at_ns": observed_at_ns,
            "fallback_start_ns": observed_at_ns,
            "inputs": [dict(item) for item in inputs],
            "output": [],
            "reasoning": [],
            "usage": _usage(None),
        }

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
