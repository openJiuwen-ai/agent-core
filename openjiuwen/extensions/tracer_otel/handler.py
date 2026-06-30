# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""OTel tracer handlers — OtelAgentHandler and OtelWorkflowHandler.

Both inherit from ``TraceExtAgentHandler`` / ``TraceExtWorkflowHandler``
(the lightweight extension base classes that do not require
StreamWriterManager).  Every method has try/except protection so OTel
failures never propagate to the business flow.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from dateutil.tz import tzlocal

from opentelemetry import context as otel_context, trace
from opentelemetry.trace import SpanKind, Status, StatusCode

from openjiuwen.core.common.exception.codes import StatusCode as OJStatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.common.logging import session_logger
from openjiuwen.core.session.tracer.data import InvokeType, NodeStatus
from openjiuwen.core.session.tracer.handler import (
    TraceExtAgentHandler,
    TraceExtWorkflowHandler,
)
from openjiuwen.core.session.tracer.span import TraceAgentSpan
from openjiuwen.extensions.tracer_otel.config import OtelTracerConfig
from openjiuwen.extensions.tracer_otel.redaction import redact
from openjiuwen.extensions.tracer_otel.semconv import (
    GEN_AI_COMPLETION,
    GEN_AI_OPERATION_NAME,
    GEN_AI_PROMPT,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_SYSTEM,
    GEN_AI_SYSTEM_VALUE,
    GEN_AI_TOOL_NAME,
    OJ_AGENT_ERROR_MESSAGE,
    OJ_AGENT_INPUTS,
    OJ_AGENT_INVOKE_TYPE,
    OJ_AGENT_NAME,
    OJ_AGENT_OUTPUTS,
    OJ_CHILD_INVOKE_IDS,
    OJ_ELAPSED_TIME,
    OJ_END_TIME,
    OJ_ERROR,
    OJ_INNER_ERROR,
    OJ_INVOKE_ID,
    OJ_META_DATA,
    OJ_PARENT_INVOKE_ID,
    OJ_PARENT_NODE_ID,
    OJ_SOURCE_IDS,
    OJ_START_TIME,
    OJ_STATUS,
    OJ_STREAM_INPUTS,
    OJ_STREAM_OUTPUTS,
    OJ_INTERACTIVE_INPUTS,
    OJ_TRACE_ID,
    OJ_WORKFLOW_COMPONENT_ID,
    OJ_WORKFLOW_COMPONENT_NAME,
    OJ_WORKFLOW_COMPONENT_TYPE,
    OJ_WORKFLOW_ERROR_MESSAGE,
    OJ_WORKFLOW_EXECUTION_ID,
    OJ_WORKFLOW_ID,
    OJ_WORKFLOW_INPUTS,
    OJ_WORKFLOW_INVOKE_DATA,
    OJ_WORKFLOW_LOOP_INDEX,
    OJ_WORKFLOW_LOOP_NODE_ID,
    OJ_WORKFLOW_NAME,
    OJ_WORKFLOW_OUTPUTS,
    OJ_WORKFLOW_VERSION,
)
from openjiuwen.extensions.tracer_otel.span_manager import (
    OtelAgentSpanManager,
    OtelSpanState,
    OtelWorkflowSpanManager,
)


def _get_parent_context(state: OtelSpanState | None) -> otel_context.Context | None:
    """Derive parent context from an existing OtelSpanState, or return None."""
    if state is None:
        return None
    return trace.set_span_in_context(state.span)


