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
    cascade_close_children,
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


class AgentSpanScope:
    """Owns the lifecycle of one open agent span and its nesting decision.

    Replaces the previous scatter of bare-Span bookkeeping (a magic
    ctx.extra key holding a bare Span, hand-written cascade-close in
    three places, and an ``AT_AGENT_KIND`` enum match driving the nesting
    decision).

    Nesting is decided structurally: the current agent span (from the
    ``_current_agent_span`` ContextVar) is the legitimate parent whenever
    it is still recording — regardless of whether it belongs to a leader,
    a teammate, or another subagent. No tier enum is consulted. The scope
    remembers the parent it nested under so ``close`` can restore it as
    current when the child returns.

    The scope does NOT touch the inherited llm/tool stacks on the nested
    path: those belong to the still-open parent and are closed by the
    parent's own scope. Cascade-close runs only when this scope is the
    outermost agent (iteration path), mirroring the prior behavior.

    The scope is parked on ``ctx.extra`` for the duration of one span —
    opened in ``before_task_iteration`` / ``before_invoke`` and retrieved
    by the matching ``after_*``. ``ctx.extra`` is per-callback-context, so
    it does not leak across asyncio tasks the way a ContextVar would under
    iteration/invoke nesting.
    """

    KIND_ITERATION = "iteration"
    KIND_INVOKE = "invoke"

    # Single ctx.extra key for an open agent span scope. One handle owns
    # both the iteration path (before_task_iteration) and the single-round
    # invoke path (before_invoke) — the scope itself records which it is.
    # Renamed from the former module-level ``_SPAN_KEY`` ("_otel_task_iter_span"),
    # which stored a bare Span and only covered the iteration path.
    _CTX_KEY = "_otel_agent_scope"

    def __init__(
        self,
        *,
        span: Span,
        kind: str,
        parent_agent_span: Span | None,
        is_outermost: bool,
        config: Any,
    ) -> None:
        self.span = span
        self.kind = kind
        # The agent span that was current when this scope opened — restored
        # as _current_agent_span on close. None when nested directly under
        # the team span (no agent-tier parent).
        self.parent_agent_span = parent_agent_span
        # True when this scope owns the cascade-close of child llm/tool
        # spans (iteration path, or an invoke scope with no agent parent).
        self.is_outermost = is_outermost
        # ObservabilityConfig captured at open time; None disables redaction
        # and the close path stores the raw output string.
        self._config = config

    @classmethod
    def current(cls, ctx: AgentCallbackContext) -> AgentSpanScope | None:
        """Return the scope parked on this callback context, or None."""
        return ctx.extra.get(cls._CTX_KEY)

    def attach(self, ctx: AgentCallbackContext) -> None:
        """Park this scope on the callback context for the matching after_*."""
        ctx.extra[self._CTX_KEY] = self

    @classmethod
    def detach(cls, ctx: AgentCallbackContext) -> AgentSpanScope | None:
        """Pop and return the scope parked on this callback context."""
        return ctx.extra.pop(cls._CTX_KEY, None)

    def close(self, *, output: Any, exception: BaseException | None) -> None:
        """End this scope's span and restore the parent as current."""
        span = self.span
        if not span.is_recording():
            return
        if output:
            output_str = str(output)
            redacted = redact_completion(output_str, self._config) if self._config else output_str
            span.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, redacted)
            span.set_attribute(AT_AGENT_OUTPUT, redacted)

        if self.is_outermost:
            cascade_close_children()

        if exception is not None:
            span.record_exception(exception)
            span.set_status(Status(StatusCode.ERROR, str(exception)))
        else:
            span.set_status(Status(StatusCode.OK))
        span.end()

        # Restore the parent agent span (None when there was none) so the
        # parent's subsequent llm/tool spans resume nesting correctly.
        set_current_agent_span(self.parent_agent_span)
        if self.parent_agent_span is not None and self.parent_agent_span.is_recording():
            parent_ctx = set_span_in_context(self.parent_agent_span, otel_context.get_current())
            otel_context.attach(parent_ctx)


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
            # Prefer the authoritative member_name property (TeamAgent exposes
            # it; some entry points set agent.card.name to the display_name,
            # which would leak the human-readable label into the span name
            # instead of the stable member identifier). Fall back to card.name
            # for agents without member_name (e.g. subagents, whose card.name
            # is the subagent type label).
            member_name = self._resolve_member_name(agent)
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

            if AgentSpanScope.current(ctx) is not None:
                old = AgentSpanScope.current(ctx).span
                team_logger.warning(
                    "RAIL: duplicate span call, skipped. old={}, id={:016x}",
                    getattr(old, "name", "<no-name>"),
                    getattr(getattr(old, "context", None), "span_id", 0),
                )
                return

            # An iteration is always the outermost agent scope for its task —
            # it owns the cascade-close of child llm/tool spans. Before opening,
            # resolve any leftover agent span in the current context:
            #   - same member, still recording → orphan from a previous
            #     iteration that never closed; drain its children and end it.
            #   - different member → stale ContextVar snapshot inherited via
            #     asyncio.create_task; leave that member's span alone, just
            #     clear the inherited llm/tool stacks so this member starts
            #     clean and our cascade-close can't touch another member's spans.
            self._drain_or_clear_stale(member_name)

            member_label = member_name or "unknown"
            parent_ctx = set_span_in_context(team_span, otel_context.get_current())
            span = self._tracer().start_span(
                name=f"agent.{member_label}.task_iteration.{iteration}",
                context=parent_ctx,
                kind=SpanKind.INTERNAL,
            )

            self._stamp_agent_attributes(
                span, agent=agent, member_name=member_name, team_name=team_name,
                session_id=session_id, is_leader=getattr(agent, "role", None) == TeamRole.LEADER,
            )
            span.set_attribute(DA_TASK_ITERATION, iteration)
            span.set_attribute(DA_TASK_IS_FOLLOW_UP, is_follow_up)

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

            AgentSpanScope(
                span=span,
                kind=AgentSpanScope.KIND_ITERATION,
                # An iteration never nests under another agent span — it is
                # the agent tier of its own member's task. parent_agent_span
                # is None after drain/clear; recorded only for restore symmetry.
                parent_agent_span=None,
                is_outermost=True,
                config=config,
            ).attach(ctx)
        except Exception as exc:
            team_logger.warning("otel rail before_task_iteration failed: {}", exc)

    async def after_task_iteration(self, ctx: AgentCallbackContext) -> None:
        try:
            scope: AgentSpanScope | None = AgentSpanScope.detach(ctx)
            if scope is None:
                return

            team_logger.info(
                "RAIL.after_task_iteration: ctx_id={} name={} span_id={:016x}",
                id(ctx), scope.span.name, scope.span.context.span_id,
            )

            output = None
            inputs = getattr(ctx, "inputs", None)
            if inputs is not None:
                output = getattr(inputs, "result", None)

            scope.close(output=output, exception=ctx.exception)

            # Iteration close restores current to None (parent_agent_span is
            # None). Re-attach the team span as the ambient context so any
            # immediately-following team-level work still nests correctly.
            team_span = get_team_span()
            if team_span is not None and team_span.is_recording():
                team_ctx = set_span_in_context(team_span, otel_context.get_current())
                otel_context.attach(team_ctx)

            # Set team span output if this is the leader
            agent = getattr(ctx, "agent", None)
            member_name = self._resolve_member_name(agent) if agent else ""
            if agent is not None and getattr(agent, "role", None) == TeamRole.LEADER:
                team_name = getattr(agent, "team_name", "")
                if team_name and output:
                    team_span = get_team_span()
                    if team_span is not None and team_span.is_recording():
                        from openjiuwen.agent_teams.observability.setup import get_config
                        config = get_config()
                        output_str = str(output)
                        redacted = redact_completion(output_str, config) if config else output_str
                        team_span.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, redacted)

            team_logger.info(
                "otel rail: agent span closed, member={}, has_output={}",
                member_name, output is not None,
            )
        except Exception as exc:
            team_logger.warning("otel rail after_task_iteration failed: {}", exc)

    # ------------------------------------------------------------------
    # Invoke-level fallback (covers single-round subagents)
    # ------------------------------------------------------------------
    # Subagents (plan/code/explore/...) default to enable_task_loop=False, so
    # they run via _run_single_round_invoke which never fires
    # BEFORE_TASK_ITERATION / AFTER_TASK_ITERATION. Without these hooks their
    # LLM/tool spans would fall back to the team span (or be skipped), leaving
    # the trace without an agent layer under the team span.
    #
    # before_invoke opens an agent span ONLY for single-round agents
    # (enable_task_loop=False). The multi-round path gets its iteration span
    # from before_task_iteration and is skipped here so the two never
    # double-open.

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        try:
            if AgentSpanScope.current(ctx) is not None:
                return

            inputs = ctx.inputs
            agent = ctx.agent

            # Decide by execution mode, NOT by whether an iteration span is
            # already open: BEFORE_INVOKE fires BEFORE BEFORE_TASK_ITERATION,
            # so at this point _current_agent_span is still None on the
            # multi-round path. A multi-round agent (team members) gets an
            # iteration span per round from before_task_iteration — it must
            # NOT get an invoke span. Only single-round agents (subagents,
            # enable_task_loop=False) fall through here.
            deep_config = getattr(agent, "deep_config", None)
            enable_task_loop = bool(getattr(deep_config, "enable_task_loop", False))
            if enable_task_loop:
                return

            member_name = self._resolve_member_name(agent) or "unknown"
            team_name = getattr(agent, "team_name", "")

            team_span = get_team_span()
            if team_span is None:
                # No team context (e.g. subagent invoked outside a team) —
                # there is no parent span to attach to, so skip rather than
                # create an orphan root span.
                return
            if not team_span.is_recording():
                return

            session_id = ""
            try:
                from openjiuwen.agent_teams.context import get_session_id
                session_id = get_session_id() or ""
            except Exception as exc:
                team_logger.warning("rail: failed to get session_id: {}", exc)

            # Nesting is decided structurally: the current agent span is the
            # legitimate parent whenever it is still recording — regardless
            # of whether it belongs to a leader, a teammate, or another
            # subagent. No tier enum is consulted. A subagent runs as a
            # synchronous await inside the parent iteration (task_tool) or as
            # an asyncio.create_task snapshot of it; in both cases nesting
            # under the still-recording parent yields the correct
            # team -> iteration -> subagent.invoke -> llm/tool tree. When the
            # parent has already ended, fall back to the team span.
            prev = get_current_agent_span()
            parent_span: Span
            is_outermost: bool
            if prev is not None and prev.is_recording():
                parent_span = prev
                # Nested under a live agent span — the parent owns cascade-
                # close. This scope must NOT touch the inherited llm/tool
                # stacks (they carry the parent's still-open children).
                is_outermost = False
            else:
                parent_span = team_span
                # No live agent parent — this scope is the outermost agent
                # tier for its task and owns cascade-close.
                is_outermost = True
                if prev is not None:
                    # Stale (ended) agent span left in the context — drop it
                    # so the team span becomes the ambient parent.
                    set_current_agent_span(None)

            parent_ctx = set_span_in_context(parent_span, otel_context.get_current())
            span = self._tracer().start_span(
                name=f"agent.{member_name}.invoke",
                context=parent_ctx,
                kind=SpanKind.INTERNAL,
            )
            self._stamp_agent_attributes(
                span, agent=agent, member_name=member_name, team_name=team_name,
                session_id=session_id, is_leader=False,
            )

            from openjiuwen.agent_teams.observability.setup import get_config
            config = get_config()
            query = getattr(inputs, "query", "") or ""
            if query:
                redacted_query = redact_prompt(query, config) if config else str(query)
                span.set_attribute(LANGFUSE_OBSERVATION_INPUT, redacted_query)
                span.set_attribute(AT_AGENT_INPUT, redacted_query)

            set_current_agent_span(span)
            agent_ctx = set_span_in_context(span, otel_context.get_current())
            otel_context.attach(agent_ctx)

            # parent_agent_span is the live agent span we nested under (so
            # close can restore it), or None when we fell back to the team
            # span.
            parent_agent_span = prev if parent_span is prev else None
            AgentSpanScope(
                span=span,
                kind=AgentSpanScope.KIND_INVOKE,
                parent_agent_span=parent_agent_span,
                is_outermost=is_outermost,
                config=config,
            ).attach(ctx)

            team_logger.info(
                "otel rail: invoke span opened (single-round fallback): agent.{} "
                "span_id={:016x} trace_id={:032x} nested={}",
                member_name, span.context.span_id, span.context.trace_id,
                parent_agent_span is not None,
            )
        except Exception as exc:
            team_logger.warning("otel rail before_invoke failed: {}", exc)

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        try:
            scope: AgentSpanScope | None = AgentSpanScope.detach(ctx)
            if scope is None or scope.kind != AgentSpanScope.KIND_INVOKE:
                # before_invoke skipped (multi-round path) — nothing to close.
                return

            output = None
            inputs = getattr(ctx, "inputs", None)
            if inputs is not None:
                output = getattr(inputs, "result", None)

            scope.close(output=output, exception=ctx.exception)

            team_logger.info(
                "otel rail: invoke span closed: name={} span_id={:016x}",
                scope.span.name, scope.span.context.span_id,
            )
        except Exception as exc:
            team_logger.warning("otel rail after_invoke failed: {}", exc)

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _drain_or_clear_stale(self, member_name: str) -> None:
        """Resolve a leftover agent span in the current context before opening a new one.

        Same-member leftover → orphan: drain child llm/tool spans and end it.
        Different-member leftover → stale ContextVar snapshot from another
        member's task: leave that span alone, clear the inherited llm/tool
        stacks so the new member starts clean. Always clears
        ``_current_agent_span`` afterwards.
        """
        prev = get_current_agent_span()
        if prev is None or not prev.is_recording():
            return
        prev_member = prev.attributes.get(AT_MEMBER_NAME, "")
        if prev_member == member_name:
            team_logger.warning(
                "otel rail: closing orphan agent span: {}",
                prev.name if hasattr(prev, "name") else "unknown",
            )
            cascade_close_children()
            prev.end()
        else:
            team_logger.info(
                "otel rail: clearing stale agent span inherited from member {} "
                "(current member: {})",
                prev_member, member_name,
            )
            _llm_span_stack.set([])
            _tool_span_map.set({})
        set_current_agent_span(None)

    @staticmethod
    def _resolve_member_name(agent: Any) -> str:
        """Return the stable member identifier for span naming.

        Source priority:
          1. ``agent.member_name`` — TeamAgent property (spawned teammates),
             the authoritative member id.
          2. ``agent.build_context.member_name`` — NativeHarness/DeepAgent
             exposes the build context whose ``member_name`` was derived
             from the runtime context. This is the leader's source: the
             leader runs as a NativeHarness (no ``member_name`` attribute),
             and its ``card.name`` carries the display_name, so without
             this source the leader span would be named after the
             display_name instead of the member id.
          3. ``agent.card.name`` — fallback for agents with neither of the
             above (e.g. subagents, whose card.name is the subagent type).

        Every source is coerced to ``str``; a non-string value (notably a
        MagicMock in tests, or a display_name leaked into card.name) is
        rejected so the fallback chain continues instead of producing an
        unusable span name.
        """
        # TeamAgent.member_name or NativeHarness.build_context.member_name
        ctx_src = getattr(agent, "member_name", None)
        if not isinstance(ctx_src, str) or not ctx_src:
            build_ctx = getattr(agent, "build_context", None)
            ctx_src = getattr(build_ctx, "member_name", None) if build_ctx else None
        if isinstance(ctx_src, str) and ctx_src:
            return ctx_src
        card_src = getattr(getattr(agent, "card", None), "name", None)
        if isinstance(card_src, str) and card_src:
            return card_src
        return ""

    @staticmethod
    def _stamp_agent_attributes(
        span: Span,
        *,
        agent: Any,
        member_name: str,
        team_name: str,
        session_id: str,
        is_leader: bool,
    ) -> None:
        """Apply the common agent-span attributes shared by iteration and invoke spans.

        ``is_leader`` is accepted for symmetry with the iteration path but
        does not alter the role attribute here — ``AT_AGENT_ROLE`` carries
        the member name (its long-standing semantics); leader-vs-teammate is
        distinguished elsewhere via ``agent.role``.
        """
        span.set_attribute(LANGFUSE_OBSERVATION_TYPE, "agent")
        if team_name and member_name:
            span.set_attribute(AT_AGENT_ID, f"{team_name}_{member_name}")
        elif member_name:
            span.set_attribute(AT_AGENT_ID, member_name)
        if member_name:
            span.set_attribute(AT_AGENT_NAME, member_name)
            span.set_attribute(AT_MEMBER_ID, member_name)
            span.set_attribute(AT_MEMBER_NAME, member_name)
        span.set_attribute(AT_AGENT_ROLE, member_name or "")
        if team_name:
            span.set_attribute(AT_TEAM_ID, team_name)
            span.set_attribute(AT_TEAM_NAME, team_name)
        if session_id:
            span.set_attribute(AT_SESSION_ID, session_id)
            span.set_attribute(LANGFUSE_SESSION_ID, session_id)


def maybe_observability_rail() -> ObservabilityRail | None:
    """Return an ``ObservabilityRail`` when observability is on, else None.

    Single source of truth for the "is observability initialized → build one
    rail" guard shared by the manifest providers
    (``_build_observability_rail`` builds a standalone rail; sub-agent
    providers append one to an existing spec via ``_attach_observability_rail``).
    Returns ``None`` when observability is not initialized so both call sites
    stay safe unconditional additions.
    """
    from openjiuwen.agent_teams.observability.setup import is_initialized

    if not is_initialized():
        return None
    return ObservabilityRail()
