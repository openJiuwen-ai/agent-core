# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""OpenTelemetry handlers for AsyncCallbackFramework events.

Agent spans are created per iteration by the host integration. This handler
manages the generic LLM/tool span lifecycle and root metadata propagation.
"""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
import time
import uuid
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any

from opentelemetry import trace
from opentelemetry import context as otel_context
from opentelemetry.trace import (
    Span,
    SpanKind,
    Status,
    StatusCode,
    Tracer,
    set_span_in_context,
)

from openjiuwen.extensions.observability.redaction import (
    redact_completion,
    redact_prompt,
    truncate,
)
from openjiuwen.extensions.observability.config import ObservabilityConfig
from openjiuwen.extensions.observability.demand import publish_span_snapshot
from openjiuwen.extensions.observability.trajectory_events import emit_context_window_commit
from openjiuwen.extensions.observability.semconv import (
    AT_AGENT_ID,
    AT_MEMBER_NAME,
    AT_SESSION_ID,

    ERROR_TYPE,
    GEN_AI_AGENT_DESCRIPTION,
    GEN_AI_AGENT_ID,
    GEN_AI_AGENT_NAME,
    GEN_AI_AGENT_VERSION,
    GEN_AI_CONVERSATION_ID,
    GEN_AI_INPUT_MESSAGES,
    GEN_AI_OPERATION_NAME,
    GEN_AI_OUTPUT_MESSAGES,
    GEN_AI_PROVIDER_NAME,
    GEN_AI_REQUEST_MAX_TOKENS,
    GEN_AI_REQUEST_MESSAGE_COUNT,
    GEN_AI_REQUEST_MESSAGE_COUNT_PREFIX,
    GEN_AI_REQUEST_ID,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_REQUEST_STREAM,
    GEN_AI_REQUEST_TEMPERATURE,
    GEN_AI_REQUEST_TOP_P,
    GEN_AI_RESPONSE_FINISH_REASON,
    GEN_AI_RESPONSE_FINISH_REASONS,
    GEN_AI_RESPONSE_ID,
    GEN_AI_RESPONSE_MODEL,
    GEN_AI_RESPONSE_TTFC,
    GEN_AI_RESPONSE_TTFT_MS,
    GEN_AI_SYSTEM,
    GEN_AI_SYSTEM_INSTRUCTIONS,
    GEN_AI_TOOL_CALL_ARGUMENTS,
    GEN_AI_TOOL_CALL_RESULT,
    GEN_AI_TOOL_INPUT,
    GEN_AI_TOOL_NAME,
    GEN_AI_TOOL_OUTPUT,
    GEN_AI_TOOL_ID,
    GEN_AI_TOOL_TYPE,
    GEN_AI_TOOL_CALLS,
    GEN_AI_TOOL_DEFINITIONS,
    GEN_AI_USAGE_COMPLETION_TOKENS,
    GEN_AI_USAGE_PROMPT_TOKENS,
    GEN_AI_USAGE_TOTAL_TOKENS,
    GEN_AI_USAGE_CACHE_CREATION_INPUT_TOKENS,
    GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS,
    GEN_AI_USAGE_INPUT_TOKENS,
    GEN_AI_USAGE_OUTPUT_TOKENS,
    GEN_AI_USAGE_REASONING_TOKENS,
    GEN_AI_USAGE_REASONING_OUTPUT_TOKENS,
    GEN_AI_REASONING_DURATION_MS,
    GEN_AI_REASONING_TIMING,
    REASONING_TIMING_UNMEASURED,
    LANGFUSE_OBSERVATION_INPUT,
    LANGFUSE_OBSERVATION_OUTPUT,
    LANGFUSE_OBSERVATION_TYPE,
    LANGFUSE_SESSION_ID,
    OJ_EVENT_SEQUENCE,
    OJ_EXECUTION_SUBJECT_DISPLAY_NAME,
    OJ_EXECUTION_SUBJECT_ID,
    OJ_EXECUTION_SUBJECT_KIND,
    OJ_EXECUTION_SUBJECT_PARENT_ID,
    OJ_EXECUTION_SUBJECT_REQUEST_NUMBER,
    OJ_EXECUTION_SUBJECT_SESSION_ID,
    OJ_GEN_AI_RESPONSE_COMPLETION_TOKEN_IDS,
    OJ_GEN_AI_INPUT_MESSAGE_PROVENANCE,
    OJ_GEN_AI_RESPONSE_LOGPROBS,
    OJ_GEN_AI_RESPONSE_PARSER_RESULT,
    OJ_GEN_AI_RESPONSE_PROVIDER_CONTENT,
    OJ_GEN_AI_RESPONSE_PROMPT_TOKEN_IDS,
    OJ_GEN_AI_RESPONSE_PROVIDER_METADATA,
    OJ_GEN_AI_RESPONSE_TOTAL_LATENCY_MS,
    OJ_GEN_AI_RESPONSE_TPOT_MS,
    OJ_GEN_AI_USAGE_INPUT_COST,
    OJ_GEN_AI_USAGE_OUTPUT_COST,
    OJ_GEN_AI_USAGE_TOTAL_COST,
    OJ_INFERENCE_ID,
    OJ_REQUEST_ID,
    OJ_REQUEST_NUMBER,
    OJ_REQUEST_PURPOSE,
    OJ_RUN_ID,
    OJ_SESSION_ID,
    OJ_STEP_ID,
    OJ_STEP_NUMBER,
    OJ_STREAM_KIND,
    OJ_STREAM_TEXT,
    OJ_STREAM_TOOL_CALL_ARGUMENTS_DELTA,
    OJ_STREAM_TOOL_CALL_ID,
    OJ_STREAM_TOOL_CALL_NAME,
    OJ_TRACE_SCHEMA_VERSION,
    OJ_TRAJECTORY_RECORD_KIND,
    OJ_TOOL_AUTHORITATIVE,
    OJ_TOOL_RESOURCE_ID,
    OJ_TOOL_TYPE,
    OJ_TRACE_ROOT,
    OJ_TURN_ID,
    OJ_TURN_NUMBER,
)
from openjiuwen.extensions.observability.tool_outcome import (
    TOOL_REPORTED_FAILURE,
    tool_failure_reason,
    tool_result_for_exception,
)
from openjiuwen.extensions.observability.span_context import (
    LlmSpanState,
    get_active_span_tracker,
    get_current_agent_span,
    get_current_llm_span,
    get_current_session_id,
    get_current_tool_span,
    get_root_span,
    next_execution_subject_request_number,
    pop_current_llm_span,
    pop_tool_span,
    push_tool_span,
    set_current_session_id,
)
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.schema.message import (
    OPENJIUWEN_MESSAGE_ORIGIN_EXTERNAL_USER,
    OPENJIUWEN_MESSAGE_ORIGIN_HARNESS_INTERNAL,
    OPENJIUWEN_MESSAGE_ORIGIN_METADATA,
    OPENJIUWEN_MESSAGE_PROVENANCE_METADATA,
    OPENJIUWEN_MESSAGE_SOURCE_KIND_METADATA,
)
from openjiuwen.core.foundation.llm.call_scope import (
    expects_unified_llm_completion,
    get_current_llm_call_id,
    is_llm_observation_suppressed,
)


_TRACER_NAME = "openjiuwen.extensions.observability"
_REQUEST_SEQUENCE_LOCK = threading.Lock()
# Counter attached to the root span so it dies with the run. Namespaced because
# the span object belongs to the OpenTelemetry SDK, not to this handler.
_REQUEST_SEQUENCE_ATTR = "_otel_llm_request_sequence"
# Fallback LLM request counters for calls made while no root span is
# resolvable, keyed by session. Bounded because nothing signals when a session
# is done; the counter for a live session is always among the most recent.
# Guarded by _REQUEST_SEQUENCE_LOCK.
_MAX_FALLBACK_REQUEST_SEQUENCES = 256
_FALLBACK_REQUEST_SEQUENCES: OrderedDict[str, int] = OrderedDict()
_PROVIDER_METADATA_ALLOWLIST = frozenset({
    "system_fingerprint",
    "service_tier",
    "status",
    "stop_reason",
    "stop_sequence",
    "incomplete_details",
})


def _gen_ai_system_name(config: ObservabilityConfig | None = None) -> str:
    """Return gen_ai.system value from config.service_name, default 'openjiuwen'."""
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


def _get_field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _trajectory_message_origin(
    message: Any,
    source_metadata: Any = None,
) -> dict[str, str]:
    """Return explicit origin facts without inspecting message content."""
    metadata = _get_field(message, "metadata")
    if not isinstance(metadata, Mapping):
        metadata = {}
    fallback_metadata = source_metadata if isinstance(source_metadata, Mapping) else {}
    origin = (
        metadata.get(OPENJIUWEN_MESSAGE_ORIGIN_METADATA)
        or fallback_metadata.get(OPENJIUWEN_MESSAGE_ORIGIN_METADATA)
    )
    external_user = (
        _message_role(message) == "user"
        and origin == OPENJIUWEN_MESSAGE_ORIGIN_EXTERNAL_USER
    )
    result = {
        "origin": (
            OPENJIUWEN_MESSAGE_ORIGIN_EXTERNAL_USER
            if external_user
            else OPENJIUWEN_MESSAGE_ORIGIN_HARNESS_INTERNAL
        )
    }
    source_kind = (
        metadata.get(OPENJIUWEN_MESSAGE_SOURCE_KIND_METADATA)
        or fallback_metadata.get(OPENJIUWEN_MESSAGE_SOURCE_KIND_METADATA)
    )
    if external_user and isinstance(source_kind, str) and source_kind.strip():
        result["source_kind"] = source_kind.strip()
    return result


def _json_value_if_unchanged(raw: str, redacted: str) -> Any:
    """Retain JSON structure when redaction/truncation did not alter it."""
    if redacted != raw:
        return redacted
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return redacted


def _controlled_string(value: Any) -> str:
    """Return a bounded string fallback that cannot raise user code errors."""
    try:
        if type(value).__str__ is object.__str__:
            return f"<{type(value).__name__}>"
        rendered = str(value)
    except Exception:
        return f"<{type(value).__name__}>"
    return rendered[:1024]


def _json_compatible(
    value: Any,
    *,
    depth: int = 0,
    seen: set[int] | None = None,
) -> Any:
    """Normalize one model/tool schema value without leaking user exceptions."""
    if depth > 20:
        return f"<max-depth:{type(value).__name__}>"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    active_ids = seen if seen is not None else set()
    value_id = id(value)
    if value_id in active_ids:
        return f"<recursive:{type(value).__name__}>"
    active_ids.add(value_id)
    try:
        if isinstance(value, Enum):
            return _json_compatible(value.value, depth=depth + 1, seen=active_ids)
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            for kwargs in (
                {"mode": "json", "exclude_none": True},
                {"exclude_none": True},
                {},
            ):
                try:
                    dumped = model_dump(**kwargs)
                except Exception as exc:
                    # Signature probing: older pydantic rejects newer kwargs.
                    logger.debug("otel: model_dump({}) rejected - {}", kwargs, exc)
                    continue
                return _json_compatible(dumped, depth=depth + 1, seen=active_ids)
            return _controlled_string(value)
        if is_dataclass(value) and not isinstance(value, type):
            try:
                dumped = asdict(value)
            except Exception:
                return _controlled_string(value)
            return _json_compatible(dumped, depth=depth + 1, seen=active_ids)
        if isinstance(value, Mapping):
            normalized: dict[str, Any] = {}
            try:
                for key, item in value.items():
                    normalized[_controlled_string(key)] = _json_compatible(
                        item,
                        depth=depth + 1,
                        seen=active_ids,
                    )
            except Exception:
                return _controlled_string(value)
            return normalized
        if isinstance(value, (list, tuple, set, frozenset)):
            try:
                return [
                    _json_compatible(item, depth=depth + 1, seen=active_ids)
                    for item in value
                ]
            except Exception:
                return _controlled_string(value)
        return _controlled_string(value)
    except Exception:
        return _controlled_string(value)
    finally:
        active_ids.discard(value_id)


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
        return trace.get_tracer(_TRACER_NAME)

    @staticmethod
    def _get_parent_context_for_llm_tool() -> Any:
        """Resolve parent context for LLM/tool span creation.

        Returns None when no valid parent span exists — callers must
        skip span creation in that case rather than attaching to the
        root context, which would produce orphan spans outside the
        active trace.
        """
        iteration_span = get_current_agent_span()
        tool_span = get_current_tool_span()
        root_for_scope = get_root_span()
        is_single_agent_trace = bool(
            root_for_scope is not None
            and root_for_scope.attributes.get(OJ_TRACE_ROOT)
        )
        if (
            is_single_agent_trace
            and tool_span is not None
            and tool_span.is_recording()
        ):
            # Pick the structurally deeper active scope. During ordinary tool
            # execution the tool hangs under the current agent, while a
            # dispatched sub-agent hangs under that tool and becomes deeper.
            agent_is_below_tool = (
                iteration_span is not None
                and iteration_span.is_recording()
                and iteration_span.parent is not None
                and iteration_span.parent.span_id == tool_span.context.span_id
            )
            if not agent_is_below_tool:
                return set_span_in_context(tool_span, otel_context.get_current())
        if iteration_span is not None:
            if iteration_span.is_recording():
                return set_span_in_context(iteration_span, otel_context.get_current())
            else:
                logger.warning(
                    "otel: _get_parent_context - agent span ENDED name={} "
                    "trace_id={:032x} span_id={:016x}",
                    iteration_span.name,
                    iteration_span.context.trace_id,
                    iteration_span.context.span_id,
                )

        root_span = get_root_span()
        if root_span is not None:
            if root_span.is_recording():
                logger.debug(
                    "otel: _get_parent_context - fallback to root span name={} "
                    "trace_id={:032x} span_id={:016x}",
                    root_span.name,
                    root_span.context.trace_id,
                    root_span.context.span_id,
                )
                return set_span_in_context(root_span, otel_context.get_current())
            else:
                logger.warning(
                    "otel: _get_parent_context - root span ENDED name={} "
                    "trace_id={:032x} span_id={:016x}",
                    root_span.name,
                    root_span.context.trace_id,
                    root_span.context.span_id,
                )

        logger.debug("otel: no valid parent span for LLM/tool — skipping span creation")
        return None

    # ------------------------------------------------------------------
    # LLM
    # ------------------------------------------------------------------

    async def on_llm_invoke_input(self, *args: Any, **kwargs: Any) -> None:
        try:
            self._open_llm_span(kwargs)
        except Exception as exc:
            logger.exception("otel: on_llm_invoke_input failed: {}", exc)

    async def on_llm_stream_input(self, *args: Any, **kwargs: Any) -> None:
        try:
            self._open_llm_span(kwargs, is_streaming=True)
        except Exception as exc:
            logger.exception("otel: on_llm_stream_input failed: {}", exc)

    async def on_llm_input(self, *args: Any, **kwargs: Any) -> None:
        """Enrich the open span with the provider-normalized request.

        ``LLM_*_INPUT`` opens the span before the model client normalizes its
        request. The provider's existing ``LLM_INPUT`` event is therefore the
        authoritative source for the additive structured request fields. It
        never closes or replaces the span and leaves all legacy indexed fields
        untouched.
        """
        try:
            span = get_current_llm_span()
            state = getattr(span, "otel_llm_state", None) if span else None
            if state is None or not state.span.is_recording():
                return
            messages = kwargs.get("messages")
            if messages and not state.context_window_committed:
                provider_messages = self._normalize_messages(messages)
                if len(provider_messages) == len(state.message_occurrence_ids):
                    self._record_standard_structured_input(state.span, provider_messages)
                    trajectory_messages = self._trajectory_messages(
                        provider_messages,
                        occurrence_ids=state.message_occurrence_ids,
                        source_metadata=state.message_metadata,
                    )
                else:
                    trajectory_messages = [dict(item) for item in state.initial_trajectory_messages]
                publish_span_snapshot(state.span, "attributes")
                emit_context_window_commit(
                    tracer=self._tracer(),
                    llm_span=state.span,
                    messages=trajectory_messages,
                    request_purpose=state.request_purpose,
                )
                state.context_window_committed = True
        except Exception as exc:
            logger.warning("otel: on_llm_input failed: {}", exc)

    async def on_llm_stream_output(self, *args: Any, **kwargs: Any) -> Any:
        try:
            span = get_current_llm_span()
            state = getattr(span, "otel_llm_state", None) if span else None

            if state is None or not state.span.is_recording():
                return kwargs.get("result")

            chunk = kwargs.get("result")
            now_ns = time.monotonic_ns()
            if state.first_chunk_ns is None:
                state.first_chunk_ns = now_ns
                ttft_ms = (state.first_chunk_ns - state.start_ns) / 1_000_000.0
                if state.span.is_recording():
                    state.span.set_attribute(GEN_AI_RESPONSE_TTFT_MS, ttft_ms)
                    state.span.set_attribute(GEN_AI_RESPONSE_TTFC, ttft_ms / 1000.0)
            state.last_chunk_ns = now_ns
            delta = _coerce_message_content(_message_content(chunk))
            reasoning_chunk = str(getattr(chunk, "reasoning_content", "") or "")
            if reasoning_chunk:
                # Record reasoning timing from chunk callbacks. SDK chunks have
                # no timestamps, so monotonic_ns here is the best available point.
                now_ns = time.monotonic_ns()
                if state.reasoning_first_ns is None:
                    state.reasoning_first_ns = now_ns
                    state.reasoning_start_wall_ns = time.time_ns()
                state.reasoning_last_ns = now_ns
            if state.span.is_recording() and delta:
                state.span.add_event(
                    name="llm.chunk",
                    attributes={
                        "delta_chars": len(delta),
                    },
                )
            if state.span.is_recording():
                self._record_stream_event(state, chunk, delta, reasoning_chunk)
            self._maybe_record_response_attrs(state, chunk)
            if state.span.is_recording():
                publish_span_snapshot(state.span, "stream_chunk")
        except Exception as exc:
            logger.warning("otel: on_llm_stream_output failed: {}", exc)
        return kwargs.get("result")

    async def on_llm_output(self, *args: Any, **kwargs: Any) -> None:
        """Apply provider output facts and preserve the legacy terminal event.

        Calls routed through ``Model`` have dedicated terminal events, so their
        provider ``LLM_OUTPUT`` only enriches the still-open span. Legacy
        callback integrations that explicitly opened a span through
        ``LLM_INVOKE_INPUT``/``LLM_STREAM_INPUT`` treated this event as
        terminal; they retain that behavior when no unified lifecycle scope
        is active. Calling a raw provider client without an opening event is
        not a public observed entry point.
        """
        state = None
        try:
            span = get_current_llm_span()
            state = getattr(span, "otel_llm_state", None) if span else None
            if state is None:
                logger.debug("otel: on_llm_output — no open LLM span to enrich")
                return
            if not state.span.is_recording():
                logger.debug("otel: on_llm_output — span already ended")
                return
            usage_from_trigger = kwargs.get("usage")
            if usage_from_trigger is not None:
                self._record_usage_attrs(state, usage_from_trigger, skip_existing=True)

            if expects_unified_llm_completion():
                return

            # Legacy manually-opened callback path: LLM_OUTPUT remains terminal.
            popped = pop_current_llm_span()
            state = getattr(popped, "otel_llm_state", None) if popped else None
            if state is None or not state.span.is_recording():
                return

            response = kwargs.get("response")
            completion_text = str(response or "")
            reasoning_text = str(kwargs.get("reasoning_content") or "")
            if not reasoning_text and response is not None and not isinstance(response, str):
                reasoning_text = str(getattr(response, "reasoning_content", "") or "")

            tool_calls = kwargs.get("tool_calls") or getattr(response, "tool_calls", None)
            tc_json = _serialize_tool_calls(tool_calls)
            self._finalize_llm_span_output(
                state,
                completion_text,
                reasoning_text,
                tc_json=tc_json,
                response=response,
                usage=usage_from_trigger,
            )
        except BaseException as exc:
            if isinstance(exc, Exception):
                logger.warning("otel: on_llm_output failed: {}", exc)
            if state is not None:
                try:
                    if state.span.is_recording():
                        state.span.set_status(
                            Status(StatusCode.ERROR, f"on_llm_output failed: {exc}")
                        )
                        state.span.end()
                except Exception as cleanup_exc:
                    logger.warning(
                        "otel: on_llm_output cleanup also failed: {}",
                        cleanup_exc,
                    )
            if not isinstance(exc, Exception):
                raise

    async def on_llm_stream_completed(self, *args: Any, **kwargs: Any) -> Any:
        """Close one naturally exhausted stream with its accumulated result."""
        state = None
        try:
            span = pop_current_llm_span()
            state = getattr(span, "otel_llm_state", None) if span else None
            if state is None:
                return kwargs.get("result")
            self._close_llm_span(state, kwargs.get("result"))
        except BaseException as exc:
            if isinstance(exc, Exception):
                logger.warning("otel: on_llm_stream_completed failed: {}", exc)
            if state is not None:
                try:
                    if state.span.is_recording():
                        state.span.set_status(
                            Status(StatusCode.ERROR, f"on_llm_stream_completed failed: {exc}")
                        )
                        state.span.end()
                except Exception as cleanup_exc:
                    logger.warning(
                        "otel: on_llm_stream_completed cleanup also failed: {}",
                        cleanup_exc,
                    )
            if not isinstance(exc, Exception):
                raise
        return kwargs.get("result")

    async def on_llm_invoke_output(self, *args: Any, **kwargs: Any) -> Any:
        state = None
        try:
            # Peek first to check if it's streaming (leave to the unified
            # LLM_STREAM_COMPLETED event).
            span_peek = get_current_llm_span()
            state_peek = getattr(span_peek, "otel_llm_state", None) if span_peek else None
            if state_peek is None:
                return kwargs.get("result")
            if state_peek.is_streaming:
                return kwargs.get("result")
            # Non-streaming: pop and close
            span = pop_current_llm_span()
            state = getattr(span, "otel_llm_state", None) if span else None
            if state is None:
                return kwargs.get("result")
            response = kwargs.get("result")
            self._close_llm_span(state, response)
        except BaseException as exc:
            if isinstance(exc, Exception):
                logger.warning("otel: on_llm_invoke_output failed: {}", exc)
            if state is not None:
                try:
                    if state.span.is_recording():
                        state.span.set_status(Status(StatusCode.ERROR, f"on_llm_invoke_output failed: {exc}"))
                        state.span.end()
                except Exception as cleanup_exc:
                    logger.warning("otel: on_llm_invoke_output cleanup also failed: {}", cleanup_exc)
            if not isinstance(exc, Exception):
                raise
        return kwargs.get("result")

    async def on_llm_call_error(self, *args: Any, **kwargs: Any) -> None:
        state = None
        try:
            span = pop_current_llm_span()
            state = getattr(span, "otel_llm_state", None) if span else None

            if state is None:
                return

            if not state.span.is_recording():
                return

            exc = kwargs.get("error") or kwargs.get("exception")
            if state.span.is_recording():
                if isinstance(exc, BaseException):
                    state.span.record_exception(exc)
                    state.span.set_attribute(ERROR_TYPE, type(exc).__name__)
                    state.span.set_status(Status(StatusCode.ERROR, str(exc)))
                else:
                    state.span.set_status(Status(StatusCode.ERROR, "llm call error"))
                state.span.end()
        except BaseException as exc:
            if isinstance(exc, Exception):
                logger.exception("otel: on_llm_call_error failed: {}", exc)
            if state is not None:
                try:
                    if state.span.is_recording():
                        state.span.set_status(Status(StatusCode.ERROR, "llm call error"))
                        state.span.end()
                except Exception as cleanup_exc:
                    logger.warning("otel: on_llm_call_error cleanup also failed: {}", cleanup_exc)
            if not isinstance(exc, Exception):
                raise

    # ------------------------------------------------------------------
    # Tool
    # ------------------------------------------------------------------

    async def on_tool_call_started(self, *args: Any, **kwargs: Any) -> None:
        """Open a tool span with explicit parent context."""
        try:
            tool_name = str(kwargs.get("tool_name") or "unknown")
            tool_id = kwargs.get("tool_id")
            inputs = kwargs.get("inputs")

            authoritative = self._matching_authoritative_tool_span(tool_name, tool_id)
            if authoritative is not None:
                # The Ability rail owns start/end, but this lower-level event
                # has the exact legacy input tuple and resource id. Enrich the
                # same span so old fields retain their historical value shape.
                if tool_id is not None:
                    authoritative.set_attribute(GEN_AI_TOOL_ID, str(tool_id))
                raw_input = self._serialize_tool_inputs(inputs)
                redacted_input = redact_prompt(raw_input, self._config)
                authoritative.set_attribute(GEN_AI_TOOL_INPUT, redacted_input)
                authoritative.set_attribute(LANGFUSE_OBSERVATION_INPUT, redacted_input)
                publish_span_snapshot(authoritative, "attributes")
                return

            parent_ctx = self._get_parent_context_for_llm_tool()
            if parent_ctx is None:
                return

            span = self._tracer().start_span(
                name=f"tool.{tool_name}",
                kind=SpanKind.INTERNAL,
                context=parent_ctx,
            )
            span.set_attribute(LANGFUSE_OBSERVATION_TYPE, "tool")
            span.set_attribute(GEN_AI_OPERATION_NAME, "execute_tool")
            span.set_attribute(OJ_TRACE_SCHEMA_VERSION, "1")
            span.set_attribute(OJ_TRAJECTORY_RECORD_KIND, "tool")
            span.set_attribute(GEN_AI_TOOL_NAME, tool_name)
            if tool_id is not None:
                span.set_attribute(GEN_AI_TOOL_ID, str(tool_id))
            raw_input = self._serialize_tool_inputs(inputs)
            redacted_input = redact_prompt(raw_input, self._config)
            span.set_attribute(GEN_AI_TOOL_INPUT, redacted_input)
            span.set_attribute(GEN_AI_TOOL_CALL_ARGUMENTS, redacted_input)
            span.set_attribute(LANGFUSE_OBSERVATION_INPUT, redacted_input)
            self._propagate_session_context(span)
            self._stamp_parent_member_name(span)
            push_tool_span(tool_name, span)
            publish_span_snapshot(span, "attributes")
        except Exception as exc:
            logger.warning("otel: on_tool_call_started failed: {}", exc)

    async def on_tool_call_finished(self, *args: Any, **kwargs: Any) -> Any:
        try:
            tool_name = str(kwargs.get("tool_name") or "unknown")
            result = kwargs.get("result")
            tool_id = kwargs.get("tool_id")
            authoritative = self._matching_authoritative_tool_span(tool_name, tool_id)
            if authoritative is not None:
                serialized_output = self._serialize_tool_result(result)
                redacted = redact_completion(serialized_output, self._config)
                authoritative.set_attribute(GEN_AI_TOOL_OUTPUT, redacted)
                authoritative.set_attribute(GEN_AI_TOOL_CALL_RESULT, redacted)
                authoritative.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, redacted)
                publish_span_snapshot(authoritative, "output")
                return result
            span = pop_tool_span(tool_name)
            if span is None:
                return result

            if not span.is_recording():
                logger.warning(
                    "WRITE_ON_ENDED_SPAN: where=on_tool_call_finished name={} span_id={:016x}",
                    getattr(span, "name", "<no-name>"),
                    getattr(getattr(span, "context", None), "span_id", 0),
                )
                return result

            serialized_output = self._serialize_tool_result(result)
            redacted = redact_completion(serialized_output, self._config)
            span.set_attribute(GEN_AI_TOOL_OUTPUT, redacted)
            span.set_attribute(GEN_AI_TOOL_CALL_RESULT, redacted)
            span.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, redacted)
            # A tool that returns ``success=False`` never raises, so the status
            # has to come from the result itself or the call reports OK.
            failure_reason = tool_failure_reason(result)
            if failure_reason is None:
                span.set_status(Status(StatusCode.OK))
            else:
                span.set_attribute(ERROR_TYPE, TOOL_REPORTED_FAILURE)
                span.set_status(Status(StatusCode.ERROR, failure_reason))
            span.end()
        except Exception as exc:
            import traceback
            logger.warning("otel: on_tool_call_finished failed: {}\n{}", exc, traceback.format_exc())
        return kwargs.get("result")

    async def on_tool_call_error(self, *args: Any, **kwargs: Any) -> None:
        try:
            tool_name = str(kwargs.get("tool_name") or "unknown")
            exc = kwargs.get("error") or kwargs.get("exception")
            tool_id = kwargs.get("tool_id")
            if self._matching_authoritative_tool_span(tool_name, tool_id) is not None:
                return
            span = pop_tool_span(tool_name)
            if span is None:
                return

            if not span.is_recording():
                logger.warning(
                    "WRITE_ON_ENDED_SPAN: where=on_tool_call_error name={} span_id={:016x}",
                    getattr(span, "name", "<no-name>"),
                    getattr(getattr(span, "context", None), "span_id", 0),
                )
                return

            if span.is_recording():
                if isinstance(exc, BaseException):
                    # The ability manager still hands the model a tool result
                    # for a raised call; record the same text so the span is
                    # not the one place that call looks like it returned
                    # nothing.
                    recorded_output = tool_result_for_exception(exc)
                    redacted = redact_completion(recorded_output, self._config)
                    span.set_attribute(GEN_AI_TOOL_OUTPUT, redacted)
                    span.set_attribute(GEN_AI_TOOL_CALL_RESULT, redacted)
                    span.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, redacted)
                    span.record_exception(exc)
                    span.set_attribute(ERROR_TYPE, type(exc).__name__)
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                else:
                    span.set_status(Status(StatusCode.ERROR, "tool call error"))
                span.end()
        except Exception as exc:
            logger.exception("otel: on_tool_call_error failed: {}", exc)

    # ------------------------------------------------------------------
    # Agent
    # ------------------------------------------------------------------

    async def on_agent_invoke_input(self, *args: Any, **kwargs: Any) -> None:
        """Handle AGENT_INVOKE_INPUT callback.

        Root span creation is owned by the host integration
        (the host owns the full lifecycle: create before invoke, close in finally).
        This callback only:
          1. Sets ContextVars (session_id)
          2. Propagates query to the root span input
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

            # Set the generic session binding for subsequent callback events.
            if session_id:
                set_current_session_id(session_id)

            # Propagate query to the host-provided root span.
            if query:
                root_span = get_root_span(session_id=session_id) if session_id else get_root_span()
                if root_span is not None and root_span.is_recording():
                    if not root_span.attributes.get(LANGFUSE_OBSERVATION_INPUT):
                        root_span.set_attribute(LANGFUSE_OBSERVATION_INPUT,
                                                redact_prompt(query, self._config))
                        publish_span_snapshot(root_span, "attributes")

        except Exception as exc:
            logger.exception("otel: on_agent_invoke_input failed: {}", exc)

    async def on_agent_invoke_output(self, *args: Any, **kwargs: Any) -> Any:
        """Handle AGENT_INVOKE_OUTPUT callback.

        DO NOT close agent span here! (managed by Rail)
        Sets root span output from the FINAL invoke result — this is the
        overall agent output, distinct from per-iteration results written by
        AgentObservabilityRail.after_task_iteration.
        """
        try:
            result = kwargs.get("result")
            if result is not None:
                session = kwargs.get("session")
                session_id = session.get_session_id() if session else None
                root_span = get_root_span(session_id=session_id) if session_id else get_root_span()
                if root_span is not None and root_span.is_recording():
                    root_span.set_attribute(
                        LANGFUSE_OBSERVATION_OUTPUT,
                        redact_completion(str(result), self._config),
                    )
                    publish_span_snapshot(root_span, "output")
        except Exception as exc:
            logger.exception("otel: on_agent_invoke_output failed: {}", exc)
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

    def _open_llm_span(
        self,
        kwargs: dict[str, Any],
        is_streaming: bool = False,
    ) -> Span | None:
        """Open an LLM span with explicit parent context."""
        if is_llm_observation_suppressed():
            return None
        parent_ctx = self._get_parent_context_for_llm_tool()
        if parent_ctx is None:
            return None

        messages = kwargs.get("messages") or []
        model_name = str(kwargs.get("model") or self._derive_model_name(kwargs) or "").strip()
        # Identity of the request this span stands for. Everything that
        # arrives later — chunks, usage, completion, errors — is matched back
        # to the span through it, so it must be read here, while the opening
        # callback still runs inside the caller's LLM call scope.
        call_id = get_current_llm_call_id()
        span = self._tracer().start_span(
            name="llm.call",
            kind=SpanKind.CLIENT,
            context=parent_ctx,
        )
        if call_id:
            span.set_attribute(GEN_AI_REQUEST_ID, call_id)
        span.set_attribute(OJ_INFERENCE_ID, f"{span.context.span_id:016x}")
        span.set_attribute(GEN_AI_SYSTEM, _gen_ai_system_name(self._config))
        span.set_attribute(GEN_AI_OPERATION_NAME, "chat")
        span.set_attribute(OJ_TRACE_SCHEMA_VERSION, "1")
        span.set_attribute(OJ_TRAJECTORY_RECORD_KIND, "inference")
        span.set_attribute(LANGFUSE_OBSERVATION_TYPE, "generation")
        span.set_attribute(GEN_AI_REQUEST_STREAM, is_streaming)
        provider_name = self._derive_provider_name(kwargs)
        span.set_attribute(GEN_AI_PROVIDER_NAME, provider_name)
        if model_name and model_name.lower() != "unknown":
            span.set_attribute(GEN_AI_REQUEST_MODEL, model_name)

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
        self._record_input_message_provenance(span, messages)
        self._record_standard_structured_input(span, messages)

        span.set_attribute(OJ_REQUEST_NUMBER, self._next_request_number())
        request_purpose = kwargs.get("request_purpose")
        if request_purpose not in ("assistant", "compaction"):
            request_purpose = "assistant"
        if request_purpose in ("assistant", "compaction"):
            span.set_attribute(OJ_REQUEST_PURPOSE, request_purpose)

        # ── Delta tracking ──────────────────────────────────────────
        # The previous LLM call's message_count decides whether this is a
        # subsequent call. It drives both per-message prompt attributes and
        # the langfuse.observation.input JSON.
        #
        #   - First call (prev_count == 0):  emit ALL messages as attributes.
        #   - Context compression (current < prev): emit ALL messages.
        #   - Subsequent call: emit only new (delta) messages.
        #
        # System messages are ALWAYS emitted regardless of delta — they form
        # the stable instruction baseline that every span needs.
        #
        # Cross-iteration: the count is stored on the root span (not the
        # iteration span) keyed by agent_id, because each iteration opens and
        # closes its own agent span — a count stored there is lost before the
        # next iteration's first LLM call, which would then re-emit the full
        # prompt. Each agent keeps its own chain
        # (gen_ai.request.prev_message_count.<agent_id>); OTel span
        # set_attribute is internally locked so no manual locking is needed.
        agent_span = get_current_agent_span()
        root_span = get_root_span()
        agent_id = ""
        if agent_span is not None:
            raw_id = agent_span.attributes.get(AT_AGENT_ID)
            if raw_id is not None:
                agent_id = str(raw_id)

        prev_count_raw: int = 0
        if root_span is not None and agent_id:
            prev_attr = root_span.attributes.get(f"{GEN_AI_REQUEST_MESSAGE_COUNT_PREFIX}{agent_id}")
            if prev_attr is not None:
                try:
                    prev_count_raw = int(str(prev_attr))
                except (ValueError, TypeError):
                    pass

        is_first_call = prev_count_raw == 0 or msg_count < prev_count_raw

        # Update the per-member count on the root span for the next LLM call
        # of this member (across iterations). Also keep the per-span display
        # count on the current iteration span.
        if root_span is not None and root_span.is_recording() and agent_id:
            root_span.set_attribute(f"{GEN_AI_REQUEST_MESSAGE_COUNT_PREFIX}{agent_id}", msg_count)
        if agent_span is not None:
            agent_span.set_attribute(GEN_AI_REQUEST_MESSAGE_COUNT, msg_count)

        # ── langfuse.observation.input (delta, same logic) ───────────
        if is_first_call:
            # System messages are carried by gen_ai.system_instructions and
            # gen_ai.input.messages; this channel is Langfuse's own view of
            # what the turn added.
            delta_msgs = [m for m in messages if _message_role(m) != "system"]
        else:
            delta_msgs = messages[prev_count_raw:]

        input_json = json.dumps(
            [{"role": _message_role(m),
              "content": _coerce_message_content(_message_content(m))}
             for m in delta_msgs],
            ensure_ascii=False, default=str,
        ) if delta_msgs else "[]"
        input_max_len = max(self._config.attribute_value_max_length * 10, 81920)
        span.set_attribute(LANGFUSE_OBSERVATION_INPUT,
                           truncate(input_json, input_max_len))

        tools = kwargs.get("tools")
        if tools:
            normalized_tools = _json_compatible(tools)
            try:
                serialized_tools = json.dumps(normalized_tools, ensure_ascii=False)
            except Exception:
                serialized_tools = json.dumps(_controlled_string(tools), ensure_ascii=False)
            span.set_attribute(GEN_AI_TOOL_DEFINITIONS, serialized_tools)

        self._propagate_session_context(span, include_additive=True)
        subject_id = str(span.attributes.get(OJ_EXECUTION_SUBJECT_ID) or "")
        session_id = str(
            span.attributes.get(OJ_SESSION_ID)
            or span.attributes.get(GEN_AI_CONVERSATION_ID)
            or get_current_session_id()
            or ""
        )
        if subject_id and session_id:
            subject_request_number = next_execution_subject_request_number(
                session_id=session_id,
                subject_id=subject_id,
            )
            span.set_attribute(
                OJ_EXECUTION_SUBJECT_REQUEST_NUMBER,
                subject_request_number,
            )
        self._stamp_parent_member_name(span)

        message_occurrence_ids = self._message_occurrence_ids(messages)
        message_metadata = tuple(_get_field(message, "metadata") for message in messages)
        initial_trajectory_messages = self._trajectory_messages(
            messages,
            occurrence_ids=message_occurrence_ids,
            source_metadata=message_metadata,
        )
        _llm_st = LlmSpanState(
            span=span,
            start_ns=time.monotonic_ns(),
            call_id=call_id,
            is_streaming=is_streaming,
            request_purpose=request_purpose,
            message_occurrence_ids=message_occurrence_ids,
            message_metadata=message_metadata,
            initial_trajectory_messages=tuple(initial_trajectory_messages),
        )
        span.otel_llm_state = _llm_st  # attach state to span object (context-immune)
        self._stamp_llm_semantic_identity(_llm_st)

        tracker = get_active_span_tracker()
        if tracker is not None:
            tracker.register_llm_span(call_id, span)
        publish_span_snapshot(span, "attributes")

        logger.debug(
            "otel: _open_llm_span name=llm.call trace_id={:032x} span_id={:016x} "
            "parent_span_id={:016x} streaming={} call_id={}",
            span.context.trace_id, span.context.span_id,
            span.parent.span_id if span.parent else 0, is_streaming, call_id or "<none>",
        )
        return span

    def _close_llm_span(self, state: LlmSpanState, response: Any) -> None:
        if not state.span.is_recording():
            logger.warning(
                "WRITE_ON_ENDED_SPAN: where=_close_llm_span name={} span_id={:016x}",
                getattr(state.span, "name", "<no-name>"),
                getattr(getattr(state.span, "context", None), "span_id", 0),
            )
            return

        try:
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
                usage=getattr(response, "usage_metadata", None),
            )
        except BaseException as exc:
            if isinstance(exc, Exception):
                logger.warning("otel: _close_llm_span failed: {}", exc)
            try:
                if state.span.is_recording():
                    state.span.set_status(Status(StatusCode.ERROR, f"_close_llm_span failed: {exc}"))
                    state.span.end()
            except Exception as cleanup_exc:
                logger.warning("otel: _close_llm_span cleanup also failed: {}", cleanup_exc)
            if not isinstance(exc, Exception):
                raise

    def _finalize_llm_span_output(
        self,
        state: LlmSpanState,
        completion_text: str,
        reasoning_text: str = "",
        *,
        tc_json: str = "",
        response: Any = None,
        usage: Any = None,
    ) -> None:
        """Shared: set completion/output attrs, reasoning sub-span, close LLM span.

        Called by both ``_close_llm_span`` (non-streaming) and
        ``on_llm_output`` (streaming final) to avoid ~130 lines of
        duplicated output assembly.

        The main llm.call span is always ended (even on error) so the
        span never becomes an orphan.  The reasoning sub-span is
        best-effort and created after the main span is safely closed.
        """
        try:
            self._record_structured_output(
                state.span,
                response,
                fallback_text=completion_text,
            )
            self._record_response_details(state, response)
            total_latency_ms = (time.monotonic_ns() - state.start_ns) / 1_000_000.0
            state.span.set_attribute(OJ_GEN_AI_RESPONSE_TOTAL_LATENCY_MS, total_latency_ms)
            redacted_compl = redact_completion(completion_text, self._config)

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
            if usage:
                # Dump the whole usage object so cache_tokens / reasoning_tokens
                # (and any future fields) flow through without per-field filters.
                dump = (
                    usage.model_dump(exclude_none=True)
                    if hasattr(usage, "model_dump")
                    else vars(usage)
                )
                if dump:
                    response_obj["usage"] = dump
            output_json = json.dumps(response_obj, ensure_ascii=False, default=str)
            state.span.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, redact_completion(output_json, self._config))
        finally:
            # Always end the main llm.call span — even if attribute setting
            # above threw, the span must not become an orphan.
            if state.span.is_recording():
                self._stamp_llm_semantic_identity(state)
                state.span.set_status(Status(StatusCode.OK))
                state.span.end()

        # Reasoning sub-span (best-effort; created after the main span is
        # safely closed so a failure here never orphans the main span).
        if reasoning_text:
            try:
                # Manual span lifecycle: start_time = wall-clock at the first
                # reasoning chunk; end_time = start + measured duration. finalize
                # runs long after the last chunk, so a default start would exceed
                # end_time and collapse duration to 0.
                reasoning_first_ns = state.reasoning_first_ns
                reasoning_last_ns = state.reasoning_last_ns
                reasoning_start_wall_ns = state.reasoning_start_wall_ns
                has_timing = (
                    reasoning_first_ns is not None
                    and reasoning_last_ns is not None
                    and reasoning_start_wall_ns is not None
                )
                # Without chunks there is nothing to measure: a non-streaming
                # call returns reasoning and answer together. Anchor the span at
                # the start of its llm.call — where the reasoning happened —
                # instead of letting it default to finalize time, which parks a
                # zero-length span at the *end* of the call, after the answer it
                # preceded.
                call_start_wall_ns = getattr(state.span, "start_time", None)
                if has_timing:
                    start_kwarg: dict[str, Any] = {"start_time": reasoning_start_wall_ns}
                elif call_start_wall_ns is not None:
                    start_kwarg = {"start_time": call_start_wall_ns}
                else:
                    start_kwarg = {}
                reasoning_span = self._tracer().start_span(
                    name="llm.reasoning",
                    context=set_span_in_context(state.span),
                    **start_kwarg,
                )
                redacted_reasoning = redact_completion(reasoning_text, self._config)
                reasoning_span.set_attribute(
                    GEN_AI_OUTPUT_MESSAGES,
                    json.dumps(
                        [{
                            "role": "reasoning",
                            "parts": [{"type": "text", "content": redacted_reasoning}],
                        }],
                        ensure_ascii=False,
                    ),
                )
                # Langfuse observation input/output for UI visibility
                reasoning_span.set_attribute(LANGFUSE_OBSERVATION_INPUT, "llm reasoning")
                reasoning_span.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, redacted_reasoning)
                reasoning_span.set_attribute(LANGFUSE_OBSERVATION_TYPE, "span")
                reasoning_span.set_attribute(GEN_AI_OPERATION_NAME, "chat")
                reasoning_span.set_attribute(OJ_TRACE_SCHEMA_VERSION, "1")
                reasoning_span.set_attribute(OJ_TRAJECTORY_RECORD_KIND, "reasoning")
                self._copy_correlation_attributes(state.span, reasoning_span)
                # Mirror reasoning_tokens onto the reasoning span (also on the
                # parent llm.call span via _record_usage_attrs). Read straight
                # from the usage object — never compute it.
                rt = getattr(usage, "reasoning_tokens", 0) or 0
                if rt:
                    reasoning_span.set_attribute(GEN_AI_USAGE_REASONING_TOKENS, int(rt))
                self._stamp_parent_member_name(reasoning_span)
                reasoning_span.set_status(Status(StatusCode.OK))
                if has_timing:
                    dur_ns = reasoning_last_ns - reasoning_first_ns  # type: ignore[operator]
                    reasoning_span.set_attribute(
                        GEN_AI_REASONING_DURATION_MS,
                        dur_ns / 1_000_000.0,
                    )
                    reasoning_span.end(end_time=reasoning_start_wall_ns + dur_ns)  # type: ignore[operator]
                else:
                    # No duration attribute: none was measured, and a zero is a
                    # measurement. The reason is recorded instead.
                    reasoning_span.set_attribute(
                        GEN_AI_REASONING_TIMING, REASONING_TIMING_UNMEASURED
                    )
                    reasoning_span.end(end_time=call_start_wall_ns)
            except Exception as exc:
                logger.warning("otel: _finalize_llm_span_output reasoning span failed: {}", exc)

    @staticmethod
    def _stamp_llm_semantic_identity(state: LlmSpanState) -> None:
        """Keep inference identity after bounded-attribute FIFO eviction."""
        span = state.span
        if state.call_id:
            span.set_attribute(GEN_AI_REQUEST_ID, state.call_id)
        span.set_attribute(OJ_INFERENCE_ID, f"{span.context.span_id:016x}")
        span.set_attribute(GEN_AI_OPERATION_NAME, "chat")
        span.set_attribute(OJ_TRACE_SCHEMA_VERSION, "1")
        span.set_attribute(OJ_TRAJECTORY_RECORD_KIND, "inference")
        span.set_attribute(LANGFUSE_OBSERVATION_TYPE, "generation")
        span.set_attribute(GEN_AI_REQUEST_STREAM, state.is_streaming)

    def _record_usage_attrs(self, state: LlmSpanState, usage: Any, *, skip_existing: bool = False) -> None:
        """Record usage attributes (tokens, model_name) from usage_metadata.

        Cached prompt tokens and reasoning tokens are *subsets* of the prompt
        and completion counts the provider reports, not additional tokens. A
        backend that treats every ``gen_ai.usage.*`` key as its own additive
        category — Langfuse does, summing them per observation and per trace —
        then counts the cached prefix twice, which on a long agent run (where
        most of each prompt is a cache hit) inflates the trace total by more
        than half.

        For such a backend the subsets are carved out of their parent, so the
        keys are disjoint and add up to the reported total: ``prompt`` becomes
        the freshly processed prompt and ``completion`` the visible output.
        The subtraction is skipped when a subset does not fit inside its parent
        (a provider counting reasoning outside the completion), leaving the raw
        numbers rather than inventing one. For plain OTLP consumers nothing
        changes: ``gen_ai.usage.prompt_tokens`` keeps its semconv meaning of
        all input tokens.

        Args:
            state: Span state for the LLM call being recorded.
            usage: Provider usage metadata.
            skip_existing: Leave an attribute already written by an earlier
                trigger untouched.
        """
        if usage is None:
            return
        prompt_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        cache_read_raw = getattr(usage, "cache_read_tokens", None)
        cache_read_tokens = (
            max(int(cache_read_raw), 0)
            if cache_read_raw is not None
            else None
        )
        cache_tokens = cache_read_tokens or 0
        reasoning_tokens = int(getattr(usage, "reasoning_tokens", 0) or 0)
        if self._config is not None and self._config.backend == "langfuse":
            if 0 < cache_tokens <= prompt_tokens:
                prompt_tokens -= cache_tokens
            if 0 < reasoning_tokens <= completion_tokens:
                completion_tokens -= reasoning_tokens

        for value, dst_attr in (
            (prompt_tokens, GEN_AI_USAGE_PROMPT_TOKENS),
            (completion_tokens, GEN_AI_USAGE_COMPLETION_TOKENS),
            (int(getattr(usage, "total_tokens", 0) or 0), GEN_AI_USAGE_TOTAL_TOKENS),
            (reasoning_tokens, GEN_AI_USAGE_REASONING_TOKENS),
        ):
            if value and not (skip_existing and state.span.attributes.get(dst_attr)):
                state.span.set_attribute(dst_attr, value)

        # Additive current-profile fields always carry the provider's raw
        # totals. They deliberately do not inherit Langfuse's legacy carve-out
        # because cache/reasoning values are breakdowns, not extra tokens.
        raw_usage = (
            (int(getattr(usage, "input_tokens", 0) or 0), GEN_AI_USAGE_INPUT_TOKENS),
            (int(getattr(usage, "output_tokens", 0) or 0), GEN_AI_USAGE_OUTPUT_TOKENS),
            (
                int(getattr(usage, "reasoning_tokens", 0) or 0),
                GEN_AI_USAGE_REASONING_OUTPUT_TOKENS,
            ),
        )
        for value, dst_attr in raw_usage:
            if not (skip_existing and dst_attr in state.span.attributes):
                state.span.set_attribute(dst_attr, value)
        if cache_read_tokens is not None and not (
            skip_existing and GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS in state.span.attributes
        ):
            state.span.set_attribute(
                GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS,
                cache_read_tokens,
            )
        cache_write_tokens = getattr(usage, "cache_write_tokens", None)
        if cache_write_tokens is not None and not (
            skip_existing and GEN_AI_USAGE_CACHE_CREATION_INPUT_TOKENS in state.span.attributes
        ):
            state.span.set_attribute(
                GEN_AI_USAGE_CACHE_CREATION_INPUT_TOKENS,
                max(int(cache_write_tokens), 0),
            )

        for value, dst_attr in (
            (float(getattr(usage, "input_cost", 0) or 0), OJ_GEN_AI_USAGE_INPUT_COST),
            (float(getattr(usage, "output_cost", 0) or 0), OJ_GEN_AI_USAGE_OUTPUT_COST),
            (float(getattr(usage, "total_cost", 0) or 0), OJ_GEN_AI_USAGE_TOTAL_COST),
        ):
            if value and not (skip_existing and dst_attr in state.span.attributes):
                state.span.set_attribute(dst_attr, value)

        raw_output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        has_chunk_window = (
            state.first_chunk_ns is not None
            and state.last_chunk_ns is not None
            and state.last_chunk_ns >= state.first_chunk_ns
        )
        if raw_output_tokens > 1 and has_chunk_window:
            tpot_ms = (
                (state.last_chunk_ns - state.first_chunk_ns)
                / (raw_output_tokens - 1)
                / 1_000_000.0
            )
            if not (skip_existing and OJ_GEN_AI_RESPONSE_TPOT_MS in state.span.attributes):
                state.span.set_attribute(OJ_GEN_AI_RESPONSE_TPOT_MS, tpot_ms)
        model_name = getattr(usage, "model_name", "")
        if model_name and not (skip_existing and state.span.attributes.get(GEN_AI_RESPONSE_MODEL)):
            state.span.set_attribute(GEN_AI_RESPONSE_MODEL, str(model_name))

    def _maybe_record_response_attrs(self, state: LlmSpanState, response: Any) -> None:
        """Record usage and finish_reason carried by a response or chunk."""
        if response is None:
            return
        usage = getattr(response, "usage_metadata", None)
        if usage is not None:
            self._record_usage_attrs(state, usage, skip_existing=False)
        finish_reason = getattr(response, "finish_reason", None)
        if finish_reason and finish_reason != "null":
            state.span.set_attribute(GEN_AI_RESPONSE_FINISH_REASON, str(finish_reason))
            state.span.set_attribute(GEN_AI_RESPONSE_FINISH_REASONS, [str(finish_reason)])

    def _record_stream_event(
        self,
        state: LlmSpanState,
        chunk: Any,
        text_delta: str,
        reasoning_delta: str,
    ) -> None:
        """Write one additive event for one actual streaming callback."""
        attributes: dict[str, Any] = {OJ_EVENT_SEQUENCE: state.stream_event_sequence}
        state.stream_event_sequence += 1

        tool_calls = _get_field(chunk, "tool_calls") or []
        usage = _get_field(chunk, "usage_metadata")
        if reasoning_delta:
            attributes[OJ_STREAM_KIND] = "reasoning-delta"
            attributes[OJ_STREAM_TEXT] = redact_completion(reasoning_delta, self._config)
        elif tool_calls:
            attributes[OJ_STREAM_KIND] = "tool-call-delta"
            tool_call = tool_calls[0]
            tool_id = _get_field(tool_call, "id")
            tool_name = _get_field(tool_call, "name")
            arguments = _get_field(tool_call, "arguments")
            if tool_id:
                attributes[OJ_STREAM_TOOL_CALL_ID] = str(tool_id)
            if tool_name:
                attributes[OJ_STREAM_TOOL_CALL_NAME] = str(tool_name)
            if arguments not in (None, ""):
                attributes[OJ_STREAM_TOOL_CALL_ARGUMENTS_DELTA] = redact_completion(
                    _coerce_message_content(arguments), self._config
                )
        elif usage is not None and not text_delta:
            attributes[OJ_STREAM_KIND] = "usage"
        else:
            attributes[OJ_STREAM_KIND] = "text-delta"
            if text_delta:
                attributes[OJ_STREAM_TEXT] = redact_completion(text_delta, self._config)

        state.span.add_event("openjiuwen.stream.chunk", attributes=attributes)

    @staticmethod
    def _message_occurrence_ids(messages: Any) -> tuple[str, ...]:
        normalized = OtelCallbackHandler._normalize_messages(messages)
        seen: dict[str, int] = {}
        occurrence_ids: list[str] = []
        system_slot = 0
        for message in normalized:
            metadata = _get_field(message, "metadata")
            explicit = metadata.get("context_message_id") if isinstance(metadata, Mapping) else None
            if not explicit:
                explicit = _get_field(message, "message_id") or _get_field(message, "id")
            if not explicit and isinstance(metadata, Mapping):
                explicit = metadata.get("message_id") or metadata.get("openjiuwen.message_id")
            if not explicit and _message_role(message) == "system":
                explicit = f"openjiuwen:request-system-slot:{system_slot}"
                system_slot += 1
            if explicit:
                base = str(explicit)
                duplicate_index = seen.get(base, 0)
                seen[base] = duplicate_index + 1
                occurrence_ids.append(
                    base if duplicate_index == 0 else f"{base}#occurrence:{duplicate_index}"
                )
            else:
                occurrence_ids.append(uuid.uuid4().hex)
        return tuple(occurrence_ids)

    def _trajectory_value(self, value: Any) -> Any:
        normalized = _json_compatible(value)
        if isinstance(normalized, str):
            return redact_prompt(normalized, self._config)
        if isinstance(normalized, list):
            return [self._trajectory_value(item) for item in normalized]
        if isinstance(normalized, dict):
            return {key: self._trajectory_value(item) for key, item in normalized.items()}
        return normalized

    def _trajectory_messages(
        self,
        messages: Any,
        *,
        occurrence_ids: tuple[str, ...],
        source_metadata: tuple[Any, ...],
    ) -> list[dict[str, Any]]:
        normalized = self._normalize_messages(messages)
        ids = occurrence_ids
        if len(ids) != len(normalized):
            ids = self._message_occurrence_ids(normalized)
        result: list[dict[str, Any]] = []
        for index, message in enumerate(normalized):
            source_message_metadata = (
                source_metadata[index]
                if index < len(source_metadata)
                else None
            )
            item: dict[str, Any] = {
                "message_id": ids[index],
                "role": _message_role(message),
                "content": self._trajectory_value(_message_content(message)),
                **_trajectory_message_origin(message, source_message_metadata),
            }
            for key in ("tool_calls", "tool_call_id", "name"):
                value = _get_field(message, key)
                if value not in (None, ""):
                    item[key] = self._trajectory_value(value)
            metadata = _get_field(message, "metadata")
            if metadata is None:
                metadata = source_message_metadata
            if metadata is not None:
                item["metadata"] = self._trajectory_value(metadata)
            result.append(item)
        return result

    @staticmethod
    def _is_prompt_attachment_history(message: Any) -> bool:
        """Report whether this system message is injected dynamic context.

        Prompt-attachment snapshots and deltas are written into the
        conversation as it runs, so they belong to the chat history rather
        than to the instructions given alongside it.
        """

        metadata = _get_field(message, "metadata")
        return (
            isinstance(metadata, Mapping)
            and metadata.get("_openjiuwen_prompt_attachment_history") is True
        )

    def _record_standard_structured_input(self, span: Span, messages: Any) -> None:
        """Record the request as the two standard input attributes.

        ``gen_ai.system_instructions`` is defined as the instructions supplied
        separately from the chat history, which is the stable system prompt.
        A system turn injected into the conversation -- prompt-attachment
        history -- stays in ``gen_ai.input.messages`` as a ``system`` entry, so
        its boundary and its history marker survive; flattening it in with the
        instructions would lose both.
        """

        normalized = self._normalize_messages(messages)
        system_parts: list[dict[str, Any]] = []
        input_messages: list[dict[str, Any]] = []
        for message in normalized:
            role = _message_role(message)
            structured = self._structured_message(message, is_output=False)
            if role == "system" and not self._is_prompt_attachment_history(message):
                system_parts.extend(
                    self._structured_content_parts(
                        _message_content(message),
                        redact_prompt,
                    )
                )
                continue
            input_messages.append(structured)
        if system_parts:
            span.set_attribute(
                GEN_AI_SYSTEM_INSTRUCTIONS,
                json.dumps(system_parts, ensure_ascii=False, default=str),
            )
        if input_messages:
            span.set_attribute(
                GEN_AI_INPUT_MESSAGES,
                json.dumps(input_messages, ensure_ascii=False, default=str),
            )

    def _record_input_message_provenance(self, span: Span, messages: Any) -> None:
        normalized = self._normalize_messages(messages)
        provenance_entries: list[dict[str, Any]] = []
        input_message_index = 0
        for request_message_index, message in enumerate(normalized):
            if _message_role(message) == "system":
                continue

            metadata = _get_field(message, "metadata")
            provenance = (
                metadata.get(OPENJIUWEN_MESSAGE_PROVENANCE_METADATA)
                if isinstance(metadata, Mapping)
                else None
            )
            if (
                isinstance(provenance, Mapping)
                and provenance.get("kind") == "prompt_attachment"
                and provenance.get("scope") == "request"
            ):
                items: list[dict[str, Any]] = []
                raw_items = provenance.get("items")
                if isinstance(raw_items, (list, tuple)):
                    for raw_item in raw_items:
                        if not isinstance(raw_item, Mapping):
                            continue
                        item: dict[str, Any] = {}
                        for key in ("id", "section", "kind", "source"):
                            value = raw_item.get(key)
                            if isinstance(value, str):
                                item[key] = value
                            elif key == "source" and value is None:
                                item[key] = None
                        priority = raw_item.get("priority")
                        if isinstance(priority, int) and not isinstance(priority, bool):
                            item["priority"] = priority
                        items.append(item)
                provenance_entries.append({
                    "request_message_index": request_message_index,
                    "input_message_index": input_message_index,
                    "kind": "prompt_attachment",
                    "scope": "request",
                    "items": items,
                })
            input_message_index += 1

        if provenance_entries:
            span.set_attribute(
                OJ_GEN_AI_INPUT_MESSAGE_PROVENANCE,
                json.dumps(provenance_entries, ensure_ascii=False),
            )

    def _record_structured_output(
        self,
        span: Span,
        response: Any,
        *,
        fallback_text: str = "",
    ) -> None:
        """Record the reply as the standard output attribute.

        Args:
            span: The LLM span to record on.
            response: The provider reply; may be a message object or bare text.
            fallback_text: Text to record when ``response`` carries no content
                of its own -- a plain string reply has no ``content`` field to
                read, and this attribute is the only carrier of the reply.
        """

        if response is None:
            return
        structured = self._structured_message(response, is_output=True)
        has_text = any(
            part.get("content") for part in structured.get("parts", [])
            if isinstance(part, Mapping)
        )
        if not has_text and fallback_text:
            structured.setdefault("parts", []).append({
                "type": "text",
                "content": redact_completion(fallback_text, self._config),
            })
        span.set_attribute(
            GEN_AI_OUTPUT_MESSAGES,
            json.dumps([structured], ensure_ascii=False, default=str),
        )

    @staticmethod
    def _normalize_messages(messages: Any) -> list[Any]:
        if messages is None:
            return []
        if isinstance(messages, str):
            return [{"role": "user", "content": messages}]
        if isinstance(messages, (list, tuple)):
            return list(messages)
        return [messages]

    def _structured_message(self, message: Any, *, is_output: bool) -> dict[str, Any]:
        role = _message_role(message) or ("assistant" if is_output else "user")
        redact = redact_completion if is_output else redact_prompt
        parts: list[dict[str, Any]] = []

        reasoning = _get_field(message, "reasoning_content")
        if reasoning not in (None, ""):
            parts.append({
                "type": "reasoning",
                "content": redact(_coerce_message_content(reasoning), self._config),
            })

        content = _message_content(message)
        raw_content = _coerce_message_content(content)
        if raw_content:
            if role == "tool":
                redacted_content = redact(raw_content, self._config)
                response_value = _json_value_if_unchanged(raw_content, redacted_content)
                part: dict[str, Any] = {
                    "type": "tool_call_response",
                    "response": response_value,
                }
                tool_call_id = _get_field(message, "tool_call_id")
                name = _get_field(message, "name")
                if tool_call_id:
                    part["id"] = str(tool_call_id)
                if name:
                    part["name"] = str(name)
                parts.append(part)
            else:
                parts.extend(self._structured_content_parts(content, redact))

        for tool_call in _get_field(message, "tool_calls") or []:
            # OpenAI-shape calls nest name/arguments under "function"; native
            # ones carry them directly. Read whichever this one uses.
            function = _get_field(tool_call, "function")
            raw_arguments = _coerce_message_content(
                _get_field(tool_call, "arguments")
                if _get_field(tool_call, "arguments") is not None
                else _get_field(function, "arguments")
            )
            redacted_arguments = redact(raw_arguments, self._config)
            part = {
                "type": "tool_call",
                "arguments": _json_value_if_unchanged(raw_arguments, redacted_arguments),
            }
            tool_id = _get_field(tool_call, "id")
            tool_name = _get_field(tool_call, "name") or _get_field(function, "name")
            if tool_id:
                part["id"] = str(tool_id)
            if tool_name:
                part["name"] = str(tool_name)
            parts.append(part)

        structured: dict[str, Any] = {"role": role, "parts": parts}
        if not is_output and role == "system":
            metadata = _get_field(message, "metadata")
            if (
                isinstance(metadata, Mapping)
                and metadata.get("_openjiuwen_prompt_attachment_history") is True
            ):
                history_mode = metadata.get("mode")
                if history_mode in {"snapshot", "delta"}:
                    structured["openjiuwen"] = {
                        "kind": "prompt_attachment_history",
                        "mode": history_mode,
                    }
        message_name = _get_field(message, "name")
        if message_name:
            structured["name"] = str(message_name)
        if is_output:
            finish_reason = _get_field(message, "finish_reason")
            if finish_reason and finish_reason != "null":
                structured["finish_reason"] = str(finish_reason)
        return structured

    def _structured_content_parts(
        self,
        content: Any,
        redact: Any,
    ) -> list[dict[str, Any]]:
        """Normalize ordered string/provider content without dropping payload.

        Provider multimodal parts do not share one schema. Their source type is
        retained verbatim. Known text-shaped parts expose a readable content
        string; every other dict is serialized into a redacted JSON content
        string so image/document-specific fields survive in the raw attribute
        and projector inspector even when the UI does not understand the type.
        """
        if content is None:
            return []
        items = list(content) if isinstance(content, (list, tuple)) else [content]
        parts: list[dict[str, Any]] = []
        for item in items:
            if isinstance(item, str):
                parts.append({
                    "type": "text",
                    "content": redact(item, self._config),
                })
                continue
            if not isinstance(item, dict):
                raw = _coerce_message_content(item)
                parts.append({
                    "type": "unknown",
                    "content": redact(raw, self._config),
                })
                continue

            part_type = str(item.get("type") or "unknown")
            text_value = item.get("text")
            if text_value is None:
                text_value = item.get("content")
            if (
                part_type in {"text", "input_text", "output_text"}
                and isinstance(text_value, str)
            ):
                part: dict[str, Any] = {
                    "type": part_type,
                    "content": redact(text_value, self._config),
                }
                extras = {
                    key: value
                    for key, value in item.items()
                    if key not in {"type", "text", "content"}
                }
                if extras:
                    raw_extras = json.dumps(extras, ensure_ascii=False, default=str)
                    protected_extras = redact(raw_extras, self._config)
                    part["metadata"] = _json_value_if_unchanged(
                        raw_extras,
                        protected_extras,
                    )
            else:
                payload = {key: value for key, value in item.items() if key != "type"}
                raw_payload = json.dumps(payload, ensure_ascii=False, default=str)
                part = {
                    "type": part_type,
                    "content": redact(raw_payload, self._config),
                }

            # Preserve profile-recognized scalar identity fields when present.
            # They are independently redacted because the JSON content above is
            # protected but these top-level mirrors would otherwise bypass it.
            for key in ("id", "name", "modality", "file_id", "uri"):
                value = item.get(key)
                if isinstance(value, str) and value:
                    part[key] = redact(value, self._config)
            parts.append(part)
        return parts

    def _record_response_details(self, state: LlmSpanState, response: Any) -> None:
        if response is None:
            return
        response_id = _get_field(response, "response_id")
        if response_id:
            state.span.set_attribute(GEN_AI_RESPONSE_ID, str(response_id))
        response_model = _get_field(response, "response_model")
        if response_model:
            state.span.set_attribute(GEN_AI_RESPONSE_MODEL, str(response_model))

        for field_name, attribute, redact in (
            ("prompt_token_ids", OJ_GEN_AI_RESPONSE_PROMPT_TOKEN_IDS, redact_prompt),
            ("completion_token_ids", OJ_GEN_AI_RESPONSE_COMPLETION_TOKEN_IDS, redact_completion),
            ("logprobs", OJ_GEN_AI_RESPONSE_LOGPROBS, redact_completion),
            ("parser_content", OJ_GEN_AI_RESPONSE_PARSER_RESULT, redact_completion),
        ):
            value = _get_field(response, field_name)
            if value is not None:
                raw = json.dumps(value, ensure_ascii=False, default=str)
                protected = redact(raw, self._config)
                state.span.set_attribute(
                    attribute,
                    raw if protected == raw else json.dumps(protected, ensure_ascii=False),
                )

        metadata = _get_field(response, "provider_metadata")
        if isinstance(metadata, dict):
            safe_metadata = {
                key: metadata[key]
                for key in _PROVIDER_METADATA_ALLOWLIST
                if key in metadata
            }
            if safe_metadata:
                state.span.set_attribute(
                    OJ_GEN_AI_RESPONSE_PROVIDER_METADATA,
                    json.dumps(safe_metadata, ensure_ascii=False, default=str),
                )
        provider_content = _get_field(response, "provider_content")
        if provider_content is not None:
            raw_provider_content = _coerce_message_content(provider_content)
            state.span.set_attribute(
                OJ_GEN_AI_RESPONSE_PROVIDER_CONTENT,
                redact_completion(raw_provider_content, self._config),
            )

    @staticmethod
    def _serialize_tool_inputs(inputs: Any) -> str:
        """Serialize the tool call's arguments for the tool span input.

        ``ToolCallEvents.TOOL_CALL_STARTED`` carries ``inputs=(args, kwargs)``
        — a 2-element tuple of positional and keyword arguments from the
        tool invocation. Preserve the original structure; Session objects
        are rendered as ``"session:<id>"`` so they remain readable.
        """
        if inputs is None:
            return ""

        def _sanitize(obj: Any) -> Any:
            if isinstance(obj, dict):
                return {k: _sanitize(v) for k, v in obj.items()}
            if isinstance(obj, (list, tuple)):
                return [_sanitize(v) for v in obj]
            if hasattr(obj, "get_session_id"):
                try:
                    return f"session:{obj.get_session_id()}"
                except Exception:
                    return "<Session>"
            return obj

        try:
            sanitized = _sanitize(inputs)
            return json.dumps(sanitized, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(inputs)

    @staticmethod
    def _serialize_tool_result(result: Any) -> str:
        if result is None:
            return ""
        if hasattr(result, "__str__") and not isinstance(result, dict):
            return str(result)
        try:
            return json.dumps(result, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(result)

    @staticmethod
    def _next_request_number() -> int:
        """Allocate this request's number within the run it belongs to.

        The counter used to live on the root span, so a call made while no
        root span was resolvable got no number at all and the trajectory UI
        had to invent one. Key it by the root span when there is one and by
        the session otherwise, so every request is numbered either way.
        """

        root_span = get_root_span()
        with _REQUEST_SEQUENCE_LOCK:
            if root_span is not None:
                # Held on the span itself, so the counter dies with the run.
                request_number = int(
                    getattr(root_span, _REQUEST_SEQUENCE_ATTR, 0) or 0
                ) + 1
                setattr(root_span, _REQUEST_SEQUENCE_ATTR, request_number)
                return request_number
            key = str(get_current_session_id() or "unknown")
            request_number = _FALLBACK_REQUEST_SEQUENCES.get(key, 0) + 1
            _FALLBACK_REQUEST_SEQUENCES[key] = request_number
            _FALLBACK_REQUEST_SEQUENCES.move_to_end(key)
            while len(_FALLBACK_REQUEST_SEQUENCES) > _MAX_FALLBACK_REQUEST_SEQUENCES:
                _FALLBACK_REQUEST_SEQUENCES.popitem(last=False)
        return request_number

    @staticmethod
    def _matching_authoritative_tool_span(
        tool_name: str,
        tool_id: Any = None,
    ) -> Span | None:
        span = get_current_tool_span()
        if span is None or not span.is_recording():
            return None
        if not span.attributes.get(OJ_TOOL_AUTHORITATIVE):
            return None
        if str(span.attributes.get(GEN_AI_TOOL_NAME) or "") == tool_name:
            return span

        tool_type = str(
            span.attributes.get(OJ_TOOL_TYPE)
            or span.attributes.get(GEN_AI_TOOL_TYPE)
            or ""
        )
        if tool_type != "mcp" or tool_id is None:
            return None
        authoritative_id = str(
            span.attributes.get(OJ_TOOL_RESOURCE_ID)
            or span.attributes.get(GEN_AI_TOOL_ID)
            or ""
        )
        if not authoritative_id or authoritative_id != str(tool_id):
            return None
        return span

    @staticmethod
    def _derive_model_name(kwargs: dict[str, Any]) -> str:
        model_config = kwargs.get("model_config")
        if model_config is None:
            return ""
        return str(getattr(model_config, "model", "") or "")

    def _derive_provider_name(self, kwargs: dict[str, Any]) -> str:
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
        return _gen_ai_system_name(self._config)

    @staticmethod
    def _propagate_session_context(
        span: Span,
        *,
        include_additive: bool = True,
    ) -> None:
        """Propagate root/agent correlation attributes to a child span."""
        try:
            sources = (get_root_span(), get_current_agent_span())
            for source in sources:
                if source is None:
                    continue
                for key in (
                    GEN_AI_CONVERSATION_ID,
                    OJ_SESSION_ID,
                    OJ_REQUEST_ID,
                    OJ_RUN_ID,
                    OJ_TURN_ID,
                    OJ_TURN_NUMBER,
                    OJ_EXECUTION_SUBJECT_ID,
                    OJ_EXECUTION_SUBJECT_DISPLAY_NAME,
                    OJ_EXECUTION_SUBJECT_KIND,
                    OJ_EXECUTION_SUBJECT_PARENT_ID,
                    OJ_EXECUTION_SUBJECT_SESSION_ID,
                    OJ_STEP_ID,
                    OJ_STEP_NUMBER,
                ):
                    value = source.attributes.get(key)
                    if value is not None:
                        span.set_attribute(key, value)
                if include_additive:
                    for key in (
                        GEN_AI_AGENT_DESCRIPTION,
                        GEN_AI_AGENT_ID,
                        GEN_AI_AGENT_NAME,
                        GEN_AI_AGENT_VERSION,
                    ):
                        value = source.attributes.get(key)
                        if value is not None:
                            span.set_attribute(key, value)
            # The root/agent session is the trajectory owner used by the sink
            # and HTTP path.  A nested subagent binds its isolated runtime
            # session while streaming, but that identity belongs in
            # OJ_EXECUTION_SUBJECT_SESSION_ID and must not move child records
            # into a different trajectory partition.
            owner_session_id = (
                span.attributes.get(OJ_SESSION_ID)
                or span.attributes.get(GEN_AI_CONVERSATION_ID)
            )
            sid = str(owner_session_id or get_current_session_id() or "")
            if sid:
                span.set_attribute(LANGFUSE_SESSION_ID, sid)
                span.set_attribute(AT_SESSION_ID, sid)
                span.set_attribute(GEN_AI_CONVERSATION_ID, sid)
                span.set_attribute(OJ_SESSION_ID, sid)
        except Exception as exc:
            logger.warning("callback_handler: failed to propagate session context: {}", exc)

    @staticmethod
    def _copy_correlation_attributes(source: Span, target: Span) -> None:
        """Copy already-resolved correlation from one trajectory span."""
        for key in (
            LANGFUSE_SESSION_ID,
            AT_SESSION_ID,
            GEN_AI_CONVERSATION_ID,
            GEN_AI_AGENT_DESCRIPTION,
            GEN_AI_AGENT_ID,
            GEN_AI_AGENT_NAME,
            GEN_AI_AGENT_VERSION,
            OJ_SESSION_ID,
            OJ_INFERENCE_ID,
            OJ_REQUEST_ID,
            OJ_RUN_ID,
            OJ_TURN_ID,
            OJ_TURN_NUMBER,
            OJ_EXECUTION_SUBJECT_ID,
            OJ_EXECUTION_SUBJECT_DISPLAY_NAME,
            OJ_EXECUTION_SUBJECT_KIND,
            OJ_EXECUTION_SUBJECT_PARENT_ID,
            OJ_EXECUTION_SUBJECT_REQUEST_NUMBER,
            OJ_EXECUTION_SUBJECT_SESSION_ID,
            OJ_STEP_ID,
            OJ_STEP_NUMBER,
            OJ_REQUEST_NUMBER,
        ):
            value = source.attributes.get(key)
            if value is not None:
                target.set_attribute(key, value)

    @staticmethod
    def _stamp_parent_member_name(span: Span) -> None:
        """Stamp ``agentteam.member.name`` from the current agent span.

        The current agent iteration/invoke span carries ``AT_MEMBER_NAME``
        (contributed by ``TeamObservabilityRail`` when the agent runs in a
        team; absent otherwise).  Child
        llm.call / reasoning / tool spans do not, so this copies the member
        name down so every child span stays attributable.

        The call sites are inside callback handlers that normally fire during
        an agent iteration, but pre-iteration LLM calls (e.g. image probe
        during ``_ensure_initialized``) legitimately have no agent span.
        """
        try:
            agent_span = get_current_agent_span()
            if agent_span is None:
                logger.debug(
                    "callback_handler: _stamp_parent_member_name — "
                    "no agent span in context; span={} will not carry agentteam.member.name",
                    span.name,
                )
                return
            raw = agent_span.attributes.get(AT_MEMBER_NAME)
            if raw is not None:
                span.set_attribute(AT_MEMBER_NAME, str(raw))
        except Exception as exc:
            logger.warning("callback_handler: failed to stamp member name: {}", exc)
