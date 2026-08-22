# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tracer extension handlers that capture events into trajectory steps.

``TrajectoryAgentHandler`` keys buffers by ``span.trace_id`` (one trace per
agent session), so a single registered instance safely serves concurrent
sessions. Capture failures are logged and never propagate into the agent
loop. ``TrajectoryCompressionBridge`` captures from a different source —
context-compression state events on the runner callback framework — because
compression rewrites history without emitting tracer spans.
"""
import hashlib
from typing import Any, Literal

from openjiuwen.core.common.logging import session_logger
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.session.tracer.handler import TraceExtAgentHandler, TraceExtWorkflowHandler
from openjiuwen.core.session.tracer.span import TraceAgentSpan
from openjiuwen.core.session.trajectory.collector import (
    TrajectoryCollector,
    current_parent_trace_id,
    current_workflow_trace_id,
    to_text,
)
from openjiuwen.core.session.trajectory.models import (
    Observation,
    ObservationResult,
    StepError,
    StepMetrics,
    TrajectoryToolCall,
)

_WORKFLOW_FALLBACK_TRACE_ID = "workflow"
_COMPONENT_META_KEYS = ("workflow_id", "workflow_name", "component_id", "component_name", "component_type")

# Upper bound on start-event records awaiting their matching end/error event.
# A span whose end never fires (e.g. its task is cancelled mid-call) would
# otherwise leak its record forever; oldest records are evicted past the cap.
_MAX_PENDING_SPANS = 1024


def _bounded_put(store: dict, key: str, value: Any) -> None:
    """Insert into an insertion-ordered dict, evicting the oldest past the cap."""
    if key not in store and len(store) >= _MAX_PENDING_SPANS:
        store.pop(next(iter(store)))
    store[key] = value


def _field(obj: Any, name: str) -> Any:
    """Read `name` from an object attribute or dict key."""
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _unwrap(payload: Any) -> Any:
    """Unwrap the ``{"inputs": ...}`` / ``{"outputs": ...}`` envelopes used by the tracer."""
    if isinstance(payload, dict) and len(payload) == 1:
        key = next(iter(payload))
        if key in ("inputs", "outputs"):
            return payload[key]
    return payload


def _input_digest(inputs: Any) -> list[dict] | None:
    """Digest of the exact prompt sent to the model: per-message role/hash/length.

    The prompt at ``model.invoke`` time is post-everything — compression,
    rails, window mutators, input overrides — so comparing digests between
    consecutive LLM steps reveals every silent history rewrite without
    persisting the (potentially huge, potentially sensitive) content itself.
    """
    messages = _unwrap(inputs)
    if not isinstance(messages, list):
        return None
    digest = []
    for message in messages:
        content = _field(message, "content")
        text = content if isinstance(content, str) else (to_text(content) or "")
        digest.append({
            "role": _field(message, "role") or "",
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
            "chars": len(text),
        })
    return digest


def _to_step_error(error: Any) -> StepError:
    code = getattr(getattr(error, "status", None), "code", None)
    message = getattr(error, "message", None) or str(error)
    return StepError(error_code=code, message=message)


def _coalesce_stream_chunks(chunks: list) -> list:
    """Fold streamed ``AssistantMessageChunk`` deltas into one merged message.

    Providers stream tool calls as fragments — id/name in the first delta,
    argument slices in later ones — so no single chunk holds a complete call.
    ``AssistantMessageChunk.__add__`` is the same merge the agent loop applies
    before executing the call; folding with it records exactly what the agent
    assembled. Mixed or non-chunk payloads are returned unchanged for the
    field-by-field fallback.
    """
    if len(chunks) < 2 or not all(isinstance(chunk, AssistantMessageChunk) for chunk in chunks):
        return chunks
    try:
        merged = chunks[0]
        for chunk in chunks[1:]:
            merged = merged + chunk
        return [merged]
    except Exception:
        # Capture must never break the agent loop; degrade to per-chunk extraction.
        return chunks


def _extract_llm_fields(result: Any) -> tuple[str | None, str | None, list[TrajectoryToolCall], StepMetrics | None,
                                              str | None]:
    """Extract (message, reasoning, tool_calls, metrics, model_name) from LLM outputs.

    Handles both a single ``AssistantMessage`` (invoke) and a list of chunks
    (stream). Chunk streams are folded via ``AssistantMessageChunk.__add__``
    so fragmented tool calls reassemble; for payloads that cannot fold, text
    fields are concatenated and tool calls / usage are taken from the last
    chunk carrying them.
    """
    chunks = _coalesce_stream_chunks(result) if isinstance(result, list) else [result]
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    raw_tool_calls: list[Any] = []
    usage = None
    for chunk in chunks:
        content = _field(chunk, "content")
        if content:
            content_parts.append(content if isinstance(content, str) else to_text(content))
        reasoning = _field(chunk, "reasoning_content")
        if reasoning:
            reasoning_parts.append(reasoning)
        tool_calls = _field(chunk, "tool_calls")
        if tool_calls:
            raw_tool_calls = tool_calls
        chunk_usage = _field(chunk, "usage_metadata")
        if chunk_usage is not None and (usage is None or _field(chunk_usage, "total_tokens")):
            usage = chunk_usage

    converted = [
        TrajectoryToolCall(
            tool_call_id=_field(call, "id"),
            function_name=_field(call, "name") or "",
            arguments=to_text(_field(call, "arguments")),
        )
        for call in raw_tool_calls
    ]
    metrics = None
    model_name = None
    if usage is not None:
        metrics = StepMetrics(
            input_tokens=_field(usage, "input_tokens") or 0,
            output_tokens=_field(usage, "output_tokens") or 0,
            total_tokens=_field(usage, "total_tokens") or 0,
            cache_tokens=_field(usage, "cache_tokens") or 0,
            cost=_field(usage, "total_cost") or 0.0,
            latency=_field(usage, "total_latency") or 0.0,
        )
        model_name = _field(usage, "model_name") or None
    message = "".join(content_parts) or None
    reasoning = "".join(reasoning_parts) or None
    return message, reasoning, converted, metrics, model_name


class TrajectoryAgentHandler(TraceExtAgentHandler):
    """Maps agent tracer events (LLM, tool, workflow, errors) to trajectory steps."""

    def __init__(self, collector: TrajectoryCollector):
        super().__init__()
        self._collector = collector
        # invoke_id -> info captured at the matching *_start event.
        self._pending: dict[str, dict] = {}

    def bind_trace(self, trace_id: str, session_id: str = None, agent_name: str = None) -> None:
        """Forward trace identity from ``Tracer.init()`` to the collector."""
        self._collector.bind_trace(trace_id, session_id=session_id, agent_name=agent_name)

    async def on_trace_close(self, trace_id: str, **kwargs) -> None:
        """Finalize and release the trace's buffer when its session finishes."""
        try:
            await self._collector.finalize_trace(trace_id)
        except Exception as exc:
            session_logger.warning("trajectory: failed to finalize trace on close: %s", exc)

    @staticmethod
    def _reset_context_token(info: dict, key: str, var) -> None:
        token = info.get(key)
        if token is None:
            return
        try:
            var.reset(token)
        except ValueError:
            # Token was created in a different context (e.g. a thread hop via
            # sync_trigger); there is nothing to restore here.
            pass

    def _note_start(self, span: TraceAgentSpan, instance_info: dict, **fields: Any) -> None:
        """Remember a start event's context until its end/error event claims it."""
        _bounded_put(self._pending, span.invoke_id, {"name": instance_info.get("class_name"), **fields})

    # --- LLM events ---
    async def on_llm_start(self, span: TraceAgentSpan, inputs: Any, instance_info: dict, **kwargs):
        fields = {}
        if self._collector.config.record_llm_input_digest:
            fields["input_digest"] = _input_digest(inputs)
        self._note_start(span, instance_info, **fields)

    async def on_llm_request(self, span: TraceAgentSpan, **kwargs):
        pass

    async def on_llm_end(self, span: TraceAgentSpan, outputs, **kwargs):
        info = self._pending.pop(span.invoke_id, {})
        try:
            message, reasoning, tool_calls, metrics, model_name = _extract_llm_fields(_unwrap(outputs))
            await self._collector.add_step(
                span.trace_id,
                role="agent",
                invoke_type="llm",
                message=message,
                model_name=model_name or info.get("name"),
                reasoning_content=reasoning,
                tool_calls=tool_calls or None,
                metrics=metrics,
                extra={"input_digest": info["input_digest"]} if info.get("input_digest") else None,
            )
        except Exception as exc:
            session_logger.warning("trajectory: failed to record llm end: %s", exc)

    async def on_llm_error(self, span: TraceAgentSpan, error, **kwargs):
        info = self._pending.pop(span.invoke_id, {})
        await self._record_error(span, "llm", info, error, role="agent")

    # --- Plugin (Tool) events ---
    async def on_plugin_start(self, span: TraceAgentSpan, inputs: Any, instance_info: dict, **kwargs):
        self._note_start(
            span,
            instance_info,
            arguments=to_text(_unwrap(inputs)),
            # Mark this context as "inside a tool of this trace" so subagent
            # sessions the tool spawns bind to it as their parent.
            parent_token=current_parent_trace_id.set(span.trace_id),
        )

    async def on_plugin_end(self, span: TraceAgentSpan, outputs, **kwargs):
        info = self._pending.pop(span.invoke_id, {})
        self._reset_context_token(info, "parent_token", current_parent_trace_id)
        try:
            function_name = info.get("name") or ""
            source_call_id = self._collector.claim_tool_call(span.trace_id, function_name)
            observation = Observation(
                results=[ObservationResult(source_call_id=source_call_id, content=to_text(_unwrap(outputs)))]
            )
            await self._collector.add_step(
                span.trace_id,
                role="system",
                invoke_type="plugin",
                observation=observation,
                extra={"function_name": function_name, "arguments": info.get("arguments")},
            )
        except Exception as exc:
            session_logger.warning("trajectory: failed to record plugin end: %s", exc)

    async def on_plugin_error(self, span: TraceAgentSpan, error, **kwargs):
        info = self._pending.pop(span.invoke_id, {})
        self._reset_context_token(info, "parent_token", current_parent_trace_id)
        await self._record_error(span, "plugin", info, error, role="system")

    # --- Prompt events ---
    async def on_prompt_start(self, span: TraceAgentSpan, inputs: Any, instance_info: dict, **kwargs):
        self._note_start(span, instance_info)

    async def on_prompt_end(self, span: TraceAgentSpan, outputs, **kwargs):
        self._pending.pop(span.invoke_id, None)

    async def on_prompt_error(self, span: TraceAgentSpan, error, **kwargs):
        info = self._pending.pop(span.invoke_id, {})
        await self._record_error(span, "prompt", info, error)

    # --- Chain events ---
    async def on_chain_start(self, span: TraceAgentSpan, inputs: Any, instance_info: dict, **kwargs):
        self._note_start(span, instance_info)

    async def on_chain_end(self, span: TraceAgentSpan, outputs, **kwargs):
        self._pending.pop(span.invoke_id, None)

    async def on_chain_error(self, span: TraceAgentSpan, error, **kwargs):
        info = self._pending.pop(span.invoke_id, {})
        await self._record_error(span, "chain", info, error)

    # --- Retriever events ---
    async def on_retriever_start(self, span: TraceAgentSpan, inputs: Any, instance_info: dict, **kwargs):
        self._note_start(span, instance_info)

    async def on_retriever_end(self, span: TraceAgentSpan, outputs, **kwargs):
        self._pending.pop(span.invoke_id, None)

    async def on_retriever_error(self, span: TraceAgentSpan, error, **kwargs):
        info = self._pending.pop(span.invoke_id, {})
        await self._record_error(span, "retriever", info, error)

    # --- Evaluator events ---
    async def on_evaluator_start(self, span: TraceAgentSpan, inputs: Any, instance_info: dict, **kwargs):
        self._note_start(span, instance_info)

    async def on_evaluator_end(self, span: TraceAgentSpan, outputs, **kwargs):
        self._pending.pop(span.invoke_id, None)

    async def on_evaluator_error(self, span: TraceAgentSpan, error, **kwargs):
        info = self._pending.pop(span.invoke_id, {})
        await self._record_error(span, "evaluator", info, error)

    # --- Workflow events (agent-level workflow invocation) ---
    async def on_workflow_start(self, span: TraceAgentSpan, inputs: Any, instance_info: dict, **kwargs):
        self._note_start(
            span,
            instance_info,
            # Mark this context so component events emitted while the
            # workflow runs route to this trace, not to whichever session
            # last re-bound the shared workflow handler.
            workflow_token=current_workflow_trace_id.set(span.trace_id),
        )

    async def on_workflow_end(self, span: TraceAgentSpan, outputs, **kwargs):
        info = self._pending.pop(span.invoke_id, {})
        self._reset_context_token(info, "workflow_token", current_workflow_trace_id)
        try:
            await self._collector.add_step(
                span.trace_id,
                role="system",
                invoke_type="workflow",
                message=to_text(_unwrap(outputs)),
                extra={"name": info.get("name")},
            )
        except Exception as exc:
            session_logger.warning("trajectory: failed to record workflow end: %s", exc)

    async def on_workflow_error(self, span: TraceAgentSpan, error, **kwargs):
        info = self._pending.pop(span.invoke_id, {})
        self._reset_context_token(info, "workflow_token", current_workflow_trace_id)
        await self._record_error(span, "workflow", info, error)

    async def _record_error(self, span: TraceAgentSpan, invoke_type: str, info: dict, error,
                            role: Literal["user", "agent", "system"] = "system"):
        try:
            extra = {"name": info.get("name")}
            if info.get("input_digest"):
                # A failing model call is exactly where "what was sent" matters.
                extra["input_digest"] = info["input_digest"]
            await self._collector.add_step(
                span.trace_id,
                role=role,
                invoke_type=invoke_type,
                model_name=info.get("name") if invoke_type == "llm" else None,
                error=_to_step_error(error),
                extra=extra,
            )
        except Exception as exc:
            session_logger.warning("trajectory: failed to record %s error: %s", invoke_type, exc)


