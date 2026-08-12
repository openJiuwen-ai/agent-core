# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""OpenTelemetry handlers for AsyncCallbackFramework events.

Agent spans are created per iteration by the host integration. This handler
manages the generic LLM/tool span lifecycle and root metadata propagation.
"""

from __future__ import annotations

import json
import time
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
from openjiuwen.extensions.observability.semconv import (
    AT_AGENT_ID,
    AT_MEMBER_NAME,
    AT_SESSION_ID,

    GEN_AI_COMPLETION,
    GEN_AI_OPERATION_NAME,
    GEN_AI_PROMPT,
    GEN_AI_PROVIDER_NAME,
    LANGFUSE_GEN_AI_COMPLETION,
    LANGFUSE_GEN_AI_PROMPT,
    GEN_AI_REQUEST_MAX_TOKENS,
    GEN_AI_REQUEST_MESSAGE_COUNT,
    GEN_AI_REQUEST_MESSAGE_COUNT_PREFIX,
    GEN_AI_REQUEST_ID,
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
    GEN_AI_USAGE_CACHE_TOKENS,
    GEN_AI_USAGE_REASONING_TOKENS,
    GEN_AI_REASONING_DURATION_MS,
    GEN_AI_REASONING_TIMING,
    REASONING_TIMING_UNMEASURED,
    LANGFUSE_OBSERVATION_INPUT,
    LANGFUSE_OBSERVATION_OUTPUT,
    LANGFUSE_OBSERVATION_TYPE,
    LANGFUSE_SESSION_ID,
)
from openjiuwen.extensions.observability.span_context import (
    LlmSpanState,
    get_active_span_tracker,
    get_current_agent_span,
    get_current_llm_span,
    get_current_session_id,
    get_root_span,
    pop_current_llm_span,
    pop_tool_span,
    push_tool_span,
    set_current_session_id,
)
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.call_scope import get_current_llm_call_id


_TRACER_NAME = "openjiuwen.extensions.observability"


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

    async def on_llm_stream_output(self, *args: Any, **kwargs: Any) -> Any:
        try:
            span = get_current_llm_span()
            state = getattr(span, "otel_llm_state", None) if span else None

            if state is None or not state.span.is_recording():
                return kwargs.get("result")

            chunk = kwargs.get("result")
            if state.first_chunk_ns is None:
                state.first_chunk_ns = time.monotonic_ns()
                ttft_ms = (state.first_chunk_ns - state.start_ns) / 1_000_000.0
                if state.span.is_recording():
                    state.span.set_attribute(GEN_AI_RESPONSE_TTFT_MS, ttft_ms)
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
            self._maybe_record_response_attrs(state, chunk)
        except Exception as exc:
            logger.warning("otel: on_llm_stream_output failed: {}", exc)
        return kwargs.get("result")

    async def on_llm_output(self, *args: Any, **kwargs: Any) -> None:
        state = None
        try:
            span = pop_current_llm_span()
            state = getattr(span, "otel_llm_state", None) if span else None
            if state is None:
                logger.debug("otel: on_llm_output — no open LLM span to close")
                return
            if not state.span.is_recording():
                logger.debug("otel: on_llm_output — span already ended")
                return

            completion_text = str(kwargs.get("response") or "")
            # Prefer the reasoning_content carried by the LLM_OUTPUT trigger
            # (the business layer already assembled the full text on the
            # final_message), so the collector does not re-stitch chunks.
            reasoning_text = str(kwargs.get("reasoning_content") or "")
            if not reasoning_text:
                resp_obj = kwargs.get("response")
                if resp_obj is not None and not isinstance(resp_obj, str):
                    reasoning_text = str(getattr(resp_obj, "reasoning_content", "") or "")

            tool_calls = kwargs.get("tool_calls") or getattr(kwargs.get("response"), "tool_calls", None)
            tc_json = _serialize_tool_calls(tool_calls)

            # Usage from streaming trigger kwargs. finish_reason is recorded
            # per-chunk in on_llm_stream_output via _maybe_record_response_attrs;
            # the LLM_OUTPUT trigger carries content as `response`, not a
            # finish_reason, so never fall back to it.
            usage_from_trigger = kwargs.get("usage")
            if usage_from_trigger is not None:
                self._record_usage_attrs(state, usage_from_trigger, skip_existing=True)

            self._finalize_llm_span_output(
                state, completion_text, reasoning_text,
                tc_json=tc_json, response=kwargs.get("response"),
                usage=kwargs.get("usage"),
            )
        except BaseException as exc:
            if isinstance(exc, Exception):
                logger.warning("otel: on_llm_output failed: {}", exc)
            # State was already popped — if we don't end the span here it
            # becomes an orphan (cascade_close_children can't find it) and
            # is ended later by ActiveSpanTracker.flush_* with no output attrs.
            if state is not None:
                try:
                    if state.span.is_recording():
                        state.span.set_status(Status(StatusCode.ERROR, f"on_llm_output failed: {exc}"))
                        state.span.end()
                except Exception as cleanup_exc:
                    logger.warning("otel: on_llm_output cleanup also failed: {}", cleanup_exc)
            if not isinstance(exc, Exception):
                raise

    async def on_llm_invoke_output(self, *args: Any, **kwargs: Any) -> Any:
        state = None
        try:
            # Peek first to check if it's streaming (leave to on_llm_output)
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
            self._propagate_session_context(span)
            self._stamp_parent_member_name(span)
            push_tool_span(tool_name, span)
        except Exception as exc:
            logger.warning("otel: on_tool_call_started failed: {}", exc)

    async def on_tool_call_finished(self, *args: Any, **kwargs: Any) -> Any:
        try:
            tool_name = str(kwargs.get("tool_name") or "unknown")
            result = kwargs.get("result")
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
            logger.warning("otel: on_tool_call_finished failed: {}\n{}", exc, traceback.format_exc())
        return kwargs.get("result")

    async def on_tool_call_error(self, *args: Any, **kwargs: Any) -> None:
        try:
            tool_name = str(kwargs.get("tool_name") or "unknown")
            exc = kwargs.get("error") or kwargs.get("exception")
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
                    span.record_exception(exc)
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

        except Exception as exc:
            logger.exception("otel: on_agent_invoke_input failed: {}", exc)

    async def on_agent_invoke_output(self, *args: Any, **kwargs: Any) -> Any:
        """Handle AGENT_INVOKE_OUTPUT callback.

        DO NOT close agent span here! (managed by Rail)
        Sets root span output from the FINAL invoke result — this is the
        overall agent output, distinct from per-iteration results written by
        ObservabilityRail.after_task_iteration.
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

    def _open_llm_span(self, kwargs: dict[str, Any], is_streaming: bool = False) -> None:
        """Open an LLM span with explicit parent context."""
        parent_ctx = self._get_parent_context_for_llm_tool()
        if parent_ctx is None:
            return

        messages = kwargs.get("messages") or []
        model_name = kwargs.get("model") or self._derive_model_name(kwargs) or "unknown"
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
        span.set_attribute(GEN_AI_SYSTEM, _gen_ai_system_name(self._config))
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

        # ── Per-message prompt attributes (delta + tail cap) ─────────
        # OTel BoundedAttributes evicts FIFO (oldest first). The top-level
        # gen_ai.system / operation.name / provider.name / request.model are
        # written before this loop, so if the prompt attributes fill the
        # span's max_attributes budget they would be evicted first. Reserve
        # a fixed non-prompt budget and write only the trailing N messages.
        emit_standard_prompt = self._config.backend != "langfuse"
        attrs_per_msg = 4 if emit_standard_prompt else 2
        non_prompt_budget = 30  # top system + request params + root context +
        # member name + output-stage completion/usage/finish_reason (≈22, 30
        # leaves headroom); covers the 1 system message too.
        writable_msg_count = max((self._config.max_attributes - non_prompt_budget) // attrs_per_msg, 0)

        # All non-system messages (full prompt, not delta). System is
        # always emitted separately, so it does not consume the writable budget
        # for non-system messages.
        # langfuse.observation.input still uses delta (messages[prev_count_raw:])
        # so each iteration shows only the new prompt content.
        delta_idxs = [i for i in range(0, msg_count) if _message_role(messages[i]) != "system"]
        if len(delta_idxs) > writable_msg_count:
            # Keep only the trailing N so the oldest prompt slots — not the
            # top-level attrs — are the ones dropped by FIFO.
            delta_idxs = delta_idxs[-writable_msg_count:] if writable_msg_count > 0 else []
        emit_set = set(delta_idxs)

        for i, m in enumerate(messages):
            role = _message_role(m)
            is_system = role == "system"
            # System message → always emit.  Non-system → only emit delta,
            # and only if it survived the tail cap.
            if not is_system and i not in emit_set:
                continue
            raw_content = _coerce_message_content(_message_content(m))
            redacted = redact_prompt(raw_content, self._config)
            if emit_standard_prompt:
                span.set_attribute(f"{GEN_AI_PROMPT}.{i}.role", role)
                span.set_attribute(f"{GEN_AI_PROMPT}.{i}.content", redacted)
            span.set_attribute(f"{LANGFUSE_GEN_AI_PROMPT}.{i}.role", role)
            span.set_attribute(f"{LANGFUSE_GEN_AI_PROMPT}.{i}.content", redacted)

            # tool_calls on assistant messages — content is often empty,
            # but the LLM context includes the full tool call metadata.
            tool_calls = m.get("tool_calls") if isinstance(m, dict) else getattr(m, "tool_calls", None)
            if tool_calls:
                tc_json = _serialize_tool_calls(tool_calls)
                if tc_json:
                    tc_redacted = redact_prompt(tc_json, self._config)
                    if emit_standard_prompt:
                        span.set_attribute(f"{GEN_AI_PROMPT}.{i}.tool_calls", tc_redacted)
                    span.set_attribute(f"{LANGFUSE_GEN_AI_PROMPT}.{i}.tool_calls", tc_redacted)

        # ── langfuse.observation.input (delta, same logic) ───────────
        if is_first_call:
            # Drop system messages from the observation input — the UI
            # already shows the full prompt via gen_ai.prompt.{i}.* attrs.
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
            try:
                span.set_attribute(
                    GEN_AI_TOOL_DEFINITIONS,
                    json.dumps(tools, ensure_ascii=False, default=str),
                )
            except (TypeError, ValueError):
                span.set_attribute(GEN_AI_TOOL_DEFINITIONS, str(tools))

        self._propagate_session_context(span)
        self._stamp_parent_member_name(span)

        _llm_st = LlmSpanState(
            span=span,
            start_ns=time.monotonic_ns(),
            call_id=call_id,
            is_streaming=is_streaming,
        )
        span.otel_llm_state = _llm_st  # attach state to span object (context-immune)

        tracker = get_active_span_tracker()
        if tracker is not None:
            tracker.register_llm_span(call_id, span)

        logger.debug(
            "otel: _open_llm_span name=llm.call trace_id={:032x} span_id={:016x} "
            "parent_span_id={:016x} streaming={} call_id={}",
            span.context.trace_id, span.context.span_id,
            span.parent.span_id if span.parent else 0, is_streaming, call_id or "<none>",
        )

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
            redacted_compl = redact_completion(completion_text, self._config)
            emit_standard_completion = self._config.backend != "langfuse"
            # Standard gen_ai.completion keys
            if emit_standard_completion:
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
            if usage:
                # Dump the whole usage object so cache_tokens / reasoning_tokens
                # (and any future fields) flow through without per-field filters.
                dump = usage.model_dump() if hasattr(usage, "model_dump") else vars(usage)
                if dump:
                    response_obj["usage"] = dump
            output_json = json.dumps(response_obj, ensure_ascii=False, default=str)
            state.span.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, redact_completion(output_json, self._config))
        finally:
            # Always end the main llm.call span — even if attribute setting
            # above threw, the span must not become an orphan.
            if state.span.is_recording():
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
                # Standard gen_ai.completion attributes (Langfuse reasoning display)
                reasoning_span.set_attribute(f"{GEN_AI_COMPLETION}.0.role", "reasoning")
                reasoning_span.set_attribute(f"{GEN_AI_COMPLETION}.0.is_reasoning", True)
                reasoning_span.set_attribute(f"{GEN_AI_COMPLETION}.0.content", redacted_reasoning)
                # Langfuse observation input/output for UI visibility
                reasoning_span.set_attribute(LANGFUSE_OBSERVATION_INPUT, "llm reasoning")
                reasoning_span.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, redacted_reasoning)
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
        cache_tokens = int(getattr(usage, "cache_tokens", 0) or 0)
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
            (cache_tokens, GEN_AI_USAGE_CACHE_TOKENS),
            (reasoning_tokens, GEN_AI_USAGE_REASONING_TOKENS),
        ):
            if value and not (skip_existing and state.span.attributes.get(dst_attr)):
                state.span.set_attribute(dst_attr, value)
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
    def _propagate_session_context(span: Span) -> None:
        """Propagate session_id to LLM/tool spans.

        Session identity is propagated to child spans. Other host-specific
        attributes are supplied directly by the host integration.
        """
        try:
            sid = get_current_session_id()
            if sid:
                span.set_attribute(LANGFUSE_SESSION_ID, sid)
                span.set_attribute(AT_SESSION_ID, sid)
        except Exception as exc:
            logger.warning("callback_handler: failed to propagate session context: {}", exc)

    @staticmethod
    def _stamp_parent_member_name(span: Span) -> None:
        """Stamp ``agentteam.member.name`` from the current agent span.

        The current agent iteration/invoke span carries ``AT_MEMBER_NAME``
        (stamped by ``ObservabilityRail._stamp_agent_attributes``).  Child
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
