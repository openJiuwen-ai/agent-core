# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Bridge Claude SDK stream chunks into OpenJiuwen OpenTelemetry spans."""

from __future__ import annotations

from contextlib import nullcontext
import json
import time
from typing import Any, ContextManager
import uuid

from opentelemetry import context as otel_context
from opentelemetry.trace import Span, SpanKind, Status, StatusCode, set_span_in_context

from openjiuwen.extensions.observability.redaction import redact_completion, redact_prompt
from openjiuwen.extensions.observability.semconv import (
    AT_AGENT_ID,
    AT_AGENT_INPUT,
    AT_AGENT_NAME,
    AT_AGENT_OUTPUT,
    AT_AGENT_ROLE,
    AT_MEMBER_ID,
    AT_MEMBER_NAME,
    AT_SESSION_ID,
    AT_TEAM_ID,
    AT_TEAM_NAME,
    GEN_AI_OUTPUT_MESSAGES,
    GEN_AI_PROVIDER_NAME,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_TOOL_ID,
    GEN_AI_TOOL_INPUT,
    GEN_AI_TOOL_NAME,
    GEN_AI_TOOL_OUTPUT,
    GEN_AI_USAGE_CACHE_TOKENS,
    GEN_AI_USAGE_COMPLETION_TOKENS,
    GEN_AI_USAGE_PROMPT_TOKENS,
    GEN_AI_USAGE_TOTAL_TOKENS,
    LANGFUSE_OBSERVATION_INPUT,
    LANGFUSE_OBSERVATION_OUTPUT,
    LANGFUSE_OBSERVATION_TYPE,
    LANGFUSE_SESSION_ID,
)
from openjiuwen.core.session.stream.base import OutputSchema

_TRACER_NAME = "openjiuwen.agent_teams.observability.claude"
# Claude Code's native span names (enhanced telemetry beta).
_CLAUDE_LLM_REQUEST_SPAN = "claude_code.llm_request"
# Claude Code's raw API body log events (OTEL_LOG_RAW_API_BODIES=1).
_CLAUDE_API_REQUEST_BODY_EVENT = "claude_code.api_request_body"
_CLAUDE_API_RESPONSE_BODY_EVENT = "claude_code.api_response_body"