class TrajectoryCompressionBridge:
    """Maps context-compression state events to trajectory steps.

    Context compression rewrites history outside any tracer span, so the
    only capture signal is ``ContextEvents.CONTEXT_COMPRESSION_STATE`` on the
    runner callback framework, carrying the compact summary that replaced
    the compressed messages and the compression model's token usage. Only
    terminal states become steps: ``completed`` records the replacement
    summary, ``failed`` records the error; ``started``/``noop``/``skipped``
    are dropped as noise. The owning trace is resolved from the session
    reference the event carries; events for unrecorded sessions are dropped
    by the collector's normal filtering. Capture failures are logged and
    never propagate into the emitting context engine.
    """

    def __init__(self, collector: TrajectoryCollector):
        self._collector = collector

    async def on_compression_state(self, session_ref: Any = None, state: Any = None, **kwargs):
        try:
            if state is None or getattr(state, "status", None) not in ("completed", "failed"):
                return
            trace_id = self._resolve_trace_id(session_ref)
            if trace_id is None:
                session_logger.debug("trajectory: compression state carries no resolvable trace; dropped")
                return
            error = None
            if state.status == "failed":
                error = StepError(message=state.error or state.summary or "context compression failed")
            await self._collector.add_step(
                trace_id,
                role="system",
                invoke_type="context_compression",
                message=state.compact_summary or state.summary or None,
                model_name=state.model or None,
                error=error,
                metrics=self._to_metrics(state),
                extra=self._to_extra(state, kwargs.get("context_id")),
            )
        except Exception as exc:
            session_logger.warning("trajectory: failed to record compression state: %s", exc)

    @staticmethod
    def _resolve_trace_id(session_ref: Any) -> str | None:
        tracer_fn = getattr(session_ref, "tracer", None)
        tracer = tracer_fn() if callable(tracer_fn) else None
        return getattr(tracer, "trace_id", None)

    @staticmethod
    def _to_metrics(state: Any) -> StepMetrics | None:
        usage = getattr(state, "compression_usage", None)
        if usage is None:
            return None
        return StepMetrics(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
            cache_tokens=usage.cache_tokens,
            cost=usage.total_cost,
            latency=(state.duration_ms or 0) / 1000,
        )

    @staticmethod
    def _to_extra(state: Any, context_id: Any) -> dict:
        extra = {
            "processor": state.processor,
            "status": state.status,
            "phase": state.phase,
            "operation_id": state.operation_id,
            "stats": state.summary,
        }
        if context_id:
            extra["context_id"] = context_id
        if state.before is not None:
            extra["before_messages"] = state.before.messages
            extra["before_tokens"] = state.before.tokens
        if state.after is not None:
            extra["after_messages"] = state.after.messages
            extra["after_tokens"] = state.after.tokens
        if state.saved is not None:
            extra["saved_tokens"] = state.saved.tokens
            extra["saved_percent"] = state.saved.percent
        return extra


