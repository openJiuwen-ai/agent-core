# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Agent RAS signal-collection rail.

Builds ``Signal`` objects from DeepAgent lifecycle hooks and forwards them to
an invoke-scoped :class:`AgentRASMonitor`. Runtime state lives in a per-session
monitor cache that ``after_invoke`` clears; only HITL resume data is persisted
on ``session.state``. The rail does not read recovery policy or apply side
effects itself (except flushing a deferred user notice before the next model
call).
"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from openjiuwen.core.common.exception.errors import Termination
from openjiuwen.core.common.logging import logger
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail

from openjiuwen.harness.agent_ras.agents.base import AGENT_RAS_SKILL_ROLES
from openjiuwen.harness.agent_ras.config import AgentRASConfig, coerce_message_locale
from openjiuwen.harness.agent_ras.monitor import AgentRASMonitor
from openjiuwen.harness.agent_ras.recovery.operations import emit_user_notice
from openjiuwen.harness.agent_ras.signal_builder import (
    build_after_model_call_signal,
    build_after_tool_call_signal,
    build_before_model_call_signal,
    build_before_tool_call_signal,
    build_model_exception_signal,
    build_tool_exception_signal,
)

# Same extra-key as LLMRetryRail; both rails append inspectors onto one list.
_STREAM_CHUNK_INSPECTORS_KEY = "_stream_chunk_inspectors"


