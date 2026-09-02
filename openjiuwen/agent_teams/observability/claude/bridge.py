# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Bridge Claude SDK stream chunks into OpenJiuwen OpenTelemetry spans."""

from __future__ import annotations

from contextlib import nullcontext
import json
from typing import Any, ContextManager

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
    GEN_AI_COMPLETION,
    GEN_AI_TOOL_ID,
    GEN_AI_TOOL_INPUT,
    GEN_AI_TOOL_NAME,
    GEN_AI_TOOL_OUTPUT,
    LANGFUSE_OBSERVATION_INPUT,
    LANGFUSE_OBSERVATION_OUTPUT,
    LANGFUSE_OBSERVATION_TYPE,
    LANGFUSE_SESSION_ID,
)
from openjiuwen.core.session.stream.base import OutputSchema

_TRACER_NAME = "openjiuwen.agent_teams.observability.claude"


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
        self._config = config
        self._output = []
        self._reasoning = []
        self._tool_records = {}

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
        if error is not None:
            span.set_attribute("claude.turn.error", self._redact_diagnostic(error))

        self._finish_pending_tools()
        self._turn_span = None
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
        span.set_attribute(f"{GEN_AI_COMPLETION}.0.role", "reasoning")
        span.set_attribute(f"{GEN_AI_COMPLETION}.0.is_reasoning", True)
        span.set_attribute(f"{GEN_AI_COMPLETION}.0.content", safe_reasoning)
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
