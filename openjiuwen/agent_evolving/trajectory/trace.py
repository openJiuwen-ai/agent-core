# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Trajectory trace state manager and tracer extension handlers.

Process-wide model
------------------
``ensure_otlp_handlers_registered()`` installs one shared
``TrajectoryTraceStateManager`` (and matching agent/workflow handlers) per
process so Tracer.init() can pick them up globally.

Multi-agent isolation relies on **trace_id** keys inside that shared manager:
each Agent/Session tracer UUID owns an independent ``TrajectoryTraceState``.
Callers **must** pair every ``bind_trace`` with ``release_trace`` (prefer
``consumer_id`` so multiple rails can share a trace safely). Failure to
release causes states to accumulate and eventually leak memory.

Tests should call ``clear_process_trajectory_state()`` between cases that use
the process-wide handlers.
"""

from __future__ import annotations

from threading import RLock
from typing import Any

from openjiuwen.agent_evolving.trajectory.semconv import OJ_TRACE_ID, TRAJECTORY_TRACE_ID
from openjiuwen.agent_evolving.trajectory.trace_state import TrajectoryTraceState
from openjiuwen.agent_evolving.trajectory.types import Trajectory
from openjiuwen.core.common.logging import logger
from openjiuwen.core.session.tracer.data import InvokeType
from openjiuwen.core.session.tracer.handler import TraceExtAgentHandler, TraceExtWorkflowHandler
from openjiuwen.core.session.tracer.span import TraceAgentSpan
from openjiuwen.core.session.tracer.tracer import TracerHandlerRegistry

TRAJECTORY_TRACE_AGENT_HANDLER_NAME = "trajectory_trace_agent"
TRAJECTORY_TRACE_WORKFLOW_HANDLER_NAME = "trajectory_trace_workflow"

# Soft warning threshold for live bound traces in the process-wide manager.
DEFAULT_MAX_BOUND_TRACES = 1024

registration_lock = RLock()
process_state_manager: "TrajectoryTraceStateManager | None" = None
process_agent_handler: "TrajectoryTraceAgentHandler | None" = None
process_workflow_handler: "TrajectoryTraceWorkflowHandler | None" = None


class TrajectoryTraceStateManager:
    """Shared state bucket keyed by ``trace_id``.

    One manager is typically process-wide (see module docstring). Concurrent
    Agents are isolated by allocating distinct tracer UUIDs as ``trace_id``.
    Lifecycle contract: ``bind_trace`` → record spans → ``release_trace``.
    """

    def __init__(self, *, max_bound_traces: int = DEFAULT_MAX_BOUND_TRACES) -> None:
        if max_bound_traces < 1:
            raise ValueError("max_bound_traces must be >= 1")
        self.states: dict[str, TrajectoryTraceState] = {}
        self.metadata_by_trace_id: dict[str, dict[str, Any]] = {}
        self.bound_trace_ids: set[str] = set()
        self.consumer_ids_by_trace_id: dict[str, set[str]] = {}
        self.max_bound_traces = max_bound_traces
        self.lock = RLock()

    @property
    def bound_trace_count(self) -> int:
        with self.lock:
            return len(self.bound_trace_ids)

    def state_for(self, trace_id: str) -> TrajectoryTraceState:
        trace_id = str(trace_id)
        with self.lock:
            state = self.states.get(trace_id)
            if state is None:
                state = TrajectoryTraceState(trace_id)
                self.states[trace_id] = state
            return state

    def bind_trace(
        self,
        trace_id: str,
        *,
        session_id: str | None = None,
        source: str | None = None,
        case_id: str | None = None,
        member_id: str | None = None,
        meta: dict[str, Any] | None = None,
        consumer_id: str | None = None,
    ) -> None:
        trace_id = str(trace_id)
        with self.lock:
            state = self.states.get(trace_id)
            if state is None:
                self.states[trace_id] = TrajectoryTraceState(trace_id)
            already_bound = trace_id in self.bound_trace_ids
            self.bound_trace_ids.add(trace_id)
            if consumer_id:
                self.consumer_ids_by_trace_id.setdefault(trace_id, set()).add(str(consumer_id))
            metadata = self.metadata_by_trace_id.setdefault(trace_id, {})
            if session_id is not None:
                metadata["session_id"] = session_id
            if source is not None:
                metadata["source"] = source
            if case_id is not None:
                metadata["case_id"] = case_id
            if member_id is not None:
                metadata["member_id"] = member_id
            if meta:
                merged = dict(metadata.get("meta") or {})
                merged.update(meta)
                metadata["meta"] = merged
            bound_count = len(self.bound_trace_ids)
        if not already_bound and bound_count > self.max_bound_traces:
            logger.warning(
                "TrajectoryTraceStateManager has %s bound traces (soft max=%s); "
                "missing release_trace() can leak process memory across Agents "
                "(latest_trace_id=%s)",
                bound_count,
                self.max_bound_traces,
                trace_id,
            )

    def is_bound_trace(self, trace_id: str) -> bool:
        trace_id = str(trace_id)
        with self.lock:
            return trace_id in self.bound_trace_ids

    def build_trajectory(
        self,
        trace_id: str,
        *,
        session_id: str | None = None,
        source: str | None = None,
        case_id: str | None = None,
        member_id: str | None = None,
        meta: dict[str, Any] | None = None,
        max_steps: int | None = None,
        finalize: bool = False,
    ) -> Trajectory | None:
        trace_id = str(trace_id)
        with self.lock:
            state = self.states.get(trace_id)
            metadata = dict(self.metadata_by_trace_id.get(trace_id) or {})
        if state is None:
            return None
        trajectory_meta = dict(metadata.get("meta") or {})
        trajectory_meta.update(meta or {})
        return state.to_trajectory(
            session_id=session_id or str(metadata.get("session_id") or ""),
            source=source or str(metadata.get("source") or "online"),
            case_id=case_id or metadata.get("case_id"),
            member_id=member_id or metadata.get("member_id"),
            meta=trajectory_meta,
            max_steps=max_steps,
            finalize=finalize,
        )

    def clear_trace(self, trace_id: str) -> None:
        trace_id = str(trace_id)
        with self.lock:
            self._clear_trace_locked(trace_id)
        _forget_workflow_trace_mappings(trace_id)

    def release_trace(self, trace_id: str, *, consumer_id: str | None = None) -> None:
        """Release a consumer's hold; clears state when no consumers remain.

        Prefer always passing ``consumer_id`` when multiple rails share one
        process-wide manager. Omitting it force-clears the trace immediately.
        """
        trace_id = str(trace_id)
        cleared = False
        with self.lock:
            if not consumer_id:
                self._clear_trace_locked(trace_id)
                cleared = True
            else:
                consumers = self.consumer_ids_by_trace_id.get(trace_id)
                if consumers is None:
                    self._clear_trace_locked(trace_id)
                    cleared = True
                else:
                    consumers.discard(str(consumer_id))
                    if not consumers:
                        self._clear_trace_locked(trace_id)
                        cleared = True
        if cleared:
            _forget_workflow_trace_mappings(trace_id)

    def _clear_trace_locked(self, trace_id: str) -> None:
        self.states.pop(trace_id, None)
        self.metadata_by_trace_id.pop(trace_id, None)
        self.bound_trace_ids.discard(trace_id)
        self.consumer_ids_by_trace_id.pop(trace_id, None)

    def clear(self) -> None:
        """Drop all in-memory traces. Intended for tests / process teardown."""
        with self.lock:
            self.states.clear()
            self.metadata_by_trace_id.clear()
            self.bound_trace_ids.clear()
            self.consumer_ids_by_trace_id.clear()


def _forget_workflow_trace_mappings(trace_id: str) -> None:
    handler = process_workflow_handler
    if handler is not None:
        handler.forget_trace(trace_id)


class TrajectoryTraceAgentHandler(TraceExtAgentHandler):
    """Agent tracer extension handler for trajectory traces."""

    def __init__(self, state_manager: TrajectoryTraceStateManager):
        TraceExtAgentHandler.__init__(self)
        self.state_manager = state_manager

    def set_trace_id(self, trace_id: str) -> None:
        return None

    def build_trajectory(self, trace_id: str, **kwargs) -> Trajectory | None:
        return self.state_manager.build_trajectory(trace_id, **kwargs)

    def state_for_span(self, span: TraceAgentSpan | None) -> TrajectoryTraceState | None:
        trace_id = getattr(span, "trace_id", None)
        if not trace_id:
            return None
        trace_id = str(trace_id)
        if not self.state_manager.is_bound_trace(trace_id):
            return None
        return self.state_manager.state_for(trace_id)

    @staticmethod
    def safe(callable_obj, *args, **kwargs) -> None:
        try:
            callable_obj(*args, **kwargs)
        except Exception as exc:
            logger.warning("trajectory trace handler failed: %s", exc)

    async def on_llm_start(self, span: TraceAgentSpan, inputs: Any, instance_info: dict, **kwargs):
        self.record_start(span, InvokeType.LLM.value, inputs, instance_info)

    async def on_llm_request(self, span: TraceAgentSpan, **kwargs):
        self.record_event(span, "llm.request", kwargs)

    async def on_llm_end(self, span: TraceAgentSpan, outputs, **kwargs):
        self.record_end(span, outputs)

    async def on_llm_error(self, span: TraceAgentSpan, error, **kwargs):
        self.record_error(span, error)

    async def on_plugin_start(self, span: TraceAgentSpan, inputs: Any, instance_info: dict, **kwargs):
        self.record_start(span, InvokeType.PLUGIN.value, inputs, instance_info)

    async def on_plugin_end(self, span: TraceAgentSpan, outputs, **kwargs):
        self.record_end(span, outputs)

    async def on_plugin_error(self, span: TraceAgentSpan, error, **kwargs):
        self.record_error(span, error)

    async def on_prompt_start(self, span: TraceAgentSpan, inputs: Any, instance_info: dict, **kwargs):
        self.record_start(span, InvokeType.PROMPT.value, inputs, instance_info)

    async def on_prompt_end(self, span: TraceAgentSpan, outputs, **kwargs):
        self.record_end(span, outputs)

    async def on_prompt_error(self, span: TraceAgentSpan, error, **kwargs):
        self.record_error(span, error)

    async def on_chain_start(self, span: TraceAgentSpan, inputs: Any, instance_info: dict, **kwargs):
        self.record_start(span, InvokeType.CHAIN.value, inputs, instance_info)

    async def on_chain_end(self, span: TraceAgentSpan, outputs, **kwargs):
        self.record_end(span, outputs)

    async def on_chain_error(self, span: TraceAgentSpan, error, **kwargs):
        self.record_error(span, error)

    async def on_retriever_start(self, span: TraceAgentSpan, inputs: Any, instance_info: dict, **kwargs):
        self.record_start(span, InvokeType.RETRIEVER.value, inputs, instance_info)

    async def on_retriever_end(self, span: TraceAgentSpan, outputs, **kwargs):
        self.record_end(span, outputs)

    async def on_retriever_error(self, span: TraceAgentSpan, error, **kwargs):
        self.record_error(span, error)

    async def on_evaluator_start(self, span: TraceAgentSpan, inputs: Any, instance_info: dict, **kwargs):
        self.record_start(span, InvokeType.EVALUATOR.value, inputs, instance_info)

    async def on_evaluator_end(self, span: TraceAgentSpan, outputs, **kwargs):
        self.record_end(span, outputs)

    async def on_evaluator_error(self, span: TraceAgentSpan, error, **kwargs):
        self.record_error(span, error)

    async def on_workflow_start(self, span: TraceAgentSpan, inputs: Any, instance_info: dict, **kwargs):
        self.record_start(span, InvokeType.WORKFLOW.value, inputs, instance_info)

    async def on_workflow_end(self, span: TraceAgentSpan, outputs, **kwargs):
        self.record_end(span, outputs)

    async def on_workflow_error(self, span: TraceAgentSpan, error, **kwargs):
        self.record_error(span, error)

    def record_start(self, span: TraceAgentSpan, invoke_type: str, inputs: Any, instance_info: dict | None) -> None:
        state = self.state_for_span(span)
        if state is None:
            return
        self.safe(
            state.record_agent_start,
            span=span,
            invoke_type=invoke_type,
            inputs=inputs,
            instance_info=instance_info,
        )

    def record_event(self, span: TraceAgentSpan, name: str, attributes: dict[str, Any]) -> None:
        state = self.state_for_span(span)
        if state is None:
            return
        self.safe(state.record_agent_event, span=span, name=name, attributes=attributes)

    def record_end(self, span: TraceAgentSpan, outputs: Any) -> None:
        state = self.state_for_span(span)
        if state is None:
            return
        self.safe(state.record_agent_end, span=span, outputs=outputs)

    def record_error(self, span: TraceAgentSpan, error: Any) -> None:
        state = self.state_for_span(span)
        if state is None:
            return
        self.safe(state.record_agent_error, span=span, error=error)


class TrajectoryTraceWorkflowHandler(TraceExtWorkflowHandler):
    """Workflow tracer extension handler for trajectory traces."""

    def __init__(self, state_manager: TrajectoryTraceStateManager):
        TraceExtWorkflowHandler.__init__(self)
        self.state_manager = state_manager
        self.workflow_trace_ids: dict[str, set[str]] = {}

    def set_trace_id(self, trace_id: str) -> None:
        return None

    def forget_trace(self, trace_id: str) -> None:
        """Drop cached invoke_id → trace_id mappings for a released trace."""
        trace_id = str(trace_id)
        for invoke_id, trace_ids in list(self.workflow_trace_ids.items()):
            trace_ids.discard(trace_id)
            if not trace_ids:
                self.workflow_trace_ids.pop(invoke_id, None)

    def build_trajectory(self, trace_id: str, **kwargs) -> Trajectory | None:
        return self.state_manager.build_trajectory(trace_id, **kwargs)

    def state_for_workflow_event(
        self,
        invoke_id: str,
        *,
        metadata: dict[str, Any] | None = None,
        kwargs: dict[str, Any] | None = None,
    ) -> TrajectoryTraceState | None:
        trace_id = self.extract_trace_id(metadata, kwargs)
        if trace_id is None:
            trace_ids = self.workflow_trace_ids.get(invoke_id) or set()
            bound_trace_ids = [item for item in trace_ids if self.state_manager.is_bound_trace(item)]
            if len(bound_trace_ids) != 1:
                return None
            trace_id = bound_trace_ids[0]
        if trace_id is None:
            return None
        if not self.state_manager.is_bound_trace(trace_id):
            trace_ids = self.workflow_trace_ids.get(invoke_id)
            if trace_ids is not None:
                trace_ids.discard(trace_id)
                if not trace_ids:
                    self.workflow_trace_ids.pop(invoke_id, None)
            return None
        self.workflow_trace_ids.setdefault(invoke_id, set()).add(trace_id)
        return self.state_manager.state_for(trace_id)

    @staticmethod
    def extract_trace_id(metadata: dict[str, Any] | None, kwargs: dict[str, Any] | None) -> str | None:
        for payload in (metadata or {}, kwargs or {}):
            for key in ("trace_id", OJ_TRACE_ID, TRAJECTORY_TRACE_ID):
                value = payload.get(key)
                if value:
                    return str(value)
        return None

    @staticmethod
    def safe(callable_obj, *args, **kwargs) -> None:
        try:
            callable_obj(*args, **kwargs)
        except Exception as exc:
            logger.warning("trajectory trace handler failed: %s", exc)

    async def on_call_start(
        self,
        invoke_id: str,
        metadata: dict = None,
        inputs: Any = None,
        need_send: bool = False,
        source_ids: list = None,
        **kwargs,
    ):
        state = self.state_for_workflow_event(invoke_id, metadata=metadata, kwargs=kwargs)
        if state is None:
            return
        self.safe(
            state.record_workflow_start,
            invoke_id=invoke_id,
            metadata=metadata,
            inputs=inputs,
            source_ids=source_ids,
            parent_node_id=kwargs.get("parent_node_id", ""),
        )

    async def on_call_done(self, invoke_id: str, outputs: Any = None, **kwargs):
        state = self.state_for_workflow_event(invoke_id, metadata=kwargs.get("metadata"), kwargs=kwargs)
        if state is None:
            return
        self.safe(state.record_workflow_done, invoke_id=invoke_id, outputs=outputs)

    async def on_pre_invoke(
        self,
        invoke_id: str,
        inputs: Any,
        component_metadata: dict,
        need_send: bool = False,
        **kwargs,
    ):
        state = self.state_for_workflow_event(invoke_id, metadata=component_metadata, kwargs=kwargs)
        if state is None:
            return
        self.safe(
            state.record_workflow_inputs,
            invoke_id=invoke_id,
            inputs=inputs,
            metadata=component_metadata,
        )

    async def on_pre_stream(self, invoke_id: str, chunk, need_send: bool = False, **kwargs):
        state = self.state_for_workflow_event(invoke_id, kwargs=kwargs)
        if state is None:
            return
        self.safe(
            state.record_workflow_event,
            invoke_id=invoke_id,
            name="workflow.pre_stream",
            attributes={"chunk": chunk},
        )

    async def on_invoke(self, invoke_id: str, on_invoke_data: dict = None, exception: Exception = None, **kwargs):
        state = self.state_for_workflow_event(invoke_id, metadata=on_invoke_data, kwargs=kwargs)
        if state is None:
            return
        self.safe(
            state.record_workflow_event,
            invoke_id=invoke_id,
            name="workflow.invoke",
            attributes=on_invoke_data,
            exception=exception,
        )

    async def on_post_invoke(self, invoke_id: str, outputs, inputs=None, **kwargs):
        state = self.state_for_workflow_event(invoke_id, kwargs=kwargs)
        if state is None:
            return
        self.safe(state.record_workflow_outputs, invoke_id=invoke_id, outputs=outputs)

    async def on_post_stream(self, invoke_id: str, chunk, **kwargs):
        state = self.state_for_workflow_event(invoke_id, kwargs=kwargs)
        if state is None:
            return
        self.safe(
            state.record_workflow_event,
            invoke_id=invoke_id,
            name="workflow.post_stream",
            attributes={"chunk": chunk},
        )

    async def on_interact(
        self,
        invoke_id: str,
        inputs: Any,
        component_metadata: dict,
        need_send: bool = False,
        **kwargs,
    ):
        state = self.state_for_workflow_event(invoke_id, metadata=component_metadata, kwargs=kwargs)
        if state is None:
            return
        self.safe(
            state.record_workflow_event,
            invoke_id=invoke_id,
            name="workflow.interact",
            attributes={"inputs": inputs, **(component_metadata or {})},
        )


def ensure_otlp_handlers_registered() -> TrajectoryTraceStateManager:
    """Register process-wide trajectory trace handlers once and return their manager."""
    global process_state_manager, process_agent_handler, process_workflow_handler

    with registration_lock:
        if process_state_manager is None:
            process_state_manager = TrajectoryTraceStateManager()
            process_agent_handler = TrajectoryTraceAgentHandler(process_state_manager)
            process_workflow_handler = TrajectoryTraceWorkflowHandler(process_state_manager)

        if process_agent_handler is None or process_workflow_handler is None:
            raise RuntimeError("trajectory trace handlers were not initialized")

        registered_agent = TracerHandlerRegistry.get_agent_handlers().get(TRAJECTORY_TRACE_AGENT_HANDLER_NAME)
        if registered_agent is None:
            TracerHandlerRegistry.register_handler(
                TRAJECTORY_TRACE_AGENT_HANDLER_NAME,
                process_agent_handler,
            )
        elif registered_agent is not process_agent_handler:
            raise ValueError(f"Handler '{TRAJECTORY_TRACE_AGENT_HANDLER_NAME}' already registered by another instance")

        registered_workflow = TracerHandlerRegistry.get_workflow_handlers().get(TRAJECTORY_TRACE_WORKFLOW_HANDLER_NAME)
        if registered_workflow is None:
            TracerHandlerRegistry.register_handler(
                TRAJECTORY_TRACE_WORKFLOW_HANDLER_NAME,
                process_workflow_handler,
            )
        elif registered_workflow is not process_workflow_handler:
            raise ValueError(
                f"Handler '{TRAJECTORY_TRACE_WORKFLOW_HANDLER_NAME}' already registered by another instance"
            )

        return process_state_manager


def clear_process_trajectory_state() -> None:
    """Clear process-wide in-memory trajectory state without unregistering handlers.

    Use between tests (or for ops recovery) to avoid cross-case / cross-Agent
    leakage while keeping the shared OTLP handlers registered.
    """
    with registration_lock:
        if process_state_manager is not None:
            process_state_manager.clear()
        if process_workflow_handler is not None:
            process_workflow_handler.workflow_trace_ids.clear()


__all__ = [
    "DEFAULT_MAX_BOUND_TRACES",
    "TRAJECTORY_TRACE_AGENT_HANDLER_NAME",
    "TRAJECTORY_TRACE_WORKFLOW_HANDLER_NAME",
    "TrajectoryTraceStateManager",
    "TrajectoryTraceAgentHandler",
    "TrajectoryTraceWorkflowHandler",
    "clear_process_trajectory_state",
    "ensure_otlp_handlers_registered",
]