class AgentRASRail(DeepAgentRail):
    """Thin collection layer: hooks -> Signal -> Monitor.handle / stream / HITL.

    Runtime isolation: ``monitor_factory(session_id)`` builds one Monitor
    (detectors + executor) for the active invoke. ``after_invoke`` stops and
    drops that monitor. The in-flight monitor cache may exceed a soft high-water
    mark; entries are never LRU-evicted while active (CR-003).

    Hook duties:

    - tool/model hooks -> ``monitor.handle(signal, ctx)``
    - ``before_model_call`` registers ``inspect_stream_chunk`` on
      ``ctx.extra["_stream_chunk_inspectors"]`` (same list as LLMRetryRail)
    - thinking-loop abort raises core ``Termination`` so ReActAgent can return a
      partial AssistantMessage (no request_retry, no RAS knowledge in core)
    - ``after_model_call`` also -> ``monitor.finalize_stream_recovery``
      (await/cancel L3 Reviewer; fail-open flush if still pending)

    Message language follows ``system_prompt_builder.language`` / DeepAgent
    config (same pattern as ``SecurityRail``), not a separate RAS config field.
    """

    priority: int = 5

    def __init__(
        self,
        *,
        monitor: AgentRASMonitor | None = None,
        monitor_factory: Callable[[str], AgentRASMonitor] | None = None,
        member_name: str = "",
        config: AgentRASConfig | None = None,
        agents: Any = None,
    ) -> None:
        super().__init__()
        self._monitor = monitor
        self._monitor_factory = monitor_factory
        self._monitors: OrderedDict[str, AgentRASMonitor] = OrderedDict()
        # Soft high-water: log when in-flight monitor count exceeds this; never
        # LRU-evict active monitors (CR-003).
        self._monitor_soft_limit = 32
        self._member = (
            member_name
            or getattr(monitor, "member_name", "")
            or "agent"
        )
        self._config = config or getattr(monitor, "config", None) or AgentRASConfig()
        self._agents = agents if agents is not None else getattr(monitor, "agents", None)
        self.system_prompt_builder = None
        self._host_agent: Any = None

    @property
    def member_name(self) -> str:
        return self._member

    def init(self, agent) -> None:
        """Bind prompt builder so recovery / steering text follow DeepAgent language."""
        self._host_agent = agent
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)

    def uninit(self, agent) -> None:
        _ = agent
        self._host_agent = None
        self.system_prompt_builder = None

    def _ensure_steering_queue(self, ctx: AgentCallbackContext) -> None:
        """Bind a steering queue before model call so recovery can push_steering.

        Prefer the task-loop ``LoopQueues.steering`` when available so EventHandler
        and Agent RAS share one queue; otherwise create a per-invoke default queue.
        Does not touch ReActAgent / DeepAgent call sites.
        """
        bind = getattr(ctx, "bind_steering_queue", None)
        if not callable(bind):
            return
        if getattr(ctx, "steering_queue", None) is not None:
            return
        queue = None
        host = self._host_agent
        handler = getattr(host, "event_handler", None) if host is not None else None
        queues = getattr(handler, "interaction_queues", None) if handler is not None else None
        if queues is not None:
            queue = getattr(queues, "steering", None)
        if queue is None:
            queue = asyncio.Queue()
        bind(queue)

    def _resolve_language(self, agent: Any = None) -> str:
        """Return cn/en from prompt builder or DeepAgent language."""
        builder = self.system_prompt_builder
        if builder is not None:
            lang = getattr(builder, "language", None)
            if isinstance(lang, str) and lang.strip():
                return coerce_message_locale(lang)
        if agent is not None:
            deep_config = getattr(agent, "deep_config", None)
            lang = getattr(deep_config, "language", None)
            if isinstance(lang, str) and lang.strip():
                return coerce_message_locale(lang)
        return "cn"

    @staticmethod
    def _session_id(ctx: AgentCallbackContext) -> str:
        """Resolve the session key used for monitor isolation."""
        session = getattr(ctx, "session", None)
        getter = getattr(session, "get_session_id", None)
        if callable(getter):
            try:
                return str(getter() or "")
            except Exception:
                logger.warning("AgentRASRail session id lookup failed", exc_info=True)
        inputs = getattr(ctx, "inputs", None)
        return str(getattr(inputs, "conversation_id", None) or "default")

    def _monitor_for(self, ctx: AgentCallbackContext) -> AgentRASMonitor:
        """Return (or create) the Monitor bound to ``ctx``'s session.

        The cache holds in-flight invoke monitors only; ``after_invoke`` pops
        each entry. When the count exceeds ``_monitor_soft_limit``, a warning is
        logged but no active monitor is evicted (CR-003).
        """
        session_id = self._session_id(ctx)
        monitor = self._monitors.get(session_id)
        if monitor is None:
            if self._monitor_factory is not None:
                monitor = self._monitor_factory(session_id)
            elif self._monitor is not None:
                monitor = self._monitor
            else:
                raise RuntimeError("AgentRASRail requires a monitor factory")
            self._monitors[session_id] = monitor
            if len(self._monitors) > self._monitor_soft_limit:
                logger.warning(
                    "AgentRASRail in-flight monitor count=%s exceeds soft "
                    "limit=%s (session_id=%s); active monitors are not evicted",
                    len(self._monitors),
                    self._monitor_soft_limit,
                    session_id,
                )
        else:
            self._monitors.move_to_end(session_id)
        agent = getattr(ctx, "agent", None)
        monitor.set_language(self._resolve_language(agent))
        return monitor

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        """Start the session monitor and warm up the detection/recovery team.

        Bind the steering queue as early as possible so recovery steering
        pushed from AFTER_TOOL_CALL paths lands on the same queue the main
        loop drains before the next model call.
        """
        self._ensure_steering_queue(ctx)
        try:
            monitor = self._monitor_for(ctx)
            await monitor.start(ctx)
        except Exception:
            logger.warning("AgentRASRail monitor start failed", exc_info=True)
        await self._warmup_agent_team()

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        """Stop and drop the session monitor.

        Also drops any steering left un-drained in this invoke so a stale
        message cannot leak into the next invoke's LLM context.
        """
        drain = getattr(ctx, "drain_steering", None)
        if callable(drain):
            drain()
        session_id = self._session_id(ctx)
        monitor = self._monitors.pop(session_id, None)
        if monitor is None:
            return
        try:
            await monitor.stop()
        except Exception:
            logger.warning("AgentRASRail monitor stop failed", exc_info=True)

    async def _warmup_agent_team(self) -> None:
        """Pre-create detection/recovery members on each invoke (internal)."""
        if self._agents is None:
            return
        started = time.monotonic()
        try:
            await self._agents.warmup_members(AGENT_RAS_SKILL_ROLES)
            logger.info(
                "[AgentRASRail] agent_team warmup done roles=%s elapsed_ms=%.1f",
                AGENT_RAS_SKILL_ROLES,
                (time.monotonic() - started) * 1000.0,
            )
        except Exception:
            logger.warning(
                "[AgentRASRail] agent_team warmup failed",
                exc_info=True,
            )

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Forward BEFORE_TOOL_CALL to ``Monitor.handle``."""
        await self._monitor_for(ctx).handle(
            build_before_tool_call_signal(self._member, ctx.inputs), ctx,
        )

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Forward AFTER_TOOL_CALL to ``Monitor.handle``."""
        await self._monitor_for(ctx).handle(
            build_after_tool_call_signal(self._member, ctx.inputs, ctx), ctx,
        )

    async def on_tool_exception(self, ctx: AgentCallbackContext) -> None:
        """Forward tool exceptions to ``Monitor.handle``."""
        await self._monitor_for(ctx).handle(
            build_tool_exception_signal(self._member, ctx.inputs, ctx.exception),
            ctx,
        )

    async def on_model_exception(self, ctx: AgentCallbackContext) -> None:
        """Forward model exceptions to ``Monitor.handle``.

        Soft-stop (``Termination``) is handled inside ``react_agent._stream`` and
        never surfaces here; this branch is a defensive fallback so a leaked
        ``Termination`` is not misreported as a model failure.
        """
        if isinstance(getattr(ctx, "exception", None), Termination):
            return
        await self._monitor_for(ctx).handle(
            build_model_exception_signal(self._member, ctx.exception), ctx,
        )

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Emit deferred recovery notice (if any), reopen recovery gates, then detect."""
        self._ensure_steering_queue(ctx)
        monitor = self._monitor_for(ctx)
        monitor.bind_ctx(ctx)
        notice = monitor.consume_notice_for_emit()
        if notice:
            await emit_user_notice(ctx, notice)
        # After abnormal recovery + steering, clear gates so the next model
        # turn can re-detect instead of silently suppressing forever.
        monitor.prepare_for_next_model_call()
        await monitor.handle(
            build_before_model_call_signal(self._member, ctx.inputs), ctx,
        )
        self._register_stream_chunk_inspector(ctx)

    def _register_stream_chunk_inspector(self, ctx: AgentCallbackContext) -> None:
        """Install this rail's inspector on the shared LLMRetry extra list."""
        inspectors = ctx.extra.get(_STREAM_CHUNK_INSPECTORS_KEY)
        if not isinstance(inspectors, list):
            inspectors = []
        inspectors = [
            inspector
            for inspector in inspectors
            if getattr(inspector, "__self__", None) is not self
        ]
        # Front of the list so an armed abort raises before LLMRetryRail can
        # treat leftover looping tokens as a repeat and request_retry.
        inspectors.insert(0, self.inspect_stream_chunk)
        ctx.extra[_STREAM_CHUNK_INSPECTORS_KEY] = inspectors

    async def inspect_stream_chunk(self, ctx: AgentCallbackContext, chunk: Any) -> None:
        """Inspect reasoning/content chunks; raise after the truncated hit chunk.

        Raises the core ``Termination`` control-flow exception so ``react_agent``
        treats this as a normal stop (soft-stop with partial output) instead of a
        model failure — no Agent RAS knowledge leaks into core.
        """
        monitor = self._monitor_for(ctx)
        if getattr(monitor, "should_abort_stream", False):
            from openjiuwen.core.common.exception.codes import StatusCode

            raise Termination(
                StatusCode.CALLBACK_EXECUTION_ABORTED,
                msg="Agent RAS stream abort: thinking-loop",
            )
        for field, chunk_type in (
            ("reasoning_content", "llm_reasoning"),
            ("content", "llm_output"),
        ):
            text = getattr(chunk, field, None)
            if not text:
                continue
            await monitor.on_stream_chunk(chunk_type, str(text), chunk, ctx)
        if getattr(monitor, "abnormal_committed", False):
            arm = getattr(monitor, "arm_stream_abort_raise", None)
            if callable(arm):
                arm()

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        """Run detection/recovery, then finalize stream recovery (L3 fail-open)."""
        monitor = self._monitor_for(ctx)
        await monitor.handle(
            build_after_model_call_signal(self._member, ctx.inputs), ctx,
        )
        try:
            await monitor.finalize_stream_recovery(ctx)
        except Exception:
            logger.warning(
                "AgentRASRail.finalize_stream_recovery failed", exc_info=True,
            )

    async def get_metrics(self, session_id: str | None = None) -> dict[str, Any]:
        """Aggregate monitor counters for one session or all active sessions."""
        if session_id is not None:
            monitor = self._monitors.get(session_id)
            return await monitor.get_metrics() if monitor else {}
        metrics = [await m.get_metrics() for m in self._monitors.values()]
        return {
            "session_count": len(metrics),
            "invoke_count": sum(m.get("invoke_count", 0) for m in metrics),
            "anomaly_count": sum(m.get("anomaly_count", 0) for m in metrics),
            "recovery_count": sum(m.get("recovery_count", 0) for m in metrics),
        }

    async def get_recent_events(
        self,
        n: int = 50,
        session_id: str | None = None,
    ) -> list[Any]:
        """Return recent anomaly events from one or all session monitors."""
        if session_id is not None:
            monitor = self._monitors.get(session_id)
            return await monitor.get_recent_events(n) if monitor else []
        events: list[Any] = []
        for monitor in self._monitors.values():
            events.extend(await monitor.get_recent_events(n))
        return events

    async def aclose(self) -> None:
        """Stop every cached session monitor (agent teardown fallback)."""
        monitors = list(self._monitors.values())
        self._monitors.clear()
        for monitor in monitors:
            await monitor.stop()
