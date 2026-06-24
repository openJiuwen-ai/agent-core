# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""OpenTelemetry handlers for AsyncCallbackFramework events.

Agent spans are created per-iteration in ObservabilityRail.
This handler only creates team spans (on first invoke) and manages
LLM/tool span lifecycles.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry.trace import (
    Span,
    SpanKind,
    Status,
    StatusCode,
    Tracer,
    set_span_in_context,
)

from openjiuwen.agent_teams.observability.config import ObservabilityConfig
from openjiuwen.agent_teams.observability.redaction import (
    _truncate,
    redact_completion,
    redact_prompt,
)
from openjiuwen.agent_teams.observability.semconv import (
    AT_SESSION_ID,

    GEN_AI_COMPLETION,
    GEN_AI_OPERATION_NAME,
    GEN_AI_PROMPT,
    GEN_AI_PROVIDER_NAME,
    LANGFUSE_GEN_AI_COMPLETION,
    LANGFUSE_GEN_AI_PROMPT,
    GEN_AI_REQUEST_MAX_TOKENS,
    GEN_AI_REQUEST_MESSAGE_COUNT,
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
    GEN_AI_TOOL_ID,
    GEN_AI_TOOL_CALLS,
    GEN_AI_TOOL_DEFINITIONS,
    GEN_AI_USAGE_COMPLETION_TOKENS,
    GEN_AI_USAGE_PROMPT_TOKENS,
    GEN_AI_USAGE_TOTAL_TOKENS,
    LANGFUSE_OBSERVATION_INPUT,
    LANGFUSE_OBSERVATION_OUTPUT,
    LANGFUSE_OBSERVATION_TYPE,
    LANGFUSE_SESSION_ID,
)
from openjiuwen.agent_teams.observability.span_context import (
    LlmSpanState,
    get_team_span,
    get_current_agent_span,
    pop_llm_span_state,
    pop_tool_span,
    push_llm_span_state,
    push_tool_span,
)
from openjiuwen.core.common.logging import team_logger


_TRACER_NAME = "openjiuwen.agent_teams.observability"


def _gen_ai_system_name() -> str:
    """Return gen_ai.system value from config.service_name, default 'openjiuwen'."""
    from openjiuwen.agent_teams.observability.setup import get_config
    config = get_config()
    return config.service_name if config else "openjiuwen"


def _coerce_message_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(content)


def _message_role(msg: Any) -> str:
    if isinstance(msg, dict):
        return str(msg.get("role", ""))
    return str(getattr(msg, "role", ""))


def _message_content(msg: Any) -> Any:
    if isinstance(msg, dict):
        return msg.get("content", "")
    return getattr(msg, "content", "")


