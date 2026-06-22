# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""DeepAgent observability rail.

Agent span is created per iteration (not per invoke).

Span tree:
  team.{name}
  ├── agent.{member}.task_iteration.1    [AGENT]
  │     ├── llm.call                     [GENERATION]
  │     └── tool.xxx                     [TOOL]
  ├── agent.{member}.task_iteration.2    [AGENT]
  │     ├── llm.call
  │     └── tool.bash
  └── task.{id}                          [SPAN]
"""

from __future__ import annotations

from opentelemetry import context as otel_context
from opentelemetry.trace import (
    Span,
    SpanKind,
    Status,
    StatusCode,
    Tracer,
    set_span_in_context,
)

from openjiuwen.agent_teams.observability.redaction import (
    redact_completion,
    redact_prompt,
)
from openjiuwen.agent_teams.observability.semconv import (
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
    DA_TASK_IS_FOLLOW_UP,
    DA_TASK_ITERATION,
    DA_TASK_LOOP_EVENT,
    LANGFUSE_OBSERVATION_INPUT,
    LANGFUSE_OBSERVATION_OUTPUT,
    LANGFUSE_OBSERVATION_TYPE,
    LANGFUSE_SESSION_ID,
)
from openjiuwen.agent_teams.observability.span_context import (
    get_current_agent_span,
    set_current_agent_span,
    get_team_span,
    _llm_span_stack,
    _tool_span_map,
)
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail

_TRACER_NAME = "openjiuwen.agent_teams.observability.rail"
_SPAN_KEY = "_otel_task_iter_span"


def _stamp_cancelled_if_empty(span: Span) -> None:
    """Set a cancelled marker on a span that was never given proper output.

    Only called from cascade-close paths (after_task_iteration,
    close_team_agent_spans) — spans that reach here had their normal
    close callback interrupted (e.g. task cancelled mid-LLM-call).
    """
    if not span.attributes.get(LANGFUSE_OBSERVATION_OUTPUT):
        span.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, "cancelled")


class ObservabilityRail(DeepAgentRail):
    """Create an AGENT span around each outer task-loop iteration."""

    priority: int = 10

    def __init__(self, *, tracer: Tracer | None = None) -> None:
        super().__init__()
        self._injected_tracer = tracer

    def _tracer(self) -> Tracer:
        if self._injected_tracer is not None:
            return self._injected_tracer
        from openjiuwen.agent_teams.observability.setup import get_tracer
        return get_tracer(_TRACER_NAME)

    async def before_task_iteration(self, ctx: AgentCallbackContext) -> None:
        try:
            inputs = ctx.inputs
            iteration = int(getattr(inputs, "iteration", 0) or 0)
            is_follow_up = bool(getattr(inputs, "is_follow_up", False))

            # Get member_name and team_name from ctx.agent (framework guarantees these exist)
            agent = ctx.agent
            member_name = getattr(getattr(agent, "card", None), "name", "")
            team_name = getattr(agent, "team_name", "")

            # Get session_id from ContextVar (no agent attribute for this)
            session_id = ""
            try:
                from openjiuwen.agent_teams.context import get_session_id
                session_id = get_session_id() or ""
            except Exception as exc:
                team_logger.warning("rail: failed to get session_id: {}", exc)

            team_logger.info(
                "RAIL.before_task_iteration: member={} iteration={} ctx_id={}",
                member_name, iteration, id(ctx),
            )

            team_span = get_team_span()
            if team_span is None:
                team_logger.error("RAIL.before_task_iteration: team_span is None! team_name={}", team_name)
                return
            if not team_span.is_recording():
                team_logger.error(
                    "RAIL.before_task_iteration: team_span ENDED! name={} trace_id={:032x} "
                    "span_id={:016x} member={} iteration={}",
                    team_span.name, team_span.context.trace_id, team_span.context.span_id,
                    member_name, iteration,
                )
                return

            if _SPAN_KEY in ctx.extra:
                old = ctx.extra[_SPAN_KEY]
                team_logger.warning(
                    "RAIL: duplicate span call, skipped. old={}, id={:016x}",
                    getattr(old, "name", "<no-name>"),
                    getattr(getattr(old, "context", None), "span_id", 0),
                )
                return

            prev_agent = get_current_agent_span()
            if prev_agent is not None and prev_agent.is_recording():
                prev_member = prev_agent.attributes.get(AT_MEMBER_NAME, "")
                if prev_member == member_name:
                    # Same member — genuinely orphan (previous iteration not cleaned up).
                    # Drain child LLM/tool spans first, then close the agent span.
                    team_logger.warning(
                        "otel rail: closing orphan agent span: {}",
                        prev_agent.name if hasattr(prev_agent, 'name') else 'unknown'
                    )
                    for bucket in _tool_span_map.get().values():
                        for ts in bucket:
                            if ts.is_recording():
                                ts.end()
                    _tool_span_map.set({})
                    for state in _llm_span_stack.get():
                        if state.span.is_recording():
                            state.span.end()
                    _llm_span_stack.set([])
                    prev_agent.end()
                else:
                    # Different member — stale copy from ContextVar inheritance
                    # at asyncio.create_task time. The span belongs to another
                    # member's task — do NOT close it.
                    team_logger.info(
                        "otel rail: clearing stale agent span inherited from member {} "
                        "(current member: {})",
                        prev_member, member_name,
                    )
                    # Cross-member ContextVar snapshots also carry stale
                    # LLM/tool span references from the leader's task.
                    # Clear them so the new member starts with clean stacks
                    # and cascade-close in after_task_iteration won't
                    # accidentally touch another member's spans.
                    _llm_span_stack.set([])
                    _tool_span_map.set({})
                set_current_agent_span(None)

            member_label = member_name or "unknown"
            parent_ctx = set_span_in_context(team_span, otel_context.get_current())
            span = self._tracer().start_span(
                name=f"agent.{member_label}.task_iteration.{iteration}",
                context=parent_ctx,
                kind=SpanKind.INTERNAL,
            )

            span.set_attribute(LANGFUSE_OBSERVATION_TYPE, "agent")
            if team_name and member_name:
                span.set_attribute(AT_AGENT_ID, f"{team_name}_{member_name}")
            elif member_name:
                span.set_attribute(AT_AGENT_ID, member_name)
            if member_name:
                span.set_attribute(AT_AGENT_NAME, member_name)
            span.set_attribute(AT_AGENT_ROLE, member_name or "")
            span.set_attribute(DA_TASK_ITERATION, iteration)
            span.set_attribute(DA_TASK_IS_FOLLOW_UP, is_follow_up)

            if team_name:
                span.set_attribute(AT_TEAM_ID, team_name)
                span.set_attribute(AT_TEAM_NAME, team_name)
            if member_name:
                span.set_attribute(AT_MEMBER_ID, member_name)
                span.set_attribute(AT_MEMBER_NAME, member_name)
            if session_id:
                span.set_attribute(AT_SESSION_ID, session_id)
                span.set_attribute(LANGFUSE_SESSION_ID, session_id)

            from openjiuwen.agent_teams.observability.setup import get_config
            config = get_config()
            query = getattr(inputs, "query", "") or ""
            if query:
                redacted_query = redact_prompt(query, config) if config else str(query)
                span.set_attribute(LANGFUSE_OBSERVATION_INPUT, redacted_query)
                span.set_attribute(AT_AGENT_INPUT, redacted_query)
            loop_event = getattr(inputs, "loop_event", None)
            if loop_event is not None:
                span.set_attribute(DA_TASK_LOOP_EVENT, str(loop_event))

            set_current_agent_span(span)
            agent_ctx = set_span_in_context(span, otel_context.get_current())
            otel_context.attach(agent_ctx)

            team_logger.info(
                "otel rail: agent span opened: agent.{}.task_iteration.{} "
                "span_id={:016x} trace_id={:032x} parent_span_id={:016x}",
                member_label, iteration,
                span.context.span_id, span.context.trace_id,
                span.parent.span_id if span.parent else 0,
            )

            ctx.extra[_SPAN_KEY] = span
        except Exception as exc:
            team_logger.warning("otel rail before_task_iteration failed: {}", exc)

    async def after_task_iteration(self, ctx: AgentCallbackContext) -> None:
        try:
            team_logger.info(
                "RAIL.after_task_iteration: ctx_id={} has_span={}",
                id(ctx), _SPAN_KEY in ctx.extra,
            )

            span: Span | None = ctx.extra.pop(_SPAN_KEY, None)
            if span is None:
                return

            if not span.is_recording():
                team_logger.warning("otel rail: agent span already ended, skipping")
                return

            team_logger.info(
                "RAIL.after_task_iteration closing agent span: name={} span_id={:016x} "
                "trace_id={:032x}",
                span.name, span.context.span_id,
                span.context.trace_id,
            )

            output = None
            inputs = getattr(ctx, "inputs", None)
            if inputs is not None:
                output = getattr(inputs, "result", None)

            from openjiuwen.agent_teams.observability.setup import get_config
            config = get_config()

            if output:
                output_str = str(output)
                redacted = redact_completion(output_str, config) if config else output_str
                span.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, redacted)
                span.set_attribute(AT_AGENT_OUTPUT, redacted)

            # Close child LLM / tool spans before closing the parent agent span.
            # This is the definitive cleanup point — no separate finally block
            # in Runner is needed for these spans.
            # Do NOT set Status(OK) here — these spans only reach this path
            # when their normal close callback did not fire.  Setting OK would
            # mask the real problem; leaving them UNSET makes them stand out.

            for bucket in _tool_span_map.get().values():
                for ts in bucket:
                    if ts.is_recording():
                        _stamp_cancelled_if_empty(ts)
                        ts.end()
            _tool_span_map.set({})

            for state in _llm_span_stack.get():
                if state.span.is_recording():
                    _stamp_cancelled_if_empty(state.span)
                    state.span.end()
            _llm_span_stack.set([])

            if ctx.exception is not None:
                span.record_exception(ctx.exception)
                span.set_status(Status(StatusCode.ERROR, str(ctx.exception)))
            else:
                span.set_status(Status(StatusCode.OK))

            span.end()
            agent = getattr(ctx, "agent", None)
            member_name = getattr(getattr(agent, "card", None), "name", "") if agent else ""
            set_current_agent_span(None)

            team_span = get_team_span()
            if team_span is not None and team_span.is_recording():
                team_ctx = set_span_in_context(team_span, otel_context.get_current())
                otel_context.attach(team_ctx)

            # Set team span output if this is the leader
            # Use role attribute (TeamRole.LEADER) instead of comparing card.name
            if agent is not None:
                team_name = getattr(agent, "team_name", "")
                agent_role = getattr(agent, "role", None)
                if agent_role == TeamRole.LEADER and team_name and output:
                    team_span = get_team_span()
                    if team_span is not None and team_span.is_recording():
                        output_str = str(output)
                        redacted = redact_completion(output_str, config) if config else output_str
                        team_span.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, redacted)

                team_logger.info(
                    "otel rail: agent span closed, member={}, has_output={}",
                    member_name, output is not None,
                )
        except Exception as exc:
            team_logger.warning("otel rail after_task_iteration failed: {}", exc)
