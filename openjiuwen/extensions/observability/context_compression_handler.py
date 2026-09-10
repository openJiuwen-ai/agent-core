# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Bridge real context-compression completion facts into trajectory v2."""

from __future__ import annotations

import threading
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Span, Tracer

from openjiuwen.core.common.logging import logger
from openjiuwen.core.context_engine.schema.context_state import ContextCompressionState
from openjiuwen.extensions.observability.semconv import (
    GEN_AI_REQUEST_ID,
    OJ_CONTEXT_OPERATION_ID,
    OJ_EXECUTION_SUBJECT_ID,
    OJ_INFERENCE_ID,
    OJ_REQUEST_ID,
    OJ_REQUEST_PURPOSE,
    OJ_SESSION_ID,
    OJ_STEP_ID,
)
from openjiuwen.extensions.observability.span_context import (
    get_current_agent_span,
    get_current_llm_span,
    get_root_span,
    queue_context_window_compaction,
)
from openjiuwen.extensions.observability.trajectory_events import emit_native_trajectory_event


class ContextCompressionObservabilityBridge:
    """Emit one immutable event for each completed compression lifecycle."""

    def __init__(self, *, tracer: Tracer) -> None:
        self._tracer = tracer
        self._model_requests_by_operation: dict[str, list[dict[str, str]]] = {}
        self._model_requests_lock = threading.Lock()

    async def on_llm_request_input(self, *args: Any, **kwargs: Any) -> None:
        """Stamp and retain the physical LLM request for one compaction operation."""
        operation_id = str(kwargs.get("context_operation_id") or "")
        if kwargs.get("request_purpose") != "compaction" or not operation_id:
            return
        span = get_current_llm_span()
        if span is None or not span.is_recording():
            return
        span.set_attribute(OJ_REQUEST_PURPOSE, "compaction")
        span.set_attribute(OJ_CONTEXT_OPERATION_ID, operation_id)
        request_ref = {
            "request_id": str(span.attributes.get(GEN_AI_REQUEST_ID) or ""),
            "inference_id": str(span.attributes.get(OJ_INFERENCE_ID) or ""),
        }
        with self._model_requests_lock:
            bucket = self._model_requests_by_operation.setdefault(operation_id, [])
            if request_ref not in bucket:
                bucket.append(request_ref)

    async def on_context_compression_state(self, *args: Any, **kwargs: Any) -> None:
        state = kwargs.get("state")
        if not isinstance(state, ContextCompressionState):
            return
        terminal = state.status in {"completed", "noop", "skipped", "failed"}
        with self._model_requests_lock:
            model_requests = (
                self._model_requests_by_operation.pop(state.operation_id, [])
                if terminal
                else list(self._model_requests_by_operation.get(state.operation_id, []))
            )
        if state.status != "completed":
            return
        try:
            parent_span = self._resolve_parent_span(str(kwargs.get("session_id") or ""))
            if parent_span is None:
                logger.debug(
                    "otel: no live parent for completed context compression operation {}",
                    state.operation_id,
                )
                return
            payload = state.model_dump(mode="json")
            payload["context_id"] = str(kwargs.get("context_id") or "")
            payload["context_session_id"] = str(kwargs.get("session_id") or "")
            payload["model_requests"] = model_requests
            event = emit_native_trajectory_event(
                tracer=self._tracer,
                parent_span=parent_span,
                event_kind="compaction.completed",
                payload=payload,
            )
            if event is not None:
                queue_context_window_compaction(
                    session_id=str(parent_span.attributes.get(OJ_SESSION_ID) or ""),
                    subject_id=str(parent_span.attributes.get(OJ_EXECUTION_SUBJECT_ID) or "main"),
                    request_id=str(parent_span.attributes.get(OJ_REQUEST_ID) or ""),
                    step_id=str(parent_span.attributes.get(OJ_STEP_ID) or ""),
                    operation_id=state.operation_id,
                )
        except Exception as exc:
            logger.warning("otel: context compression completion bridge failed - {}", exc)

    @staticmethod
    def _resolve_parent_span(session_id: str) -> Span | None:
        current = trace.get_current_span()
        if current is not None and current.is_recording():
            return current
        agent = get_current_agent_span()
        if agent is not None and agent.is_recording():
            return agent
        root = get_root_span(session_id=session_id)
        if root is not None and root.is_recording():
            return root
        return None


__all__ = ["ContextCompressionObservabilityBridge"]
