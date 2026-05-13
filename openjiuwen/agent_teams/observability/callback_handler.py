# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""OpenTelemetry handlers for AsyncCallbackFramework events.

Each handler converts one framework event into OTel span operations.
All exceptions are caught and logged: observability code must never
break the business path.

Notes on parameter shapes (verified against trigger sites):
    - LLM events: triggered by emit_before/emit_after wrappers around
      ``BaseModelClient.invoke`` and ``stream``. ``messages`` /
      ``temperature`` / ``top_p`` / ``model`` arrive as kwargs;
      ``model_config`` / ``model_client_config`` arrive as extra_kwargs;
      ``result`` carries the AssistantMessage(Chunk) on output events.
    - Tool events: triggered explicitly with ``tool_name`` / ``tool_id``
      / ``inputs=(args, kwargs)`` / ``result`` / ``error``.
    - Agent events: triggered by emit_before/emit_after around
      ``BaseAgent.invoke``; ``inputs`` is positional (args[0]),
      ``session`` is a kwarg, ``result`` is on output.
"""

from __future__ import annotations

import json
import time
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry.trace import (
    SpanKind,
    Status,
    StatusCode,
    Tracer,
    set_span_in_context,
)

from openjiuwen.agent_teams.observability.config import ObservabilityConfig
from openjiuwen.agent_teams.observability.redaction import (
    redact_completion,
    redact_prompt,
)
from openjiuwen.agent_teams.observability.semconv import (
    AT_AGENT_ID,
    AT_AGENT_INPUT,
    AT_AGENT_OUTPUT,
    AT_AGENT_ROLE,
    GEN_AI_COMPLETION,
    GEN_AI_PROMPT,
    GEN_AI_REQUEST_MAX_TOKENS,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_REQUEST_TEMPERATURE,
    GEN_AI_REQUEST_TOP_P,
    GEN_AI_RESPONSE_FINISH_REASON,
    GEN_AI_RESPONSE_MODEL,
    GEN_AI_RESPONSE_TTFT_MS,
    GEN_AI_SYSTEM,
    GEN_AI_TOOL_INPUT,
    GEN_AI_TOOL_NAME,
    GEN_AI_TOOL_OUTPUT,
    GEN_AI_USAGE_COMPLETION_TOKENS,
    GEN_AI_USAGE_PROMPT_TOKENS,
    GEN_AI_USAGE_TOTAL_TOKENS,
)
from openjiuwen.agent_teams.observability.span_context import (
    LlmSpanState,
    pop_agent_span,
    pop_llm_span_state,
    pop_tool_span,
    push_agent_span,
    push_llm_span_state,
    push_tool_span,
)
from openjiuwen.core.common.logging import team_logger


_TRACER_NAME = "openjiuwen.agent_teams.observability"
_GEN_AI_SYSTEM_VALUE = "openjiuwen"


def _coerce_message_content(content: Any) -> str:
    """Coerce BaseMessage.content (str or list) into a flat string for attribute storage."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(content)


def _message_role(msg: Any) -> str:
    """Extract role from a BaseMessage instance or dict."""
    if isinstance(msg, dict):
        return str(msg.get("role", ""))
    return str(getattr(msg, "role", ""))


def _message_content(msg: Any) -> Any:
    """Extract content from a BaseMessage instance or dict."""
    if isinstance(msg, dict):
        return msg.get("content", "")
    return getattr(msg, "content", "")