def _serialize(value: Any) -> str:
    """Serialize a value to a string suitable for OTel attributes.

    OTel ``set_attribute`` only accepts primitive types (str, int, float, bool)
    and arrays of primitives.  Complex types (dict, list-of-dict) must be
    serialized to string first.  ``json.dumps`` produces standard JSON that
    OTel backends (Jaeger, Zipkin, etc.) can parse and display, whereas
    Python's ``str()`` produces non-standard repr format.
    """
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def _normalize_llm_payload(value: Any) -> Any:
    """Recursively convert Pydantic models to plain dicts via ``model_dump()``.

    LLM inputs/outputs contain message objects (``BaseMessage`` subclasses).
    Their default ``str()`` includes class names and empty fields, producing
    non-standard repr output like ``SystemMessage(role='system', content='x', name=None, metadata={})``.
    Calling ``model_dump()`` yields clean ``{"role": "...", "content": "..."}`` dicts
    that serialize to standard JSON.  Uses duck typing so any Pydantic v2 model
    (and the custom ``AssistantMessage.model_dump``) is handled uniformly.
    """
    if hasattr(value, "model_dump"):
        return _normalize_llm_payload(value.model_dump())
    if isinstance(value, dict):
        return {str(k): _normalize_llm_payload(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize_llm_payload(v) for v in value]
    return value


# ---------------------------------------------------------------------------
# OtelAgentHandler
# ---------------------------------------------------------------------------


class OtelAgentHandler(TraceExtAgentHandler):
    """Agent-dimension OTel handler.

    Translates tracer agent events (LLM, plugin, chain, …) into OTel spans.
    LLM spans use ``SpanKind.CLIENT`` and ``gen_ai.*`` semantic conventions;
    all other types use ``SpanKind.INTERNAL`` and ``openjiuwen.agent.*``.
    """

    def __init__(self, otel_tracer: trace.Tracer, config: OtelTracerConfig, trace_id: str | None = None):
        super().__init__()
        self._otel_tracer = otel_tracer
        self._config = config
        self._trace_id = trace_id or ""
        self._span_manager = OtelAgentSpanManager()

    # --- helper: resolve parent context via parent_invoke_id ---

    def _resolve_parent_context(self, span: TraceAgentSpan) -> otel_context.Context | None:
        parent_state = self._span_manager.get(span.parent_invoke_id) if span.parent_invoke_id else None
        return _get_parent_context(parent_state)

    # --- helper: start a span, push to manager, return state ---

    def _start_and_push(
        self,
        name: str,
        kind: SpanKind,
        parent_ctx: otel_context.Context | None,
        agent_span: TraceAgentSpan,
    ) -> OtelSpanState:
        otel_span = self._otel_tracer.start_span(name=name, kind=kind, context=parent_ctx)
        # OTel standard attribute
        otel_span.set_attribute(GEN_AI_SYSTEM, GEN_AI_SYSTEM_VALUE)
        # Span base fields — use span value if present, otherwise set ourselves
        otel_span.set_attribute(OJ_TRACE_ID, agent_span.trace_id)
        otel_span.set_attribute(OJ_INVOKE_ID, agent_span.invoke_id or "")
        otel_span.set_attribute(OJ_PARENT_INVOKE_ID, agent_span.parent_invoke_id or "")
        start_time = agent_span.start_time or datetime.now(tz=tzlocal()).replace(tzinfo=None)
        otel_span.set_attribute(OJ_START_TIME, str(start_time))
        context_token = otel_context.attach(trace.set_span_in_context(otel_span))
        state = OtelSpanState(
            span=otel_span, context_token=context_token, invoke_id=agent_span.invoke_id, start_time=start_time
        )
        self._span_manager.push(agent_span.invoke_id, state)
        return state

    # --- helper: end span and pop from manager ---

    def _end_and_pop(self, invoke_id: str) -> None:
        state = self._span_manager.pop(invoke_id)
        if state is None:
            return
        state.span.set_status(Status(StatusCode.OK))
        state.span.end()
        otel_context.detach(state.context_token)

    # --- helper: set end-time attributes before closing a span ---

    def _set_end_attrs(self, otel_span: trace.Span, agent_span: TraceAgentSpan) -> None:
        """Set TraceSpan end-time fields as attributes before span closes.
        Uses span values if present, otherwise sets ourselves (matching TraceAgentHandler)."""
        end_time = agent_span.end_time or datetime.now(tz=tzlocal()).replace(tzinfo=None)
        otel_span.set_attribute(OJ_END_TIME, str(end_time))
        if agent_span.elapsed_time is not None:
            otel_span.set_attribute(OJ_ELAPSED_TIME, agent_span.elapsed_time)
        else:
            # Calculate elapsed_time from cached start_time (same as TraceBaseHandler._get_elapsed_time)
            state = self._span_manager.get(agent_span.invoke_id)
            start = agent_span.start_time or (state.start_time if state else None)
            if start is not None:
                elapsed_ms = (end_time - start).total_seconds() * 1000
                elapsed_str = f"{elapsed_ms:.0f}ms" if elapsed_ms < 1000 else f"{(elapsed_ms / 1000):.2f}s"
                otel_span.set_attribute(OJ_ELAPSED_TIME, elapsed_str)
        if agent_span.child_invokes_id is not None:
            otel_span.set_attribute(OJ_CHILD_INVOKE_IDS, _serialize(agent_span.child_invokes_id))
        otel_span.set_attribute(OJ_STATUS, agent_span.status or NodeStatus.FINISH.value)

    # --- helper: set error on span and end ---

    def _mark_error_and_end(self, invoke_id: str, error: Any) -> None:
        state = self._span_manager.pop(invoke_id)
        if state is None:
            return
        otel_span = state.span
        otel_span.set_status(Status(StatusCode.ERROR))
        otel_span.set_attribute(OJ_AGENT_ERROR_MESSAGE, str(error))
        otel_span.set_attribute(OJ_STATUS, NodeStatus.ERROR.value)
        # Complete error dict with error_code
        if isinstance(error, BaseError):
            otel_span.set_attribute(OJ_ERROR, _serialize({"error_code": error.status.code, "message": error.message}))
        else:
            otel_span.set_attribute(
                OJ_ERROR, _serialize({"error_code": OJStatusCode.WORKFLOW_EXECUTION_ERROR.code, "message": str(error)})
            )
        otel_span.record_exception(error)
        otel_span.end()
        otel_context.detach(state.context_token)

    # --- helper: set common agent attributes (non-LLM) ---

    def _set_non_llm_attrs(
        self,
        otel_span: trace.Span,
        agent_span: TraceAgentSpan,
        invoke_type: str,
        instance_info: dict | None = None,
    ) -> None:
        invoke_type_val = agent_span.invoke_type or invoke_type
        name_val = agent_span.name or (instance_info.get("class_name", "") if instance_info else "")
        otel_span.set_attribute(OJ_AGENT_INVOKE_TYPE, invoke_type_val)
        otel_span.set_attribute(OJ_AGENT_NAME, name_val)
        meta_data = agent_span.meta_data or instance_info
        if meta_data is not None:
            otel_span.set_attribute(OJ_META_DATA, _serialize(meta_data))

    # ================================================================
    # LLM events — SpanKind.CLIENT, gen_ai.* attributes
    # ================================================================

    async def on_llm_start(self, span: TraceAgentSpan, inputs: Any, instance_info: dict, **kwargs):
        try:
            parent_ctx = self._resolve_parent_context(span)
            state = self._start_and_push(
                name=f"llm.{instance_info.get('class_name', 'unknown')}",
                kind=SpanKind.CLIENT,
                parent_ctx=parent_ctx,
                agent_span=span,
            )
            # LLM-specific OTel attributes
            state.span.set_attribute(GEN_AI_REQUEST_MODEL, instance_info.get("class_name", ""))
            state.span.set_attribute(GEN_AI_OPERATION_NAME, "chat")
            # Agent base fields (use span values if present, otherwise set ourselves)
            invoke_type_val = span.invoke_type or InvokeType.LLM.value
            name_val = span.name or instance_info.get("class_name", "")
            state.span.set_attribute(OJ_AGENT_INVOKE_TYPE, invoke_type_val)
            state.span.set_attribute(OJ_AGENT_NAME, name_val)
            meta_data = span.meta_data or instance_info
            state.span.set_attribute(OJ_META_DATA, _serialize(meta_data))
            if inputs is not None:
                # Normalize message objects to plain dicts before serialization,
                # so GEN_AI_PROMPT carries standard JSON instead of class repr.
                payload = _serialize(_normalize_llm_payload(inputs))
                state.span.set_attribute(GEN_AI_PROMPT, redact(payload, self._config, field="prompts"))
        except Exception as exc:
            session_logger.warning("otel agent handler: on_llm_start failed: %s", exc)

    async def on_llm_request(self, span: TraceAgentSpan, **kwargs):
        try:
            state = self._span_manager.get(span.invoke_id)
            if state is None:
                return
            state.span.add_event("llm.request", attributes=kwargs)
        except Exception as exc:
            session_logger.warning("otel agent handler: on_llm_request failed: %s", exc)

    async def on_llm_end(self, span: TraceAgentSpan, outputs, **kwargs):
        try:
            state = self._span_manager.get(span.invoke_id)
            if state is None:
                return
            if outputs is not None:
                # Normalize message objects (e.g. AssistantMessage) to plain dicts.
                payload = _serialize(_normalize_llm_payload(outputs))
                state.span.set_attribute(GEN_AI_COMPLETION, redact(payload, self._config, field="completions"))
            self._set_end_attrs(state.span, span)
            self._end_and_pop(span.invoke_id)
        except Exception as exc:
            session_logger.warning("otel agent handler: on_llm_end failed: %s", exc)

    async def on_llm_error(self, span: TraceAgentSpan, error, **kwargs):
        try:
            self._mark_error_and_end(span.invoke_id, error)
        except Exception as exc:
            session_logger.warning("otel agent handler: on_llm_error failed: %s", exc)

    # --- helper: start a non-LLM span, set attrs, push to manager ---
    #     extra_attrs: dict of additional OTel attributes specific to the event
    #     type (e.g. plugin sets GEN_AI_OPERATION_NAME / GEN_AI_TOOL_NAME).

    def _start_non_llm_span(
        self,
        agent_span: TraceAgentSpan,
        inputs: Any,
        instance_info: dict,
        invoke_type: str,
        span_name_prefix: str,
        *,
        extra_attrs: dict[str, str] | None = None,
    ) -> None:
        parent_ctx = self._resolve_parent_context(agent_span)
        state = self._start_and_push(
            name=f"{span_name_prefix}.{instance_info.get('class_name', 'unknown')}",
            kind=SpanKind.INTERNAL,
            parent_ctx=parent_ctx,
            agent_span=agent_span,
        )
        self._set_non_llm_attrs(state.span, agent_span, invoke_type, instance_info)
        if extra_attrs:
            for key, value in extra_attrs.items():
                state.span.set_attribute(key, value)
        if inputs is not None:
            state.span.set_attribute(OJ_AGENT_INPUTS, redact(inputs, self._config))

    # --- helper: end a non-LLM span, set end attrs, pop from manager ---

    def _end_non_llm_span(self, span: TraceAgentSpan, outputs: Any) -> None:
        state = self._span_manager.get(span.invoke_id)
        if state is None:
            return
        if outputs is not None:
            state.span.set_attribute(OJ_AGENT_OUTPUTS, redact(outputs, self._config))
        self._set_end_attrs(state.span, span)
        self._end_and_pop(span.invoke_id)

    # --- helper: mark error on a non-LLM span and end ---

    def _error_non_llm_span(self, span: TraceAgentSpan, error: Any) -> None:
        self._mark_error_and_end(span.invoke_id, error)

    # ================================================================
    # Plugin (Tool) events — SpanKind.INTERNAL
    # ================================================================

    async def on_plugin_start(self, span: TraceAgentSpan, inputs: Any, instance_info: dict, **kwargs):
        try:
            self._start_non_llm_span(
                span, inputs, instance_info, InvokeType.PLUGIN.value, "tool",
                extra_attrs={GEN_AI_OPERATION_NAME: "execute_tool",
                             GEN_AI_TOOL_NAME: instance_info.get("class_name", "")},
            )
        except Exception as exc:
            session_logger.warning("otel agent handler: on_plugin_start failed: %s", exc)

    async def on_plugin_end(self, span: TraceAgentSpan, outputs, **kwargs):
        try:
            self._end_non_llm_span(span, outputs)
        except Exception as exc:
            session_logger.warning("otel agent handler: on_plugin_end failed: %s", exc)

    async def on_plugin_error(self, span: TraceAgentSpan, error, **kwargs):
        try:
            self._error_non_llm_span(span, error)
        except Exception as exc:
            session_logger.warning("otel agent handler: on_plugin_error failed: %s", exc)

    # ================================================================
    # Prompt events — SpanKind.INTERNAL
    # ================================================================

    async def on_prompt_start(self, span: TraceAgentSpan, inputs: Any, instance_info: dict, **kwargs):
        try:
            self._start_non_llm_span(span, inputs, instance_info, InvokeType.PROMPT.value, "prompt")
        except Exception as exc:
            session_logger.warning("otel agent handler: on_prompt_start failed: %s", exc)

    async def on_prompt_end(self, span: TraceAgentSpan, outputs, **kwargs):
        try:
            self._end_non_llm_span(span, outputs)
        except Exception as exc:
            session_logger.warning("otel agent handler: on_prompt_end failed: %s", exc)

    async def on_prompt_error(self, span: TraceAgentSpan, error, **kwargs):
        try:
            self._error_non_llm_span(span, error)
        except Exception as exc:
            session_logger.warning("otel agent handler: on_prompt_error failed: %s", exc)

    # ================================================================
    # Chain events — SpanKind.INTERNAL
    # ================================================================

    async def on_chain_start(self, span: TraceAgentSpan, inputs: Any, instance_info: dict, **kwargs):
        try:
            self._start_non_llm_span(span, inputs, instance_info, InvokeType.CHAIN.value, "chain")
        except Exception as exc:
            session_logger.warning("otel agent handler: on_chain_start failed: %s", exc)

    async def on_chain_end(self, span: TraceAgentSpan, outputs, **kwargs):
        try:
            self._end_non_llm_span(span, outputs)
        except Exception as exc:
            session_logger.warning("otel agent handler: on_chain_end failed: %s", exc)

    async def on_chain_error(self, span: TraceAgentSpan, error, **kwargs):
        try:
            self._error_non_llm_span(span, error)
        except Exception as exc:
            session_logger.warning("otel agent handler: on_chain_error failed: %s", exc)

    # ================================================================
    # Retriever events — SpanKind.INTERNAL
    # ================================================================

    async def on_retriever_start(self, span: TraceAgentSpan, inputs: Any, instance_info: dict, **kwargs):
        try:
            self._start_non_llm_span(span, inputs, instance_info, InvokeType.RETRIEVER.value, "retriever")
        except Exception as exc:
            session_logger.warning("otel agent handler: on_retriever_start failed: %s", exc)

    async def on_retriever_end(self, span: TraceAgentSpan, outputs, **kwargs):
        try:
            self._end_non_llm_span(span, outputs)
        except Exception as exc:
            session_logger.warning("otel agent handler: on_retriever_end failed: %s", exc)

    async def on_retriever_error(self, span: TraceAgentSpan, error, **kwargs):
        try:
            self._error_non_llm_span(span, error)
        except Exception as exc:
            session_logger.warning("otel agent handler: on_retriever_error failed: %s", exc)

    # ================================================================
    # Evaluator events — SpanKind.INTERNAL
    # ================================================================

    async def on_evaluator_start(self, span: TraceAgentSpan, inputs: Any, instance_info: dict, **kwargs):
        try:
            self._start_non_llm_span(span, inputs, instance_info, InvokeType.EVALUATOR.value, "evaluator")
        except Exception as exc:
            session_logger.warning("otel agent handler: on_evaluator_start failed: %s", exc)

    async def on_evaluator_end(self, span: TraceAgentSpan, outputs, **kwargs):
        try:
            self._end_non_llm_span(span, outputs)
        except Exception as exc:
            session_logger.warning("otel agent handler: on_evaluator_end failed: %s", exc)

    async def on_evaluator_error(self, span: TraceAgentSpan, error, **kwargs):
        try:
            self._error_non_llm_span(span, error)
        except Exception as exc:
            session_logger.warning("otel agent handler: on_evaluator_error failed: %s", exc)

    # ================================================================
    # Workflow events (agent-level) — SpanKind.INTERNAL
    # ================================================================

    async def on_workflow_start(self, span: TraceAgentSpan, inputs: Any, instance_info: dict, **kwargs):
        try:
            self._start_non_llm_span(span, inputs, instance_info, InvokeType.WORKFLOW.value, "workflow")
        except Exception as exc:
            session_logger.warning("otel agent handler: on_workflow_start failed: %s", exc)

    async def on_workflow_end(self, span: TraceAgentSpan, outputs, **kwargs):
        try:
            self._end_non_llm_span(span, outputs)
        except Exception as exc:
            session_logger.warning("otel agent handler: on_workflow_end failed: %s", exc)

    async def on_workflow_error(self, span: TraceAgentSpan, error, **kwargs):
        try:
            self._error_non_llm_span(span, error)
        except Exception as exc:
            session_logger.warning("otel agent handler: on_workflow_error failed: %s", exc)


# ---------------------------------------------------------------------------
# OtelWorkflowHandler
# ---------------------------------------------------------------------------


class OtelWorkflowHandler(TraceExtWorkflowHandler):
    """Workflow-dimension OTel handler.

    Translates tracer workflow events into OTel spans.  Maintains three
    internal mappings for building a hierarchical span tree:

    - ``_span_manager`` (invoke_id → OtelSpanState): lifecycle map
    - ``_layer_root_spans`` (node_id → root OtelSpanState): each
      workflow layer's root span
    - ``_component_spans`` (node_id → component OtelSpanState): host
      component spans that serve as parents for sub-workflow roots
    """

    def __init__(self, otel_tracer: trace.Tracer, config: OtelTracerConfig, trace_id: str | None = None):
        super().__init__()
        self._otel_tracer = otel_tracer
        self._config = config
        self._trace_id = trace_id or ""
        self._span_manager = OtelWorkflowSpanManager()
        # Mapping 2: parent_node_id → root OtelSpanState for the layer
        self._layer_root_spans: dict[str, OtelSpanState] = {}
        # Mapping 3: node_id → host-component OtelSpanState
        self._component_spans: dict[str, OtelSpanState] = {}

    # --- helper: resolve parent context for a new span ---

    def _resolve_parent_context(
        self,
        parent_node_id: str,
        metadata: dict | None,
    ) -> otel_context.Context | None:
        is_workflow_root = metadata and "workflow_id" in metadata and "component_id" not in metadata

        if parent_node_id == "" and is_workflow_root:
            # Root workflow root — no parent (top-level)
            return None
        if parent_node_id == "" and not is_workflow_root:
            # Component in root workflow → parent = root workflow root
            return _get_parent_context(self._layer_root_spans.get(""))
        if parent_node_id != "" and is_workflow_root:
            # Sub-workflow root → parent = host component span
            return _get_parent_context(self._component_spans.get(parent_node_id))
        # Component in sub-workflow → parent = host component span
        return _get_parent_context(self._component_spans.get(parent_node_id))

    # --- helper: set workflow / component attributes on an OTel span ---

    def _set_workflow_attrs(self, otel_span: trace.Span, metadata: dict | None, invoke_id: str) -> None:
        if metadata is None:
            return
        is_workflow_root = "workflow_id" in metadata and "component_id" not in metadata

        if is_workflow_root:
            otel_span.set_attribute(OJ_WORKFLOW_ID, metadata.get("workflow_id", ""))
            otel_span.set_attribute(OJ_WORKFLOW_NAME, metadata.get("workflow_name", ""))
            otel_span.set_attribute(OJ_WORKFLOW_VERSION, metadata.get("workflow_version", ""))
            otel_span.set_attribute(OJ_WORKFLOW_EXECUTION_ID, metadata.get("workflow_id", invoke_id))
        else:
            otel_span.set_attribute(OJ_WORKFLOW_COMPONENT_ID, metadata.get("component_id", ""))
            otel_span.set_attribute(OJ_WORKFLOW_COMPONENT_TYPE, metadata.get("component_type", ""))
            otel_span.set_attribute(OJ_WORKFLOW_COMPONENT_NAME, metadata.get("component_name", ""))
            otel_span.set_attribute(OJ_WORKFLOW_ID, metadata.get("workflow_id", ""))
            if "loop_node_id" in metadata:
                otel_span.set_attribute(OJ_WORKFLOW_LOOP_NODE_ID, metadata["loop_node_id"])
            if "loop_index" in metadata:
                otel_span.set_attribute(OJ_WORKFLOW_LOOP_INDEX, str(metadata["loop_index"]))

    # --- helper: flush buffered data as span events before end ---

    def _flush_buffered_data(self, invoke_id: str) -> None:
        state = self._span_manager.get(invoke_id)
        if state is None:
            return

        on_invoke_data = self._span_manager.get_on_invoke_data(invoke_id)
        if on_invoke_data:
            state.span.set_attribute(OJ_WORKFLOW_INVOKE_DATA, _serialize(on_invoke_data))

        stream_inputs = self._span_manager.get_stream_inputs(invoke_id)
        if stream_inputs:
            state.span.set_attribute(OJ_STREAM_INPUTS, _serialize(stream_inputs))

        stream_outputs = self._span_manager.get_stream_outputs(invoke_id)
        if stream_outputs:
            state.span.set_attribute(OJ_STREAM_OUTPUTS, _serialize(stream_outputs))

    # --- helper: set end-time attributes before closing a workflow span ---

    def _set_workflow_end_attrs(self, state: OtelSpanState) -> None:
        """Set end_time / elapsed_time before closing a workflow span.

        Mirrors the Agent handler's ``_set_end_attrs`` for consistency.
        Uses the cached ``start_time`` on ``OtelSpanState`` (set in ``on_call_start``).
        """
        if state.start_time is None:
            return
        end_time = datetime.now(tz=tzlocal()).replace(tzinfo=None)
        state.span.set_attribute(OJ_END_TIME, str(end_time))
        elapsed_ms = (end_time - state.start_time).total_seconds() * 1000
        elapsed_str = f"{elapsed_ms:.0f}ms" if elapsed_ms < 1000 else f"{(elapsed_ms / 1000):.2f}s"
        state.span.set_attribute(OJ_ELAPSED_TIME, elapsed_str)

    # ================================================================
    # Lifecycle events
    # ================================================================

    async def on_call_start(
        self,
        invoke_id: str,
        metadata: dict = None,
        inputs: Any = None,
        need_send: bool = False,
        source_ids: list = None,
        **kwargs,
    ):
        try:
            parent_node_id = kwargs.get("parent_node_id", "")
            parent_ctx = self._resolve_parent_context(parent_node_id, metadata)

            if metadata is None:
                metadata = {}
            is_workflow_root = "workflow_id" in metadata and "component_id" not in metadata
            component_type = metadata.get("component_type", "")
            span_kind = SpanKind.CLIENT if "LLM" in component_type else SpanKind.INTERNAL

            if is_workflow_root:
                span_name = invoke_id
            else:
                span_name = f"component.{invoke_id}"

            otel_span = self._otel_tracer.start_span(
                name=span_name,
                kind=span_kind,
                context=parent_ctx,
            )
            # OTel standard + base attributes
            start_time = datetime.now(tz=tzlocal()).replace(tzinfo=None)
            otel_span.set_attribute(GEN_AI_SYSTEM, GEN_AI_SYSTEM_VALUE)
            otel_span.set_attribute(OJ_TRACE_ID, self._trace_id)
            otel_span.set_attribute(OJ_INVOKE_ID, invoke_id)
            otel_span.set_attribute(OJ_PARENT_NODE_ID, parent_node_id)
            otel_span.set_attribute(OJ_START_TIME, str(start_time))
            if source_ids is not None:
                otel_span.set_attribute(OJ_SOURCE_IDS, _serialize(source_ids))
            # LLM component: gen_ai attributes
            if "LLM" in component_type:
                otel_span.set_attribute(GEN_AI_OPERATION_NAME, "chat")

            self._set_workflow_attrs(otel_span, metadata, invoke_id)

            if inputs is not None:
                otel_span.set_attribute(OJ_WORKFLOW_INPUTS, redact(inputs, self._config))

            state = OtelSpanState(
                span=otel_span, context_token=None, invoke_id=invoke_id, start_time=start_time,
            )
            self._span_manager.push(invoke_id, state)

            # Register in layer / component mappings
            if is_workflow_root:
                self._layer_root_spans[parent_node_id] = state
                # Also register this workflow root as the parent for its children
                # Children's parent_node_id will be "" for root workflow,
                # and the node_id of the host component for sub-workflows.
                self._layer_root_spans[invoke_id] = state
            else:
                component_id = metadata.get("component_id", "")
                if component_id:
                    self._component_spans[component_id] = state
        except Exception as exc:
            session_logger.warning("otel workflow handler: on_call_start failed: %s", exc)

    async def on_call_done(self, invoke_id: str, outputs: Any = None, **kwargs):
        try:
            self._flush_buffered_data(invoke_id)

            state = self._span_manager.pop(invoke_id)
            if state is None:
                return

            if outputs is not None:
                state.span.set_attribute(OJ_WORKFLOW_OUTPUTS, redact(outputs, self._config))

            self._set_workflow_end_attrs(state)
            state.span.set_attribute(OJ_STATUS, NodeStatus.FINISH.value)
            state.span.set_status(Status(StatusCode.OK))
            state.span.end()

            # Clean up layer / component mappings
            # Remove from _layer_root_spans if this invoke_id is a root
            # Remove from _component_spans if this invoke_id was registered as a component
            for key, val in list(self._layer_root_spans.items()):
                if val.invoke_id == invoke_id:
                    self._layer_root_spans.pop(key, None)
            for key, val in list(self._component_spans.items()):
                if val.invoke_id == invoke_id:
                    self._component_spans.pop(key, None)
        except Exception as exc:
            session_logger.warning("otel workflow handler: on_call_done failed: %s", exc)

    # ================================================================
    # Input/output events
    # ================================================================

    async def on_pre_invoke(
        self,
        invoke_id: str,
        inputs: Any,
        component_metadata: dict,
        need_send: bool = False,
        **kwargs,
    ):
        try:
            state = self._span_manager.get(invoke_id)
            if state is None:
                return
            if inputs is not None:
                state.span.set_attribute(OJ_WORKFLOW_INPUTS, redact(inputs, self._config))
            self._set_workflow_attrs(state.span, component_metadata, invoke_id)
        except Exception as exc:
            session_logger.warning("otel workflow handler: on_pre_invoke failed: %s", exc)

    async def on_pre_stream(self, invoke_id: str, chunk, need_send: bool = False, **kwargs):
        try:
            if isinstance(chunk, dict):
                self._span_manager.append_stream_input(invoke_id, chunk)
        except Exception as exc:
            session_logger.warning("otel workflow handler: on_pre_stream failed: %s", exc)

    async def on_invoke(
        self,
        invoke_id: str,
        on_invoke_data: dict = None,
        exception: Exception = None,
        **kwargs,
    ):
        try:
            if exception is not None:
                # Mark span as ERROR, flush buffer, end span
                state = self._span_manager.get(invoke_id)
                if state is not None:
                    state.span.set_status(Status(StatusCode.ERROR))
                    state.span.set_attribute(OJ_WORKFLOW_ERROR_MESSAGE, str(exception))
                    state.span.set_attribute(OJ_STATUS, NodeStatus.ERROR.value)
                    # Complete error dict
                    if isinstance(exception, BaseError):
                        state.span.set_attribute(
                            OJ_ERROR, _serialize({"error_code": exception.status.code, "message": exception.message})
                        )
                    else:
                        state.span.set_attribute(OJ_ERROR, _serialize({"message": str(exception)}))
                    # inner_error from on_invoke_data
                    if on_invoke_data and isinstance(on_invoke_data, dict) and "inner_error" in on_invoke_data:
                        state.span.set_attribute(OJ_INNER_ERROR, _serialize(on_invoke_data["inner_error"]))
                    state.span.record_exception(exception)

                self._flush_buffered_data(invoke_id)

                pop_state = self._span_manager.pop(invoke_id)
                if pop_state is not None:
                    self._set_workflow_end_attrs(pop_state)
                    pop_state.span.end()
                    for key, val in list(self._layer_root_spans.items()):
                        if val.invoke_id == invoke_id:
                            self._layer_root_spans.pop(key, None)
                    for key, val in list(self._component_spans.items()):
                        if val.invoke_id == invoke_id:
                            self._component_spans.pop(key, None)
                return

            # Non-exception: buffer on_invoke_data
            if on_invoke_data is not None:
                self._span_manager.append_on_invoke_data(invoke_id, on_invoke_data)
        except Exception as exc:
            session_logger.warning("otel workflow handler: on_invoke failed: %s", exc)

    async def on_post_invoke(self, invoke_id: str, outputs, inputs=None, **kwargs):
        try:
            state = self._span_manager.get(invoke_id)
            if state is None:
                return
            if outputs is not None:
                state.span.set_attribute(OJ_WORKFLOW_OUTPUTS, redact(outputs, self._config))
        except Exception as exc:
            session_logger.warning("otel workflow handler: on_post_invoke failed: %s", exc)

    async def on_post_stream(self, invoke_id: str, chunk, **kwargs):
        try:
            if isinstance(chunk, dict):
                self._span_manager.append_stream_output(invoke_id, chunk)
        except Exception as exc:
            session_logger.warning("otel workflow handler: on_post_stream failed: %s", exc)

    # ================================================================
    # Interactive events
    # ================================================================

    async def on_interact(
        self,
        invoke_id: str,
        inputs: Any,
        component_metadata: dict,
        need_send: bool = False,
        **kwargs,
    ):
        try:
            state = self._span_manager.get(invoke_id)
            if state is None:
                return
            if inputs is not None:
                state.span.set_attribute(OJ_INTERACTIVE_INPUTS, redact(inputs, self._config))
        except Exception as exc:
            session_logger.warning("otel workflow handler: on_interact failed: %s", exc)