class TrajectoryWorkflowHandler(TraceExtWorkflowHandler):
    """Maps workflow component events to trajectory steps.

    Workflow events carry no trace id. Routing prefers the async-context
    marker set by ``TrajectoryAgentHandler.on_workflow_start`` (correct
    under concurrency), falling back to the instance-level trace id injected
    via ``set_trace_id`` (last session wins) for events arriving outside any
    marked context.
    """

    def __init__(self, collector: TrajectoryCollector):
        super().__init__()
        self._collector = collector
        # invoke_id -> component metadata gathered across events.
        self._components: dict[str, dict] = {}

    def _trace_key(self) -> str:
        return current_workflow_trace_id.get() or self._trace_id or _WORKFLOW_FALLBACK_TRACE_ID

    @staticmethod
    def _component_extra(invoke_id: str, meta: dict) -> dict:
        extra = {key: meta[key] for key in _COMPONENT_META_KEYS if meta.get(key) is not None}
        extra["invoke_id"] = invoke_id
        return extra

    def _component(self, invoke_id: str) -> dict:
        entry = self._components.get(invoke_id)
        if entry is None:
            entry = {}
            _bounded_put(self._components, invoke_id, entry)
        return entry

    def _merge_component_meta(self, invoke_id: str, metadata: dict | None):
        if not metadata:
            return
        self._component(invoke_id).update(metadata)

    async def on_call_start(self, invoke_id: str, metadata: dict = None, inputs: Any = None,
                            need_send: bool = False, source_ids: list = None, **kwargs):
        self._merge_component_meta(invoke_id, metadata)

    async def on_pre_invoke(self, invoke_id: str, inputs: Any, component_metadata: dict,
                            need_send: bool = False, **kwargs):
        self._merge_component_meta(invoke_id, component_metadata)

    async def on_pre_stream(self, invoke_id: str, chunk, need_send: bool = False, **kwargs):
        pass

    async def on_invoke(self, invoke_id: str, on_invoke_data: dict = None,
                        exception: Exception = None, **kwargs):
        if exception is None:
            return
        try:
            await self._collector.add_step(
                self._trace_key(),
                role="system",
                invoke_type="workflow_component",
                error=_to_step_error(exception),
                extra=self._component_extra(invoke_id, self._components.get(invoke_id, {})),
            )
        except Exception as exc:
            session_logger.warning("trajectory: failed to record component error: %s", exc)

    async def on_post_invoke(self, invoke_id: str, outputs, inputs=None, **kwargs):
        self._component(invoke_id)["outputs"] = outputs

    async def on_post_stream(self, invoke_id: str, chunk, **kwargs):
        pass

    async def on_call_done(self, invoke_id: str, outputs: Any = None, **kwargs):
        try:
            meta = self._components.pop(invoke_id, {})
            payload = outputs if outputs is not None else meta.get("outputs")
            extra = self._component_extra(invoke_id, meta)
            await self._collector.add_step(
                self._trace_key(),
                role="system",
                invoke_type="workflow_component",
                message=to_text(payload),
                extra=extra,
            )
        except Exception as exc:
            session_logger.warning("trajectory: failed to record component done: %s", exc)

    async def on_interact(self, invoke_id: str, inputs: Any, component_metadata: dict,
                          need_send: bool = False, **kwargs):
        self._merge_component_meta(invoke_id, component_metadata)