class OtelCallbackHandler:
    """Bundle of async callback handlers that emit OTel spans / events.

    Designed to be registered against ``AsyncCallbackFramework`` once at
    process startup. The handler holds the active ``ObservabilityConfig``
    so every callback can apply the same redaction policy.
    """

    def __init__(
        self,
        config: ObservabilityConfig,
        *,
        tracer: Tracer | None = None,
    ) -> None:
        """Bind the handler to runtime configuration.

        Args:
            config: Active observability configuration.
            tracer: Optional explicit Tracer. When omitted the handler
                resolves the active observability tracer at call time
                so re-init in tests is reflected immediately.
        """
        self._config = config
        self._injected_tracer = tracer

    def _tracer(self) -> Tracer:
        """Resolve the tracer lazily to follow the active provider."""
        if self._injected_tracer is not None:
            return self._injected_tracer
        from openjiuwen.agent_teams.observability.setup import get_tracer

        return get_tracer(_TRACER_NAME)

    # ------------------------------------------------------------------
    # LLM
    # ------------------------------------------------------------------

    async def on_llm_invoke_input(self, *args: Any, **kwargs: Any) -> None:
        """Open an LLM span and attach prompts as attributes."""
        try:
            self._open_llm_span(kwargs)
        except Exception as exc:
            team_logger.warning("otel: on_llm_invoke_input failed: {}", exc)

    async def on_llm_stream_input(self, *args: Any, **kwargs: Any) -> None:
        """Open an LLM span for a streaming call (mirrors invoke_input)."""
        try:
            self._open_llm_span(kwargs)
        except Exception as exc:
            team_logger.warning("otel: on_llm_stream_input failed: {}", exc)

    async def on_llm_stream_output(self, *args: Any, **kwargs: Any) -> None:
        """Record per-chunk span event; on the first chunk record TTFT.

        The chunk text itself is not stored per-chunk (cardinality
        blowup); the merged AssistantMessage is delivered later through
        emit_after's transform_io path or accumulated client-side.
        """
        try:
            state = pop_llm_span_state(peek=True)
            if state is None:
                return
            chunk = kwargs.get("result")
            seq = state.next_chunk_seq()
            if state.first_chunk_ns is None:
                state.first_chunk_ns = time.monotonic_ns()
                ttft_ms = (state.first_chunk_ns - state.start_ns) / 1_000_000.0
                state.span.set_attribute(GEN_AI_RESPONSE_TTFT_MS, ttft_ms)
            delta = _coerce_message_content(_message_content(chunk))
            state.span.add_event(
                name="llm.chunk",
                attributes={
                    "seq": seq,
                    "delta_chars": len(delta),
                },
            )
            # When the chunk carries terminal usage / finish_reason we record
            # them so streaming spans get the same closing attributes as invoke.
            self._maybe_record_response_attrs(state, chunk)
        except Exception as exc:
            team_logger.warning("otel: on_llm_stream_output failed: {}", exc)

    async def on_llm_invoke_output(self, *args: Any, **kwargs: Any) -> None:
        """Close the LLM span; emit a child reasoning span if present."""
        try:
            state = pop_llm_span_state()
            if state is None:
                return
            response = kwargs.get("result")
            self._close_llm_span(state, response)
        except Exception as exc:
            team_logger.warning("otel: on_llm_invoke_output failed: {}", exc)

    async def on_llm_call_error(self, *args: Any, **kwargs: Any) -> None:
        """Mark the LLM span as ERROR and close it."""
        try:
            state = pop_llm_span_state()
            if state is None:
                return
            exc = kwargs.get("error") or kwargs.get("exception")
            if isinstance(exc, BaseException):
                state.span.record_exception(exc)
                state.span.set_status(Status(StatusCode.ERROR, str(exc)))
            else:
                state.span.set_status(Status(StatusCode.ERROR, "llm call error"))
            state.span.end()
            if state.context_token is not None:
                otel_context.detach(state.context_token)
        except Exception as exc:
            team_logger.warning("otel: on_llm_call_error failed: {}", exc)

    # ------------------------------------------------------------------
    # Tool
    # ------------------------------------------------------------------

    async def on_tool_call_started(self, *args: Any, **kwargs: Any) -> None:
        """Open a tool span keyed by tool_name."""
        try:
            tool_name = str(kwargs.get("tool_name") or "unknown")
            tool_id = kwargs.get("tool_id")
            inputs = kwargs.get("inputs")
            span = self._tracer().start_span(name=f"tool.{tool_name}", kind=SpanKind.INTERNAL)
            span.set_attribute(GEN_AI_TOOL_NAME, tool_name)
            if tool_id is not None:
                span.set_attribute("gen_ai.tool.id", str(tool_id))
            span.set_attribute(
                GEN_AI_TOOL_INPUT,
                redact_prompt(self._serialize_tool_inputs(inputs), self._config),
            )
            push_tool_span(tool_name, span)
        except Exception as exc:
            team_logger.warning("otel: on_tool_call_started failed: {}", exc)

    async def on_tool_call_finished(self, *args: Any, **kwargs: Any) -> None:
        """Attach result and close the tool span."""
        try:
            tool_name = str(kwargs.get("tool_name") or "unknown")
            result = kwargs.get("result")
            span = pop_tool_span(tool_name)
            if span is None:
                return
            span.set_attribute(GEN_AI_TOOL_OUTPUT, redact_completion(result, self._config))
            span.set_status(Status(StatusCode.OK))
            span.end()
        except Exception as exc:
            team_logger.warning("otel: on_tool_call_finished failed: {}", exc)

    async def on_tool_call_error(self, *args: Any, **kwargs: Any) -> None:
        """Mark and close the tool span on exception."""
        try:
            tool_name = str(kwargs.get("tool_name") or "unknown")
            exc = kwargs.get("error") or kwargs.get("exception")
            span = pop_tool_span(tool_name)
            if span is None:
                return
            if isinstance(exc, BaseException):
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
            else:
                span.set_status(Status(StatusCode.ERROR, "tool call error"))
            span.end()
        except Exception as exc:
            team_logger.warning("otel: on_tool_call_error failed: {}", exc)

    # ------------------------------------------------------------------
    # Agent
    # ------------------------------------------------------------------

    async def on_agent_invoke_input(self, *args: Any, **kwargs: Any) -> None:
        """Open an agent span; positions later LLM/Tool spans under it."""
        try:
            inputs = self._extract_agent_inputs(args, kwargs)
            agent_id, role, query = self._unpack_agent_inputs(inputs)
            span = self._tracer().start_span(name=f"agent.{agent_id}", kind=SpanKind.INTERNAL)
            span.set_attribute(AT_AGENT_ID, agent_id)
            if role:
                span.set_attribute(AT_AGENT_ROLE, role)
            if query:
                span.set_attribute(AT_AGENT_INPUT, redact_prompt(query, self._config))
            push_agent_span(agent_id, span)
        except Exception as exc:
            team_logger.warning("otel: on_agent_invoke_input failed: {}", exc)

    async def on_agent_invoke_output(self, *args: Any, **kwargs: Any) -> None:
        """Close the agent span with output."""
        try:
            inputs = self._extract_agent_inputs(args, kwargs)
            agent_id, _, _ = self._unpack_agent_inputs(inputs)
            output = kwargs.get("result")
            span = pop_agent_span(agent_id)
            if span is None:
                return
            span.set_attribute(AT_AGENT_OUTPUT, redact_completion(output, self._config))
            span.set_status(Status(StatusCode.OK))
            span.end()
        except Exception as exc:
            team_logger.warning("otel: on_agent_invoke_output failed: {}", exc)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _open_llm_span(self, kwargs: dict[str, Any]) -> None:
        """Shared logic for opening an LLM span from invoke/stream input event."""
        messages = kwargs.get("messages") or []
        model_name = kwargs.get("model") or self._derive_model_name(kwargs) or "unknown"

        span = self._tracer().start_span(name="llm.call", kind=SpanKind.CLIENT)
        span.set_attribute(GEN_AI_SYSTEM, _GEN_AI_SYSTEM_VALUE)
        span.set_attribute(GEN_AI_REQUEST_MODEL, str(model_name))

        for src_key, attr_key, caster in (
            ("temperature", GEN_AI_REQUEST_TEMPERATURE, float),
            ("top_p", GEN_AI_REQUEST_TOP_P, float),
            ("max_tokens", GEN_AI_REQUEST_MAX_TOKENS, int),
        ):
            value = kwargs.get(src_key)
            if value is not None:
                try:
                    span.set_attribute(attr_key, caster(value))
                except (TypeError, ValueError):
                    pass

        for i, msg in enumerate(messages):
            role = _message_role(msg)
            content = _coerce_message_content(_message_content(msg))
            span.set_attribute(f"{GEN_AI_PROMPT}.{i}.role", role)
            span.set_attribute(
                f"{GEN_AI_PROMPT}.{i}.content",
                redact_prompt(content, self._config),
            )

        # Attach the new span as the current OTel context so any spans
        # opened by downstream callbacks (Tool, nested LLM) inherit it
        # as parent. Token is stashed on the state for paired detach.
        token = otel_context.attach(set_span_in_context(span))
        push_llm_span_state(
            LlmSpanState(span=span, start_ns=time.monotonic_ns(), context_token=token),
        )

    def _close_llm_span(self, state: LlmSpanState, response: Any) -> None:
        """Write completion attributes, optionally split reasoning, and close."""
        completion_text = _coerce_message_content(_message_content(response))
        reasoning_text = str(getattr(response, "reasoning_content", "") or "")

        self._maybe_record_response_attrs(state, response)

        state.span.set_attribute(f"{GEN_AI_COMPLETION}.0.role", "assistant")
        state.span.set_attribute(
            f"{GEN_AI_COMPLETION}.0.content",
            redact_completion(completion_text, self._config),
        )

        if reasoning_text:
            with self._tracer().start_as_current_span(
                name="llm.reasoning",
                context=set_span_in_context(state.span),
            ) as reasoning_span:
                reasoning_span.set_attribute(f"{GEN_AI_COMPLETION}.0.role", "reasoning")
                reasoning_span.set_attribute(f"{GEN_AI_COMPLETION}.0.is_reasoning", True)
                reasoning_span.set_attribute(
                    f"{GEN_AI_COMPLETION}.0.content",
                    redact_completion(reasoning_text, self._config),
                )

        state.span.set_status(Status(StatusCode.OK))
        state.span.end()
        if state.context_token is not None:
            otel_context.detach(state.context_token)

    @staticmethod
    def _maybe_record_response_attrs(state: LlmSpanState, response: Any) -> None:
        """Write usage / finish_reason / model attributes if present on the response."""
        if response is None:
            return
        usage = getattr(response, "usage_metadata", None)
        if usage is not None:
            for src_attr, dst_attr in (
                ("input_tokens", GEN_AI_USAGE_PROMPT_TOKENS),
                ("output_tokens", GEN_AI_USAGE_COMPLETION_TOKENS),
                ("total_tokens", GEN_AI_USAGE_TOTAL_TOKENS),
            ):
                value = getattr(usage, src_attr, 0) or 0
                if value:
                    state.span.set_attribute(dst_attr, int(value))
            model_name = getattr(usage, "model_name", "")
            if model_name:
                state.span.set_attribute(GEN_AI_RESPONSE_MODEL, str(model_name))

        finish_reason = getattr(response, "finish_reason", None)
        if finish_reason and finish_reason != "null":
            state.span.set_attribute(GEN_AI_RESPONSE_FINISH_REASON, str(finish_reason))

    @staticmethod
    def _serialize_tool_inputs(inputs: Any) -> str:
        """Render the (args, kwargs) tuple supplied by the tool framework."""
        if inputs is None:
            return ""
        try:
            return json.dumps(inputs, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(inputs)

    @staticmethod
    def _derive_model_name(kwargs: dict[str, Any]) -> str:
        """Best-effort extraction of model name from extra_kwargs metadata."""
        model_config = kwargs.get("model_config")
        if model_config is None:
            return ""
        return str(getattr(model_config, "model", "") or "")

    @staticmethod
    def _extract_agent_inputs(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        """BaseAgent.invoke is called with positional ``inputs`` (or as kwarg)."""
        if args:
            return args[0]
        return kwargs.get("inputs")

    @staticmethod
    def _unpack_agent_inputs(inputs: Any) -> tuple[str, str, str]:
        """Pull (agent_id, role, query) from the heterogeneous inputs payload.

        BaseAgent.invoke accepts either a dict (with ``user_input`` /
        ``session_id`` / etc.) or a bare string. We treat the string form as
        the query and leave agent_id/role blank when not derivable.
        """
        if isinstance(inputs, str):
            return ("unknown", "", inputs)
        if isinstance(inputs, dict):
            agent_id = str(inputs.get("agent_id") or inputs.get("session_id") or "unknown")
            role = str(inputs.get("role", "") or "")
            query = inputs.get("user_input") or inputs.get("query") or ""
            return (agent_id, role, str(query))
        return ("unknown", "", str(inputs) if inputs is not None else "")
