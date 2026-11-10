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

# P0 perf: cache tzlocal() once at module level to avoid repeated
# Windows registry reads on every datetime.now() call.
_CACHED_TZLOCAL = tzlocal()

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import NonRecordingSpan, SpanContext, SpanKind, Status, StatusCode, TraceFlags

from openjiuwen.core.common.exception.codes import StatusCode as OJStatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.common.logging import session_logger
from openjiuwen.core.graph.pregel import GraphInterrupt
from openjiuwen.core.session.tracer.data import InvokeType, NodeStatus
from openjiuwen.core.session.tracer.handler import (
    TraceExtAgentHandler,
    TraceExtWorkflowHandler,
)
from openjiuwen.core.session.tracer.span import TraceAgentSpan
from openjiuwen.extensions.tracer_otel.config import OtelTracerConfig
from openjiuwen.extensions.tracer_otel.redaction import redact
from openjiuwen.extensions.tracer_otel.semconv import (
    ERROR_TYPE,
    GEN_AI_AGENT_DESCRIPTION,
    GEN_AI_AGENT_ID,
    GEN_AI_AGENT_NAME,
    GEN_AI_CONVERSATION_ID,
    GEN_AI_DATA_SOURCE_ID,
    GEN_AI_EMBEDDINGS_DIMENSION_COUNT,
    GEN_AI_INPUT_MESSAGES,
    GEN_AI_MEMORY_RECORD_COUNT,
    GEN_AI_OPERATION_NAME,
    GEN_AI_OUTPUT_MESSAGES,
    GEN_AI_PROVIDER_NAME,
    GEN_AI_REQUEST_MAX_TOKENS,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_REQUEST_REASONING_LEVEL,
    GEN_AI_REQUEST_STOP_SEQUENCES,
    GEN_AI_REQUEST_TEMPERATURE,
    GEN_AI_REQUEST_TOP_K,
    GEN_AI_REQUEST_TOP_P,
    GEN_AI_RESPONSE_FINISH_REASONS,
    GEN_AI_RESPONSE_MODEL,
    GEN_AI_RESPONSE_TIME_TO_FIRST_CHUNK,
    GEN_AI_RETRIEVAL_DOCUMENTS,
    GEN_AI_RETRIEVAL_QUERY_TEXT,
    GEN_AI_RETRIEVAL_TOP_K,
    GEN_AI_SYSTEM,
    GEN_AI_SYSTEM_VALUE,
    GEN_AI_TOOL_CALL_ID,
    GEN_AI_TOOL_NAME,
    GEN_AI_TOOL_TYPE,
    GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS,
    GEN_AI_USAGE_INPUT_TOKENS,
    GEN_AI_USAGE_OUTPUT_TOKENS,
    GEN_AI_USAGE_REASONING_OUTPUT_TOKENS,
    OJ_AGENT_ERROR_MESSAGE,
    OJ_AGENT_INPUTS,
    OJ_AGENT_INVOKE_TYPE,
    OJ_AGENT_NAME,
    OJ_AGENT_OUTPUTS,
    OJ_CHILD_INVOKE_IDS,
    OJ_ELAPSED_TIME,
    OJ_END_TIME,
    OJ_ERROR,
    OJ_GEN_AI_METADATA,
    OJ_GEN_AI_REASONING_DURATION_MS,
    OJ_GEN_AI_RESPONSE_TPOT_MS,
    OJ_GEN_AI_TRACE_NAME,
    OJ_GEN_AI_USER_ID,
    OJ_INNER_ERROR,
    OJ_INTERACTIVE_INPUTS,
    OJ_INVOKE_ID,
    OJ_LLM_PREV_MESSAGE_COUNT,
    OJ_PARENT_INVOKE_ID,
    OJ_PARENT_NODE_ID,
    OJ_SESSION_ID,
    OJ_SOURCE_IDS,
    OJ_START_TIME,
    OJ_STATUS,
    OJ_STREAM_INPUTS,
    OJ_STREAM_OUTPUTS,
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


def _explicit_parent_context(instance_info: dict | None) -> otel_context.Context | None:
    """Build a parent context from explicit embedded-side span refs, or None.

    嵌入侧（studio 双写桥）llm span 的逻辑父不在本 handler 的 span manager
    表中，环境回退又会被链路中的上下文污染破坏（部署实证：llm span 与
    node span 并列、误挂死根）。identity 通道显式携带 node span 的
    (parent_trace_id, parent_span_id[, parent_trace_flags])，此处构造
    NonRecordingSpan 父上下文。refs 缺失或非法（非 hex / 全零）返回 None，
    调用方走既有 逻辑父→环境 解析，行为不变。flags 缺省按已采样处理：
    显式 refs 只来自活着的 recording span，宁可多采不丢数据。
    """
    info = instance_info or {}
    trace_hex = str(info.get("parent_trace_id") or "")
    span_hex = str(info.get("parent_span_id") or "")
    try:
        trace_id = int(trace_hex, 16)
        span_id = int(span_hex, 16)
    except ValueError:
        return None
    if not (0 < span_id < 2**64 and 0 < trace_id < 2**128):
        return None
    try:
        flags = TraceFlags(int(str(info.get("parent_trace_flags") or "1"), 16) & TraceFlags.SAMPLED)
    except ValueError:
        flags = TraceFlags(TraceFlags.SAMPLED)
    ctx = SpanContext(trace_id=trace_id, span_id=span_id, is_remote=True, trace_flags=flags)
    return trace.set_span_in_context(NonRecordingSpan(ctx))



# Workflow component_type (set by TracerWorkflowUtils._get_component_metadata as
# type(executor).__name__) substring matches for LLM-related components:
#   - "LLM"             covers LLMComponent and any future LLM-named variants
#   - "IntentDetection" covers IntentDetectionComponent (compound, internally invokes LLM)
#   - "Questioner"      covers QuestionerComponent (compound, internally invokes LLM)
# Substring match avoids needing a registry update when new LLM-named components are added.
_LLM_SUBSTRINGS: tuple[str, ...] = ("LLM", "IntentDetection", "Questioner")

# Component types that represent tool execution — tagged with
# gen_ai.operation.name="execute_tool" per OTel GenAI semantic conventions.
# Substring match on "Tool" covers ToolExecutable and any future Tool-named variants.
_TOOL_SUBSTRINGS: tuple[str, ...] = ("Tool",)

# Component types that represent retrieval (knowledge/memory retrieval) —
# tagged with gen_ai.operation.name="retrieval" and named "retrieval <name>"
# per OTel GenAI semantic conventions.  Substring match on "Retrieval" covers
# KnowledgeRetrievalExecutable, MemoryRetrievalExecutable and future variants.
_RETRIEVAL_SUBSTRINGS: tuple[str, ...] = ("Retrieval",)

# gen_ai.operation.name values (OTel GenAI semantic conventions) — constants so
# no call site re-types the literal.
_OPERATION_NAME_CHAT = "chat"
_OPERATION_NAME_EXECUTE_TOOL = "execute_tool"
_OPERATION_NAME_EMBEDDINGS = "embeddings"
_OPERATION_NAME_RETRIEVAL = "retrieval"


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


def _extract_retrieval_query(inputs: Any) -> str | None:
    """Pull the query text out of a retrieval component's inputs."""
    if isinstance(inputs, str):
        return inputs or None
    if isinstance(inputs, dict):
        query = inputs.get("query")
        if isinstance(query, str) and query:
            return query
    return None


def _extract_retrieval_documents(outputs: Any) -> list[str] | None:
    """Pull the retrieved documents out of a retrieval component's outputs."""
    results = None
    if isinstance(outputs, dict):
        results = outputs.get("results")
    elif hasattr(outputs, "results"):
        results = outputs.results
    if isinstance(results, list) and results:
        return [str(item) for item in results]
    return None


def _is_retrieval_component_span(otel_span: trace.Span) -> bool:
    """Whether an open workflow component span belongs to a retrieval component."""
    component_type = str((otel_span.attributes or {}).get(OJ_WORKFLOW_COMPONENT_TYPE, ""))
    return any(s in component_type for s in _RETRIEVAL_SUBSTRINGS)


def _unwrap_llm_payload(value: Any) -> Any:
    """Unwrap single-key containers (``{"messages": ...}`` / ``{"outputs": ...}``)
    that OtelRail wraps around LLM inputs/outputs payloads."""
    normalized = _normalize_llm_payload(value)
    if isinstance(normalized, dict):
        for container_key in ("messages", "outputs", "inputs"):
            inner = normalized.get(container_key)
            if inner is not None:
                return inner
    return normalized


def _set_llm_response_attrs(otel_span: trace.Span, outputs: Any) -> None:
    """Set GenAI response / usage / cost / latency attributes on the span.

    Reads ``finish_reason`` and ``usage_metadata`` (input/output tokens, cache
    read tokens, reasoning tokens, response model, per-kind costs,
    first-token timing) off an ``AssistantMessage`` payload, normalized via
    ``model_dump`` semantics; decorator-measured streams instead carry usage
    as a container-level ``usage_metadata`` sibling of ``outputs`` (see the
    note at the unwrap call below). Present values are set unconditionally —
    a recorded 0 is a fact, not an absence.
    """
    obj = _unwrap_llm_payload(outputs)
    # Decorator-measured streams attach usage as a container-level sibling of
    # "outputs": unwrap drills into the chunk list, which cannot carry usage
    # itself, so the sibling is read before the unwrapped-object fallback
    # (rail payloads embed usage on the message and keep that path). A chunk
    # list also skips the finish_reason block instead of aborting the read.
    sibling = outputs.get("usage_metadata") if isinstance(outputs, dict) else None
    usage = sibling if isinstance(sibling, dict) else None
    if usage is None:
        usage = obj.get("usage_metadata") if isinstance(obj, dict) else None
    if isinstance(obj, dict):
        finish_reason = obj.get("finish_reason")
        # "null" is the schema default for AssistantMessage.finish_reason, i.e.
        # "provider did not report one" — not a zero value, so it stays filtered.
        if finish_reason and str(finish_reason) != "null":
            otel_span.set_attribute(GEN_AI_RESPONSE_FINISH_REASONS, [str(finish_reason)])
    if not isinstance(usage, dict):
        return
    otel_span.set_attribute(GEN_AI_RESPONSE_MODEL, str(usage.get("model_name") or ""))
    otel_span.set_attribute(GEN_AI_USAGE_INPUT_TOKENS, int(usage.get("input_tokens") or 0))
    otel_span.set_attribute(GEN_AI_USAGE_OUTPUT_TOKENS, int(usage.get("output_tokens") or 0))
    # 0.1.17 UsageMetadata carries cache_tokens only (no read/write split);
    # the split keys stay schema-transparent for raw-dict callers.
    otel_span.set_attribute(GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS, int(usage.get("cache_tokens") or 0))
    otel_span.set_attribute(GEN_AI_USAGE_REASONING_OUTPUT_TOKENS, int(usage.get("reasoning_tokens") or 0))
    # Streaming / reasoning latency. TTFT comes from the semantically explicit
    # `ttft_seconds` field (decorator-measured streams stamp it on the usage
    # snapshot); `first_token_time` is kept as a **legacy fallback** only — by
    # contract it is a "first token instant" (timestamp string), so a
    # non-numeric value simply skips the attribute. The *_ms keys are
    # schema-transparent: raw-dict callers and future UsageMetadata fields
    # flow through unchanged. All parses are guarded — a malformed value skips
    # its attribute; it must never raise and abort the span finalization that
    # follows in on_llm_end.
    ttft_seconds = usage.get("ttft_seconds")
    if ttft_seconds is None:
        ttft_seconds = usage.get("first_token_time") or ""
    if ttft_seconds != "":
        try:
            otel_span.set_attribute(GEN_AI_RESPONSE_TIME_TO_FIRST_CHUNK, float(ttft_seconds))
        except (TypeError, ValueError):
            pass
    # TPOT payload keys: tpot_ms is canonical; inter_token_latency_ms is the
    # legacy producer key (agent-studio usage payload) — still accepted so
    # every studio release keeps flowing, written once under the single key.
    inter_token = usage.get("tpot_ms")
    if inter_token is None:
        inter_token = usage.get("inter_token_latency_ms")
    if inter_token is not None:
        try:
            otel_span.set_attribute(OJ_GEN_AI_RESPONSE_TPOT_MS, float(inter_token))
        except (TypeError, ValueError):
            pass
    reasoning_duration = usage.get("reasoning_duration_ms")
    if reasoning_duration is not None:
        try:
            otel_span.set_attribute(OJ_GEN_AI_REASONING_DURATION_MS, float(reasoning_duration))
        except (TypeError, ValueError):
            pass


# ---------------------------------------------------------------------------
# OtelAgentHandler
# ---------------------------------------------------------------------------


class OtelAgentHandler(TraceExtAgentHandler):
    """Agent-dimension OTel handler.

    Translates tracer agent events (LLM, plugin, chain, …) into OTel spans.
    LLM spans use ``SpanKind.CLIENT`` and ``gen_ai.*`` semantic conventions;
    all other types use ``SpanKind.INTERNAL`` and ``openjiuwen.agent.*``.
    """

    # Capability probe for embedding applications (e.g. agent-studio's
    # trace-compat gate): this build consumes decorator-measured llm spans —
    # a container-level "usage_metadata" sibling on the end payload and
    # workflow_id/workflow_name identity keys — as pure additions, without
    # any span-merge logic. Bump when the consumed shape changes.
    LLM_SPAN_DECORATOR_CAPABILITY = 1

    def __init__(self, otel_tracer: trace.Tracer, config: OtelTracerConfig, trace_id: str | None = None):
        super().__init__()
        self._otel_tracer = otel_tracer
        self._config = config
        self._trace_id = trace_id or ""
        self._span_manager = OtelAgentSpanManager()
        # Bound later by the tracer registry via set_session_id(); declared
        # here so _start_and_push never depends on the base-class attribute.
        self._session_id: str | None = None

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
        start_time = agent_span.start_time or datetime.now(tz=_CACHED_TZLOCAL).replace(tzinfo=None)
        # The SDK start_span expects int nanoseconds; the datetime form is
        # kept for OJ_START_TIME / elapsed-time math. Passing the true start
        # (same idiom as the workflow/component span site) keeps the span's
        # own timestamps on the business clock instead of the event-processing
        # wall clock, so the duration is real even when events flush together.
        otel_span = self._otel_tracer.start_span(
            name=name,
            kind=kind,
            context=parent_ctx,
            start_time=int(start_time.timestamp() * 1e9),
        )
        # OTel standard attributes (gen_ai.system is the legacy pre-registry
        # key kept as frozen wire format; provider.name is the standard one)
        otel_span.set_attribute(GEN_AI_SYSTEM, GEN_AI_SYSTEM_VALUE)
        otel_span.set_attribute(GEN_AI_PROVIDER_NAME, GEN_AI_SYSTEM_VALUE)
        # Span base fields — use span value if present, otherwise set ourselves
        otel_span.set_attribute(OJ_TRACE_ID, agent_span.trace_id)
        # Absent for tracers not bound to a Session, so old consumers see no new key.
        session_id = getattr(agent_span, "session_id", None) or self._session_id
        if session_id:
            otel_span.set_attribute(OJ_SESSION_ID, session_id)
        otel_span.set_attribute(OJ_INVOKE_ID, agent_span.invoke_id or "")
        otel_span.set_attribute(OJ_PARENT_INVOKE_ID, agent_span.parent_invoke_id or "")
        otel_span.set_attribute(OJ_START_TIME, str(start_time))
        context_token = otel_context.attach(trace.set_span_in_context(otel_span))
        state = OtelSpanState(
            span=otel_span, context_token=context_token, invoke_id=agent_span.invoke_id, start_time=start_time
        )
        # Always populate attributes on this path: set_attribute is a no-op on
        # a non-recording span, and span wrappers may misreport is_recording().
        state.recorded = True
        self._span_manager.push(agent_span.invoke_id, state)
        return state

    # --- helper: end span and pop from manager ---

    def _end_and_pop(self, invoke_id: str, end_time: datetime | None = None) -> None:
        state = self._span_manager.pop(invoke_id)
        if state is None:
            return
        state.span.set_status(Status(StatusCode.OK))
        # Explicit end timestamp (true business end) instead of the
        # event-processing wall clock — spans flushed in one pass would
        # otherwise end ~instantly and report a zero duration.
        state.span.end(
            end_time=int(end_time.timestamp() * 1e9) if end_time is not None else None
        )
        otel_context.detach(state.context_token)

    # --- helper: set end-time attributes before closing a span ---

    def _set_end_attrs(self, otel_span: trace.Span, agent_span: TraceAgentSpan) -> datetime:
        """Set TraceSpan end-time fields as attributes before span closes.
        Uses span values if present, otherwise sets ourselves (matching TraceAgentHandler).
        Returns the resolved end_time so the caller can stamp it on span.end()."""
        end_time = agent_span.end_time or datetime.now(tz=_CACHED_TZLOCAL).replace(tzinfo=None)
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
        return end_time

    # --- helper: set error on span and end ---

    def _mark_error_and_end(self, invoke_id: str, error: Any, end_time: datetime | None = None) -> None:
        state = self._span_manager.pop(invoke_id)
        if state is None:
            return
        otel_span = state.span
        otel_span.set_status(Status(StatusCode.ERROR))
        otel_span.set_attribute(OJ_AGENT_ERROR_MESSAGE, str(error))
        otel_span.set_attribute(OJ_STATUS, NodeStatus.ERROR.value)
        # Complete error dict with error_code
        if isinstance(error, BaseError):
            error_code = error.status.code
            otel_span.set_attribute(OJ_ERROR, _serialize({"error_code": error_code, "message": error.message}))
        else:
            error_code = OJStatusCode.WORKFLOW_EXECUTION_ERROR.code
            otel_span.set_attribute(
                OJ_ERROR, _serialize({"error_code": error_code, "message": str(error)})
            )
        # OTel error.type carries the exception class name; the project error
        # code stays in OJ_ERROR above.
        otel_span.set_attribute(ERROR_TYPE, type(error).__name__)
        otel_span.record_exception(error)
        otel_span.end(
            end_time=int(end_time.timestamp() * 1e9) if end_time is not None else None
        )
        otel_context.detach(state.context_token)

    # --- helper: set GenAI agent identity attributes on any span ---

    def _set_agent_identity_attrs(self, otel_span: trace.Span, instance_info: dict | None) -> None:
        """Set agent identity / conversation attributes from OtelRail common info.

        ``instance_info`` keys come from ``OtelRail._build_common_info``:
        ``agent_id`` / ``agent_name`` / ``agent_description`` /
        ``conversation_id`` / ``user_id`` / ``metadata``. Events raised
        without them (other rails, direct handler callers) simply skip the
        attributes. Decorator-measured llm spans forward the same info plus
        ``workflow_id`` / ``workflow_name``; ``component_*`` keys are
        deliberately not mapped — ``openjiuwen.workflow.component_id`` is the
        backend's shell discriminator and inner llm spans must not claim it.
        """
        info = instance_info or {}
        agent_id = info.get("agent_id")
        if agent_id:
            otel_span.set_attribute(GEN_AI_AGENT_ID, str(agent_id))
        # Executing agent name (not the span entity name — on tool spans the
        # entity is the tool). Tool spans also set this via extra_attrs with a
        # class_name fallback for direct callers; via the rail both agree.
        if info.get("agent_name"):
            otel_span.set_attribute(GEN_AI_AGENT_NAME, str(info["agent_name"]))
        if info.get("agent_description"):
            otel_span.set_attribute(GEN_AI_AGENT_DESCRIPTION, str(info["agent_description"]))
        if info.get("conversation_id"):
            otel_span.set_attribute(GEN_AI_CONVERSATION_ID, str(info["conversation_id"]))
        if info.get("user_id"):
            otel_span.set_attribute(OJ_GEN_AI_USER_ID, str(info["user_id"]))
        # First writer wins for metadata: decorator identity stamps it at span
        # start, and a later rail-forwarded info must not clobber it.
        if info.get("metadata") and OJ_GEN_AI_METADATA not in (otel_span.attributes or {}):
            otel_span.set_attribute(OJ_GEN_AI_METADATA, _serialize(info["metadata"]))
        # Workflow context from decorator-forwarded identity: written only when
        # absent so the start-time value survives a second identity forward.
        existing = otel_span.attributes or {}
        if info.get("workflow_id") and OJ_WORKFLOW_ID not in existing:
            otel_span.set_attribute(OJ_WORKFLOW_ID, str(info["workflow_id"]))
        if info.get("workflow_name") and OJ_WORKFLOW_NAME not in existing:
            otel_span.set_attribute(OJ_WORKFLOW_NAME, str(info["workflow_name"]))

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
        # Trace display name: the executing agent (rail-forwarded), falling
        # back to the span entity name.
        trace_name = ((instance_info or {}).get("agent_name") or "") or name_val
        otel_span.set_attribute(OJ_GEN_AI_TRACE_NAME, str(trace_name))
        # Agent identity + conversation facts, forwarded by OtelRail
        self._set_agent_identity_attrs(otel_span, instance_info)

    # ================================================================
    # LLM events — SpanKind.CLIENT, gen_ai.* attributes
    # ================================================================

    async def on_llm_start(self, span: TraceAgentSpan, inputs: Any, instance_info: dict, **kwargs):
        try:
            # 显式父 refs（嵌入桥经 identity 通道带来）> 逻辑父 > 环境回退
            parent_ctx = _explicit_parent_context(instance_info)
            if parent_ctx is None:
                parent_ctx = self._resolve_parent_context(span)
            state = self._start_and_push(
                name=f"llm.{instance_info.get('class_name', 'unknown')}",
                kind=SpanKind.CLIENT,
                parent_ctx=parent_ctx,
                agent_span=span,
            )
            if not state.recorded:
                return
            # LLM-specific OTel attributes
            state.span.set_attribute(GEN_AI_REQUEST_MODEL, instance_info.get("class_name", ""))
            state.span.set_attribute(GEN_AI_OPERATION_NAME, _OPERATION_NAME_CHAT)
            # Trace display name: the executing agent (rail-forwarded), falling
            # back to the model name for direct on_llm_start callers.
            agent_name = instance_info.get("agent_name") or instance_info.get("class_name", "")
            state.span.set_attribute(OJ_GEN_AI_TRACE_NAME, str(agent_name))
            # Agent identity + conversation facts, forwarded by OtelRail
            self._set_agent_identity_attrs(state.span, instance_info)
            # GenAI request parameters, forwarded by OtelRail.before_model_call
            for param, key in (
                ("temperature", GEN_AI_REQUEST_TEMPERATURE),
                ("top_p", GEN_AI_REQUEST_TOP_P),
                ("top_k", GEN_AI_REQUEST_TOP_K),
                ("max_tokens", GEN_AI_REQUEST_MAX_TOKENS),
                ("stop_sequences", GEN_AI_REQUEST_STOP_SEQUENCES),
                ("reasoning_level", GEN_AI_REQUEST_REASONING_LEVEL),
            ):
                value = (instance_info.get("request_params") or {}).get(param)
                if value is not None:
                    state.span.set_attribute(key, value)
            message_count = instance_info.get("message_count")
            if message_count is not None:
                state.span.set_attribute(OJ_LLM_PREV_MESSAGE_COUNT, int(message_count))
            # Agent base fields (use span values if present, otherwise set ourselves)
            invoke_type_val = span.invoke_type or InvokeType.LLM.value
            name_val = span.name or instance_info.get("class_name", "")
            state.span.set_attribute(OJ_AGENT_INVOKE_TYPE, invoke_type_val)
            state.span.set_attribute(OJ_AGENT_NAME, name_val)
            if inputs is not None:
                # Normalize message objects to plain dicts before serialization,
                # so GEN_AI_INPUT_MESSAGES carries standard JSON instead of class repr.
                payload = _serialize(_normalize_llm_payload(inputs))
                state.span.set_attribute(GEN_AI_INPUT_MESSAGES, redact(payload, self._config, field="prompts"))
        except Exception as exc:
            session_logger.warning("otel agent handler: on_llm_start failed: %s", exc)

    async def on_llm_request(self, span: TraceAgentSpan, **kwargs):
        try:
            state = self._span_manager.get(span.invoke_id)
            if state is None or not state.recorded:
                return
            state.span.add_event("llm.request", attributes=kwargs)
        except Exception as exc:
            session_logger.warning("otel agent handler: on_llm_request failed: %s", exc)

    async def on_llm_end(self, span: TraceAgentSpan, outputs, **kwargs):
        try:
            state = self._span_manager.get(span.invoke_id)
            if state is None:
                return
            end_time = None
            if state.recorded:
                if outputs is not None:
                    # Unwrap the {"outputs": AssistantMessage} container OtelRail
                    # sends and normalize message objects to plain dicts, so the
                    # completion payload carries real content as standard JSON.
                    payload = _serialize(_unwrap_llm_payload(outputs))
                    state.span.set_attribute(GEN_AI_OUTPUT_MESSAGES, redact(payload, self._config, field="completions"))
                    _set_llm_response_attrs(state.span, outputs)
                end_time = self._set_end_attrs(state.span, span)
            self._end_and_pop(span.invoke_id, end_time)
        except Exception as exc:
            session_logger.warning("otel agent handler: on_llm_end failed: %s", exc)

    async def on_llm_error(self, span: TraceAgentSpan, error, **kwargs):
        try:
            self._mark_error_and_end(span.invoke_id, error, span.end_time)
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
        span_name: str | None = None,
    ) -> None:
        parent_ctx = self._resolve_parent_context(agent_span)
        state = self._start_and_push(
            name=span_name or f"{span_name_prefix}.{instance_info.get('class_name', 'unknown')}",
            kind=SpanKind.INTERNAL,
            parent_ctx=parent_ctx,
            agent_span=agent_span,
        )
        if not state.recorded:
            return
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
        end_time = None
        if state.recorded:
            if outputs is not None:
                state.span.set_attribute(OJ_AGENT_OUTPUTS, redact(outputs, self._config))
            end_time = self._set_end_attrs(state.span, span)
        self._end_and_pop(span.invoke_id, end_time)

    # --- helper: mark error on a non-LLM span and end ---

    def _error_non_llm_span(self, span: TraceAgentSpan, error: Any) -> None:
        self._mark_error_and_end(span.invoke_id, error, span.end_time)

    # ================================================================
    # Plugin (Tool) events — SpanKind.INTERNAL
    # ================================================================

    def _is_duplicate_tool_event(self, span: TraceAgentSpan, instance_info: dict) -> bool:
        """Whether this on_plugin_start duplicates an open OtelRail tool span.

        Bug #2046: with OtelRail registered, one physical tool call emits TWO
        on_plugin_start events — the rail's (instance_info type="plugin",
        carries tool_call_id) wraps the tool execution, and the legacy
        session-tracer decorator on ``tool.invoke`` (type="tool", no
        tool_call_id) fires strictly nested inside it (rail-before →
        decorator-start → decorator-end → rail-after). The decorator's
        event is the duplicate: its rail twin span is still open at that
        point, so skipping it leaves exactly one execute_tool span per
        call. Same-name parallel calls stay correct — each call keeps its
        own rail span — and the decorator's matching end/error events are
        natural no-ops because this start never registered a span.
        """
        # Decorator fingerprint; rail events carry type="plugin" plus the id.
        if instance_info.get("type") != "tool" or instance_info.get("tool_call_id"):
            return False
        tool_name = str(instance_info.get("class_name") or "")
        if not tool_name:
            return False
        return self._span_manager.has_open_tool_span(
            tool_name,
            str(getattr(span, "trace_id", "") or ""),
        )

    async def on_plugin_start(self, span: TraceAgentSpan, inputs: Any, instance_info: dict, **kwargs):
        try:
            # Dedupe is a best-effort optimization and must fail OPEN: with
            # the probe sharing this method's except-and-return fallback, a
            # raising probe would swallow the rail's sole execute_tool span
            # for the call — telemetry loss, strictly worse than the
            # duplicate it guards against. Its own guard logs and treats
            # the event as non-duplicate, so span creation proceeds.
            try:
                duplicate = self._is_duplicate_tool_event(span, instance_info)
            except Exception as exc:
                session_logger.warning(
                    "otel agent handler: tool dedupe probe failed, fail-open: %s", exc
                )
                duplicate = False
            if duplicate:
                return
            extra_attrs: dict[str, Any] = {
                GEN_AI_OPERATION_NAME: _OPERATION_NAME_EXECUTE_TOOL,
                GEN_AI_TOOL_NAME: instance_info.get("class_name", ""),
            }
            tool_type = instance_info.get("tool_type")
            if tool_type:
                extra_attrs[GEN_AI_TOOL_TYPE] = str(tool_type)
            # Agent executing the tool; OtelRail forwards it, fall back to the
            # tool name for direct on_plugin_start callers.
            agent_name = instance_info.get("agent_name") or instance_info.get("class_name", "")
            extra_attrs[GEN_AI_AGENT_NAME] = str(agent_name)
            # Tool-call id rides instance_info (forwarded by OtelRail.before_tool_call).
            tool_call_id = instance_info.get("tool_call_id", "")
            if tool_call_id:
                extra_attrs[GEN_AI_TOOL_CALL_ID] = str(tool_call_id)
            self._start_non_llm_span(
                span, inputs, instance_info, InvokeType.PLUGIN.value, "tool",
                extra_attrs=extra_attrs,
            )
            if instance_info.get("type") == InvokeType.PLUGIN.value:
                # OtelRail's tool event (the decorator's carries type="tool"):
                # flag the span state so has_open_tool_span can identify it
                # without relying on the optional gen_ai.tool.call.id.
                state = self._span_manager.get(span.invoke_id)
                if state is not None:
                    state.from_tool_rail = True
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
    # Retriever events — SpanKind.INTERNAL, gen_ai.operation.name="retrieval"
    # ================================================================
    #
    # Long-dormant first-class family (declared on the handler interface,
    # implemented by the built-in trace-schema handler) with no emitter until
    # TracerAgentUtils.trace_retriever_start/end/error wired retrieval tool
    # bodies to it.  The emitted span carries no parent_invoke_id:
    # _resolve_parent_context falls back to the ambient OTel context, which
    # during tool execution is the execute_tool span attached by
    # _start_and_push — so the retrieval span nests under the tool call that
    # performed it.  Typed as a retrieval operation per OTel GenAI semantic
    # conventions, mirroring the workflow-side retrieval component spans.

    async def on_retriever_start(self, span: TraceAgentSpan, inputs: Any, instance_info: dict, **kwargs):
        try:
            self._start_non_llm_span(
                span,
                inputs,
                instance_info,
                InvokeType.RETRIEVER.value,
                "retrieval",
                span_name="retrieval",
                extra_attrs={GEN_AI_OPERATION_NAME: _OPERATION_NAME_RETRIEVAL},
            )
            state = self._span_manager.get(span.invoke_id)
            if state is None or not state.recorded:
                return
            query = _extract_retrieval_query(inputs)
            if query is not None:
                state.span.set_attribute(GEN_AI_RETRIEVAL_QUERY_TEXT, redact(query, self._config))
            normalized = _normalize_llm_payload(inputs)
            if isinstance(normalized, dict):
                top_k = normalized.get("top_k") or normalized.get("k")
                if top_k is not None:
                    state.span.set_attribute(GEN_AI_RETRIEVAL_TOP_K, int(top_k))
                data_source = normalized.get("data_source_id") or normalized.get("data_source")
                if data_source is not None:
                    state.span.set_attribute(GEN_AI_DATA_SOURCE_ID, str(data_source))
        except Exception as exc:
            session_logger.warning("otel agent handler: on_retriever_start failed: %s", exc)

    async def on_retriever_end(self, span: TraceAgentSpan, outputs, **kwargs):
        try:
            state = self._span_manager.get(span.invoke_id)
            if state is not None and state.recorded:
                documents = _extract_retrieval_documents(outputs)
                if documents is not None:
                    # Same policy as the workflow side: hash under
                    # redaction, truncate otherwise — never raw text.
                    state.span.set_attribute(
                        GEN_AI_RETRIEVAL_DOCUMENTS,
                        [redact(doc, self._config) for doc in documents],
                    )
            self._end_non_llm_span(span, outputs)
        except Exception as exc:
            session_logger.warning("otel agent handler: on_retriever_end failed: %s", exc)

    async def on_retriever_error(self, span: TraceAgentSpan, error, **kwargs):
        try:
            self._error_non_llm_span(span, error)
        except Exception as exc:
            session_logger.warning("otel agent handler: on_retriever_error failed: %s", exc)

    def drop_agent_span(self, invoke_id: str) -> None:
        """Best-effort close of an agent span whose end/error event never arrived.

        Degradation-tolerant emitters (e.g. studio's retrieval tool) fire
        on_retriever_end / on_retriever_error through ``Tracer.trigger``; when
        the trigger itself raises, the emitter can only drop the span on its
        side. Without this hook the OTel span opened by on_*_start stays
        half-open in this manager forever. The span is closed with OK status
        (the business call may well have succeeded — only telemetry delivery
        failed) plus a drop marker event. Never raises.
        """
        try:
            state = self._span_manager.get(invoke_id)
            if state is not None and state.recorded:
                state.span.add_event(
                    "oj.span.dropped", attributes={"reason": "end event undelivered by emitter"}
                )
            self._end_and_pop(invoke_id)
        except Exception as exc:
            session_logger.warning("otel agent handler: drop_agent_span failed: %s", exc)

    # ================================================================
    # Embedding events — INTERNAL span, embeddings operation (issue #1833).
    # Core emitter: APIEmbedding._get_embeddings fires these under a traced
    # session context (workflow component / agent run); the span parents onto
    # whatever OTel span is ambient at the trigger point.
    # ================================================================

    async def on_embedding_start(self, span: TraceAgentSpan, inputs: Any, instance_info: dict, **kwargs):
        try:
            model_name = str(instance_info.get("model_name") or instance_info.get("class_name") or "unknown")
            extra_attrs: dict[str, Any] = {
                GEN_AI_OPERATION_NAME: _OPERATION_NAME_EMBEDDINGS,
                GEN_AI_REQUEST_MODEL: model_name,
            }
            # GenAI naming rule: "{operation.name} {gen_ai.request.model}"
            self._start_non_llm_span(
                span, inputs, instance_info, "embedding", "embedding",
                extra_attrs=extra_attrs, span_name=f"embeddings {model_name}",
            )
            state = self._span_manager.get(span.invoke_id)
            if state is None or not state.recorded:
                return
            normalized = _normalize_llm_payload(inputs)
            if isinstance(normalized, dict):
                dimension = normalized.get("dimension") or normalized.get("dimensions")
                if dimension is not None:
                    state.span.set_attribute(GEN_AI_EMBEDDINGS_DIMENSION_COUNT, int(dimension))
        except Exception as exc:
            session_logger.warning("otel agent handler: on_embedding_start failed: %s", exc)

    async def on_embedding_end(self, span: TraceAgentSpan, outputs, **kwargs):
        try:
            state = self._span_manager.get(span.invoke_id)
            if state is not None and state.recorded and isinstance(outputs, dict):
                # Service-side facts captured by the core emitter from the raw
                # embedding API response (see APIEmbedding._trace_embedding_end).
                response_model = outputs.get("response_model")
                if response_model:
                    state.span.set_attribute(GEN_AI_RESPONSE_MODEL, str(response_model))
                dimension = outputs.get("dimension")
                if dimension is not None:
                    state.span.set_attribute(GEN_AI_EMBEDDINGS_DIMENSION_COUNT, int(dimension))
                usage_input_tokens = outputs.get("usage_input_tokens")
                if isinstance(usage_input_tokens, int):
                    state.span.set_attribute(GEN_AI_USAGE_INPUT_TOKENS, usage_input_tokens)
            self._end_non_llm_span(span, outputs)
        except Exception as exc:
            session_logger.warning("otel agent handler: on_embedding_end failed: %s", exc)

    async def on_embedding_error(self, span: TraceAgentSpan, error, **kwargs):
        try:
            self._error_non_llm_span(span, error)
        except Exception as exc:
            session_logger.warning("otel agent handler: on_embedding_error failed: %s", exc)

    # ================================================================
    # Memory events — INTERNAL span (issue #1833; no core emitter yet,
    # entry points ready).
    # ================================================================

    async def on_memory_start(self, span: TraceAgentSpan, inputs: Any, instance_info: dict, **kwargs):
        try:
            self._start_non_llm_span(span, inputs, instance_info, "memory", "memory")
            state = self._span_manager.get(span.invoke_id)
            if state is None or not state.recorded:
                return
            normalized = _normalize_llm_payload(inputs)
            if isinstance(normalized, dict):
                record_count = normalized.get("record_count") or normalized.get("count")
                if record_count is not None:
                    state.span.set_attribute(GEN_AI_MEMORY_RECORD_COUNT, int(record_count))
        except Exception as exc:
            session_logger.warning("otel agent handler: on_memory_start failed: %s", exc)

    async def on_memory_end(self, span: TraceAgentSpan, outputs, **kwargs):
        try:
            self._end_non_llm_span(span, outputs)
        except Exception as exc:
            session_logger.warning("otel agent handler: on_memory_end failed: %s", exc)

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
        # Session binding arrives per-event or via set_session_id(); declared
        # here so on_call_start never depends on the base-class attribute.
        self._session_id: str | None = None
        # Mapping 2: parent_node_id → root OtelSpanState for the layer
        self._layer_root_spans: dict[str, OtelSpanState] = {}
        # Mapping 3: node_id → host-component OtelSpanState
        self._component_spans: dict[str, OtelSpanState] = {}
        # Cached OTel context from the first root span creation.
        # Ensures all spans within the same execution share one OTel trace.
        self._cached_root_ctx: otel_context.Context | None = None
        self._cached_session_id: str | None = None
        # The trace_id that was active when _cached_root_ctx was created.
        # Used by set_trace_id to distinguish QA resume (same trace_id)
        # from a new conversation round (different trace_id).
        self._cached_trace_id: str | None = None
        # Stream-consumer end nodes whose on_call_start arrived at the first
        # chunk (metadata.defer_span_start): span creation is deferred to the
        # on_call_finalize marker. Stash holds the begin payload plus stream
        # chunks observed during the deferral window, replayed on the span
        # when it materializes (on_call_finalize, or on_call_done fallback
        # with the original begin timestamp when the marker never arrives).
        self._deferred_end_starts: dict[str, dict] = {}

    # --- helper: resolve parent context for a new span ---

    def _has_live_spans(self) -> bool:
        """Whether any tracked workflow span is still recording.

        Tracer.init() broadcasts set_trace_id/set_session_id to every
        registered handler singleton, including handlers currently tracing a
        live run for another session (e.g. a fresh lightweight agent session
        created inside a running workflow component). Resetting caches or
        force-cleaning spans at that moment would detach the live run's
        ambient context mid-node and force-end in-flight spans, so resets
        must be deferred while any tracked span is still recording.
        """
        tracked = list(self._layer_root_spans.values()) + list(self._component_spans.values())
        return any(state.span.is_recording() for state in tracked)

    def set_session_id(self, session_id: str | None) -> None:
        """Override to clear cached trace context when session changes."""
        if self._has_live_spans():
            # Per-event session binding keeps serving the live run; rebind
            # once the run finishes.
            return
        if session_id and session_id != self._cached_session_id:
            self._cached_root_ctx = None
            self._cached_trace_id = None
            self._cached_session_id = session_id
        super().set_session_id(session_id)

    def set_trace_id(self, trace_id: str) -> None:
        """Override to clear cached trace context when trace_id changes.

        Each conversation round creates a new Tracer with a new trace_id.
        When the incoming trace_id differs from the one used to build the
        cached OTel context, it means a *new conversation round* has started
        (the workflow_runner reuses the first execution's trace_id for QA
        resume, so the cache is preserved in that case).
        """
        if self._has_live_spans():
            # A live run is being traced for another session; a foreign
            # tracer's init must not clear caches or force-clean its spans.
            return
        session_logger.debug(
            "otel: set_trace_id called: old=%s, new=%s, cached_trace_id=%s, cached_ctx=%s",
            self._trace_id, trace_id, self._cached_trace_id, self._cached_root_ctx is not None
        )
        if trace_id and trace_id != self._trace_id:
            # New conversation round: clear cached context and stale spans
            session_logger.debug(
                "otel: NEW ROUND - clearing cache (cached_trace_id=%s != new_trace_id=%s)",
                self._cached_trace_id, trace_id,
            )
            self._cached_root_ctx = None
            self._cached_trace_id = None
            self._cleanup_stale_spans()
        super().set_trace_id(trace_id)

    def _cleanup_stale_spans(self) -> None:
        """End and remove spans from a previous conversation round.

        Called when a new round starts for the same workflow. Without cleanup,
        the new root span would inherit the old round's OTel trace_id.
        """
        for state in list(self._layer_root_spans.values()):
            try:
                state.span.end()
                self._span_manager.pop(state.invoke_id)
                self._detach_state(state)
            except Exception as exc:
                session_logger.warning("otel workflow handler: cleanup layer root span failed: %s", exc)
        for state in list(self._component_spans.values()):
            try:
                state.span.end()
                self._span_manager.pop(state.invoke_id)
                self._detach_state(state)
            except Exception as exc:
                session_logger.warning("otel workflow handler: cleanup component span failed: %s", exc)
        self._layer_root_spans.clear()
        self._component_spans.clear()

    @staticmethod
    def _detach_state(state: OtelSpanState) -> None:
        """Detach the ambient context attached with a workflow span, if any.

        Tolerates tokens created in another context (stale-session cleanup may
        run outside the context that attached the span).
        """
        if state is None or state.context_token is None:
            return
        try:
            otel_context.detach(state.context_token)
        except Exception:  # noqa: BLE001 - token belongs to another context
            pass

    # --- helper: resolve parent context for a new span ---

    def _resolve_parent_context(
        self,
        parent_node_id: str,
        metadata: dict | None,
    ) -> otel_context.Context | None:
        is_workflow_root = metadata and "workflow_id" in metadata and "component_id" not in metadata

        if parent_node_id == "" and is_workflow_root:
            # Check if there is already a root workflow
            existing_root = self._layer_root_spans.get("")
            session_logger.debug(
                "otel: _resolve_parent_context: trace_id=%s, cached_trace_id=%s, "
                "cached_ctx=%s, layer_root_spans=%s, existing_root=%s",
                self._trace_id, self._cached_trace_id,
                self._cached_root_ctx is not None,
                list(self._layer_root_spans.keys()),
                existing_root is not None,
            )
            if existing_root is None:
                # This is the first root span for this execution
                # But we might have a cached context from a previous execution
                if self._cached_root_ctx is not None:
                    session_logger.debug(f"otel: REUSING cached context for trace_id={self._trace_id}")
                    return self._cached_root_ctx
                session_logger.debug(f"otel: NO CACHED CONTEXT, creating new trace for trace_id={self._trace_id}")
                return None
            # Isolate different workflows: clean stale context from another workflow
            current_workflow_id = metadata.get("workflow_id") if metadata else None
            if current_workflow_id and existing_root.workflow_id != current_workflow_id:
                self._cached_root_ctx = None
                self._layer_root_spans.clear()
                self._component_spans.clear()
                return None
            # Same workflow_id: reuse cached OTel context to keep all spans
            # in one trace, even across multiple stream calls.
            if self._cached_root_ctx is not None:
                return self._cached_root_ctx
            # Fallback: clean up stale spans from the previous round
            self._cleanup_stale_spans()
            return None
        if parent_node_id == "" and not is_workflow_root:
            # Component in root workflow → parent = root workflow root
            return _get_parent_context(self._layer_root_spans.get(""))
        if parent_node_id != "" and is_workflow_root:
            # Sub-workflow root — parent = host component span (check both mappings)
            parent_state = self._component_spans.get(parent_node_id)
            if parent_state is None:
                parent_state = self._layer_root_spans.get(parent_node_id)
            return _get_parent_context(parent_state)
        # Component in sub-workflow — parent = host component span (check both mappings)
        parent_state = self._component_spans.get(parent_node_id)
        if parent_state is None:
            parent_state = self._layer_root_spans.get(parent_node_id)
        return _get_parent_context(parent_state)

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
        if state is None or not state.recorded:
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

    def _refresh_inputs_attribute(self, invoke_id: str) -> None:
        """Re-serialize OJ_WORKFLOW_INPUTS after transform callbacks mutated inputs.

        resolve_global_vars_transform (and other COMPONENT_BATCH_INPUT transform
        callbacks) mutate the inputs dict in place after on_pre_invoke. This method
        re-reads the cached inputs reference and overwrites the OTel attribute so
        the span reflects the post-transform values (e.g. ${global.xxx} resolved
        to actual values, instead of None / the literal ref string).
        """
        state = self._span_manager.get(invoke_id)
        if state is None or not state.recorded or state.inputs is None:
            return
        state.span.set_attribute(OJ_WORKFLOW_INPUTS, redact(state.inputs, self._config))

    # --- helper: set end-time attributes before closing a workflow span ---

    def _set_workflow_end_attrs(self, state: OtelSpanState) -> None:
        """Set end_time / elapsed_time before closing a workflow span.

        Mirrors the Agent handler's ``_set_end_attrs`` for consistency.
        Uses the cached ``start_time`` on ``OtelSpanState`` (set in ``on_call_start``).
        """
        if not state.recorded or state.start_time is None:
            return
        end_time = datetime.now(tz=_CACHED_TZLOCAL).replace(tzinfo=None)
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
            # Stream-consumer end nodes: on_call_start fires at the FIRST CHUNK
            # (passive stream consumption), so opening the span here makes the
            # end node appear before the upstream nodes still streaming in the
            # waterfall and inflates its duration by the whole streaming
            # window. Stash
            # the payload and open the span at the on_call_finalize marker;
            # on_call_done falls back to the first-chunk timestamp when the
            # marker never arrives (error path), preserving old semantics.
            if metadata and metadata.get("defer_span_start"):
                self._deferred_end_starts[invoke_id] = {
                    "metadata": metadata,
                    "source_ids": source_ids,
                    "session_id": kwargs.get("session_id"),
                    "parent_node_id": parent_node_id,
                    "begin_time": datetime.now(tz=_CACHED_TZLOCAL).replace(tzinfo=None),
                    "stream_inputs": [],
                    "stream_outputs": [],
                }
                return
            self._create_workflow_span(
                invoke_id=invoke_id,
                metadata=metadata,
                inputs=inputs,
                source_ids=source_ids,
                session_id=kwargs.get("session_id"),
                parent_node_id=parent_node_id,
            )
        except Exception as exc:
            session_logger.warning("otel workflow handler: on_call_start failed: %s", exc)

    def _create_workflow_span(
        self,
        invoke_id: str,
        metadata: dict,
        inputs: Any,
        source_ids: list,
        session_id: str | None,
        parent_node_id: str,
        start_time=None,
    ) -> None:
        """Open a workflow / component span (extracted from on_call_start).

        ``start_time`` defaults to the clock; the deferred-end-node fallback
        passes the stashed first-chunk timestamp to reproduce the pre-deferral
        [begin, done] window.
        """
        parent_ctx = self._resolve_parent_context(parent_node_id, metadata)

        if metadata is None:
            metadata = {}
        is_workflow_root = "workflow_id" in metadata and "component_id" not in metadata
        component_type = metadata.get("component_type", "")
        is_llm_component = any(s in component_type for s in _LLM_SUBSTRINGS)
        span_kind = SpanKind.CLIENT if is_llm_component else SpanKind.INTERNAL

        is_retrieval_component = any(s in component_type for s in _RETRIEVAL_SUBSTRINGS)
        if is_workflow_root:
            span_name = metadata.get("workflow_name") or invoke_id
        elif is_retrieval_component:
            retrieval_name = str(metadata.get("component_name") or invoke_id)
            span_name = f"retrieval {retrieval_name}"
        else:
            component_name = metadata.get("component_name") or invoke_id
            span_name = f"component.{component_name}"

        otel_span = self._otel_tracer.start_span(
            name=span_name,
            kind=span_kind,
            context=parent_ctx,
            # The SDK start_span expects int nanoseconds; the datetime form is
            # kept for OJ_START_TIME / elapsed-time math.
            start_time=int(start_time.timestamp() * 1e9) if isinstance(start_time, datetime) else start_time,
        )
        # P0 perf: skip all attribute work for non-recording spans
        if not otel_span.is_recording():
            state = OtelSpanState(
                span=otel_span, context_token=None, invoke_id=invoke_id,
                start_time=None,
                workflow_id=metadata.get("workflow_id") if metadata else None,
            )
            self._span_manager.push(invoke_id, state)
            if is_workflow_root:
                self._layer_root_spans[parent_node_id] = state
                self._layer_root_spans[invoke_id] = state
                # Cache OTel context for subsequent root spans
                self._cached_root_ctx = trace.set_span_in_context(otel_span)
                self._cached_trace_id = self._trace_id
            else:
                self._component_spans[invoke_id] = state
            return
        # OTel standard + base attributes
        if start_time is None:
            start_time = datetime.now(tz=_CACHED_TZLOCAL).replace(tzinfo=None)
        otel_span.set_attribute(GEN_AI_SYSTEM, GEN_AI_SYSTEM_VALUE)
        otel_span.set_attribute(GEN_AI_PROVIDER_NAME, GEN_AI_SYSTEM_VALUE)
        otel_span.set_attribute(OJ_TRACE_ID, self._trace_id)
        # Workflow events carry no TraceWorkflowSpan, so the session id arrives
        # per-event; fall back to the tracer-injected one for direct callers.
        session_id = session_id or self._session_id
        if session_id:
            otel_span.set_attribute(OJ_SESSION_ID, session_id)
        otel_span.set_attribute(OJ_INVOKE_ID, invoke_id)
        otel_span.set_attribute(OJ_PARENT_NODE_ID, parent_node_id)
        otel_span.set_attribute(OJ_START_TIME, str(start_time))
        if source_ids is not None:
            otel_span.set_attribute(OJ_SOURCE_IDS, _serialize(source_ids))
        # LLM component: gen_ai attributes
        if is_llm_component:
            otel_span.set_attribute(GEN_AI_OPERATION_NAME, _OPERATION_NAME_CHAT)
        elif any(s in component_type for s in _TOOL_SUBSTRINGS):
            otel_span.set_attribute(GEN_AI_OPERATION_NAME, _OPERATION_NAME_EXECUTE_TOOL)
        elif is_retrieval_component:
            otel_span.set_attribute(GEN_AI_OPERATION_NAME, _OPERATION_NAME_RETRIEVAL)
            # Static retrieval facts exported by the component through
            # ComponentExecutable.trace_metadata() (core plumbing).
            top_k = metadata.get("top_k")
            if isinstance(top_k, int):
                otel_span.set_attribute(GEN_AI_RETRIEVAL_TOP_K, top_k)
            data_source_ids = metadata.get("data_source_ids")
            if isinstance(data_source_ids, list) and data_source_ids:
                otel_span.set_attribute(GEN_AI_DATA_SOURCE_ID, [str(i) for i in data_source_ids])
            request_model = metadata.get("request_model")
            if request_model:
                otel_span.set_attribute(GEN_AI_REQUEST_MODEL, str(request_model))

        self._set_workflow_attrs(otel_span, metadata, invoke_id)

        if inputs is not None:
            otel_span.set_attribute(OJ_WORKFLOW_INPUTS, redact(inputs, self._config))
            if is_retrieval_component:
                query = _extract_retrieval_query(inputs)
                if query is not None:
                    otel_span.set_attribute(GEN_AI_RETRIEVAL_QUERY_TEXT, redact(query, self._config))

        # Attach the span as the ambient OTel context for the duration of
        # component execution, so spans created inside the component
        # (embedding spans from the core emitter, HTTP client
        # instrumentation) parent onto it instead of becoming orphan
        # roots.  Detached wherever the span state is popped.
        context_token = otel_context.attach(trace.set_span_in_context(otel_span))
        state = OtelSpanState(
            span=otel_span, context_token=context_token, invoke_id=invoke_id, start_time=start_time,
            workflow_id=metadata.get("workflow_id") if metadata else None,
        )
        self._span_manager.push(invoke_id, state)

        # Register in layer / component mappings
        if is_workflow_root:
            self._layer_root_spans[parent_node_id] = state
            # Also register this workflow root as the parent for its children
            # Children's parent_node_id will be "" for root workflow,
            # and the node_id of the host component for sub-workflows.
            self._layer_root_spans[invoke_id] = state
            # Cache OTel context for subsequent root spans
            self._cached_root_ctx = trace.set_span_in_context(otel_span)
            self._cached_trace_id = self._trace_id
        else:
            self._component_spans[invoke_id] = state

    async def on_call_finalize(self, invoke_id: str, **kwargs):
        """Open the deferred end-node span at the finalize marker.

        Core emits this once a stream-consumer end node has fully consumed
        its stream and starts real finalization (sanitize / collect /
        post-invoke); the span opened here covers the actual execution
        window. Stream chunks buffered during the deferral window are
        replayed onto the span. Unknown invoke_ids (no stash) are ignored —
        e.g. markers observed by a handler that never saw the begin event.
        """
        try:
            deferred = self._deferred_end_starts.pop(invoke_id, None)
            if deferred is None:
                return
            self._materialize_deferred_span(invoke_id, deferred, start_time=None)
        except Exception as exc:
            session_logger.warning("otel workflow handler: on_call_finalize failed: %s", exc)

    def _materialize_deferred_span(self, invoke_id: str, deferred: dict, start_time) -> None:
        self._create_workflow_span(
            invoke_id=invoke_id,
            metadata=deferred["metadata"],
            inputs=None,
            source_ids=deferred["source_ids"],
            session_id=deferred["session_id"],
            parent_node_id=deferred["parent_node_id"],
            start_time=start_time,
        )
        # Replay stream chunks observed while the span was deferred so the
        # end-node span keeps its OJ_STREAM_INPUTS / OJ_STREAM_OUTPUTS
        # payload parity with the pre-deferral behavior.
        for chunk in deferred["stream_inputs"]:
            self._span_manager.append_stream_input(invoke_id, chunk)
        for chunk in deferred["stream_outputs"]:
            self._span_manager.append_stream_output(invoke_id, chunk)

    async def on_call_done(self, invoke_id: str, outputs: Any = None, **kwargs):
        try:
            # Deferred end node whose finalize marker never arrived (stream
            # error / interrupt): fall back to the old [first-chunk, done]
            # window using the stashed begin timestamp.
            deferred = self._deferred_end_starts.pop(invoke_id, None)
            if deferred is not None:
                self._materialize_deferred_span(
                    invoke_id, deferred, start_time=deferred["begin_time"]
                )
            self._refresh_inputs_attribute(invoke_id)
            self._flush_buffered_data(invoke_id)

            state = self._span_manager.pop(invoke_id)
            if state is None:
                return

            if outputs is not None:
                state.span.set_attribute(OJ_WORKFLOW_OUTPUTS, redact(outputs, self._config))
                if _is_retrieval_component_span(state.span):
                    documents = _extract_retrieval_documents(outputs)
                    if documents is not None:
                        # Same policy as query.text / workflow.outputs: hash
                        # under redaction, truncate otherwise — never raw text.
                        state.span.set_attribute(
                            GEN_AI_RETRIEVAL_DOCUMENTS,
                            [redact(doc, self._config) for doc in documents],
                        )

            self._set_workflow_end_attrs(state)
            state.span.set_attribute(OJ_STATUS, NodeStatus.FINISH.value)
            state.span.set_status(Status(StatusCode.OK))
            state.span.end()
            self._detach_state(state)

            # Remove from layer/component mappings so the handler can
            # distinguish completed spans from interrupted ones.
            # On QA interrupt the QA node and downstream nodes never reach
            # on_call_done, so their entries persist — that is the signal
            # the workflow_runner uses to detect resume vs. new round.
            self._layer_root_spans.pop(invoke_id, None)
            self._component_spans.pop(invoke_id, None)
            # Also remove the parent_node_id → root mapping (keyed by "")
            root_state = self._layer_root_spans.get("")
            if root_state is state:
                self._layer_root_spans.pop("", None)

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
            if state is None or not state.recorded:
                return
            if inputs is not None:
                # Store reference for on_invoke / on_call_done to re-serialize
                # after transform callbacks mutate inputs in place.
                state.inputs = inputs
                state.span.set_attribute(OJ_WORKFLOW_INPUTS, redact(inputs, self._config))
                if _is_retrieval_component_span(state.span):
                    query = _extract_retrieval_query(inputs)
                    if query is not None:
                        state.span.set_attribute(GEN_AI_RETRIEVAL_QUERY_TEXT, redact(query, self._config))
            self._set_workflow_attrs(state.span, component_metadata, invoke_id)
        except Exception as exc:
            session_logger.warning("otel workflow handler: on_pre_invoke failed: %s", exc)

    async def on_pre_stream(self, invoke_id: str, chunk, need_send: bool = False, **kwargs):
        try:
            if isinstance(chunk, dict):
                state = self._span_manager.get(invoke_id)
                if state is not None:
                    self._span_manager.append_stream_input(invoke_id, chunk)
                else:
                    deferred = self._deferred_end_starts.get(invoke_id)
                    if deferred is not None:
                        deferred["stream_inputs"].append(chunk)
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
            state = self._span_manager.get(invoke_id)
            if state is not None and not state.recorded and exception is None:
                return
            if exception is not None:
                # Deferred end node on the error path: neither materializer
                # runs — core raises before the finalize marker (stream_call)
                # and before on_call_done (call), so without this pop the
                # stash leaks and the end-node span silently disappears on
                # error/interrupt. Open the span from the stashed first-chunk
                # timestamp (same fallback window as on_call_done) so the
                # error marking below has a span to annotate.
                deferred = self._deferred_end_starts.pop(invoke_id, None)
                if deferred is not None:
                    self._materialize_deferred_span(
                        invoke_id, deferred, start_time=deferred["begin_time"]
                    )
                # Distinguish GraphInterrupt (control-flow signal) from real errors,
                # mirroring builtin TraceWorkflowHandler.on_invoke:
                #   - GraphInterrupt -> openjiuwen.status="interrupted", no OTel ERROR,
                #     no OJ_ERROR / OJ_WORKFLOW_ERROR_MESSAGE (interrupt is not a failure)
                #   - BaseError      -> openjiuwen.status="error", OJ_ERROR with error_code
                #   - other Exception -> openjiuwen.status="error", OJ_ERROR with WORKFLOW_EXECUTION_ERROR
                state = self._span_manager.get(invoke_id)
                if state is not None:
                    if isinstance(exception, GraphInterrupt):
                        state.span.set_attribute(OJ_STATUS, NodeStatus.INTERRUPTED.value)
                        # Record interrupt payload for debuggability without error semantics
                        state.span.set_attribute(OJ_WORKFLOW_ERROR_MESSAGE, str(exception))
                    else:
                        state.span.set_status(Status(StatusCode.ERROR))
                        state.span.set_attribute(OJ_WORKFLOW_ERROR_MESSAGE, str(exception))
                        state.span.set_attribute(OJ_STATUS, NodeStatus.ERROR.value)
                        if isinstance(exception, BaseError):
                            error_code = exception.status.code
                            state.span.set_attribute(
                                OJ_ERROR,
                                _serialize({"error_code": error_code, "message": exception.message}),
                            )
                        else:
                            error_code = OJStatusCode.WORKFLOW_EXECUTION_ERROR.code
                            state.span.set_attribute(
                                OJ_ERROR,
                                _serialize(
                                    {
                                        "error_code": error_code,
                                        "message": str(exception),
                                    }
                                ),
                            )
                        state.span.set_attribute(ERROR_TYPE, type(exception).__name__)
                        state.span.record_exception(exception)
                    # inner_error from on_invoke_data (applies to both interrupt and error paths)
                    if on_invoke_data and isinstance(on_invoke_data, dict) and "inner_error" in on_invoke_data:
                        state.span.set_attribute(OJ_INNER_ERROR, _serialize(on_invoke_data["inner_error"]))

                self._refresh_inputs_attribute(invoke_id)
                self._flush_buffered_data(invoke_id)

                pop_state = self._span_manager.pop(invoke_id)
                if pop_state is not None:
                    self._set_workflow_end_attrs(pop_state)
                    pop_state.span.end()
                    self._detach_state(pop_state)
                    # Preserve _layer_root_spans for multi-round context
                    for key, val in list(self._component_spans.items()):
                        if val.invoke_id == invoke_id:
                            self._component_spans.pop(key, None)
                            # Component span still open at workflow failure: finish
                            # it like stale-session cleanup (end + manager pop +
                            # detach). Dropping only the index entry leaks the span,
                            # its context token, and the _span_manager entry the
                            # cleanup safety net would need to reach it.
                            try:
                                val.span.end()
                                self._span_manager.pop(key)
                                self._detach_state(val)
                            except Exception as exc:
                                session_logger.warning("otel workflow handler: cleanup component span failed: %s", exc)
                return

            # Non-exception: buffer on_invoke_data
            if on_invoke_data is not None:
                self._span_manager.append_on_invoke_data(invoke_id, on_invoke_data)
            self._refresh_inputs_attribute(invoke_id)
        except Exception as exc:
            session_logger.warning("otel workflow handler: on_invoke failed: %s", exc)

    async def on_post_invoke(self, invoke_id: str, outputs, inputs=None, **kwargs):
        try:
            state = self._span_manager.get(invoke_id)
            if state is None or not state.recorded:
                return
            if outputs is not None:
                state.span.set_attribute(OJ_WORKFLOW_OUTPUTS, redact(outputs, self._config))
                if _is_retrieval_component_span(state.span):
                    documents = _extract_retrieval_documents(outputs)
                    if documents is not None:
                        # Same policy as query.text / workflow.outputs: hash
                        # under redaction, truncate otherwise — never raw text.
                        state.span.set_attribute(
                            GEN_AI_RETRIEVAL_DOCUMENTS,
                            [redact(doc, self._config) for doc in documents],
                        )
        except Exception as exc:
            session_logger.warning("otel workflow handler: on_post_invoke failed: %s", exc)

    async def on_post_stream(self, invoke_id: str, chunk, **kwargs):
        try:
            state = self._span_manager.get(invoke_id)
            if state is None:
                # Deferred end node: buffer output chunks for replay when the
                # span materializes at the finalize marker.
                deferred = self._deferred_end_starts.get(invoke_id)
                if deferred is not None and isinstance(chunk, dict):
                    deferred["stream_outputs"].append(chunk)
                return
            if not state.recorded:
                return
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
            if state is None or not state.recorded:
                return
            if inputs is not None:
                state.span.set_attribute(OJ_INTERACTIVE_INPUTS, redact(inputs, self._config))
        except Exception as exc:
            session_logger.warning("otel workflow handler: on_interact failed: %s", exc)
