# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""DeepAgent observability rail.

Only fills the gap left by AsyncCallbackFramework: the outer task-loop
iteration boundary. Other lifecycle hooks (model_call, tool_call,
invoke) are intentionally left as no-ops because the Callback handlers
already cover them and double-instrumenting would produce duplicate
spans.
"""

from __future__ import annotations

from opentelemetry.trace import (
    Span,
    SpanKind,
    Status,
    StatusCode,
    Tracer,
)

from openjiuwen.agent_teams.observability.semconv import (
    DA_TASK_IS_FOLLOW_UP,
    DA_TASK_ITERATION,
)
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail


_TRACER_NAME = "openjiuwen.agent_teams.observability.rail"
_SPAN_KEY = "_otel_task_iter_span"


class ObservabilityRail(DeepAgentRail):
    """Open / close a span around each outer task-loop iteration.

    Intentionally does NOT override before/after_model_call,
    before/after_tool_call, before/after_invoke. Those are handled by
    OtelCallbackHandler so we avoid duplicate spans and keep the
    contract: each observable point has exactly one source.
    """

    priority: int = 10  # Low priority: observability runs last.

    def __init__(self, *, tracer: Tracer | None = None) -> None:
        """Initialize parent rail state.

        Args:
            tracer: Optional explicit Tracer. When omitted the rail
                resolves the active observability tracer at call time
                via ``setup.get_tracer``, so it picks up the provider
                created by ``init_observability`` even when the rail
                was constructed before init.
        """
        super().__init__()
        self._injected_tracer = tracer

    def _tracer(self) -> Tracer:
        """Resolve the tracer lazily so we see the currently-active provider."""
        if self._injected_tracer is not None:
            return self._injected_tracer
        from openjiuwen.agent_teams.observability.setup import get_tracer

        return get_tracer(_TRACER_NAME)

    async def before_task_iteration(self, ctx: AgentCallbackContext) -> None:
        """Open a span scoped to one outer task-loop iteration."""
        try:
            inputs = ctx.inputs
            iteration = int(getattr(inputs, "iteration", 0) or 0)
            is_follow_up = bool(getattr(inputs, "is_follow_up", False))
            span = self._tracer().start_span(
                name=f"deepagent.task_iteration.{iteration}",
                kind=SpanKind.INTERNAL,
            )
            span.set_attribute(DA_TASK_ITERATION, iteration)
            span.set_attribute(DA_TASK_IS_FOLLOW_UP, is_follow_up)
            ctx.extra[_SPAN_KEY] = span
        except Exception as exc:
            team_logger.warning("otel rail before_task_iteration failed: {}", exc)

    async def after_task_iteration(self, ctx: AgentCallbackContext) -> None:
        """Close the iteration span; mark ERROR if exception is present."""
        try:
            span: Span | None = ctx.extra.pop(_SPAN_KEY, None)
            if span is None:
                return
            if ctx.exception is not None:
                span.record_exception(ctx.exception)
                span.set_status(Status(StatusCode.ERROR, str(ctx.exception)))
            else:
                span.set_status(Status(StatusCode.OK))
            span.end()
        except Exception as exc:
            team_logger.warning("otel rail after_task_iteration failed: {}", exc)