def _int_or_none(value: Any) -> int | None:
    """Coerce an OTel attribute into an int when it is numeric."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class NoopClaudeSpanBridge:
    """No-op bridge used when agent team observability is not initialized."""

    @staticmethod
    def start_turn(**_: Any) -> None:
        """Ignore turn start."""

    @staticmethod
    def record_chunk(_: OutputSchema) -> None:
        """Ignore one Claude stream chunk."""

    @staticmethod
    def finish_turn(*, status: str, error: Any | None = None) -> None:
        """Ignore turn completion."""

    @staticmethod
    def record_cancel_reason(_: str) -> None:
        """Ignore the cancellation reason."""

    @staticmethod
    def attach_native_trace() -> str | None:
        """Report no native trace endpoint (no-op bridge)."""
        return None

    @staticmethod
    def detach_native_trace() -> None:
        """Ignore native trace detachment (no-op bridge)."""

    @staticmethod
    def native_traceparent() -> str | None:
        """Report no trace parent (no-op bridge)."""
        return None

    @staticmethod
    def native_source_id() -> str | None:
        """Report no native OTel source identity (no-op bridge)."""
        return None

    @staticmethod
    def record_native_model_span(_: dict[str, Any]) -> None:
        """Ignore one native Claude Code span (no-op bridge)."""

    @staticmethod
    def wait_for_native_observations() -> Any:
        """Return an immediately-resolved wait (no-op bridge)."""
        import asyncio

        return asyncio.sleep(0)

    @staticmethod
    def tool_execution_context() -> ContextManager[None]:
        """Return a no-op context for local tool execution."""
        return nullcontext()


class ClaudeSpanBridge:
    """Create OpenJiuwen spans from Claude SDK runtime chunks."""

    def __init__(
        self,
        *,
        member_name: str,
        member_agent_id: str | None = None,
        team_name: str | None = None,
        session_id: str | None = None,
        role: str | None = None,
    ) -> None:
        """Store the stable member context used on emitted spans."""
        self._member_name = member_name
        self._member_agent_id = member_agent_id or member_name
        self._team_name = team_name or ""
        self._session_id = session_id or ""
        self._role = role or ""
        self._turn_index = 0
        self._turn_span: Span | None = None
        self._config: Any | None = None
        self._output: list[str] = []
        self._reasoning: list[str] = []
        self._tool_records: dict[str, dict[str, Any]] = {}
        self._native_trace_enabled = False
        self._native_source_id = uuid.uuid4().hex
        self._native_subscriber_id: int | None = None
        self._native_span_count = 0
        self._pending_native_calls: list[dict[str, Any]] = []
        self._pending_native_request_bodies: list[dict[str, Any]] = []
        self._pending_native_response_bodies: dict[str, str] = {}
        self._turn_started_at_ns = 0

    @classmethod
    def build(
        cls,
        *,
        member_name: str,
        member_agent_id: str | None = None,
        team_name: str | None = None,
        session_id: str | None = None,
        role: str | None = None,
    ) -> ClaudeSpanBridge | NoopClaudeSpanBridge:
        """Build a bridge only when agent team observability is initialized."""
        try:
            from openjiuwen.agent_teams.observability.setup import is_initialized
        except ImportError:
            return NoopClaudeSpanBridge()
        if not is_initialized():
            return NoopClaudeSpanBridge()
        return cls(
            member_name=member_name,
            member_agent_id=member_agent_id,
            team_name=team_name,
            session_id=session_id,
            role=role,
        )

    def start_turn(self, *, prompt: str) -> None:
        """Start one Claude round span under the current team span."""
        self.finish_turn(status="cancelled")
        runtime = self._observability_runtime()
        if runtime is None:
            return
        tracer, config, team_span = runtime

        self._turn_index += 1
        span = tracer.start_span(
            name=f"agent.{self._member_name}.claude_turn.{self._turn_index}",
            context=set_span_in_context(team_span, otel_context.get_current()),
            kind=SpanKind.INTERNAL,
        )
        safe_prompt = redact_prompt(prompt, config)
        span.set_attribute(LANGFUSE_OBSERVATION_TYPE, "agent")
        span.set_attribute(LANGFUSE_OBSERVATION_INPUT, safe_prompt)
        span.set_attribute(AT_AGENT_INPUT, safe_prompt)
        span.set_attribute(AT_AGENT_ID, self._member_agent_id)
        span.set_attribute(AT_AGENT_NAME, self._member_name)
        span.set_attribute(AT_AGENT_ROLE, self._role or self._member_name)
        span.set_attribute(AT_MEMBER_ID, self._member_name)
        span.set_attribute(AT_MEMBER_NAME, self._member_name)
        span.set_attribute("agentteam.backend", "claude")
        if self._team_name:
            span.set_attribute(AT_TEAM_ID, self._team_name)
            span.set_attribute(AT_TEAM_NAME, self._team_name)
        if self._session_id:
            span.set_attribute(AT_SESSION_ID, self._session_id)
            span.set_attribute(LANGFUSE_SESSION_ID, self._session_id)

        self._turn_span = span
        self._turn_started_at_ns = time.time_ns()
        self._config = config
        self._output = []
        self._reasoning = []
        self._tool_records = {}
        self._pending_native_calls = []
        self._pending_native_request_bodies = []
        self._pending_native_response_bodies = {}

    def record_chunk(self, chunk: OutputSchema) -> None:
        """Record one Claude runtime chunk into pending turn state."""
        if self._turn_span is None:
            return
        payload = chunk.payload if isinstance(chunk.payload, dict) else {}
        if chunk.type == "llm_output":
            content = payload.get("content")
            if content:
                self._output.append(str(content))
            return
        if chunk.type == "llm_reasoning":
            content = payload.get("content")
            if content:
                self._reasoning.append(str(content))
            return
        if chunk.type == "tool_call":
            self._record_tool_call(payload)
            return
        if chunk.type == "tool_result":
            self._record_tool_result(payload)

    def record_external_runtime_failure(
        self,
        *,
        failure_id: str,
        round_id: int | None,
        phase: str,
        category: str,
        summary: str,
    ) -> None:
        """Stamp the finalized external runtime failure on a trace span.

        Prefers the current turn span; falls back to the long-lived team span
        so a startup-phase failure (no turn span yet) is still correlated in
        trace. Correlates the failed mailbox message, round result and logs
        with the member round via ``failure_id`` / ``round_id``. No-op when no
        recording span is available (observability is best-effort).
        """
        span = self._turn_span
        if span is None or not span.is_recording():
            # Startup failures happen before any turn span exists; fall back to
            # the team span so the event is not lost from trace.
            runtime = self._observability_runtime()
            if runtime is None:
                return
            _tracer, _config, team_span = runtime
            span = team_span
            if not span.is_recording():
                return
        span.add_event(
            "external_runtime.failed",
            {
                "external_runtime.failure_id": failure_id,
                "external_runtime.round_id": round_id if round_id is not None else "",
                "external_runtime.phase": phase,
                "external_runtime.category": category,
                "external_runtime.summary": summary,
                "external_runtime.member_name": self._member_name,
                "external_runtime.member_agent_id": self._member_agent_id,
                "external_runtime.team_name": self._team_name,
                "external_runtime.agent_kind": "claude",
            },
        )
        if span.is_recording():
            # Attribute mirror: OTLP UIs such as Langfuse drop span events, so
            # the finalized failure must also live on attributes to be visible
            # there. The turn span status is upgraded here because the SDK
            # reports terminal failures inside the message stream (the turn
            # generator returns normally), which would otherwise leave the
            # span reading as a successful turn.
            span.set_attribute("external_runtime.failure_id", failure_id)
            span.set_attribute("external_runtime.failure_category", category)
            span.set_attribute("external_runtime.failure_phase", phase)
            if round_id is not None:
                span.set_attribute("external_runtime.failure_round_id", round_id)
            span.set_attribute("external_runtime.failure_summary", summary)
            span.set_status(Status(StatusCode.ERROR, summary))

    def record_cancel_reason(self, reason: str) -> None:
        """Explain a cancelled turn on an attribute for OTLP UIs.

        A ``cancelled`` span is ambiguous: it may be a user abort, a shutdown,
        or a designed self-healing path such as the auth fallback retry. The
        reason attribute makes the distinction visible in trace backends that
        drop span events (e.g. Langfuse).
        """
        span = self._turn_span
        if span is not None and span.is_recording():
            span.set_attribute("claude.turn.cancel_reason", reason)

    async def attach_native_trace(self) -> str | None:
        """Subscribe to the shared OTLP receiver for this member's spans.

        Returns the shared loopback gRPC endpoint to point Claude Code's OTel
        export at (its exporter only speaks gRPC), or ``None`` when native
        spans are unavailable. The subscription filters events down to spans
        belonging to this member's active turn trace before they reach
        ``record_native_model_span``.
        """
        if self._native_subscriber_id is not None:
            return self._native_endpoint()
        from openjiuwen.agent_teams.observability.shared_otlp import get_shared_otlp_receiver

        receiver = get_shared_otlp_receiver()
        subscriber_id = await receiver.subscribe(self._on_native_span)
        if subscriber_id is None:
            return None
        self._native_subscriber_id = subscriber_id
        self._native_trace_enabled = True
        return receiver.grpc_endpoint or receiver.endpoint

    @staticmethod
    def _native_endpoint() -> str | None:
        """Return the shared receiver's gRPC endpoint, if still bound."""
        from openjiuwen.agent_teams.observability.shared_otlp import get_shared_otlp_receiver

        receiver = get_shared_otlp_receiver()
        return receiver.grpc_endpoint or receiver.endpoint

    def detach_native_trace(self) -> None:
        """Unsubscribe from the shared OTLP receiver."""
        if self._native_subscriber_id is None:
            return
        from openjiuwen.agent_teams.observability.shared_otlp import get_shared_otlp_receiver

        get_shared_otlp_receiver().unsubscribe(self._native_subscriber_id)
        self._native_subscriber_id = None
        self._native_trace_enabled = False

    def _on_native_span(self, event: dict[str, Any]) -> None:
        """Route one shared-receiver event when it belongs to this member."""
        if not self._native_trace_enabled:
            return
        if event.get("signal") == "log":
            self._on_native_log(event)
            return
        if str(event.get("name") or "") != _CLAUDE_LLM_REQUEST_SPAN:
            return
        # Only spans from this member's turn trace are relevant: the shared
        # receiver fans out every member's spans to every subscriber.
        turn_span = self._turn_span
        if turn_span is None:
            return
        context = turn_span.get_span_context()
        if not context.is_valid:
            return
        if str(event.get("trace_id") or "") != f"{context.trace_id:032x}":
            return
        if not self._is_current_native_source(event):
            return
        if int(event.get("start_time_ns") or 0) < self._turn_started_at_ns:
            return
        self.record_native_model_span(event)

    def _on_native_log(self, event: dict[str, Any]) -> None:
        """Route one raw API body log event to its pending llm.call span.

        ``claude_code.api_request_body`` events carry no request id, so they
        attach in arrival order to the oldest pending call still missing an
        input; ``claude_code.api_response_body`` events carry ``request_id``,
        which matches the ``request_id`` attribute on the corresponding
        ``claude_code.llm_request`` span.
        """
        name = str(event.get("name") or "")
        if name not in (_CLAUDE_API_REQUEST_BODY_EVENT, _CLAUDE_API_RESPONSE_BODY_EVENT):
            return
        turn_span = self._turn_span
        if turn_span is None:
            return
        context = turn_span.get_span_context()
        if not context.is_valid:
            return
        if str(event.get("trace_id") or "") != f"{context.trace_id:032x}":
            return
        if not self._is_current_native_source(event):
            return
        if int(event.get("time_ns") or 0) < self._turn_started_at_ns:
            return
        attributes = event.get("attributes")
        if not isinstance(attributes, dict):
            return
        body = str(attributes.get("body") or "")
        if not body:
            return
        if name == _CLAUDE_API_RESPONSE_BODY_EVENT:
            request_id = str(attributes.get("request_id") or "")
            if not request_id:
                return
            for pending in self._pending_native_calls:
                if pending["request_id"] == request_id and pending.get("output") is None:
                    pending["output"] = body
                    return
            self._pending_native_response_bodies[request_id] = body
            return
        self._pending_native_request_bodies.append(
            {
                "time_ns": int(event.get("time_ns") or 0),
                "span_id": str(event.get("span_id") or ""),
                "body": body,
            },
        )

    def _is_current_native_source(self, event: dict[str, Any]) -> bool:
        """Return whether an OTLP event came from this Claude CLI process."""
        from openjiuwen.agent_teams.observability.shared_otlp import OTEL_RESOURCE_SOURCE_ID

        resource_attributes = event.get("resource_attributes")
        if not isinstance(resource_attributes, dict):
            return False
        return str(resource_attributes.get(OTEL_RESOURCE_SOURCE_ID) or "") == self._native_source_id

    def record_native_model_span(self, event: dict[str, Any]) -> None:
        """Emit one ``llm.call`` span from a native ``claude_code.llm_request``."""
        turn_span = self._turn_span
        config = self._config
        if not self._native_trace_enabled or turn_span is None or config is None:
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

        from openjiuwen.agent_teams.observability.setup import get_tracer

        span = get_tracer(_TRACER_NAME).start_span(
            name="llm.call",
            context=set_span_in_context(turn_span, otel_context.get_current()),
            kind=SpanKind.CLIENT,
            start_time=start_ns,
        )
        span.set_attribute(LANGFUSE_OBSERVATION_TYPE, "generation")
        span.set_attribute("gen_ai.system", "claude")
        span.set_attribute(GEN_AI_PROVIDER_NAME, "Anthropic")
        span.set_attribute(GEN_AI_REQUEST_MODEL, str(attributes.get("model") or "unknown"))
        span.set_attribute("claude.observation.granularity", "native_llm_request_span")
        span.set_attribute("claude.model.call.observed", True)
        span.set_attribute("claude.native.span_id", str(event.get("span_id") or ""))
        input_tokens = _int_or_none(attributes.get("input_tokens"))
        output_tokens = _int_or_none(attributes.get("output_tokens"))
        if input_tokens is not None:
            span.set_attribute(GEN_AI_USAGE_PROMPT_TOKENS, input_tokens)
        if output_tokens is not None:
            span.set_attribute(GEN_AI_USAGE_COMPLETION_TOKENS, output_tokens)
        if input_tokens is not None and output_tokens is not None:
            span.set_attribute(GEN_AI_USAGE_TOTAL_TOKENS, input_tokens + output_tokens)
        cache_read_tokens = _int_or_none(attributes.get("cache_read_tokens"))
        cache_creation_tokens = _int_or_none(attributes.get("cache_creation_tokens"))
        if cache_read_tokens is not None or cache_creation_tokens is not None:
            span.set_attribute(
                GEN_AI_USAGE_CACHE_TOKENS,
                (cache_read_tokens or 0) + (cache_creation_tokens or 0),
            )
        # Transparent diagnostics from the native span.
        passthrough_keys = (
            "ttft_ms",
            "duration_ms",
            "stop_reason",
            "success",
            "attempt",
            "request_id",
            "speed",
            "llm_request.context",
            "query_source",
        )
        for key in passthrough_keys:
            value = attributes.get(key)
            if value is not None:
                span.set_attribute(f"claude.llm_request.{key}", value)
        span.set_attribute(AT_MEMBER_NAME, self._member_name)
        if self._team_name:
            span.set_attribute(AT_TEAM_NAME, self._team_name)
        if self._session_id:
            span.set_attribute(AT_SESSION_ID, self._session_id)
            span.set_attribute(LANGFUSE_SESSION_ID, self._session_id)

        if int(event.get("status_code") or 0) == 2:
            description = self._redact_diagnostic(
                event.get("status_message") or "native llm_request span failed",
            )
            span.set_status(Status(StatusCode.ERROR, description))
        else:
            span.set_status(Status(StatusCode.OK))
        # Hold the span open: raw API body log events (request/response JSON)
        # arrive on the logs signal shortly after, and span attributes are
        # immutable once ended. finish_turn closes it after attaching them.
        request_id = str(attributes.get("request_id") or "")
        self._pending_native_calls.append(
            {
                "span": span,
                "native_span_id": str(event.get("span_id") or ""),
                "start_ns": start_ns,
                "end_ns": end_ns,
                "request_id": request_id,
                "input": None,
                "output": self._pending_native_response_bodies.pop(request_id, None),
            },
        )
        self._native_span_count += 1

    def _close_pending_native_calls(self) -> None:
        """Attach redacted raw bodies and close every pending llm.call span."""
        self._match_native_request_bodies()
        config = self._config
        for pending in self._pending_native_calls:
            span = pending["span"]
            if span.is_recording():
                if pending.get("input") is not None and config is not None:
                    span.set_attribute(
                        LANGFUSE_OBSERVATION_INPUT,
                        redact_prompt(str(pending["input"]), config),
                    )
                if pending.get("output") is not None and config is not None:
                    span.set_attribute(
                        LANGFUSE_OBSERVATION_OUTPUT,
                        redact_completion(str(pending["output"]), config),
                    )
                span.end(end_time=pending["end_ns"])
        self._pending_native_calls = []
        self._pending_native_request_bodies = []
        self._pending_native_response_bodies = {}

    def _match_native_request_bodies(self) -> None:
        """Match request bodies to calls by event time, not export arrival."""
        calls = [pending for pending in self._pending_native_calls if pending.get("input") is None]
        bodies = sorted(self._pending_native_request_bodies, key=lambda pending: pending["time_ns"])
        for body_record in bodies:
            body_span_id = body_record["span_id"]
            matching_call = None
            if body_span_id:
                matching_call = next(
                    (pending for pending in calls if pending["native_span_id"] == body_span_id),
                    None,
                )
            if matching_call is None:
                eligible_calls = [pending for pending in calls if pending["start_ns"] <= body_record["time_ns"]]
                if eligible_calls:
                    matching_call = max(eligible_calls, key=lambda pending: pending["start_ns"])
            if matching_call is None:
                continue
            matching_call["input"] = body_record["body"]
            calls.remove(matching_call)

    def native_source_id(self) -> str:
        """Return the resource identity injected into this Claude CLI process."""
        return self._native_source_id

    async def wait_for_native_observations(self, *, timeout_s: float = 1.0) -> None:
        """Allow Claude Code's batched OTel export to flush after the stream."""
        if not self._native_trace_enabled or self._turn_span is None:
            return
        import asyncio

        # Claude Code batches span export (default 5s; shortened via env).
        # Give the final batch a moment to land before the turn span closes.
        await asyncio.sleep(min(0.2, timeout_s))

    def native_traceparent(self) -> str | None:
        """Return a W3C parent carrier for the CLI subprocess.

        Prefers the active turn span; falls back to the long-lived team span
        so the carrier also works before the first turn starts (runtime
        construction time). Turn spans are children of the team span, so both
        carry the same trace id — the native-span filter matches either way.
        """
        span = self._turn_span
        if span is None:
            runtime = self._observability_runtime()
            if runtime is None:
                return None
            _tracer, _config, span = runtime
        if span is None:
            return None
        context = span.get_span_context()
        if not context.is_valid:
            return None
        flags = int(context.trace_flags) & 0xFF
        return f"00-{context.trace_id:032x}-{context.span_id:016x}-{flags:02x}"

    def finish_turn(self, *, status: str, error: Any | None = None) -> None:
        """Close the current Claude round span and any pending child spans."""
        span = self._turn_span
        config = self._config
        if span is None:
            return

        if config is not None:
            output = "".join(self._output)
            reasoning = "".join(self._reasoning)
            if output:
                safe_output = redact_completion(output, config)
                span.set_attribute(AT_AGENT_OUTPUT, safe_output)
                span.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, safe_output)
            if reasoning:
                self._emit_reasoning_span(reasoning)

        span.set_attribute("claude.turn.status", status)
        span.set_attribute("claude.native.model_span_count", self._native_span_count)
        if error is not None:
            span.set_attribute("claude.turn.error", self._redact_diagnostic(error))

        self._finish_pending_tools()
        # Close child llm.call spans (with any attached raw bodies) before the
        # enclosing turn span ends.
        self._close_pending_native_calls()
        self._turn_span = None
        self._turn_started_at_ns = 0
        if status == "ok":
            span.set_status(Status(StatusCode.OK))
        elif status == "cancelled":
            span.set_status(Status(StatusCode.ERROR, "cancelled"))
        else:
            span.set_status(Status(StatusCode.ERROR, str(error) if error is not None else status))
        span.end()
        self._config = None
        self._output = []
        self._reasoning = []
        self._tool_records = {}

    def _record_tool_call(self, payload: dict[str, Any]) -> None:
        tool_call_id = str(payload.get("tool_call_id") or "")
        key = tool_call_id or f"index:{len(self._tool_records) + 1}"
        record = self._tool_records.setdefault(key, {})
        record["tool_call_id"] = tool_call_id
        record["tool_name"] = str(payload.get("name") or payload.get("tool_name") or "unknown")
        record["tool_args"] = payload.get("arguments")
        if payload.get("is_team_tool"):
            record["suppress_sdk_span"] = True

    def _record_tool_result(self, payload: dict[str, Any]) -> None:
        tool_call_id = str(payload.get("tool_call_id") or "")
        key = tool_call_id or self._first_pending_tool_key()
        if not key:
            key = f"result:{len(self._tool_records) + 1}"
        record = self._tool_records.setdefault(key, {})
        record["tool_call_id"] = tool_call_id
        record["tool_name"] = str(payload.get("tool_name") or record.get("tool_name") or "unknown")
        record["tool_result"] = payload.get("result")
        record["completed"] = True
        if payload.get("is_team_tool"):
            record["suppress_sdk_span"] = True
        self._emit_tool_span(key, record)

    def _emit_tool_span(self, key: str, record: dict[str, Any]) -> None:
        turn_span = self._turn_span
        config = self._config
        if turn_span is None or config is None:
            return
        if record.get("suppress_sdk_span"):
            self._tool_records.pop(key, None)
            return
        tool_name = str(record.get("tool_name") or "unknown")
        tracer, _, _ = self._observability_runtime() or (None, None, None)
        if tracer is None:
            return
        span = tracer.start_span(
            name=f"tool.{tool_name}",
            context=set_span_in_context(turn_span, otel_context.get_current()),
            kind=SpanKind.INTERNAL,
        )
        safe_input = redact_prompt(_json_text(record.get("tool_args")), config)
        span.set_attribute(LANGFUSE_OBSERVATION_TYPE, "tool")
        span.set_attribute(LANGFUSE_OBSERVATION_INPUT, safe_input)
        span.set_attribute(GEN_AI_TOOL_NAME, tool_name)
        span.set_attribute(GEN_AI_TOOL_INPUT, safe_input)
        tool_call_id = str(record.get("tool_call_id") or "")
        if tool_call_id:
            span.set_attribute(GEN_AI_TOOL_ID, tool_call_id)
            span.set_attribute("claude.tool.call_id", tool_call_id)
        span.set_attribute(AT_MEMBER_NAME, self._member_name)
        span.set_attribute("agentteam.backend", "claude")
        if self._team_name:
            span.set_attribute(AT_TEAM_NAME, self._team_name)
        if self._session_id:
            span.set_attribute(AT_SESSION_ID, self._session_id)
            span.set_attribute(LANGFUSE_SESSION_ID, self._session_id)

        if record.get("completed"):
            safe_output = redact_completion(_json_text(record.get("tool_result")), config)
            span.set_attribute(GEN_AI_TOOL_OUTPUT, safe_output)
            span.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, safe_output)
            span.set_status(Status(StatusCode.OK))
        else:
            span.set_status(Status(StatusCode.ERROR, "incomplete tool call"))
            span.set_attribute("claude.tool.error", "incomplete tool call")
        span.end()
        self._tool_records.pop(key, None)

    def _emit_reasoning_span(self, reasoning: str) -> None:
        turn_span = self._turn_span
        config = self._config
        if turn_span is None or config is None or not turn_span.is_recording():
            return
        try:
            from openjiuwen.agent_teams.observability.setup import get_tracer
        except ImportError:
            return

        span = get_tracer(_TRACER_NAME).start_span(
            name="llm.reasoning",
            context=set_span_in_context(turn_span, otel_context.get_current()),
            kind=SpanKind.INTERNAL,
        )
        safe_reasoning = redact_completion(reasoning, config)
        span.set_attribute(LANGFUSE_OBSERVATION_INPUT, "llm reasoning")
        span.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, safe_reasoning)
        span.set_attribute(
            GEN_AI_OUTPUT_MESSAGES,
            _json_text([{
                "role": "reasoning",
                "parts": [{"type": "text", "content": safe_reasoning}],
            }]),
        )
        span.set_attribute(AT_MEMBER_NAME, self._member_name)
        span.set_attribute("agentteam.backend", "claude")
        if self._team_name:
            span.set_attribute(AT_TEAM_NAME, self._team_name)
        if self._session_id:
            span.set_attribute(AT_SESSION_ID, self._session_id)
            span.set_attribute(LANGFUSE_SESSION_ID, self._session_id)
        span.set_status(Status(StatusCode.OK))
        span.end()

    def _finish_pending_tools(self) -> None:
        for key, record in list(self._tool_records.items()):
            self._emit_tool_span(key, record)

    def _first_pending_tool_key(self) -> str:
        for key, record in self._tool_records.items():
            if not record.get("completed"):
                return key
        return ""

    def tool_execution_context(self) -> ContextManager[None]:
        """Bind the active Claude turn as parent while a local team tool runs."""
        turn_span = self._turn_span
        if turn_span is None or not turn_span.is_recording():
            return nullcontext()
        try:
            from openjiuwen.extensions.observability.span_context import (
                get_current_agent_span,
                set_current_agent_span,
            )
        except ImportError:
            return nullcontext()
        return _AgentSpanBinding(
            turn_span=turn_span,
            previous_span=get_current_agent_span(),
            set_current_agent_span=set_current_agent_span,
        )

    @staticmethod
    def _observability_runtime() -> tuple[Any, Any, Span] | None:
        try:
            from openjiuwen.agent_teams.observability.setup import get_config, get_tracer
            from openjiuwen.agent_teams.observability.span_context import get_team_span
        except ImportError:
            return None
        config = get_config()
        team_span = get_team_span()
        if config is None or team_span is None or not team_span.is_recording():
            return None
        return get_tracer(_TRACER_NAME), config, team_span

    def _redact_diagnostic(self, value: Any) -> str:
        config = self._config
        text = _json_text(value)
        if config is None:
            return text
        if config.redact_prompts:
            return redact_prompt(text, config)
        return redact_completion(text, config)


def _json_text(value: Any) -> str:
    """Serialize a value as stable text for span attributes."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, BaseException):
        return str(value)
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


class _AgentSpanBinding:
    """Temporarily bind a Claude turn span as the current agent span."""

    def __init__(
        self,
        *,
        turn_span: Span,
        previous_span: Span | None,
        set_current_agent_span: Any,
    ) -> None:
        """Store the binding state."""
        self._turn_span = turn_span
        self._previous_span = previous_span
        self._set_current_agent_span = set_current_agent_span

    def __enter__(self) -> None:
        """Set the Claude turn span for this synchronous context."""
        self._set_current_agent_span(self._turn_span)

    def __exit__(self, *_exc: Any) -> None:
        """Restore the previous current agent span."""
        self._set_current_agent_span(self._previous_span)


__all__ = ["ClaudeSpanBridge", "NoopClaudeSpanBridge"]
