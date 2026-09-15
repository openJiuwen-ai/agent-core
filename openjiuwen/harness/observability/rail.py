# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Agent-tier observability rail — the agent span every DeepAgent gets.

This rail owns one thing: the ``agent.*`` tier of the span tree, for any
DeepAgent whatsoever — a single agent, a team member, a dispatched sub-agent.
It knows nothing about teams.

Span tree::

  <run root>                              (team.{name} / agent.{mode}.{session})
  ├── agent.{name}.task_iteration.1       [AGENT]
  │     ├── llm.call                      [GENERATION]
  │     └── tool.xxx                      [TOOL]
  └── agent.{name}.invoke                 [AGENT]  single-round agents

Layers that need more on the same span — the Team layer stamping its
``agentteam.*`` identity block, say — do not subclass this rail and do not
re-open the span. They mount their own rail with a *higher* priority and park
an :class:`AgentSpanDecoration` on the callback context; this rail applies it
when it opens and closes the span. One callback context is shared by every rail
in a hook chain and by the before/after pair of one iteration or invoke, which
is what makes that handoff exact — no ContextVar guessing about which span a
contribution belongs to.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
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

from openjiuwen.core.common.logging import logger
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.extensions.observability.redaction import (
    redact_completion,
    redact_prompt,
)
from openjiuwen.extensions.observability.demand import publish_span_snapshot
from openjiuwen.extensions.observability.tool_outcome import (
    TOOL_REPORTED_FAILURE,
    tool_failure_reason,
    tool_result_for_exception,
)
from openjiuwen.extensions.observability.semconv import (
    DA_AGENT_NAME,
    DA_TASK_IS_FOLLOW_UP,
    DA_TASK_ITERATION,
    DA_TASK_LOOP_EVENT,
    ERROR_TYPE,
    GEN_AI_AGENT_DESCRIPTION,
    GEN_AI_AGENT_ID,
    GEN_AI_AGENT_NAME,
    GEN_AI_AGENT_VERSION,
    GEN_AI_CONVERSATION_ID,
    GEN_AI_OPERATION_NAME,
    GEN_AI_TOOL_CALL_ARGUMENTS,
    GEN_AI_TOOL_CALL_ID,
    GEN_AI_TOOL_CALL_RESULT,
    GEN_AI_TOOL_DESCRIPTION,
    GEN_AI_TOOL_ID,
    GEN_AI_TOOL_INPUT,
    GEN_AI_TOOL_NAME,
    GEN_AI_TOOL_OUTPUT,
    GEN_AI_TOOL_TYPE,
    LANGFUSE_OBSERVATION_INPUT,
    LANGFUSE_OBSERVATION_OUTPUT,
    LANGFUSE_OBSERVATION_TYPE,
    LANGFUSE_SESSION_ID,
    OJ_REQUEST_ID,
    OJ_RUN_ID,
    OJ_SESSION_ID,
    OJ_EXECUTION_SUBJECT_DISPLAY_NAME,
    OJ_EXECUTION_SUBJECT_ID,
    OJ_EXECUTION_SUBJECT_KIND,
    OJ_EXECUTION_SUBJECT_PARENT_ID,
    OJ_EXECUTION_SUBJECT_SESSION_ID,
    OJ_STEP_ID,
    OJ_STEP_NUMBER,
    OJ_TOOL_AUTHORITATIVE,
    OJ_TOOL_RESOURCE_ID,
    OJ_TOOL_TYPE,
    OJ_TRACE_ROOT,
    OJ_TRACE_SCHEMA_VERSION,
    OJ_TRAJECTORY_RECORD_KIND,
    OJ_TURN_ID,
    OJ_TURN_NUMBER,
)
from openjiuwen.harness.execution_subject import current_execution_subject
# Imported as a module, never by name: the run-root fallback installs itself by
# rebinding ``get_root_span`` on this module, and a name bound at import time
# would keep calling the unwrapped accessor.
from openjiuwen.extensions.observability import span_context as shared_span_context
from openjiuwen.extensions.observability.span_context import (
    cascade_close_children,
    clear_tool_span_context,
    get_current_agent_span,
    get_current_tool_span,
    mark_span_forced_close,
    pop_tool_span,
    push_tool_span,
    set_current_agent_span,
)
from openjiuwen.harness.observability.span_context import current_session_id
from openjiuwen.harness.rails.base import DeepAgentRail

_TRACER_NAME = "openjiuwen.harness.observability.rail"
_ORPHAN_AGENT_FORCED_CLOSE_REASON = "missing_agent_terminal_callback"