def _serialize_tool_calls(tool_calls: Any) -> str:
    """Serialize tool_calls to JSON string for OTel span attribute."""
    if not tool_calls:
        return ""
    items = []
    for tc in tool_calls:
        if hasattr(tc, "model_dump"):
            items.append(tc.model_dump(exclude_none=True))
        elif isinstance(tc, dict):
            items.append(tc)
        else:
            items.append(str(tc))
    try:
        return json.dumps(items, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(items)


class OtelCallbackHandler:
    """Bundle of async callback handlers that emit OTel spans / events."""

    def __init__(
        self,
        config: ObservabilityConfig,
        *,
        tracer: Tracer | None = None,
    ) -> None:
        self._config = config
        self._injected_tracer = tracer

    def _tracer(self) -> Tracer:
        if self._injected_tracer is not None:
            return self._injected_tracer
        from openjiuwen.agent_teams.observability.setup import get_tracer
        return get_tracer(_TRACER_NAME)

    @staticmethod
    def _get_parent_context_for_llm_tool() -> Any:
        """Resolve parent context for LLM/tool span creation.

        Returns None when no valid parent span exists — callers must
        skip span creation in that case rather than attaching to the
        root context, which would produce orphan spans outside the
        team trace.
        """
        iteration_span = get_current_agent_span()
        if iteration_span is not None:
            if iteration_span.is_recording():
                return set_span_in_context(iteration_span, otel_context.get_current())
            else:
                team_logger.warning(
                    "otel: _get_parent_context - agent span ENDED name={} "
                    "trace_id={:032x} span_id={:016x}",
                    iteration_span.name,
                    iteration_span.context.trace_id,
                    iteration_span.context.span_id,
                )

        team_span = get_team_span()
        if team_span is not None:
            if team_span.is_recording():
                team_logger.info(
                    "otel: _get_parent_context - fallback to team span name={} "
                    "trace_id={:032x} span_id={:016x}",
                    team_span.name,
                    team_span.context.trace_id,
                    team_span.context.span_id,
                )
                return set_span_in_context(team_span, otel_context.get_current())
            else:
                team_logger.warning(
                    "otel: _get_parent_context - team span ENDED name={} "
                    "trace_id={:032x} span_id={:016x}",
                    team_span.name,
                    team_span.context.trace_id,
                    team_span.context.span_id,
                )

        team_logger.error("otel: no valid parent span for LLM/tool — skipping span creation")
        return None

    # ------------------------------------------------------------------
    # LLM
    # ------------------------------------------------------------------

    async def on_llm_invoke_input(self, *args: Any, **kwargs: Any) -> None:
        try:
            self._open_llm_span(kwargs)
        except Exception as exc:
            team_logger.exception("otel: on_llm_invoke_input failed: {}", exc)

    async def on_llm_stream_input(self, *args: Any, **kwargs: Any) -> None:
        try:
            self._open_llm_span(kwargs, is_streaming=True)
        except Exception as exc:
            team_logger.exception("otel: on_llm_stream_input failed: {}", exc)

    async def on_llm_stream_output(self, *args: Any, **kwargs: Any) -> Any:
        try:
            state = pop_llm_span_state(peek=True)
            if state is None:
                return kwargs.get("result")

            if not state.span.is_recording():
                return kwargs.get("result")

            chunk = kwargs.get("result")
            seq = state.next_chunk_seq()
            if state.first_chunk_ns is None:
                state.first_chunk_ns = time.monotonic_ns()
                ttft_ms = (state.first_chunk_ns - state.start_ns) / 1_000_000.0
                if state.span.is_recording():
                    state.span.set_attribute(GEN_AI_RESPONSE_TTFT_MS, ttft_ms)
            delta = _coerce_message_content(_message_content(chunk))
            if delta:
                state.accumulated_content += delta
            reasoning_chunk = str(getattr(chunk, "reasoning_content", "") or "")
            if reasoning_chunk:
                state.accumulated_reasoning += reasoning_chunk
            if state.span.is_recording():
                state.span.add_event(
                    name="llm.chunk",
                    attributes={
                        "seq": seq,
                        "delta_chars": len(delta),
                    },
                )
            self._maybe_record_response_attrs(state, chunk)
        except Exception as exc:
            team_logger.warning("otel: on_llm_stream_output failed: {}", exc)
        return kwargs.get("result")

    async def on_llm_output(self, *args: Any, **kwargs: Any) -> None:
        try:
            state = pop_llm_span_state()
            if state is None:
                team_logger.debug("otel: on_llm_output — no open LLM span to close")
                return
            if not state.span.is_recording():
                team_logger.debug("otel: on_llm_output — span already ended")
                return

            completion_text = state.accumulated_content or str(kwargs.get("response") or "")
            reasoning_text = state.accumulated_reasoning
            if not reasoning_text:
                resp_obj = kwargs.get("response")
                if resp_obj is not None:
                    reasoning_text = str(getattr(resp_obj, "reasoning_content", "") or "")

            tool_calls = kwargs.get("tool_calls") or getattr(kwargs.get("response"), "tool_calls", None)
            tc_json = _serialize_tool_calls(tool_calls)

            # Usage / finish_reason from streaming trigger kwargs
            usage_from_trigger = kwargs.get("usage")
            if usage_from_trigger is not None:
                self._record_usage_attrs(state, usage_from_trigger, skip_existing=True)
            finish_from_trigger = kwargs.get("finish_reason") or kwargs.get("response")
            if finish_from_trigger is not None and not state.span.attributes.get(GEN_AI_RESPONSE_FINISH_REASON):
                self._maybe_record_finish_from_trigger(state, finish_from_trigger)

            self._finalize_llm_span_output(
                state, completion_text, reasoning_text,
                tc_json=tc_json, response=kwargs.get("response"),
            )
        except Exception as exc:
            team_logger.warning("otel: on_llm_output failed: {}", exc)

    async def on_llm_invoke_output(self, *args: Any, **kwargs: Any) -> Any:
        try:
            state = pop_llm_span_state(peek=True)
            if state is None:
                return kwargs.get("result")
            if state.is_streaming:
                return kwargs.get("result")
            state = pop_llm_span_state()
            response = kwargs.get("result")
            self._close_llm_span(state, response)
        except Exception as exc:
            team_logger.warning("otel: on_llm_invoke_output failed: {}", exc)
        return kwargs.get("result")

    async def on_llm_call_error(self, *args: Any, **kwargs: Any) -> None:
        try:
            state = pop_llm_span_state()
            if state is None:
                return

            if not state.span.is_recording():
                return

            exc = kwargs.get("error") or kwargs.get("exception")
            if state.span.is_recording():
                if isinstance(exc, BaseException):
                    state.span.record_exception(exc)
                    state.span.set_status(Status(StatusCode.ERROR, str(exc)))
                else:
                    state.span.set_status(Status(StatusCode.ERROR, "llm call error"))
                state.span.end()
        except Exception as exc:
            team_logger.exception("otel: on_llm_call_error failed: {}", exc)

    # ------------------------------------------------------------------
    # Tool
    # ------------------------------------------------------------------

    async def on_tool_call_started(self, *args: Any, **kwargs: Any) -> None:
        """Open a tool span with explicit parent context."""
        try:
            tool_name = str(kwargs.get("tool_name") or "unknown")
            tool_id = kwargs.get("tool_id")
            inputs = kwargs.get("inputs")

            parent_ctx = self._get_parent_context_for_llm_tool()
            if parent_ctx is None:
                return

            span = self._tracer().start_span(
                name=f"tool.{tool_name}",
                kind=SpanKind.INTERNAL,
                context=parent_ctx,
            )
            span.set_attribute(LANGFUSE_OBSERVATION_TYPE, "tool")
            span.set_attribute(GEN_AI_TOOL_NAME, tool_name)
            if tool_id is not None:
                span.set_attribute(GEN_AI_TOOL_ID, str(tool_id))
            raw_input = self._serialize_tool_inputs(inputs)
            redacted_input = redact_prompt(raw_input, self._config)
            span.set_attribute(GEN_AI_TOOL_INPUT, redacted_input)
            span.set_attribute(LANGFUSE_OBSERVATION_INPUT, redacted_input)
            self._propagate_team_context(span)
            push_tool_span(tool_name, span)
        except Exception as exc:
            team_logger.warning("otel: on_tool_call_started failed: {}", exc)

    async def on_tool_call_finished(self, *args: Any, **kwargs: Any) -> Any:
        try:
            tool_name = str(kwargs.get("tool_name") or "unknown")
            result = kwargs.get("result")
            span = pop_tool_span(tool_name)
            if span is None:
                return result

            if not span.is_recording():
                team_logger.error(
                    "WRITE_ON_ENDED_SPAN: where=on_tool_call_finished name={} span_id={:016x}",
                    getattr(span, "name", "<no-name>"),
                    getattr(getattr(span, "context", None), "span_id", 0),
                )
                return result

            if result is None:
                serialized_output = ""
            elif hasattr(result, "__str__") and not isinstance(result, dict):
                serialized_output = str(result)
            else:
                try:
                    serialized_output = json.dumps(result, ensure_ascii=False, default=str)
                except (TypeError, ValueError):
                    serialized_output = str(result)
            redacted = redact_completion(serialized_output, self._config)
            span.set_attribute(GEN_AI_TOOL_OUTPUT, redacted)
            span.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, redacted)
            span.set_status(Status(StatusCode.OK))
            span.end()
        except Exception as exc:
            import traceback
            team_logger.warning("otel: on_tool_call_finished failed: {}\n{}", exc, traceback.format_exc())
        return kwargs.get("result")

    async def on_tool_call_error(self, *args: Any, **kwargs: Any) -> None:
        try:
            tool_name = str(kwargs.get("tool_name") or "unknown")
            exc = kwargs.get("error") or kwargs.get("exception")
            span = pop_tool_span(tool_name)
            if span is None:
                return

            if not span.is_recording():
                team_logger.error(
                    "WRITE_ON_ENDED_SPAN: where=on_tool_call_error name={} span_id={:016x}",
                    getattr(span, "name", "<no-name>"),
                    getattr(getattr(span, "context", None), "span_id", 0),
                )
                return

            if span.is_recording():
                if isinstance(exc, BaseException):
                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                else:
                    span.set_status(Status(StatusCode.ERROR, "tool call error"))
                span.end()
        except Exception as exc:
            team_logger.exception("otel: on_tool_call_error failed: {}", exc)

    # ------------------------------------------------------------------
    # Agent
    # ------------------------------------------------------------------

    async def on_agent_invoke_input(self, *args: Any, **kwargs: Any) -> None:
        """Handle AGENT_INVOKE_INPUT callback.

        Team span creation is in Runner._maybe_attach_observability
        (runner owns the full lifecycle: create before invoke, close in finally).
        This callback only:
          1. Sets ContextVars (session_id)
          2. Propagates query to team span input
        """
        try:
            inputs = args[0] if args else None
            session = kwargs.get("session")
            session_id = session.get_session_id() if session else ""

            # Extract query from inputs
            query = ""
            if isinstance(inputs, str):
                query = inputs
            elif isinstance(inputs, dict):
                query = str(inputs.get("user_input") or inputs.get("query") or "")

            # Set ContextVar for session_id
            from openjiuwen.agent_teams.context import set_session_id
            if session_id:
                set_session_id(session_id)

            # Propagate query to team span (created by runner)
            if query:
                team_span = get_team_span()
                if team_span is not None and team_span.is_recording():
                    if not team_span.attributes.get(LANGFUSE_OBSERVATION_INPUT):
                        team_span.set_attribute(LANGFUSE_OBSERVATION_INPUT,
                                                redact_prompt(query, self._config))

        except Exception as exc:
            team_logger.exception("otel: on_agent_invoke_input failed: {}", exc)

    async def on_agent_invoke_output(self, *args: Any, **kwargs: Any) -> Any:
        """Handle AGENT_INVOKE_OUTPUT callback.

        DO NOT close agent span here! (managed by Rail)
        Sets team span output from the FINAL invoke result — this is the
        overall agent output, distinct from per-iteration results written by
        ObservabilityRail.after_task_iteration.
        """
        try:
            result = kwargs.get("result")
            if result is not None:
                team_span = get_team_span()
                if team_span is not None and team_span.is_recording():
                    team_span.set_attribute(
                        LANGFUSE_OBSERVATION_OUTPUT,
                        redact_completion(str(result), self._config),
                    )
        except Exception as exc:
            team_logger.exception("otel: on_agent_invoke_output failed: {}", exc)
        return kwargs.get("result")

    async def on_agent_stream_input(self, *args: Any, **kwargs: Any) -> None:
        """Handle AGENT_STREAM_INPUT callback. Same logic as on_agent_invoke_input."""
        await self.on_agent_invoke_input(*args, **kwargs)

    async def on_agent_stream_output(self, *args: Any, **kwargs: Any) -> Any:
        """Handle AGENT_STREAM_OUTPUT callback. Same logic as on_agent_invoke_output."""
        return await self.on_agent_invoke_output(*args, **kwargs)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _open_llm_span(self, kwargs: dict[str, Any], is_streaming: bool = False) -> None:
        """Open an LLM span with explicit parent context."""
        parent_ctx = self._get_parent_context_for_llm_tool()
        if parent_ctx is None:
            return

        messages = kwargs.get("messages") or []
        model_name = kwargs.get("model") or self._derive_model_name(kwargs) or "unknown"

        span = self._tracer().start_span(
            name="llm.call",
            kind=SpanKind.CLIENT,
            context=parent_ctx,
        )
        span.set_attribute(GEN_AI_SYSTEM, _gen_ai_system_name())
        span.set_attribute(GEN_AI_OPERATION_NAME, "chat")
        provider_name = self._derive_provider_name(kwargs)
        span.set_attribute(GEN_AI_PROVIDER_NAME, provider_name)
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

        msg_count = len(messages)
        span.set_attribute(GEN_AI_REQUEST_MESSAGE_COUNT, msg_count)

        for i, m in enumerate(messages):
            role = _message_role(m)
            raw_content = _coerce_message_content(_message_content(m))
            # Per-message prompt attributes (standard + Langfuse)
            redacted = redact_prompt(raw_content, self._config)
            span.set_attribute(f"{GEN_AI_PROMPT}.{i}.role", role)
            span.set_attribute(f"{GEN_AI_PROMPT}.{i}.content", redacted)
            span.set_attribute(f"{LANGFUSE_GEN_AI_PROMPT}.{i}.role", role)
            span.set_attribute(f"{LANGFUSE_GEN_AI_PROMPT}.{i}.content", redacted)

        # langfuse.observation.input: delta-based for UI readability.
        #
        # LLM calls within one agent span are sequential.  We track the
        # message count on the agent span (via gen_ai.request.message_count)
        # and only show messages that are *new* since the previous call:
        #
        #   - First call (prev_count == 0):  show all messages except system.
        #   - Context compression (current < prev): show all except system.
        #   - Otherwise: show messages[prev_count:].
        #
        # Full message list is available via gen_ai.prompt.{i}.* attributes.
        agent_span = get_current_agent_span()
        prev_count_raw: int = 0
        if agent_span is not None:
            prev_attr = agent_span.attributes.get(GEN_AI_REQUEST_MESSAGE_COUNT)
            if prev_attr is not None:
                try:
                    prev_count_raw = int(str(prev_attr))
                except (ValueError, TypeError):
                    pass

        if prev_count_raw == 0 or msg_count < prev_count_raw:
            # First LLM call in this agent span, or context compression
            # reduced the message count.  Drop system messages so the
            # input is focused on the dialogue.
            delta_msgs = [m for m in messages if _message_role(m) != "system"]
        else:
            delta_msgs = messages[prev_count_raw:]

        # Update the count on the agent span for the next LLM call.
        if agent_span is not None:
            agent_span.set_attribute(GEN_AI_REQUEST_MESSAGE_COUNT, msg_count)

        input_json = json.dumps(
            [{"role": _message_role(m),
              "content": _coerce_message_content(_message_content(m))}
             for m in delta_msgs],
            ensure_ascii=False, default=str,
        ) if delta_msgs else "[]"
        input_max_len = max(self._config.attribute_value_max_length * 10, 81920)
        span.set_attribute(LANGFUSE_OBSERVATION_INPUT,
                           _truncate(input_json, input_max_len))

        tools = kwargs.get("tools")
        if tools:
            try:
                span.set_attribute(
                    GEN_AI_TOOL_DEFINITIONS,
                    json.dumps(tools, ensure_ascii=False, default=str),
                )
            except (TypeError, ValueError):
                span.set_attribute(GEN_AI_TOOL_DEFINITIONS, str(tools))

        self._propagate_team_context(span)

        push_llm_span_state(
            LlmSpanState(span=span, start_ns=time.monotonic_ns(), is_streaming=is_streaming),
        )

        team_logger.info(
            "otel: _open_llm_span name=llm.call trace_id={:032x} span_id={:016x} "
            "parent_span_id={:016x}",
            span.context.trace_id, span.context.span_id,
            span.parent.span_id if span.parent else 0,
        )

    def _close_llm_span(self, state: LlmSpanState, response: Any) -> None:
        if not state.span.is_recording():
            team_logger.error(
                "WRITE_ON_ENDED_SPAN: where=_close_llm_span name={} span_id={:016x}",
                getattr(state.span, "name", "<no-name>"),
                getattr(getattr(state.span, "context", None), "span_id", 0),
            )
            return

        raw_content = _message_content(response)
        completion_text = _coerce_message_content(raw_content)
        reasoning_text = str(getattr(response, "reasoning_content", "") or "")

        tool_calls = getattr(response, "tool_calls", None)
        tc_json = _serialize_tool_calls(tool_calls)
        if tc_json:
            state.span.set_attribute(GEN_AI_TOOL_CALLS, tc_json)
            if not isinstance(raw_content, str):
                completion_text = ""

        self._maybe_record_response_attrs(state, response)

        self._finalize_llm_span_output(
            state, completion_text, reasoning_text,
            tc_json=tc_json, response=response,
        )

    def _finalize_llm_span_output(
        self,
        state: LlmSpanState,
        completion_text: str,
        reasoning_text: str = "",
        *,
        tc_json: str = "",
        response: Any = None,
    ) -> None:
        """Shared: set completion/output attrs, reasoning sub-span, close LLM span.

        Called by both ``_close_llm_span`` (non-streaming) and
        ``on_llm_output`` (streaming final) to avoid ~130 lines of
        duplicated output assembly.
        """
        redacted_compl = redact_completion(completion_text, self._config)
        # Standard gen_ai.completion keys
        state.span.set_attribute(f"{GEN_AI_COMPLETION}.0.role", "assistant")
        state.span.set_attribute(f"{GEN_AI_COMPLETION}.0.content", redacted_compl)
        # Langfuse-compatible t_ prefix keys
        state.span.set_attribute(f"{LANGFUSE_GEN_AI_COMPLETION}.0.role", "assistant")
        state.span.set_attribute(f"{LANGFUSE_GEN_AI_COMPLETION}.0.content", redacted_compl)

        # Build langfuse.observation.output
        choice_obj: dict[str, Any] = {"index": 0, "message": {"role": "assistant"}}
        finish_reason = state.span.attributes.get(GEN_AI_RESPONSE_FINISH_REASON)
        if finish_reason:
            choice_obj["finish_reason"] = finish_reason
        if completion_text:
            choice_obj["message"]["content"] = completion_text
        if tc_json:
            try:
                choice_obj["message"]["tool_calls"] = json.loads(tc_json)
            except (json.JSONDecodeError, TypeError):
                pass
        response_obj: dict[str, Any] = {"choices": [choice_obj]}
        if response is not None:
            usage = getattr(response, "usage_metadata", None)
            if usage:
                usage_obj: dict[str, int] = {}
                if hasattr(usage, "total_tokens") and usage.total_tokens:
                    usage_obj["total_tokens"] = usage.total_tokens
                if hasattr(usage, "input_tokens") and usage.input_tokens:
                    usage_obj["prompt_tokens"] = usage.input_tokens
                if hasattr(usage, "output_tokens") and usage.output_tokens:
                    usage_obj["completion_tokens"] = usage.output_tokens
                if usage_obj:
                    response_obj["usage"] = usage_obj
        output_json = json.dumps(response_obj, ensure_ascii=False, default=str)
        state.span.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, redact_completion(output_json, self._config))

        if reasoning_text:
            with self._tracer().start_as_current_span(
                name="llm.reasoning",
                context=set_span_in_context(state.span),
            ) as reasoning_span:
                redacted_reasoning = redact_completion(reasoning_text, self._config)
                # Standard gen_ai.completion attributes (Langfuse reasoning display)
                reasoning_span.set_attribute(f"{GEN_AI_COMPLETION}.0.role", "reasoning")
                reasoning_span.set_attribute(f"{GEN_AI_COMPLETION}.0.is_reasoning", True)
                reasoning_span.set_attribute(f"{GEN_AI_COMPLETION}.0.content", redacted_reasoning)
                # Langfuse observation input/output for UI visibility
                reasoning_span.set_attribute(LANGFUSE_OBSERVATION_INPUT, "llm reasoning")
                reasoning_span.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, redacted_reasoning)
                reasoning_span.set_status(Status(StatusCode.OK))

        state.span.set_status(Status(StatusCode.OK))
        state.span.end()

    @staticmethod
    def _record_usage_attrs(state: LlmSpanState, usage: Any, *, skip_existing: bool = False) -> None:
        """Record usage attributes (tokens, model_name) from usage_metadata."""
        if usage is None:
            return
        for src_attr, dst_attr in (
            ("input_tokens", GEN_AI_USAGE_PROMPT_TOKENS),
            ("output_tokens", GEN_AI_USAGE_COMPLETION_TOKENS),
            ("total_tokens", GEN_AI_USAGE_TOTAL_TOKENS),
        ):
            value = getattr(usage, src_attr, 0) or 0
            if value and not (skip_existing and state.span.attributes.get(dst_attr)):
                state.span.set_attribute(dst_attr, int(value))
        model_name = getattr(usage, "model_name", "")
        if model_name and not (skip_existing and state.span.attributes.get(GEN_AI_RESPONSE_MODEL)):
            state.span.set_attribute(GEN_AI_RESPONSE_MODEL, str(model_name))

    @staticmethod
    def _maybe_record_response_attrs(state: LlmSpanState, response: Any) -> None:
        if response is None:
            return
        usage = getattr(response, "usage_metadata", None)
        if usage is not None:
            OtelCallbackHandler._record_usage_attrs(state, usage, skip_existing=False)
        finish_reason = getattr(response, "finish_reason", None)
        if finish_reason and finish_reason != "null":
            state.span.set_attribute(GEN_AI_RESPONSE_FINISH_REASON, str(finish_reason))

    @staticmethod
    def _maybe_record_finish_from_trigger(state: LlmSpanState, response: Any) -> None:
        if not state.span.is_recording():
            return
        if isinstance(response, str):
            finish_reason = response
        else:
            finish_reason = getattr(response, "finish_reason", None)
        if finish_reason and finish_reason != "null" and not state.span.attributes.get(GEN_AI_RESPONSE_FINISH_REASON):
            state.span.set_attribute(GEN_AI_RESPONSE_FINISH_REASON, str(finish_reason))

    @staticmethod
    def _serialize_tool_inputs(inputs: Any) -> str:
        if inputs is None:
            return ""

        def _sanitize(obj: Any) -> Any:
            if hasattr(obj, "get_session_id"):
                try:
                    return f"session:{obj.get_session_id()}"
                except Exception:
                    return "<Session>"
            if isinstance(obj, dict):
                return {k: _sanitize(v) for k, v in obj.items()}
            if isinstance(obj, (list, tuple)):
                return type(obj)(_sanitize(v) for v in obj)
            return obj

        try:
            sanitized = _sanitize(inputs)
            return json.dumps(sanitized, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(inputs)

    @staticmethod
    def _derive_model_name(kwargs: dict[str, Any]) -> str:
        model_config = kwargs.get("model_config")
        if model_config is None:
            return ""
        return str(getattr(model_config, "model", "") or "")

    @staticmethod
    def _derive_provider_name(kwargs: dict[str, Any]) -> str:
        mcc = kwargs.get("model_client_config")
        if mcc is not None:
            cp = getattr(mcc, "client_provider", None)
            if cp:
                return str(cp.value if hasattr(cp, "value") else cp).lower()
        mc = kwargs.get("model_config")
        if mc is not None:
            cp = getattr(mc, "client_provider", None)
            if cp:
                return str(cp.value if hasattr(cp, "value") else cp).lower()
        return _gen_ai_system_name()

    @staticmethod
    def _propagate_team_context(span: Span) -> None:
        """Propagate session_id to LLM/tool spans.

        v21: team_name and member_name are no longer propagated via ContextVar.
        They are read directly from agent.team_name and agent.card.name in Rail.
        """
        try:
            from openjiuwen.agent_teams.context import get_session_id as get_ctx_session_id
            sid = get_ctx_session_id()
            if sid:
                span.set_attribute(LANGFUSE_SESSION_ID, sid)
                span.set_attribute(AT_SESSION_ID, sid)
        except Exception as exc:
            team_logger.warning("callback_handler: failed to propagate team context: {}", exc)
