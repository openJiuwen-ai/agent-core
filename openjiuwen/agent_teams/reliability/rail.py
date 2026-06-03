# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Reliability rail: collects member-internal lifecycle signals.

One rail per member. Each lifecycle hook converts its callback context into a
``Signal``, feeds it to the member's ``ReliabilityMonitor`` (which detects and
reports anomalies), and applies reversible local self-steering for any
returned anomaly via the optional ``LocalAutoRemediator``. The rail never
alters tool args, model calls, or the agent loop — the only effect it can have
is a non-destructive steering nudge — so it carries a low priority and runs
after the behavior-shaping rails.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from openjiuwen.agent_teams.reliability.anomaly import Anomaly
from openjiuwen.agent_teams.reliability.monitor import ReliabilityMonitor
from openjiuwen.agent_teams.reliability.remediation.local import LocalAutoRemediator
from openjiuwen.agent_teams.reliability.reporter import LocalAnomalyReporter
from openjiuwen.agent_teams.reliability.signals import Signal, SignalKind
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail


def _error_text(exc: Exception | None) -> str:
    """Render an exception into a stable short error string."""
    return str(exc) if exc is not None else "error"


def _args_as_dict(tool_args: Any) -> dict[str, Any] | None:
    """Return tool args only when they are a dict (for stable hashing)."""
    return tool_args if isinstance(tool_args, dict) else None


def _measure_response(response: Any) -> tuple[int | None, int | None]:
    """Best-effort extraction of output text / thinking lengths.

    Returns ``(text_len, thinking_len)``, each None when the response does not
    carry that field as a string. Tolerant of the various response shapes the
    underlying LLM layer may produce.
    """
    if response is None:
        return None, None
    content = getattr(response, "content", None)
    text_len = len(content) if isinstance(content, str) else None
    reasoning = getattr(response, "reasoning_content", None) or getattr(response, "thinking", None)
    thinking_len = len(reasoning) if isinstance(reasoning, str) else None
    return text_len, thinking_len


class ReliabilityRail(DeepAgentRail):
    """Capture member-internal signals and apply reversible local steering."""

    priority = 5

    def __init__(
        self,
        *,
        monitor: ReliabilityMonitor,
        member_name: str,
        auto_remediator: LocalAutoRemediator | None = None,
        local_reporter: LocalAnomalyReporter | None = None,
    ) -> None:
        super().__init__()
        self._monitor = monitor
        self._member = member_name
        self._auto = auto_remediator
        self._local_reporter = local_reporter

    def bind_local_sink(self, sink: Callable[[Anomaly], Awaitable[None]]) -> None:
        """Bind the leader's in-process anomaly sink (no-op for non-leader rails).

        Leader self-monitoring routes anomalies straight to the in-process
        handler instead of publishing an event the leader's own messager
        self-filter would drop. Called once after the dispatcher is built (see
        ``TeamAgent._register_reliability_local_sink``).
        """
        if self._local_reporter is not None:
            self._local_reporter.bind(sink)

    async def _emit(self, signal: Signal, ctx: AgentCallbackContext) -> None:
        """Feed the signal, then steer locally for any returned anomaly."""
        anomalies = await self._monitor.feed(signal)
        if self._auto is None:
            return
        for anomaly in anomalies:
            message = self._auto.steer_message(anomaly)
            if message:
                ctx.push_steering(message)

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Capture the tool name + args before a tool runs."""
        inputs = ctx.inputs
        await self._emit(
            Signal(
                kind=SignalKind.BEFORE_TOOL_CALL,
                member_name=self._member,
                tool_name=getattr(inputs, "tool_name", "") or "",
                tool_args=_args_as_dict(getattr(inputs, "tool_args", None)),
            ),
            ctx,
        )

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Mark a successful tool completion."""
        inputs = ctx.inputs
        await self._emit(
            Signal(
                kind=SignalKind.AFTER_TOOL_CALL,
                member_name=self._member,
                tool_name=getattr(inputs, "tool_name", "") or "",
                tool_result=getattr(inputs, "tool_result", None),
            ),
            ctx,
        )

    async def on_tool_exception(self, ctx: AgentCallbackContext) -> None:
        """Capture a tool failure."""
        inputs = ctx.inputs
        await self._emit(
            Signal(
                kind=SignalKind.TOOL_EXCEPTION,
                member_name=self._member,
                tool_name=getattr(inputs, "tool_name", "") or "",
                error=_error_text(ctx.exception),
            ),
            ctx,
        )

    async def on_model_exception(self, ctx: AgentCallbackContext) -> None:
        """Capture a model-call failure."""
        await self._emit(
            Signal(
                kind=SignalKind.MODEL_EXCEPTION,
                member_name=self._member,
                error=_error_text(ctx.exception),
            ),
            ctx,
        )

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Capture the current context message count (compaction hint)."""
        messages = getattr(ctx.inputs, "messages", None)
        count = len(messages) if isinstance(messages, list) else None
        await self._emit(
            Signal(
                kind=SignalKind.BEFORE_MODEL_CALL,
                member_name=self._member,
                message_count=count,
            ),
            ctx,
        )

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        """Capture output text / thinking lengths from the response."""
        text_len, thinking_len = _measure_response(getattr(ctx.inputs, "response", None))
        await self._emit(
            Signal(
                kind=SignalKind.AFTER_MODEL_CALL,
                member_name=self._member,
                text_len=text_len,
                thinking_len=thinking_len,
            ),
            ctx,
        )