def serialize_ability_value(value: Any) -> str:
    """Render a tool argument or result as the text recorded on a span.

    Args:
        value: The ability payload to render.

    Returns:
        The value itself when it is already text, its JSON form when it is
        serializable, and its ``str`` form otherwise.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        if hasattr(value, "model_dump"):
            value = value.model_dump(exclude_none=True)
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


@dataclass(frozen=True)
class AgentSpanDecoration:
    """Extra attributes another rail contributes to the agent span.

    A contributing rail builds one in its ``before_*`` hook and parks it with
    :meth:`park`; :class:`AgentObservabilityRail` applies it to the span it
    opens. The two mirror lists exist because the contributor's namespace often
    duplicates the generic input/output attributes under its own keys, and the
    output is only known when the span closes.

    Attributes:
        attributes: Applied verbatim when the span opens.
        input_attribute_keys: Keys the redacted query is mirrored into.
        output_attribute_keys: Keys the redacted result is mirrored into at close.
    """

    attributes: Mapping[str, Any] = field(default_factory=dict)
    input_attribute_keys: tuple[str, ...] = ()
    output_attribute_keys: tuple[str, ...] = ()

    # One ctx.extra key for the pending contribution. Parked per callback
    # context, so contributions never leak across agents or asyncio tasks.
    _CTX_KEY = "_otel_agent_span_decoration"

    def park(self, ctx: AgentCallbackContext) -> None:
        """Offer this contribution to the agent span about to be opened."""
        ctx.extra[self._CTX_KEY] = self

    @classmethod
    def collect(cls, ctx: AgentCallbackContext) -> AgentSpanDecoration:
        """Return the contribution parked on this context, or an empty one."""
        parked = ctx.extra.get(cls._CTX_KEY)
        if isinstance(parked, cls):
            return parked
        return cls()


class AgentSpanScope:
    """Owns the lifecycle of one open agent span and its nesting decision.

    Nesting is decided structurally: the current agent span (from the
    ``_current_agent_span`` ContextVar) is the legitimate parent whenever it is
    still recording — regardless of which agent it belongs to. No tier enum is
    consulted. The scope remembers the parent it nested under so ``close`` can
    restore it as current when the child returns.

    The scope does NOT touch the inherited llm/tool stacks on the nested path:
    those belong to the still-open parent and are closed by the parent's own
    scope. Cascade-close runs only when this scope is the outermost agent.

    The scope is parked on ``ctx.extra`` for the duration of one span — opened
    in ``before_task_iteration`` / ``before_invoke`` and retrieved by the
    matching ``after_*``. ``ctx.extra`` is per-callback-context, so it does not
    leak across asyncio tasks the way a ContextVar would under
    iteration/invoke nesting.
    """

    KIND_ITERATION = "iteration"
    KIND_INVOKE = "invoke"

    # Single ctx.extra key for an open agent span scope. One handle owns both
    # the iteration path (before_task_iteration) and the single-round invoke
    # path (before_invoke) — the scope itself records which it is.
    _CTX_KEY = "_otel_agent_scope"

    def __init__(
        self,
        *,
        span: Span,
        kind: str,
        parent_agent_span: Span | None,
        is_outermost: bool,
        config: Any,
        output_attribute_keys: tuple[str, ...] = (),
    ) -> None:
        self.span = span
        self.kind = kind
        # The agent span that was current when this scope opened — restored
        # as _current_agent_span on close. None when nested directly under
        # the run root (no agent-tier parent).
        self.parent_agent_span = parent_agent_span
        # True when this scope owns the cascade-close of child llm/tool
        # spans (iteration path, or an invoke scope with no agent parent).
        self.is_outermost = is_outermost
        # ObservabilityConfig captured at open time; None disables redaction
        # and the close path stores the raw output string.
        self._config = config
        # Contributed keys the redacted output is mirrored into at close.
        self._output_attribute_keys = output_attribute_keys

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
        if output is not None:
            output_str = str(output)
            redacted = redact_completion(output_str, self._config) if self._config else output_str
            span.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, redacted)
            for key in self._output_attribute_keys:
                span.set_attribute(key, redacted)

        if self.is_outermost:
            cascade_close_children()

        if exception is not None:
            span.record_exception(exception)
            span.set_attribute(ERROR_TYPE, type(exception).__name__)
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


class ToolSpanScope:
    """Own one AbilityManager call span across its rail callbacks."""

    _CTX_KEY = "_otel_ability_tool_scopes"

    def __init__(self, *, span: Span, tool_name: str, config: Any) -> None:
        self.span = span
        self.tool_name = tool_name
        self._config = config

    def attach(self, ctx: AgentCallbackContext) -> None:
        scopes = ctx.extra.setdefault(self._CTX_KEY, {})
        scopes[id(ctx)] = self

    @classmethod
    def detach(cls, ctx: AgentCallbackContext) -> ToolSpanScope | None:
        scopes = ctx.extra.get(cls._CTX_KEY)
        if not isinstance(scopes, dict):
            return None
        scope = scopes.pop(id(ctx), None)
        if not scopes:
            ctx.extra.pop(cls._CTX_KEY, None)
        return scope if isinstance(scope, cls) else None

    def close(self, *, output: Any, exception: BaseException | None) -> None:
        """End the tool span, recording the result and the real outcome.

        The output is written on every path, raised calls included: the ability
        manager hands the model a tool result for those too, and a span without
        one makes the trajectory disagree with the conversation. The status is
        read from the outcome rather than from "did it raise", so a tool that
        returns ``success=False`` no longer lands as OK.

        Args:
            output: What the ability returned; None when the call raised.
            exception: The exception the call raised, or None.
        """

        popped = pop_tool_span(self.tool_name)
        span = self.span
        if popped is not None and popped is not span:
            # Preserve a differently nested span if a caller violated the
            # expected stack order; ending somebody else's span is worse than
            # leaving this one to the active-span safety net.
            push_tool_span(self.tool_name, popped)
        if not span.is_recording():
            return

        recorded_output = (
            tool_result_for_exception(exception) if exception is not None else output
        )
        raw_output = serialize_ability_value(recorded_output)
        raw_call_result = (
            "null" if recorded_output is None else raw_output
        )
        redacted = (
            redact_completion(raw_output, self._config)
            if self._config
            else raw_output
        )
        redacted_call_result = (
            redact_completion(raw_call_result, self._config)
            if self._config
            else raw_call_result
        )
        span.set_attribute(GEN_AI_TOOL_OUTPUT, redacted)
        span.set_attribute(GEN_AI_TOOL_CALL_RESULT, redacted_call_result)
        span.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, redacted)

        if exception is not None:
            span.record_exception(exception)
            span.set_attribute(ERROR_TYPE, type(exception).__name__)
            span.set_status(Status(StatusCode.ERROR, str(exception)))
            span.end()
            return

        failure_reason = tool_failure_reason(output)
        if failure_reason is None:
            span.set_status(Status(StatusCode.OK))
        else:
            span.set_attribute(ERROR_TYPE, TOOL_REPORTED_FAILURE)
            span.set_status(Status(StatusCode.ERROR, failure_reason))
        span.end()


class AgentObservabilityRail(DeepAgentRail):
    """Create an AGENT span around each task-loop iteration, or around one invoke."""

    priority: int = 10

    def __init__(self, *, tracer: Tracer | None = None) -> None:
        super().__init__()
        self._injected_tracer = tracer
        # The invoke span currently open for this agent, when ``before_invoke``
        # opened one. A rail instance belongs to a single agent and a DeepAgent
        # runs one invoke at a time, so this needs no per-context storage —
        # which is the point: the iteration hook fires in a task that may not
        # inherit the invoke's ContextVars, and it still has to find this span
        # to nest under. Without it an agent that gets both hooks emits the
        # invoke and its iterations as siblings, the invoke empty.
        self._open_invoke_span: Span | None = None
        self._open_react_step_span: Span | None = None
        self._open_react_step_parent: Span | None = None
        self._open_react_step_iteration: int = 0

    def fork_for_agent(self) -> "AgentObservabilityRail":
        """Return an empty lifecycle owner for one concrete Agent instance.

        Sub-agent specs are reusable templates.  Reusing this rail object from
        such a spec would also reuse its open invoke/Step state, so concurrent
        sub-agents could close or parent each other's spans.  Preserve only
        the injected tracer; all lifecycle state must start empty.
        """
        return AgentObservabilityRail(tracer=self._injected_tracer)

    def _tracer(self) -> Tracer:
        if self._injected_tracer is not None:
            return self._injected_tracer
        from openjiuwen.extensions.observability.setup import get_tracer

        return get_tracer(_TRACER_NAME)

    @staticmethod
    def _config() -> Any:
        """Return the active ObservabilityConfig, or None when tracing is off."""
        from openjiuwen.extensions.observability.setup import get_config

        return get_config()

    @staticmethod
    def _root_span_for(ctx: AgentCallbackContext) -> Span | None:
        """Resolve only the run owning this callback's concrete session."""
        session = getattr(ctx, "session", None)
        session_id = ""
        if session is not None:
            try:
                session_id = str(session.get_session_id() or "")
            except Exception:
                session_id = ""
        return (
            shared_span_context.get_root_span(session_id=session_id)
            if session_id
            else shared_span_context.get_root_span()
        )

    async def before_task_iteration(self, ctx: AgentCallbackContext) -> None:
        try:
            inputs = ctx.inputs
            iteration = int(getattr(inputs, "iteration", 0) or 0)
            is_follow_up = bool(getattr(inputs, "is_follow_up", False))
            agent = ctx.agent
            agent_name = self.resolve_agent_name(agent)

            root_span = self._root_span_for(ctx)
            if root_span is None:
                # No run root — nothing to attach to, and an orphan root span
                # would start a trace of its own. The host opens the root
                # (Team: team.{name}; single agent: the run span).
                logger.debug(
                    "[AgentObservability] no run root span; skipping agent span for %s",
                    agent_name,
                )
                return
            if not root_span.is_recording():
                logger.warning(
                    "[AgentObservability] run root span already ended: name=%s "
                    "agent=%s iteration=%s",
                    getattr(root_span, "name", "<unknown>"),
                    agent_name,
                    iteration,
                )
                return

            if AgentSpanScope.current(ctx) is not None:
                old = AgentSpanScope.current(ctx).span
                logger.warning(
                    "[AgentObservability] duplicate agent span call, skipped: old=%s",
                    getattr(old, "name", "<no-name>"),
                )
                return

            # An iteration is always the outermost agent scope for its task —
            # it owns the cascade-close of child llm/tool spans. Before opening,
            # resolve any leftover agent span in the current context:
            #   - same agent, still recording → orphan from a previous
            #     iteration that never closed; drain its children and end it.
            #   - different agent → stale ContextVar snapshot inherited via
            #     asyncio.create_task; leave that agent's span alone, just
            #     clear the inherited llm/tool stacks so this agent starts
            #     clean and our cascade-close can't touch another agent's spans.
            self._drain_or_clear_stale(agent_name, root_span)

            label = agent_name or "unknown"
            # One invoke, N iterations: when this agent also got an invoke span,
            # the iterations belong under it, not beside it.
            iteration_parent = root_span
            if self._open_invoke_span is not None and self._open_invoke_span.is_recording():
                iteration_parent = self._open_invoke_span
            parent_ctx = set_span_in_context(iteration_parent, otel_context.get_current())
            span = self._tracer().start_span(
                name=f"agent.{label}.task_iteration.{iteration}",
                context=parent_ctx,
                kind=SpanKind.INTERNAL,
            )

            config = self._config()
            decoration = AgentSpanDecoration.collect(ctx)
            self._stamp_agent_attributes(
                span,
                agent=agent,
                agent_name=agent_name,
                decoration=decoration,
                root_span=root_span,
            )
            span.set_attribute(DA_TASK_ITERATION, iteration)
            span.set_attribute(DA_TASK_IS_FOLLOW_UP, is_follow_up)

            query = getattr(inputs, "query", "") or ""
            if query:
                redacted_query = redact_prompt(query, config) if config else str(query)
                span.set_attribute(LANGFUSE_OBSERVATION_INPUT, redacted_query)
                for key in decoration.input_attribute_keys:
                    span.set_attribute(key, redacted_query)
            loop_event = getattr(inputs, "loop_event", None)
            if loop_event is not None:
                span.set_attribute(DA_TASK_LOOP_EVENT, str(loop_event))

            set_current_agent_span(span)
            agent_ctx = set_span_in_context(span, otel_context.get_current())
            otel_context.attach(agent_ctx)

            logger.debug(
                "[AgentObservability] agent span opened: agent.%s.task_iteration.%s "
                "span_id=%016x trace_id=%032x",
                label,
                iteration,
                span.context.span_id,
                span.context.trace_id,
            )

            AgentSpanScope(
                span=span,
                kind=AgentSpanScope.KIND_ITERATION,
                # An iteration never nests under another agent span — it is
                # the agent tier of its own task. parent_agent_span is None
                # after drain/clear; recorded only for restore symmetry.
                parent_agent_span=None,
                is_outermost=True,
                config=config,
                output_attribute_keys=decoration.output_attribute_keys,
            ).attach(ctx)
            publish_span_snapshot(span, "attributes")
        except Exception as exc:
            logger.warning("[AgentObservability] before_task_iteration failed: %s", exc)

    async def after_task_iteration(self, ctx: AgentCallbackContext) -> None:
        try:
            self._close_react_step(exception=ctx.exception)
            scope: AgentSpanScope | None = AgentSpanScope.detach(ctx)
            if scope is None:
                return

            output = None
            inputs = getattr(ctx, "inputs", None)
            if inputs is not None:
                output = getattr(inputs, "result", None)

            scope.close(output=output, exception=ctx.exception)

            # Iteration close restores current to None (parent_agent_span is
            # None), leaving the ended agent span as the ambient OTel context.
            # Re-attach the run root so anything that follows this round —
            # another rail's work, the host's own spans — still nests correctly
            # instead of hanging off a span that has ended.
            root_span = self._root_span_for(ctx)
            if root_span is not None and root_span.is_recording():
                root_ctx = set_span_in_context(root_span, otel_context.get_current())
                otel_context.attach(root_ctx)

            logger.debug(
                "[AgentObservability] agent span closed: name=%s has_output=%s",
                scope.span.name,
                output is not None,
            )
        except Exception as exc:
            logger.warning("[AgentObservability] after_task_iteration failed: %s", exc)

    # ------------------------------------------------------------------
    # Invoke-level fallback (covers single-round agents and sub-agents)
    # ------------------------------------------------------------------
    # Sub-agents (plan/code/explore/...) default to enable_task_loop=False, so
    # they run via _run_single_round_invoke which never fires
    # BEFORE_TASK_ITERATION / AFTER_TASK_ITERATION. Without these hooks their
    # LLM/tool spans would fall back to the run root (or be skipped), leaving
    # the trace without an agent layer.
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
            # multi-round path. A multi-round agent gets an iteration span per
            # round from before_task_iteration — it must NOT get an invoke
            # span. Only single-round agents fall through here.
            deep_config = getattr(agent, "deep_config", None)
            enable_task_loop = bool(getattr(deep_config, "enable_task_loop", False))
            if enable_task_loop:
                return

            agent_name = self.resolve_agent_name(agent) or "unknown"

            root_span = self._root_span_for(ctx)
            if root_span is None:
                # No run root (e.g. a sub-agent invoked outside any run) —
                # there is no parent span to attach to, so skip rather than
                # create an orphan root span.
                return
            if not root_span.is_recording():
                return

            # Nesting is decided structurally: the current agent span is the
            # legitimate parent whenever it is still recording — regardless of
            # which agent it belongs to. A sub-agent runs as a synchronous
            # await inside the parent iteration (task_tool) or as an
            # asyncio.create_task snapshot of it; in both cases nesting under
            # the still-recording parent yields the correct
            # root -> iteration -> subagent.invoke -> llm/tool tree. When the
            # parent has already ended, fall back to the run root.
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
                parent_span = root_span
                # No live agent parent — this scope is the outermost agent
                # tier for its task and owns cascade-close.
                is_outermost = True
                if prev is not None:
                    # Stale (ended) agent span left in the context — drop it
                    # so the run root becomes the ambient parent.
                    set_current_agent_span(None)

            # A sub-agent is dispatched *from* a tool call and runs to
            # completion inside it, so the dispatching tool span — not the
            # agent span the tool itself hangs off — is its span parent. Only
            # a tool span opened directly under the parent resolved above
            # qualifies, which keeps this to the actual dispatch and never
            # re-parents across a trace or an unrelated branch. The agent tier
            # is unaffected: ``parent_agent_span`` below still records the
            # agent span to restore, and ``is_outermost`` still follows it.
            dispatch_tool_span = get_current_tool_span()
            otel_parent: Span = parent_span
            if (
                dispatch_tool_span is not None
                and dispatch_tool_span.parent is not None
                and dispatch_tool_span.parent.span_id == parent_span.context.span_id
            ):
                otel_parent = dispatch_tool_span

            parent_ctx = set_span_in_context(otel_parent, otel_context.get_current())
            span = self._tracer().start_span(
                name=f"agent.{agent_name}.invoke",
                context=parent_ctx,
                kind=SpanKind.INTERNAL,
            )

            config = self._config()
            decoration = AgentSpanDecoration.collect(ctx)
            self._stamp_agent_attributes(
                span,
                agent=agent,
                agent_name=agent_name,
                decoration=decoration,
                root_span=root_span,
            )

            query = getattr(inputs, "query", "") or ""
            if query:
                redacted_query = redact_prompt(query, config) if config else str(query)
                span.set_attribute(LANGFUSE_OBSERVATION_INPUT, redacted_query)
                for key in decoration.input_attribute_keys:
                    span.set_attribute(key, redacted_query)

            set_current_agent_span(span)
            agent_ctx = set_span_in_context(span, otel_context.get_current())
            otel_context.attach(agent_ctx)

            # parent_agent_span is the live agent span we nested under (so
            # close can restore it), or None when we fell back to the run root.
            parent_agent_span = prev if parent_span is prev else None
            AgentSpanScope(
                span=span,
                kind=AgentSpanScope.KIND_INVOKE,
                parent_agent_span=parent_agent_span,
                is_outermost=is_outermost,
                config=config,
                output_attribute_keys=decoration.output_attribute_keys,
            ).attach(ctx)
            # Published for ``before_task_iteration``: an agent that gets both
            # hooks nests its iterations under this span.
            self._open_invoke_span = span
            publish_span_snapshot(span, "attributes")

            logger.debug(
                "[AgentObservability] invoke span opened (single-round): agent.%s "
                "span_id=%016x nested=%s",
                agent_name,
                span.context.span_id,
                parent_agent_span is not None,
            )
        except Exception as exc:
            logger.warning("[AgentObservability] before_invoke failed: %s", exc)

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        try:
            self._close_react_step(exception=ctx.exception)
            scope: AgentSpanScope | None = AgentSpanScope.detach(ctx)
            if scope is None or scope.kind != AgentSpanScope.KIND_INVOKE:
                # before_invoke skipped (multi-round path) — nothing to close.
                return
            if scope.span is self._open_invoke_span:
                self._open_invoke_span = None

            output = None
            inputs = getattr(ctx, "inputs", None)
            if inputs is not None:
                output = getattr(inputs, "result", None)

            scope.close(output=output, exception=ctx.exception)

            logger.debug(
                "[AgentObservability] invoke span closed: name=%s", scope.span.name
            )
        except Exception as exc:
            logger.warning("[AgentObservability] after_invoke failed: %s", exc)

    # ------------------------------------------------------------------
    # Inner ReAct step lifecycle
    # ------------------------------------------------------------------

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Open the Step that owns this physical model request and its tools."""
        try:
            iteration = int(getattr(ctx.inputs, "react_iteration", 0) or 0)
            if iteration <= 0:
                return
            current_step = self._open_react_step_span
            if (
                current_step is not None
                and current_step.is_recording()
                and self._open_react_step_iteration == iteration
            ):
                set_current_agent_span(current_step)
                return

            self._close_react_step(exception=None)
            scope_parent = get_current_agent_span()
            root_span = self._root_span_for(ctx)
            if scope_parent is None or not scope_parent.is_recording():
                scope_parent = root_span
            if scope_parent is None or not scope_parent.is_recording():
                return

            # The main single-Agent ReAct Step is a semantic child of the
            # Turn root, not of the outer harness task-loop bookkeeping span.
            # Nested/sub-agent scopes keep their structural parent.
            otel_parent = scope_parent
            scope_parent_is_turn_child = (
                root_span is not None
                and root_span.is_recording()
                and root_span.attributes.get(OJ_TRACE_ROOT)
                and scope_parent.parent is not None
                and scope_parent.parent.span_id == root_span.context.span_id
            )
            if scope_parent_is_turn_child:
                otel_parent = root_span

            agent = ctx.agent
            agent_name = str(
                scope_parent.attributes.get(DA_AGENT_NAME)
                or scope_parent.attributes.get(GEN_AI_AGENT_NAME)
                or self.resolve_agent_name(agent)
                or "unknown"
            )
            parent_ctx = set_span_in_context(otel_parent, otel_context.get_current())
            span = self._tracer().start_span(
                name=f"agent.{agent_name}.react_iteration.{iteration}",
                context=parent_ctx,
                kind=SpanKind.INTERNAL,
            )
            self._stamp_agent_attributes(
                span,
                agent=agent,
                agent_name=agent_name,
                decoration=AgentSpanDecoration.collect(ctx),
                root_span=root_span,
            )
            span.set_attribute(OJ_TRAJECTORY_RECORD_KIND, "step")
            span.set_attribute(DA_TASK_ITERATION, iteration)
            span.set_attribute(OJ_STEP_ID, f"{span.context.span_id:016x}")
            span.set_attribute(OJ_STEP_NUMBER, iteration)

            self._open_react_step_span = span
            self._open_react_step_parent = scope_parent
            self._open_react_step_iteration = iteration
            set_current_agent_span(span)
            otel_context.attach(
                set_span_in_context(span, otel_context.get_current())
            )
            publish_span_snapshot(span, "attributes")
        except Exception as exc:
            logger.warning("[AgentObservability] before_model_call failed: %s", exc)

    async def after_react_iteration(self, ctx: AgentCallbackContext) -> None:
        """Close a successful ReAct Step after its model and tools complete."""
        try:
            self._close_react_step(exception=ctx.exception)
        except Exception as exc:
            logger.warning("[AgentObservability] after_react_iteration failed: %s", exc)

    def _close_react_step(self, *, exception: BaseException | None) -> None:
        span = self._open_react_step_span
        parent = self._open_react_step_parent
        self._open_react_step_span = None
        self._open_react_step_parent = None
        self._open_react_step_iteration = 0
        if span is None:
            return
        if span.is_recording():
            cascade_close_children()
            if exception is not None:
                span.record_exception(exception)
                span.set_attribute(ERROR_TYPE, type(exception).__name__)
                span.set_status(Status(StatusCode.ERROR, str(exception)))
            else:
                span.set_status(Status(StatusCode.OK))
            span.end()
        set_current_agent_span(
            parent if parent is not None and parent.is_recording() else None
        )
        if parent is not None and parent.is_recording():
            otel_context.attach(
                set_span_in_context(parent, otel_context.get_current())
            )

    # ------------------------------------------------------------------
    # Ability/tool lifecycle
    # ------------------------------------------------------------------

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Open the authoritative span around one AbilityManager execution."""
        try:
            inputs = getattr(ctx, "inputs", None)
            tool_name = str(getattr(inputs, "tool_name", "") or "unknown")
            current_agent = get_current_agent_span()
            root_span = self._root_span_for(ctx)
            if (
                root_span is None
                or not root_span.attributes.get(OJ_TRACE_ROOT)
            ):
                # This authoritative Ability scope is the single-Agent
                # integration. Team roots keep their existing global Tool
                # callback behavior until the later Team trajectory phase.
                return
            parent = (
                current_agent
                if current_agent is not None and current_agent.is_recording()
                else root_span
            )
            if parent is None or not parent.is_recording():
                return

            parent_ctx = set_span_in_context(parent, otel_context.get_current())
            span = self._tracer().start_span(
                name=f"tool.{tool_name}",
                context=parent_ctx,
                kind=SpanKind.INTERNAL,
            )
            span.set_attribute(LANGFUSE_OBSERVATION_TYPE, "tool")
            span.set_attribute(GEN_AI_OPERATION_NAME, "execute_tool")
            span.set_attribute(GEN_AI_TOOL_NAME, tool_name)
            span.set_attribute(OJ_TRACE_SCHEMA_VERSION, "1")
            span.set_attribute(OJ_TRAJECTORY_RECORD_KIND, "tool")
            span.set_attribute(OJ_TOOL_AUTHORITATIVE, True)

            tool_call = getattr(inputs, "tool_call", None)
            tool_call_id = getattr(tool_call, "id", None)
            if tool_call_id:
                span.set_attribute(GEN_AI_TOOL_CALL_ID, str(tool_call_id))

            card = self._resolve_ability_card(ctx, tool_name)
            resource_id = str(getattr(card, "id", "") or "")
            if resource_id:
                span.set_attribute(GEN_AI_TOOL_ID, resource_id)
                span.set_attribute(OJ_TOOL_RESOURCE_ID, resource_id)
            description = str(getattr(card, "description", "") or "")
            if description:
                span.set_attribute(GEN_AI_TOOL_DESCRIPTION, description)
            ability_type = self._ability_type(ctx, card, tool_name)
            if ability_type:
                span.set_attribute(GEN_AI_TOOL_TYPE, ability_type)
                span.set_attribute(OJ_TOOL_TYPE, ability_type)

            raw_arguments = serialize_ability_value(
                getattr(inputs, "tool_args", None)
            )
            config = self._config()
            redacted_arguments = (
                redact_prompt(raw_arguments, config) if config else raw_arguments
            )
            span.set_attribute(GEN_AI_TOOL_INPUT, redacted_arguments)
            span.set_attribute(GEN_AI_TOOL_CALL_ARGUMENTS, redacted_arguments)
            span.set_attribute(LANGFUSE_OBSERVATION_INPUT, redacted_arguments)
            self._copy_parent_correlation(parent, span)

            push_tool_span(tool_name, span)
            ToolSpanScope(span=span, tool_name=tool_name, config=config).attach(ctx)
            publish_span_snapshot(span, "attributes")
        except Exception as exc:
            logger.warning("[AgentObservability] before_tool_call failed: %s", exc)

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        try:
            scope = ToolSpanScope.detach(ctx)
            if scope is None:
                return
            inputs = getattr(ctx, "inputs", None)
            output = getattr(inputs, "tool_result", None)
            scope.close(output=output, exception=ctx.exception)
        except Exception as exc:
            logger.warning("[AgentObservability] after_tool_call failed: %s", exc)

    async def on_tool_exception(self, ctx: AgentCallbackContext) -> None:
        try:
            scope = ToolSpanScope.detach(ctx)
            if scope is None:
                return
            scope.close(output=None, exception=ctx.exception)
        except Exception as exc:
            logger.warning("[AgentObservability] on_tool_exception failed: %s", exc)

    @staticmethod
    def _resolve_ability_card(ctx: AgentCallbackContext, tool_name: str) -> Any:
        manager = getattr(ctx.agent, "ability_manager", None)
        getter = getattr(manager, "get", None)
        if not callable(getter):
            return None
        try:
            return getter(tool_name)
        except Exception:
            return None

    @staticmethod
    def _ability_type(
        ctx: AgentCallbackContext,
        card: Any,
        tool_name: str,
    ) -> str | None:
        manager = getattr(ctx.agent, "ability_manager", None)
        mcp_resolver = getattr(manager, "_resolve_mcp_tool_scope", None)
        if callable(mcp_resolver):
            try:
                if mcp_resolver(tool_name) is not None:
                    return "mcp"
            except Exception as exc:
                # Fall through to the card class name heuristic below.
                logger.debug("otel: mcp scope resolution failed for {} - {}", tool_name, exc)
        class_name = type(card).__name__.lower() if card is not None else ""
        if "workflow" in class_name:
            return "workflow"
        if "agent" in class_name:
            return "subagent"
        if "mcp" in class_name:
            return "mcp"
        if "tool" in class_name:
            return "tool"
        return None

    @staticmethod
    def _copy_parent_correlation(parent: Span, span: Span) -> None:
        for key in (
            LANGFUSE_SESSION_ID,
            GEN_AI_CONVERSATION_ID,
            GEN_AI_AGENT_DESCRIPTION,
            GEN_AI_AGENT_ID,
            GEN_AI_AGENT_NAME,
            GEN_AI_AGENT_VERSION,
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
            value = parent.attributes.get(key)
            if value is not None:
                span.set_attribute(key, value)

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _drain_or_clear_stale(self, agent_name: str, root_span: Span) -> None:
        """Resolve a leftover agent span in the current context before opening a new one.

        Own orphan (same agent, same trace) → drain child llm/tool spans and
        end it. Anything else → a ContextVar snapshot inherited from another
        agent's task, or from a concurrent run of the *same* agent in another
        session: leave that span alone, clear the inherited llm/tool stacks so
        this round starts clean. Always clears ``_current_agent_span``
        afterwards.

        The trace check is what keeps overlapping sessions safe. A process
        serves several runs at once, and the same agent name appears in all of
        them, so name equality alone would let one session's fresh round end
        another session's still-open span — its remaining llm/tool spans would
        then have no parent and its trace would break mid-run.

        Args:
            agent_name: Name of the agent about to open a span.
            root_span: Run root this round will attach to; its trace is the one
                a leftover must belong to before it can be treated as an orphan.
        """
        prev = get_current_agent_span()
        if prev is None or not prev.is_recording():
            return
        if prev is self._open_invoke_span:
            # This agent's own live invoke span, which the iteration is about
            # to nest under — not a leftover, and ending it here would cut the
            # request's span short at its first round.
            return
        prev_name = prev.attributes.get(DA_AGENT_NAME, "")
        prev_trace_id = getattr(getattr(prev, "context", None), "trace_id", None)
        same_run = prev_trace_id == root_span.context.trace_id
        if prev_name == agent_name and same_run:
            logger.warning(
                "[AgentObservability] closing orphan agent span: %s",
                getattr(prev, "name", "unknown"),
            )
            cascade_close_children()
            mark_span_forced_close(prev, _ORPHAN_AGENT_FORCED_CLOSE_REASON)
            prev.end()
        else:
            logger.info(
                "[AgentObservability] clearing stale agent span inherited from %s "
                "(current agent: %s, same_run=%s)",
                prev_name,
                agent_name,
                same_run,
            )
            # Clear the tool span ContextVar so the new agent starts clean.
            # LLM spans need no equivalent cleanup: ActiveSpanTracker indexes
            # them by the id of the request that opened them, so an inherited
            # context can never make this agent resolve another agent's span.
            clear_tool_span_context()
        set_current_agent_span(None)

    @staticmethod
    def resolve_agent_name(agent: Any) -> str:
        """Return the stable identifier used to name this agent's spans.

        Source priority:
          1. ``agent.member_name`` — TeamAgent property (spawned teammates),
             the authoritative member id.
          2. ``agent.build_context.member_name`` — NativeHarness/DeepAgent
             exposes the build context whose ``member_name`` was derived
             from the runtime context. This is the team leader's source: the
             leader runs as a NativeHarness (no ``member_name`` attribute),
             and its ``card.name`` carries the display_name, so without
             this source the leader span would be named after the
             display_name instead of the member id.
          3. ``agent.card.name`` — fallback for agents with neither of the
             above (e.g. sub-agents, whose card.name is the sub-agent type).

        Every source is coerced to ``str``; a non-string value (notably a
        MagicMock in tests, or a display_name leaked into card.name) is
        rejected so the fallback chain continues instead of producing an
        unusable span name.
        """
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
        agent_name: str,
        decoration: AgentSpanDecoration,
        root_span: Span | None,
    ) -> None:
        """Apply the attributes shared by iteration and invoke spans."""
        span.set_attribute(LANGFUSE_OBSERVATION_TYPE, "agent")
        span.set_attribute(GEN_AI_OPERATION_NAME, "invoke_agent")
        span.set_attribute(OJ_TRACE_SCHEMA_VERSION, "1")
        span.set_attribute(OJ_TRAJECTORY_RECORD_KIND, "agent")
        if agent_name:
            span.set_attribute(DA_AGENT_NAME, agent_name)
            span.set_attribute(GEN_AI_AGENT_NAME, agent_name)
        card = getattr(agent, "card", None)
        if card is not None:
            agent_id = getattr(card, "id", None)
            if isinstance(agent_id, str) and agent_id:
                span.set_attribute(GEN_AI_AGENT_ID, agent_id)
            description = getattr(card, "description", None)
            if isinstance(description, str) and description:
                span.set_attribute(GEN_AI_AGENT_DESCRIPTION, description)
            # AgentCard currently has no version field. Honor an explicit
            # version on compatible/future cards without fabricating one.
            version = getattr(card, "version", None)
            if isinstance(version, str) and version:
                span.set_attribute(GEN_AI_AGENT_VERSION, version)
        if root_span is not None:
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
            ):
                value = root_span.attributes.get(key)
                if value is not None:
                    span.set_attribute(key, value)
        subject = current_execution_subject()
        if subject is not None:
            span.set_attribute(OJ_EXECUTION_SUBJECT_ID, subject.subject_id)
            span.set_attribute(
                OJ_EXECUTION_SUBJECT_DISPLAY_NAME,
                subject.display_name,
            )
            span.set_attribute(OJ_EXECUTION_SUBJECT_KIND, subject.kind)
            if subject.parent_subject_id:
                span.set_attribute(
                    OJ_EXECUTION_SUBJECT_PARENT_ID,
                    subject.parent_subject_id,
                )
            if subject.session_id:
                span.set_attribute(
                    OJ_EXECUTION_SUBJECT_SESSION_ID,
                    subject.session_id,
                )
        # Preserve the root trajectory owner. A subagent's isolated runtime
        # session is already represented by OJ_EXECUTION_SUBJECT_SESSION_ID.
        session_id = str(
            span.attributes.get(OJ_SESSION_ID)
            or span.attributes.get(GEN_AI_CONVERSATION_ID)
            or current_session_id()
            or ""
        )
        if session_id:
            span.set_attribute(LANGFUSE_SESSION_ID, session_id)
        for key, value in decoration.attributes.items():
            span.set_attribute(key, value)


def maybe_agent_observability_rail() -> AgentObservabilityRail | None:
    """Return an ``AgentObservabilityRail`` when observability is on, else None.

    Single source of truth for the "is observability initialized → build one
    rail" guard shared by every mount point (manifest providers, the sub-agent
    dispatch hook, platform adapters), so all of them stay safe unconditional
    additions.
    """
    from openjiuwen.extensions.observability.setup import is_initialized

    if not is_initialized():
        return None
    return AgentObservabilityRail()


__all__ = [
    "AgentObservabilityRail",
    "AgentSpanDecoration",
    "AgentSpanScope",
    "maybe_agent_observability_rail",
]
